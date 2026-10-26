#!/usr/bin/env python3
"""Safely patch a local VCE v3.19 engine for CPU Whisper + MLX/Metal overlap.

Default mode is dry-run. Use --apply to write after preflight succeeds.
The script makes timestamped backups and refuses unknown source layouts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import re
import shutil
import sys
from pathlib import Path

MARKER = "# VCE_MLX_METAL_CPU_PARALLEL_V1"


class PatchError(RuntimeError):
    pass


RUNTIME_HELPERS = '''# VCE_MLX_METAL_CPU_PARALLEL_V1
VCE_MLX_RUNTIME_DIAGNOSTICS: Dict[str, Dict[str, Any]] = {}


def _vce_configure_mlx_metal(stage: str) -> Dict[str, Any]:
    """Select the MLX GPU on native Apple Silicon and fail closed without Metal."""
    if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
        raise RuntimeError(
            f"[VCE MLX] {stage}: MLX/Metal requires native Apple Silicon macOS; "
            f"got {platform.system()}/{platform.machine()}."
        )
    import mlx.core as mx

    if not mx.metal.is_available() or not mx.is_available(mx.gpu):
        raise RuntimeError(f"[VCE MLX] {stage}: Metal GPU is unavailable; CPU fallback is disabled.")
    mx.set_default_device(mx.gpu)
    if mx.default_device() != mx.gpu:
        raise RuntimeError(f"[VCE MLX] {stage}: failed to select the MLX GPU device.")

    # Evaluate and synchronize a real GPU operation; no large model allocation.
    probe = mx.array([1.0, 2.0, 3.0]) * 2
    mx.eval(probe)
    mx.synchronize()
    if [float(x) for x in probe.tolist()] != [2.0, 4.0, 6.0]:
        raise RuntimeError(f"[VCE MLX] {stage}: Metal smoke test returned an unexpected result.")

    try:
        mlx_version = __import__("importlib.metadata", fromlist=["version"]).version("mlx")
    except Exception:
        mlx_version = "unknown"
    try:
        device_info = {str(k): str(v) for k, v in mx.device_info(mx.gpu).items()}
    except Exception:
        device_info = {}
    info = {
        "backend": "MLX",
        "accelerator": "Metal",
        "stage": stage,
        "mlx_version": mlx_version,
        "device": str(mx.default_device()),
        "device_info": device_info,
        "metal_smoke_test": "passed",
    }
    VCE_MLX_RUNTIME_DIAGNOSTICS[stage] = info
    print(f"[VCE runtime] {json.dumps(info, ensure_ascii=False, sort_keys=True)}")
    return info


def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Whisper is pinned to CPU so it can overlap with MLX/Metal inference."""
    if acceleration_enabled:
        print("[VCE Whisper] CPU/MLX parallel mode: legacy whisper_acceleration is ignored.")
    device_override = os.environ.get("VIDEOCONTEXT_WHISPER_DEVICE", "cpu").strip().lower()
    if device_override != "cpu":
        raise RuntimeError(
            "VCE parallel mode requires VIDEOCONTEXT_WHISPER_DEVICE=cpu; "
            "Whisper GPU/MPS is incompatible with the requested CPU/GPU overlap."
        )
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Load OpenAI Whisper explicitly on CPU."""
    device = resolve_whisper_device(acceleration_enabled)
    print(f"[VCE Whisper] Loading {model_name} on {device}; running concurrently with MLX/Metal.")
    model = whisper.load_model(model_name, device="cpu")
    actual = str(getattr(model, "device", "")).lower()
    if actual not in {"cpu", "cpu:0"}:
        raise RuntimeError(f"[VCE Whisper] Expected CPU model but got device={actual!r}.")
    return model


'''


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected one exact match, found {count}. No file was changed.")
    return text.replace(old, new, 1)


def patch_engine_text(text: str) -> str:
    if MARKER in text:
        return text
    required = [
        "def resolve_whisper_device(",
        "def run_text_llm_postprocess(",
        "class MLXEngine(VLMProvider):",
        '            from mlx_vlm import load, generate',
        '                whisper_segments: List[Dict[str, Any]] = []',
        '                if not skip_visual or generate_summary:',
        '                set_job_progress(job_id, "summary", None, "Формируем сводку по всем сценам"',
        '    finally:\n        if cap is not None:',
    ]
    for token in required:
        if token not in text:
            raise PatchError(f"Could not find required source anchor: {token!r}. No file was changed.")

    # Replace the Whisper placement functions and insert the fail-closed MLX/Metal helper.
    helper_anchor = "# Optionnel : supprimer le warning HF tokenizers"
    i = text.index("def resolve_whisper_device(")
    j = text.index(helper_anchor, i)
    if j <= i:
        raise PatchError("Whisper helper boundaries are invalid.")
    text = text[:i] + RUNTIME_HELPERS + text[j:]

    # The VLM loader used to permit VIDEOCONTEXT_MLX_DEVICE=cpu. Now only Metal is accepted.
    text, n = re.subn(
        r'            device_override = os\.environ\.get\("VIDEOCONTEXT_MLX_DEVICE".*?'
        r'            from mlx_vlm import load, generate',
        '''            device_override = os.environ.get(
                "VIDEOCONTEXT_MLX_DEVICE", str(setting("mlx_device", "metal"))
            ).strip().lower()
            if device_override not in {"auto", "metal"}:
                raise ValueError(
                    "VIDEOCONTEXT_MLX_DEVICE must be auto or metal in CPU/Metal parallel mode"
                )
            _vce_configure_mlx_metal("vlm")
            from mlx_vlm import load, generate''',
        text,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise PatchError(f"MLX VLM loader anchor matched {n} times; no file was written.")

    # The final text-analysis stage also loads MLX weights, so select Metal in that call path.
    llm_anchor = (
        '    """Load the selected MLX VLM-family checkpoint in text-only mode and return validated enrichment."""\n'
        '    import gc\n'
    )
    llm_replacement = llm_anchor + '    _vce_configure_mlx_metal("text_llm")\n'
    text = _replace_once(text, llm_anchor, llm_replacement, "Text LLM Metal selection")

    # Replace the serial Whisper block with a CPU task that runs while the VLM stage proceeds.
    block_start_token = "                whisper_segments: List[Dict[str, Any]] = []\n\n                if not skip_audio:\n"
    block_start = text.find(block_start_token)
    if block_start < 0:
        raise PatchError("Could not locate the serial Whisper block.")
    block_end_token = "\n                if not skip_visual or generate_summary:\n"
    block_end = text.find(block_end_token, block_start + len(block_start_token))
    if block_end < 0:
        raise PatchError("Could not locate the VLM stage following the Whisper block.")

    parallel_block = '''                whisper_segments: List[Dict[str, Any]] = []
                _vce_whisper_task = None

                async def _vce_run_whisper_cpu_stage() -> List[Dict[str, Any]]:
                    nonlocal whisper_load_time, whisper_infer_time
                    nonlocal _vce_whisper_started_at, _vce_whisper_finished_at
                    _vce_whisper_started_at = time.perf_counter()
                    try:
                        set_job_progress(
                            job_id, "whisper", None,
                            "Whisper CPU: распознаём речь параллельно с MLX/Metal VLM",
                            indeterminate=True, stage_index=2, stage_count=6,
                        )
                        print("[VCE Whisper] Starting CPU ASR in parallel with the GPU stage.")
                        if RAM_MODE == "ram+":
                            if target_whisper in WHISPER_CACHE:
                                w_model = WHISPER_CACHE[target_whisper]
                                whisper_load_time = 0.0
                                print(f"Whisper '{target_whisper}' reused from CPU cache.")
                            else:
                                t0 = time.time()
                                w_model = await asyncio.to_thread(
                                    load_whisper_model, target_whisper, acceleration_enabled=False
                                )
                                WHISPER_CACHE[target_whisper] = w_model
                                whisper_load_time = time.time() - t0
                        else:
                            t0 = time.time()
                            w_model = await asyncio.to_thread(
                                load_whisper_model, target_whisper, acceleration_enabled=False
                            )
                            whisper_load_time = time.time() - t0

                        # Older RAM-mode caches may have been created with MPS enabled.
                        w_model = await asyncio.to_thread(w_model.to, "cpu")
                        t_infer_start = time.perf_counter()
                        audio_res = await asyncio.to_thread(
                            w_model.transcribe,
                            temp_path,
                            word_timestamps=word_timestamps,
                            condition_on_previous_text=False,
                            temperature=0.0,
                            compression_ratio_threshold=1.8,
                        )
                        whisper_infer_time = time.perf_counter() - t_infer_start
                        result = audio_res.get("segments", []) or []
                        set_job_progress(
                            job_id, "whisper_done", 25,
                            f"Whisper CPU завершён: фрагментов речи {len(result)}",
                            stage_index=2, stage_count=6, transcript_segments=len(result),
                        )
                        print(f"[VCE Whisper] CPU inference: {whisper_infer_time:.2f}s")
                        if RAM_MODE == "ram-":
                            del w_model
                            gc.collect()
                        return result
                    finally:
                        _vce_whisper_finished_at = time.perf_counter()

                if not skip_audio:
                    _vce_whisper_task = asyncio.create_task(_vce_run_whisper_cpu_stage())
                '''
    text = text[:block_start] + parallel_block + text[block_end:]

    # Add per-request stage timestamps for measured overlap telemetry.
    timing_anchor = "    whisper_load_time = 0.0\n\n    target_whisper"
    timing_replacement = (
        "    whisper_load_time = 0.0\n"
        "    _vce_whisper_started_at = None\n"
        "    _vce_whisper_finished_at = None\n"
        "    _vce_vlm_scene_started_at = None\n"
        "    _vce_vlm_scene_finished_at = None\n\n"
        "    target_whisper"
    )
    text = _replace_once(text, timing_anchor, timing_replacement, "Parallel stage timestamps")

    # Start the VLM timing and opportunistically pass ASR context to later scenes if ready.
    scene_start_anchor = (
        '                set_job_progress(job_id, "scene_analysis", None, "Начинаем OCR и визуальный анализ сцен", '
        "indeterminate=True, stage_index=4, stage_count=6)\n"
        "                cap = cv2.VideoCapture(temp_path)\n"
    )
    scene_start_replacement = scene_start_anchor + "                _vce_vlm_scene_started_at = time.perf_counter()\n"
    text = _replace_once(text, scene_start_anchor, scene_start_replacement, "VLM scene timer start")

    loop_anchor = "                for idx, scene in enumerate(scenes_raw):\n                    if cancel_event.is_set():"
    loop_start = text.find(loop_anchor, text.find(scene_start_replacement))
    if loop_start < 0:
        raise PatchError("Could not locate the VLM per-scene loop.")
    loop_replacement = (
        "                for idx, scene in enumerate(scenes_raw):\n"
        "                    if _vce_whisper_task is not None and _vce_whisper_task.done():\n"
        "                        whisper_segments = _vce_whisper_task.result()\n"
        "                    if cancel_event.is_set():"
    )
    text = text[:loop_start] + loop_replacement + text[loop_start + len(loop_anchor):]

    summary_anchor = (
        '                set_job_progress(job_id, "summary", None, "Формируем сводку по всем сценам", '
        "indeterminate=True, stage_index=5, stage_count=6)\n"
    )
    overlap_block = '''                _vce_vlm_scene_finished_at = time.perf_counter()
                if _vce_whisper_task is not None:
                    whisper_segments = await _vce_whisper_task
                    # Attach every ASR transcript after the concurrent visual pass. This keeps the
                    # evidence complete even for scenes whose VLM prompts ran before ASR finished.
                    for _vce_idx, _vce_scene in enumerate(scenes_raw):
                        if _vce_idx >= len(final_segments):
                            break
                        _vce_segment = final_segments[_vce_idx]
                        _vce_segment["audio_transcript"] = (
                            transcript_for_scene(_vce_scene, whisper_segments)
                            if whisper_segments else ""
                        )
                        _vce_segment["audio_features"] = (
                            compute_audio_features_for_scene(_vce_scene, whisper_segments)
                            if (whisper_segments and enable_audio_features) else {}
                        )
                _vce_overlap_seconds = 0.0
                if all(v is not None for v in (
                    _vce_whisper_started_at, _vce_whisper_finished_at,
                    _vce_vlm_scene_started_at, _vce_vlm_scene_finished_at,
                )):
                    _vce_overlap_seconds = max(
                        0.0,
                        min(_vce_whisper_finished_at, _vce_vlm_scene_finished_at)
                        - max(_vce_whisper_started_at, _vce_vlm_scene_started_at),
                    )
                print(f"[VCE parallelism] CPU Whisper / MLX VLM overlap: {_vce_overlap_seconds:.2f}s")
'''
    text = _replace_once(text, summary_anchor, overlap_block + summary_anchor, "ASR join and scene transcript attachment")

    # Runtime backend evidence in returned timing JSON.
    vlm_timing_anchor = (
        '            "vlm": {\n'
        '                "model": target_vlm,\n'
        '                "load_time": round(vlm_load_time, 3),\n'
        '                "inference_time": round(vlm_infer_time, 3),\n'
        '            },'
    )
    vlm_timing_replacement = (
        '            "vlm": {\n'
        '                "model": target_vlm,\n'
        '                "load_time": round(vlm_load_time, 3),\n'
        '                "inference_time": round(vlm_infer_time, 3),\n'
        '                "runtime": VCE_MLX_RUNTIME_DIAGNOSTICS.get("vlm", {}),\n'
        '            },\n'
        '            "parallelism": {\n'
        '                "whisper_device": "cpu",\n'
        '                "whisper_vlm_overlap_seconds": round(_vce_overlap_seconds, 3) if "_vce_overlap_seconds" in locals() else 0.0,\n'
        '            },'
    )
    text = _replace_once(text, vlm_timing_anchor, vlm_timing_replacement, "Runtime telemetry")

    text_llm_timing_anchor = (
        '        timings["text_llm"] = {\n'
        '            "enabled": bool(text_llm_enabled),\n'
        '            "model": text_llm_model if text_llm_enabled else None,\n'
        '            "status": text_llm_status,\n'
        '            "inference_time": round(text_llm_infer_time, 3),\n'
        '            "error": text_llm_error or None,\n'
        '        }'
    )
    text_llm_timing_replacement = (
        '        timings["text_llm"] = {\n'
        '            "enabled": bool(text_llm_enabled),\n'
        '            "model": text_llm_model if text_llm_enabled else None,\n'
        '            "status": text_llm_status,\n'
        '            "inference_time": round(text_llm_infer_time, 3),\n'
        '            "runtime": VCE_MLX_RUNTIME_DIAGNOSTICS.get("text_llm", {}),\n'
        '            "error": text_llm_error or None,\n'
        '        }'
    )
    text = _replace_once(text, text_llm_timing_anchor, text_llm_timing_replacement, "Text LLM telemetry")

    # Guard against accidental partial edits.
    for marker in (
        MARKER,
        '_vce_run_whisper_cpu_stage',
        '_vce_configure_mlx_metal("vlm")',
        '_vce_configure_mlx_metal("text_llm")',
        '"whisper_vlm_overlap_seconds"',
    ):
        if marker not in text:
            raise PatchError(f"Post-patch validation failed: missing {marker!r}.")
    return text


def patch_settings_text(text: str) -> str:
    try:
        settings = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PatchError(f"Settings file is not valid JSON: {exc}") from exc
    if not isinstance(settings, dict):
        raise PatchError("Settings root must be a JSON object.")
    settings["whisper_acceleration"] = False
    settings["mlx_device"] = "metal"
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=Path.home() / "VideoContext-Engine" / "VideoContextEngine_v3.19.py")
    parser.add_argument("--settings", type=Path, default=Path.home() / "VideoContext-Engine" / "videocontext_settings.json")
    parser.add_argument("--apply", action="store_true", help="write files after creating backups (default: dry-run)")
    args = parser.parse_args()

    if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
        print("WARNING: patch preparation is allowed off-host, but runtime is designed for native Apple Silicon macOS.")

    if not args.engine.is_file():
        print(f"ERROR: engine file not found: {args.engine}", file=sys.stderr)
        return 2
    if not args.settings.is_file():
        print(f"ERROR: settings file not found: {args.settings}", file=sys.stderr)
        return 2

    original_engine = args.engine.read_text(encoding="utf-8")
    original_settings = args.settings.read_text(encoding="utf-8")
    try:
        patched_engine = patch_engine_text(original_engine)
        patched_settings = patch_settings_text(original_settings)
        compile(patched_engine, str(args.engine), "exec")
    except Exception as exc:
        print(f"ERROR: preflight failed; files were not changed: {exc}", file=sys.stderr)
        return 2

    if patched_engine == original_engine and patched_settings == original_settings:
        print("Already patched; no changes needed.")
        return 0

    print("Preflight passed.")
    print(f"Engine: {args.engine} ({len(original_engine)} -> {len(patched_engine)} characters)")
    print(f"Settings: {args.settings} (Whisper CPU, MLX device=metal)")
    print("Changes: CPU Whisper scheduled concurrently with VLM scene analysis; both MLX stages fail closed unless Metal is available.")
    if not args.apply:
        print("Dry run only. Re-run with --apply to write changes.")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    engine_backup = args.engine.with_name(args.engine.name + f".before_mlx_metal_{stamp}.bak")
    settings_backup = args.settings.with_name(args.settings.name + f".before_mlx_metal_{stamp}.bak")
    shutil.copy2(args.engine, engine_backup)
    shutil.copy2(args.settings, settings_backup)
    try:
        args.engine.write_text(patched_engine, encoding="utf-8")
        args.settings.write_text(patched_settings, encoding="utf-8")
    except Exception:
        # Restore from backups if either write fails.
        shutil.copy2(engine_backup, args.engine)
        shutil.copy2(settings_backup, args.settings)
        raise
    print(f"Applied. Engine backup: {engine_backup}")
    print(f"Settings backup: {settings_backup}")
    print("Restart the VCE server, then run the runtime verifier and end-to-end benchmark.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
