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
MLX_THREAD_AFFINITY_MARKER = "# VCE_MLX_VLM_SINGLE_THREAD_EXECUTOR_V1"


class PatchError(RuntimeError):
    pass


RUNTIME_HELPERS = '''# VCE_MLX_METAL_CPU_PARALLEL_MLXWHISPER_V2
VCE_MLX_RUNTIME_DIAGNOSTICS: Dict[str, Dict[str, Any]] = {}
import threading as _vce_threading
_VCE_MLX_WHISPER_LOCK = _vce_threading.Lock()


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

    def release(self) -> None:
        """Release mlx-whisper's private global model cache in RAM- mode."""
        import gc
        import importlib

        with _VCE_MLX_WHISPER_LOCK:
            module = importlib.import_module("mlx_whisper.transcribe")
            holder = getattr(module, "ModelHolder", None)
            if holder is not None and getattr(holder, "model_path", None) == self.model_path:
                holder.model = None
                holder.model_path = None
            gc.collect()


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


MLX_THREAD_RUNTIME_HELPERS = '''
# VCE_MLX_VLM_SINGLE_THREAD_EXECUTOR_V1
import concurrent.futures as _vce_mlx_concurrent_futures
import functools as _vce_mlx_functools

# mlx-vlm creates a thread-local generation stream at import time. Keep model
# loading and all generation calls on the same dedicated OS thread.
_VCE_MLX_EXECUTOR = _vce_mlx_concurrent_futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="vce-mlx-metal"
)


async def _vce_mlx_run(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    call = _vce_mlx_functools.partial(fn, *args, **kwargs)
    return await loop.run_in_executor(_VCE_MLX_EXECUTOR, call)

'''


def patch_mlx_thread_affinity(text: str) -> str:
    """Upgrade VCE's V2 patch so mlx-vlm imports, loads, and generates on one thread."""
    if MLX_THREAD_AFFINITY_MARKER in text:
        return text
    if MARKER not in text:
        raise PatchError("Cannot add MLX thread affinity before the V2 runtime patch is present.")

    diagnostics_anchor = "VCE_MLX_RUNTIME_DIAGNOSTICS: Dict[str, Dict[str, Any]] = {}\\n"
    if text.count(diagnostics_anchor) != 1:
        raise PatchError("Expected one runtime diagnostics anchor for the MLX executor helper.")
    text = text.replace(
        diagnostics_anchor,
        diagnostics_anchor + MLX_THREAD_RUNTIME_HELPERS,
        1,
    )

    load_pattern = re.compile(
        r"await asyncio\\.to_thread\\(\\s*vlm_engine\\.load_model,\\s*target_vlm\\s*\\)"
    )
    text, load_count = load_pattern.subn(
        "await _vce_mlx_run(vlm_engine.load_model, target_vlm)",
        text,
    )
    if load_count != 1:
        raise PatchError(
            f"Expected one VLM load through asyncio.to_thread, found {load_count}. "
            "No file was changed."
        )

    inference_patterns = (
        (
            re.compile(r"await asyncio\\.to_thread\\(\\s*_vce_describe_scene_with_memory_fallback\\s*,"),
            "await _vce_mlx_run(_vce_describe_scene_with_memory_fallback,",
        ),
        (
            re.compile(r"await asyncio\\.to_thread\\(\\s*vlm_engine\\.describe_scene\\s*,"),
            "await _vce_mlx_run(vlm_engine.describe_scene,",
        ),
    )
    inference_count = 0
    for pattern, replacement in inference_patterns:
        text, count = pattern.subn(replacement, text)
        inference_count += count
    if inference_count < 1:
        raise PatchError(
            "Could not route any VLM scene-generation call to the dedicated MLX thread."
        )

    # The optional final text-LLM uses the same mlx-vlm internals and must share
    # the same executor to keep its generation stream thread-affine as well.
    text, text_llm_count = re.subn(
        r"await asyncio\\.to_thread\\(\\s*run_text_llm_postprocess\\s*,",
        "await _vce_mlx_run(run_text_llm_postprocess,",
        text,
    )

    for forbidden in (
        "await asyncio.to_thread(vlm_engine.load_model, target_vlm)",
        "await asyncio.to_thread(vlm_engine.describe_scene,",
        "await asyncio.to_thread(_vce_describe_scene_with_memory_fallback,",
    ):
        if forbidden in text:
            raise PatchError(f"Post-patch validation found a generic worker-thread MLX call: {forbidden}")

    if "_vce_mlx_run(run_text_llm_postprocess," not in text and "run_text_llm_postprocess" in text:
        # Some local layouts call text post-processing directly; don't silently
        # invent an async boundary, but make the layout visible in preflight.
        print("WARNING: text-LLM call was not wrapped in asyncio.to_thread; inspect its execution context.")

    if "_vce_mlx_run(" not in text or "_VCE_MLX_EXECUTOR" not in text:
        raise PatchError("MLX thread-affinity runtime helper was not installed.")
    return text


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected one exact match, found {count}. No file was changed.")
    return text.replace(old, new, 1)


def patch_engine_text(text: str) -> str:
    if MARKER in text:
        return patch_mlx_thread_affinity(text)
    if "# VCE_MLX_METAL_CPU_PARALLEL_V1" in text:
        raise PatchError("Detected previous CPU/Metal patch version. Restore the pre-patch backup before applying MLX Whisper CPU-stream v2.")
    required_tokens = [
        "def resolve_whisper_device(",
        "def run_text_llm_postprocess(",
        "class MLXEngine(VLMProvider):",
        "from mlx_vlm import load, generate",
        "whisper_segments: List[Dict[str, Any]] = []",
        'set_job_progress(job_id, "summary", None, "Формируем сводку по всем сценам"',
    ]
    for token in required_tokens:
        if token not in text:
            raise PatchError(f"Could not find required source anchor: {token!r}. No file was changed.")
    # Indentation varies between local VCE edits, so validate structural lines
    # by their stripped text rather than requiring a specific number of spaces.
    for line_text in (
        "whisper_segments: List[Dict[str, Any]] = []",
    ):
        occurrences = [line for line in text.splitlines() if line.strip() == line_text]
        if len(occurrences) != 1:
            raise PatchError(
                f"Expected one structurally matching line {line_text!r}, got {len(occurrences)}. "
                "No file was changed."
            )
    # Endpoint cleanup formatting varies in local copies. Identify its finalizer
    # structurally by capture release, allowing blank/comment lines and custom indentation.
    _vce_source_lines = text.splitlines(keepends=True)
    _vce_cleanup_finalizers = []
    for _vce_i, _vce_line in enumerate(_vce_source_lines):
        if _vce_line.strip() != "finally:":
            continue
        _vce_tail = "".join(_vce_source_lines[_vce_i + 1:_vce_i + 81])
        if "cap.release()" in _vce_tail and ("cap is not None" in _vce_tail or "if cap:" in _vce_tail):
            _vce_cleanup_finalizers.append(_vce_i)
    if not _vce_cleanup_finalizers:
        raise PatchError("Could not identify endpoint cleanup finalizer containing cap.release(). No file was changed.")

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

    # Prefer the historic visual-stage condition. If local edits changed its
    # spelling, locate the visual-model load marker and derive its parent condition.
    block_end_re = re.compile(r"(?m)^(?P<indent>[ \t]*)if not skip_visual or generate_summary:\s*$")
    block_end_match = block_end_re.search(text, block_start_match.end())
    if block_end_match is None:
        # Use the actual model-load call rather than an optional UI progress label.
        load_progress_re = re.compile(
            r"(?m)^[ \t]*await asyncio\.to_thread\(vlm_engine\.load_model,\s*target_vlm\)"
        )
        load_progress = load_progress_re.search(text, block_start_match.end())
        if load_progress is None:
            raise PatchError(
                "Cannot locate the visual-model load boundary (no VLM model-load call). "
                "No file was changed."
            )
        candidates = list(re.finditer(
            r"(?m)^(?P<indent>[ \t]*)if\b[^\n]*:\s*$",
            text[block_start_match.end():load_progress.start()],
        ))
        candidates = [m for m in candidates if m.group("indent") == base_indent]
        if not candidates:
            raise PatchError(
                "Found load_vlm but no matching parent condition at the Whisper block indentation. "
                "No file was changed."
            )
        candidate = candidates[-1]
        block_end = block_start_match.end() + candidate.start()
        if block_end <= block_start:
            raise PatchError("Derived visual-stage boundary overlaps Whisper block. No file was changed.")
    else:
        block_end = block_end_match.start()

    parallel_block = '''                whisper_segments: List[Dict[str, Any]] = []
                _vce_whisper_task = None
                whisper_audio_error = None
                _vce_whisper_model = None

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
                            if hasattr(w_model, "release"):
                                await asyncio.to_thread(w_model.release)
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
    scene_helper = r'''
_vce_prefetch_tasks: Dict[int, asyncio.Task] = {}
_vce_cpu_prefetch_seconds = 0.0

async def _vce_prepare_scene_keyframes_cpu(_scene: Dict[str, Any]):
    nonlocal _vce_cpu_prefetch_seconds
    _started = time.perf_counter()

    def _prepare():
        _prefetch_cap = cv2.VideoCapture(temp_path)
        try:
            return sample_keyframes_for_scene(
                _prefetch_cap,
                _scene,
                base_res=vlm_resolution,
                max_frames=keyframes_per_scene,
            )
        finally:
            _prefetch_cap.release()

    _frames = await asyncio.to_thread(_prepare)
    _vce_cpu_prefetch_seconds += time.perf_counter() - _started
    return _frames
'''
    scene_helper = "\n" + textwrap.indent(textwrap.dedent(scene_helper).strip("\n"), scene_indent) + "\n"
    _scene_started_line = scene_indent + "_vce_vlm_scene_started_at = time.perf_counter()"
    _scene_started_pos = text.index(_scene_started_line) + len(_scene_started_line)
    _vce_has_existing_bounded_prefetch = (
        "_vce_start_bounded_keyframe_prefetch" in text
        and "_vce_frame_prefetch_queue" in text
        and "_vce_frame_prefetch_stats" in text
    )
    if not _vce_has_existing_bounded_prefetch:
        text = text[:_scene_started_pos] + scene_helper + text[_scene_started_pos:]

    loop_re = re.compile(r"(?m)^(?P<indent>[ \t]*)for idx, scene in enumerate\(scenes_raw\):\s*$")
    loop_matches = [m for m in loop_re.finditer(text, cap_end) if len(m.group("indent")) >= len(scene_indent)]
    if len(loop_matches) != 1:
        raise PatchError(f"Expected one scene loop after VideoCapture, found {len(loop_matches)}.")
    loop_match = loop_matches[0]
    loop_indent = loop_match.group("indent")
    loop_line_end = loop_match.end()

    if not _vce_has_existing_bounded_prefetch:
        # Replace synchronous keyframe sampling with a one-scene-ahead CPU prefetch.
        keyframes_re = re.compile(
            r"(?m)^(?P<indent>[ \t]*)keyframes = sample_keyframes_for_scene\(\r?\n"
            r"(?P=indent)    cap,\r?\n"
            r"(?P=indent)    scene,\r?\n"
            r"(?P=indent)    base_res=vlm_resolution,\r?\n"
            r"(?P=indent)    max_frames=keyframes_per_scene,\r?\n"
            r"(?P=indent)\)"
        )
        keyframes_matches = list(keyframes_re.finditer(text, loop_line_end))
        if len(keyframes_matches) != 1:
            raise PatchError(f"Expected one scene keyframe sampling block, found {len(keyframes_matches)}.")
        _kf_match = keyframes_matches[0]
        _kf_indent = _kf_match.group("indent")
        _kf_replacement = (
            _kf_indent + "_vce_prefetch_task = _vce_prefetch_tasks.pop(idx, None)\n"
            + _kf_indent + "if _vce_prefetch_task is not None:\n"
            + _kf_indent + "    keyframes = await _vce_prefetch_task\n"
            + _kf_indent + "else:\n"
            + _kf_indent + "    keyframes = sample_keyframes_for_scene(\n"
            + _kf_indent + "        cap,\n"
            + _kf_indent + "        scene,\n"
            + _kf_indent + "        base_res=vlm_resolution,\n"
            + _kf_indent + "        max_frames=keyframes_per_scene,\n"
            + _kf_indent + "    )"
        )
        text = text[:_kf_match.start()] + _kf_replacement + text[_kf_match.end():]
    
        # Start preparing the next scene immediately before current-scene GPU inference.
        _vlm_start_matches = list(re.finditer(
            r"(?m)^(?P<indent>[ \t]*)t_vlm_start = time\.time\(\)\s*$",
            text[_kf_match.start():],
        ))
        if not _vlm_start_matches:
            raise PatchError("Could not find VLM inference timer for one-scene-ahead prefetch.")
        _vlm_start = _vlm_start_matches[0]
        _vlm_start_pos = _kf_match.start() + _vlm_start.start()
        _vlm_indent = _vlm_start.group("indent")
        _prefetch_code = (
            _vlm_indent + "if idx + 1 < len(scenes_raw) and (idx + 1) not in _vce_prefetch_tasks:\n"
            + _vlm_indent + "    _vce_prefetch_tasks[idx + 1] = asyncio.create_task(\n"
            + _vlm_indent + "        _vce_prepare_scene_keyframes_cpu(scenes_raw[idx + 1])\n"
            + _vlm_indent + "    )\n"
        )
        text = text[:_vlm_start_pos] + _prefetch_code + text[_vlm_start_pos:]
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
        '                "whisper_backend": "mlx-whisper",\n'
        '                "whisper_device": "cpu",\n'
        '                "whisper_runtime": VCE_MLX_RUNTIME_DIAGNOSTICS.get("whisper", {}),\n'
        '                "whisper_vlm_overlap_seconds": round(_vce_overlap_seconds, 3) if "_vce_overlap_seconds" in locals() else 0.0,\n'
        '                "cpu_keyframe_prefetch_seconds": round(float(locals().get("_vce_frame_prefetch_stats", {}).get("worker_seconds", locals().get("_vce_cpu_prefetch_seconds", 0.0))), 3),\n'
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

    # Do not delete a temporary video file while worker-thread tasks are still using it.
    # Insert cleanup at the start of the endpoint finalizer, regardless of blank lines
    # or local indentation before the capture-release logic.
    _vce_lines = text.splitlines(keepends=True)
    _vce_finalizer_candidates = []
    for _vce_i, _vce_line in enumerate(_vce_lines):
        if _vce_line.strip() != "finally:":
            continue
        _vce_tail = "".join(_vce_lines[_vce_i + 1:_vce_i + 81])
        if "cap.release()" in _vce_tail and ("cap is not None" in _vce_tail or "if cap:" in _vce_tail):
            _vce_finalizer_candidates.append(_vce_i)
    if not _vce_finalizer_candidates:
        raise PatchError("Cannot inject task cleanup: endpoint finalizer with cap.release() was not found.")
    _vce_finalizer_i = _vce_finalizer_candidates[-1]
    _vce_finalizer_line = _vce_lines[_vce_finalizer_i]
    _vce_finalizer_indent = _vce_finalizer_line[:len(_vce_finalizer_line) - len(_vce_finalizer_line.lstrip(" \\t"))]
    _vce_cleanup_indent = _vce_finalizer_indent + "    "
    _vce_cleanup_code = [
        "_vce_pending_whisper_task = locals().get('_vce_whisper_task')",
        "if _vce_pending_whisper_task is not None:",
        "    try:",
        "        await _vce_pending_whisper_task",
        "    except BaseException:",
        "        # Preserve the original endpoint exception; observe task completion before cleanup.",
        "        pass",
        "for _vce_prefetch in locals().get('_vce_prefetch_tasks', {}).values():",
        "    try:",
        "        await _vce_prefetch",
        "    except BaseException:",
        "        pass",
        "_vce_pf_stop = locals().get('_vce_frame_prefetch_stop')",
        "if _vce_pf_stop is not None:",
        "    _vce_pf_stop.set()",
        "_vce_pf_thread = locals().get('_vce_frame_prefetch_thread')",
        "if _vce_pf_thread is not None and _vce_pf_thread.is_alive():",
        "    await asyncio.to_thread(_vce_pf_thread.join, 2.0)",
    ]
    _vce_newline = "\r\n" if "\r\n" in text else "\n"
    _vce_insert_at = sum(len(line) for line in _vce_lines[:_vce_finalizer_i + 1])
    _vce_insert_text = "".join(_vce_cleanup_indent + line + _vce_newline for line in _vce_cleanup_code)
    text = text[:_vce_insert_at] + _vce_insert_text + text[_vce_insert_at:]

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
    text = patch_mlx_thread_affinity(text)
    for marker in (MLX_THREAD_AFFINITY_MARKER, "_VCE_MLX_EXECUTOR", "_vce_mlx_run("):
        if marker not in text:
            raise PatchError(f"Post-patch MLX thread-affinity validation failed: missing {marker!r}.")
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
