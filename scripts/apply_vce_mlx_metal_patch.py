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
import textwrap
from pathlib import Path

MARKER = "# VCE_MLX_METAL_CPU_PARALLEL_MLXWHISPER_V2"


class PatchError(RuntimeError):
    pass


RUNTIME_HELPERS = '''# VCE_MLX_METAL_CPU_PARALLEL_MLXWHISPER_V2
VCE_MLX_RUNTIME_DIAGNOSTICS: Dict[str, Dict[str, Any]] = {}
_VCE_MLX_WHISPER_LOCK = threading.Lock()


def _vce_mlx_whisper_model_id(model_name: str) -> str:
    """Map VCE's Whisper preset names to compatible MLX Whisper checkpoints."""
    name = str(model_name).strip()
    if not name:
        raise ValueError("Whisper model name cannot be empty.")
    if "/" in name or os.path.exists(name):
        return name
    key = name.lower().replace("_", "-")
    aliases = {
        "tiny": "mlx-community/whisper-tiny",
        "base": "mlx-community/whisper-base",
        "small": "mlx-community/whisper-small",
        "medium": "mlx-community/whisper-medium",
        "large": "mlx-community/whisper-large-v3-mlx",
        "large-v1": "mlx-community/whisper-large-v1",
        "large-v2": "mlx-community/whisper-large-v2",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
        "turbo": "mlx-community/whisper-large-v3-turbo",
    }
    if key in aliases:
        return aliases[key]
    raise ValueError(
        f"Unsupported Whisper preset {name!r}. Use a known preset or a local/Hugging Face model path."
    )


class _VCEWhisperMLXCPU:
    """mlx-whisper adapter that pins each MLX operation to a CPU thread-local stream."""

    def __init__(self, model_name: str):
        model_override = os.environ.get("VIDEOCONTEXT_MLX_WHISPER_MODEL", "").strip()
        self.model_name = model_override or model_name
        self.model_path = _vce_mlx_whisper_model_id(self.model_name)
        self.device = "cpu"
        print(
            f"[VCE Whisper] backend=mlx-whisper device=cpu stream=mx.cpu "
            f"model={self.model_path}; weights load on first transcription."
        )

    def transcribe(self, audio_path: str, **kwargs: Any) -> Dict[str, Any]:
        import mlx.core as mx
        import mlx_whisper

        started = time.perf_counter()
        cpu_stream = mx.new_thread_local_stream(mx.cpu)
        with _VCE_MLX_WHISPER_LOCK:
            with mx.stream(cpu_stream):
                result = mlx_whisper.transcribe(
                    audio_path,
                    path_or_hf_repo=self.model_path,
                    **kwargs,
                )
                mx.synchronize(cpu_stream)
        elapsed = time.perf_counter() - started
        VCE_MLX_RUNTIME_DIAGNOSTICS["whisper"] = {
            "backend": "mlx-whisper",
            "device": "cpu",
            "stream": "mx.cpu",
            "model": self.model_path,
            "inference_seconds_including_first_load": round(elapsed, 3),
            "model_load_included_in_first_inference": True,
        }
        print(
            "[VCE runtime] "
            + json.dumps(VCE_MLX_RUNTIME_DIAGNOSTICS["whisper"], ensure_ascii=False, sort_keys=True)
        )
        return result


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

    probe = mx.array([1.0, 2.0, 3.0]) * 2
    mx.eval(probe)
    mx.synchronize(mx.gpu)
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
    """Whisper is pinned to MLX CPU stream to overlap with MLX/Metal inference."""
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Return an mlx-whisper adapter; MLX model weights load inside the CPU stream."""
    return _VCEWhisperMLXCPU(model_name)

'''


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected one exact match, found {count}. No file was changed.")
    return text.replace(old, new, 1)


def patch_engine_text(text: str) -> str:
    if MARKER in text:
        return text
    if "# VCE_MLX_METAL_CPU_PARALLEL_V1" in text:
        raise PatchError("Detected previous CPU/Metal patch version. Restore the pre-patch backup before applying MLX Whisper CPU-stream v2.")
    required_tokens = [
        "def resolve_whisper_device(",
        "def run_text_llm_postprocess(",
        "class MLXEngine(VLMProvider):",
        "from mlx_vlm import load, generate",
        "enable_mlx_whisper",
        "whisper_segments: List[Dict[str, Any]] = []",
        "if not skip_visual or generate_summary:",
        'set_job_progress(job_id, "summary", None, "Формируем сводку по всем сценам"',
    ]
    for token in required_tokens:
        if token not in text:
            raise PatchError(f"Could not find required source anchor: {token!r}. No file was changed.")
    # Indentation varies between local VCE edits, so validate structural lines
    # by their stripped text rather than requiring a specific number of spaces.
    for line_text in (
        "whisper_segments: List[Dict[str, Any]] = []",
        "if not skip_visual or generate_summary:",
    ):
        occurrences = [line for line in text.splitlines() if line.strip() == line_text]
        if len(occurrences) != 1:
            raise PatchError(
                f"Expected one structurally matching line {line_text!r}, got {len(occurrences)}. "
                "No file was changed."
            )
    if not re.search(r"(?m)^[ \t]*finally:\r?\n[ \t]*if cap is not None:", text):
        raise PatchError("Could not find endpoint cleanup block. No file was changed.")

    # Replace the Whisper placement functions# Replace the Whisper placement functions and insert the fail-closed MLX/Metal helper.
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

    # Replace the serial Whisper block using its discovered indentation.
    block_start_re = re.compile(
        r"(?m)^(?P<indent>[ \t]*)whisper_segments: List\[Dict\[str, Any\]\] = \[\]\r?\n"
        r"\r?\n(?P=indent)if not skip_audio:\s*$"
    )
    block_start_match = block_start_re.search(text)
    if not block_start_match:
        raise PatchError("Could not locate the serial Whisper block. No file was changed.")
    block_start = block_start_match.start()
    base_indent = block_start_match.group("indent")

    block_end_re = re.compile(r"(?m)^[ \t]*if not skip_visual or generate_summary:\s*$")
    block_end_match = block_end_re.search(text, block_start_match.end())
    if not block_end_match:
        raise PatchError("Could not locate the VLM stage following the Whisper block. No file was changed.")
    block_end = block_end_match.start()

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

                        # mlx-whisper assigns its operations to a thread-local mx.cpu stream.
                        # Do not call .to("cpu"): MLX uses unified memory and explicit stream placement.
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
    parallel_block = textwrap.indent(textwrap.dedent(parallel_block).strip("\n"), base_indent) + "\n\n"
    text = text[:block_start] + parallel_block + text[block_end:]

    # Add per-request stage timestamps    # Add per-request stage timestamps for measured overlap telemetry.
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

    # Find scene-analysis and summary boundaries independently of source indentation.
    scene_progress_re = re.compile(
        r'(?m)^(?P<indent>[ \t]*)set_job_progress\(job_id, "scene_analysis", None, "Начинаем OCR и визуальный анализ сцен".*$'
    )
    scene_progress_matches = list(scene_progress_re.finditer(text))
    if len(scene_progress_matches) != 1:
        raise PatchError(f"Expected one scene-analysis progress line, found {len(scene_progress_matches)}.")
    scene_progress_match = scene_progress_matches[0]
    cap_re = re.compile(r"(?m)^(?P<indent>[ \t]*)cap = cv2.VideoCapture\(temp_path\)\s*$")
    cap_match = None
    for candidate in cap_re.finditer(text, scene_progress_match.end()):
        if candidate.group("indent") == scene_progress_match.group("indent"):
            cap_match = candidate
            break
    if cap_match is None:
        raise PatchError("Could not find VideoCapture at the scene-analysis stage.")
    cap_end = cap_match.end()
    scene_indent = cap_match.group("indent")
    text = text[:cap_end] + "\n" + scene_indent + "_vce_vlm_scene_started_at = time.perf_counter()" + text[cap_end:]

    loop_re = re.compile(r"(?m)^(?P<indent>[ \t]*)for idx, scene in enumerate\(scenes_raw\):\s*$")
    loop_matches = [m for m in loop_re.finditer(text, cap_end) if len(m.group("indent")) >= len(scene_indent)]
    if len(loop_matches) != 1:
        raise PatchError(f"Expected one scene loop after VideoCapture, found {len(loop_matches)}.")
    loop_match = loop_matches[0]
    loop_indent = loop_match.group("indent")
    loop_line_end = loop_match.end()
    loop_insert = (
        "\n" + loop_indent + "    if _vce_whisper_task is not None and _vce_whisper_task.done():"
        + "\n" + loop_indent + "        whisper_segments = _vce_whisper_task.result()"
    )
    text = text[:loop_line_end] + loop_insert + text[loop_line_end:]

    summary_re = re.compile(
        r'(?m)^(?P<indent>[ \t]*)set_job_progress\(job_id, "summary", None, "Формируем сводку по всем сценам".*$'
    )
    summary_matches = list(summary_re.finditer(text, loop_line_end))
    if len(summary_matches) != 1:
        raise PatchError(f"Expected one final summary boundary after the scene loop, found {len(summary_matches)}.")
    summary_match = summary_matches[0]
    summary_indent = summary_match.group("indent")

    overlap_block = '''                _vce_vlm_scene_finished_at = time.perf_counter()
                if _vce_whisper_task is not None:
                    whisper_segments = await _vce_whisper_task
                    # Attach every ASR transcript after the concurrent visual pass.
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
    overlap_block = textwrap.indent(textwrap.dedent(overlap_block).strip("\n"), summary_indent) + "\n"
    text = text[:summary_match.start()] + overlap_block + text[summary_match.start():]

    # Runtime backend evidence    # Runtime backend evidence in returned timing JSON.
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

    # Do not delete a temporary video file while a worker-thread ASR call is still using it.
    finally_anchor = "    finally:\n        if cap is not None:"
    finally_replacement = (
        "    finally:\n"
        "        _vce_pending_whisper_task = locals().get('_vce_whisper_task')\n"
        "        if _vce_pending_whisper_task is not None:\n"
        "            try:\n"
        "                await _vce_pending_whisper_task\n"
        "            except BaseException:\n"
        "                # Preserve the original endpoint exception; the task is now observed.\n"
        "                pass\n"
        "        if cap is not None:"
    )
    text = _replace_once(text, finally_anchor, finally_replacement, "ASR task cleanup")

    # Guard against accidental partial edits.
    for marker in (
        MARKER,
        '_vce_run_whisper_cpu_stage',
        'mlx_whisper.transcribe(',
        'mx.new_thread_local_stream(mx.cpu)',
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
    settings["enable_mlx_whisper"] = True
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
    print(f"Settings: {args.settings} (mlx-whisper on CPU stream, VLM/LLM on MLX/Metal)")
    print("Changes: mlx-whisper CPU stream overlaps VLM scene inference on Metal; VLM and final text analysis require MLX/Metal.")
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
