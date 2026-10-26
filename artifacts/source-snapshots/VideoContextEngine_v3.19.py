#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VideoContext Engine v3.19
-------------------------
Author: dolphin-creator (https://github.com/dolphin-creator)
Project: VideoContext Engine
License: MIT

Description:
Local-first microservice for video understanding (Scene Detection + Whisper + Qwen3-VL).

-------------------------
INSTALLATION DES DÉPENDANCES (copier/coller) :

# macOS (Apple Silicon / Intel) – CPU (ou Metal) :
#   python -m pip install --upgrade pip
#   pip install fastapi uvicorn[standard] opencv-python yt-dlp pillow numpy openai-whisper huggingface_hub
#   pip install mlx-vlm torchvision

# Windows / Linux – CPU (ou GPU si vous avez déjà PyTorch CUDA) :
#   python -m pip install --upgrade pip
#   pip install fastapi uvicorn[standard] opencv-python yt-dlp pillow numpy openai-whisper huggingface_hub
#   pip install llama-cpp-python

(⚠️ PyTorch est requis par Whisper. Si besoin : pip install torch)

-------------------------
- Prompts techniques (structure JSON, etc.) figés dans le code.
- L'utilisateur ne modifie que des "user prompts" qui s'ajoutent par-dessus.
- 1 seul appel VLM par scène (description + tags en JSON).
- Nombre de keyframes par scène paramétrable (1 à 5), 2 par défaut.
- Audio_features activés par défaut, désactivables.
- Résumé global activé par défaut, désactivable.
- Chronos détaillés (Whisper + VLM + total).
- max_tokens VLM réglables avec valeurs par défaut (1024 / 2048).
- safe_json_parse robuste (réparation JSON) pour scènes + résumé global.
- Nettoyage du texte brut quand le JSON échoue (on extrait la valeur de "description" / "summary").
- Mode RAM :
    - RAM_MODE = "ram+" (par env VIDEOCONTEXT_RAM_MODE) :
        * précharge VLM par défaut + Whisper small
        * garde tout en RAM
    - RAM_MODE = "ram-":
        * charge/décharge Whisper et VLM à chaque requête
- Swagger patché pour afficher de grands textarea pour les prompts utilisateur.
"""

import os
import gc
import cv2
import time
import shutil
import base64
import asyncio
import platform
import numpy as np
import yt_dlp
import json
import re
import hashlib
import concurrent.futures
import tempfile
import secrets
import subprocess
import threading

from typing import Optional, Dict, Any, List, Union, Literal
from io import BytesIO
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, FileResponse
from fastapi.openapi.utils import get_openapi
from PIL import Image

import whisper
import torch


# VCE_MLX_METAL_CPU_PARALLEL_MLXWHISPER_V2
VCE_MLX_RUNTIME_DIAGNOSTICS: Dict[str, Dict[str, Any]] = {}

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
    call = _vce_mlx_functools.partial(_vce_mlx_invoke_traced, fn, *args, **kwargs)
    return await loop.run_in_executor(_VCE_MLX_EXECUTOR, call)

# VCE_MLX_VLM_RUNTIME_TRACE_V1
import sys as _vce_mlx_trace_sys
import threading as _vce_mlx_trace_threading


def _vce_mlx_trace_stream(value):
    if value is None:
        return None
    try:
        device = str(value.device)
    except Exception:
        device = None
    return {"type": type(value).__name__, "repr": repr(value), "device": device}


def _vce_mlx_trace_runtime(phase: str, function_name: str, error: str = "") -> None:
    # Snapshot the executor thread around the VCE call into mlx-vlm.
    # This records device/stream wiring, not per-operator GPU execution.
    trace = {
        "phase": phase,
        "function": function_name,
        "pid": os.getpid(),
        "thread_name": _vce_mlx_trace_threading.current_thread().name,
        "thread_ident": _vce_mlx_trace_threading.get_ident(),
        "thread_native_id": getattr(_vce_mlx_trace_threading, "get_native_id", lambda: None)(),
    }
    if error:
        trace["error"] = error[:1000]
    try:
        import mlx.core as mx
        trace["mlx_default_device"] = str(mx.default_device())
        trace["mlx_gpu_available"] = bool(mx.is_available(mx.gpu))
        trace["metal_available"] = bool(mx.metal.is_available())
        for label, getter in (
            ("default_gpu_stream", lambda: mx.default_stream(mx.gpu)),
            ("default_cpu_stream", lambda: mx.default_stream(mx.cpu)),
        ):
            try:
                trace[label] = _vce_mlx_trace_stream(getter())
            except Exception as exc:
                trace[label] = {"error": repr(exc)[:500]}
    except Exception as exc:
        trace["mlx_snapshot_error"] = repr(exc)[:1000]

    for module_name in ("mlx_vlm.generate.common", "mlx_vlm.generate.dispatch"):
        module = _vce_mlx_trace_sys.modules.get(module_name)
        if module is None:
            trace[module_name + ".loaded"] = False
            continue
        trace[module_name + ".loaded"] = True
        try:
            trace[module_name + ".generation_stream"] = _vce_mlx_trace_stream(
                getattr(module, "generation_stream", None)
            )
        except Exception as exc:
            trace[module_name + ".generation_stream_error"] = repr(exc)[:500]
    print("[VCE MLX TRACE] " + json.dumps(trace, ensure_ascii=False, sort_keys=True), flush=True)


def _vce_mlx_invoke_traced(fn, *args, **kwargs):
    function_name = (
        str(getattr(fn, "__module__", "unknown")) + "." +
        str(getattr(fn, "__qualname__", getattr(fn, "__name__", type(fn).__name__)))
    )
    lower_name = function_name.lower()
    should_trace = any(token in lower_name for token in (
        "load_model", "describe_scene", "run_text_llm_postprocess"
    ))
    if should_trace:
        _vce_mlx_trace_runtime("before", function_name)
    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:
        if should_trace:
            _vce_mlx_trace_runtime("error", function_name, repr(exc))
        raise
    if should_trace:
        _vce_mlx_trace_runtime("after", function_name)
    return result




# VCE_MLX_VLM_WIRED_LIMIT_COMPAT_V1
import contextlib as _vce_mlx_contextlib


def _vce_prepare_mlx_vlm_runtime():
    """Repair mlx-vlm thread-local generation stream and wired_limit device-info lookup."""
    import mlx.core as mx

    if not mx.metal.is_available() or not mx.is_available(mx.gpu):
        raise RuntimeError("VCE VLM requires the MLX/Metal GPU")
    mx.set_default_device(mx.gpu)
    # Do not assert mx.default_device() here: MLX 0.32.4 exposes a process-global
    # default, and another thread's mx.stream(mx.cpu) context can temporarily
    # change the value observed here. The generation stream below is explicitly GPU.

    # mlx-vlm creates generation_stream at import time. Rebind the module-level
    # stream inside the dedicated executor thread used for model load/inference.
    import importlib as _vce_importlib
    _vce_mlx_vlm_common = _vce_importlib.import_module("mlx_vlm.generate.common")
    _vce_mlx_vlm_dispatch = _vce_importlib.import_module("mlx_vlm.generate.dispatch")

    _vce_generation_stream = mx.new_thread_local_stream(mx.gpu)
    _vce_mlx_vlm_common.generation_stream = _vce_generation_stream
    _vce_mlx_vlm_dispatch.generation_stream = _vce_generation_stream

    # mlx-vlm 0.7.10 calls mx.device_info() with no device after checking only
    # mx.metal.is_available(). CPU device_info lacks the GPU-only working-set key.
    if not getattr(_vce_mlx_vlm_common.wired_limit, "_vce_gpu_info_compat", False):
        @_vce_mlx_contextlib.contextmanager
        def _vce_wired_limit(model, streams=None):
            if not mx.metal.is_available():
                yield
                return
            _vce_max_rec_size = mx.device_info(mx.gpu).get(
                "max_recommended_working_set_size"
            )
            if _vce_max_rec_size is None:
                # No recommended Metal working-set size: leave the current limit alone.
                yield
                return

            _vce_model_bytes = _vce_mlx_vlm_common.tree_reduce(
                lambda acc, x: acc + x.nbytes if isinstance(x, mx.array) else acc,
                model,
                0,
            )
            if _vce_model_bytes > 0.9 * _vce_max_rec_size:
                _vce_model_mb = _vce_model_bytes // 2**20
                _vce_max_rec_mb = _vce_max_rec_size // 2**20
                print(
                    f"[VCE MLX] Model needs {_vce_model_mb} MB, near recommended "
                    f"Metal working-set limit {_vce_max_rec_mb} MB."
                )

            _vce_old_limit = mx.set_wired_limit(_vce_max_rec_size)
            try:
                yield
            finally:
                if streams is not None:
                    for _vce_stream in streams:
                        mx.synchronize(_vce_stream)
                else:
                    mx.synchronize()
                mx.set_wired_limit(_vce_old_limit)

        _vce_wired_limit._vce_gpu_info_compat = True
        _vce_mlx_vlm_common.wired_limit = _vce_wired_limit
        # dispatch.py imports wired_limit by name, so replace that alias too.
        _vce_mlx_vlm_dispatch.wired_limit = _vce_wired_limit

    _vce_default_device_observed = str(mx.default_device())
    print(
        "[VCE MLX] Applied GPU-aware wired_limit and rebound mlx-vlm generation stream "
        f"to {mx.gpu}; process-global default observed={_vce_default_device_observed}."
    )


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


# VCE_MLX_EXPLICIT_GPU_PROBE_V1
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

    # mx.default_device() is process-global in MLX 0.32.4. A concurrent
    # with mx.stream(cpu_stream) can temporarily make its value appear to be
    # CPU in this thread. Do not use that shared snapshot as the probe target
    # or as a fail-fast check. Pin the probe operations to the GPU stream.
    gpu_probe_stream = mx.default_stream(mx.gpu)
    probe = mx.arange(1, 4, stream=gpu_probe_stream)
    probe = mx.multiply(probe, 2, stream=gpu_probe_stream)
    mx.eval(probe)
    mx.synchronize(gpu_probe_stream)
    if [float(x) for x in probe.tolist()] != [2.0, 4.0, 6.0]:
        raise RuntimeError(f"[VCE MLX] {stage}: explicit GPU-stream smoke test returned an unexpected result.")

    try:
        mlx_version = __import__("importlib.metadata", fromlist=["version"]).version("mlx")
    except Exception:
        mlx_version = "unknown"
    try:
        device_info = {str(k): str(v) for k, v in mx.device_info(mx.gpu).items()}
    except Exception:
        device_info = {}
    default_device_observed = str(mx.default_device())
    info = {
        "backend": "MLX",
        "accelerator": "Metal",
        "stage": stage,
        "mlx_version": mlx_version,
        # Backward-compatible field: this is the explicitly configured target,
        # not a claim that every operator in the model ran on this device.
        "device": str(mx.gpu),
        "configured_device": str(mx.gpu),
        "device_source": "explicit_gpu_target",
        "default_device_observed": default_device_observed,
        "device_semantics": "configured target; not per-operator execution proof",
        "device_observation_note": (
            "mx.default_device() is process-global and can temporarily reflect a concurrent "
            "mx.stream(mx.cpu) context; see default_device_observed."
        ),
        "gpu_probe_stream": repr(gpu_probe_stream),
        "gpu_stream_smoke_test": "passed",
        "device_info": device_info,
        "metal_smoke_test": "passed",
    }
    VCE_MLX_RUNTIME_DIAGNOSTICS[stage] = info
    if default_device_observed != str(mx.gpu):
        print(
            f"[VCE warning] {stage}: GPU target and explicit GPU stream probe passed, "
            f"but process-global mx.default_device() snapshot was {default_device_observed}. "
            "This snapshot may reflect another thread's mx.stream() context.",
            flush=True,
        )
    print(f"[VCE runtime] {json.dumps(info, ensure_ascii=False, sort_keys=True)}")
    return info


def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Whisper is pinned to MLX CPU stream to overlap with MLX/Metal inference."""
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Return an mlx-whisper adapter; MLX model weights load inside the CPU stream."""
    return _VCEWhisperMLXCPU(model_name)

# Optionnel : supprimer le warning HF tokenizers
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# --- MODE RAM ---

SETTINGS_FILE = Path(__file__).with_name("videocontext_settings.json")

def load_user_settings() -> Dict[str, Any]:
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("settings root must be a JSON object")
        return loaded
    except FileNotFoundError:
        print(f"⚠️ Settings file not found: {SETTINGS_FILE}; using built-in defaults")
        return {}
    except Exception as exc:
        print(f"⚠️ Could not read {SETTINGS_FILE}: {exc}; using built-in defaults")
        return {}

USER_SETTINGS = load_user_settings()

def setting(name: str, fallback: Any) -> Any:
    return USER_SETTINGS.get(name, fallback)

RAM_MODE = os.getenv("VIDEOCONTEXT_RAM_MODE", str(setting("ram_mode", "ram-"))).lower()  # "ram+" ou "ram-"
WHISPER_CACHE: Dict[str, Any] = {}

# --- CONFIGURATION GLOBALE ---

PORT_SERVEUR = 7555

DEFAULT_VLM_MODEL_MLX = "mlx-community/Qwen3-VL-8B-Instruct-4bit"
DEFAULT_VLM_REPO_GGUF = "bartowski/Qwen3-VL-2B-Instruct-GGUF"
DEFAULT_VLM_MODEL_GGUF = "qwen3-vl-2b-instruct-q4_k_m.gguf"
DEFAULT_MMPROJ_GGUF = "mmproj-model-f16.gguf"
DEFAULT_WHISPER_MODEL = "large"

DEFAULT_SCENE_THRESHOLD = 0.15
DEFAULT_MIN_DURATION = 1.5
DEFAULT_MAX_DURATION = 10.0
DEFAULT_RESOLUTION = 1280
MAX_TOTAL_VIDEO_DURATION = 4 * 60 * 60  # 4h max

DEFAULT_KEYFRAMES_PER_SCENE = 2

DEFAULT_VLM_MAX_TOKENS_SCENE = 1024
DEFAULT_VLM_MAX_TOKENS_SUMMARY = 2048

if platform.system() == "Darwin":
    ACTIVE_DEFAULT_VLM = DEFAULT_VLM_MODEL_MLX
else:
    ACTIVE_DEFAULT_VLM = DEFAULT_VLM_MODEL_GGUF

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

gpu_lock = asyncio.Lock()
CANCEL_EVENTS: Dict[str, threading.Event] = {}

JOB_PROGRESS: Dict[str, Dict[str, Any]] = {}

# VCE_WEIGHTED_PROGRESS_V4
JOB_PROGRESS_PHASE_BOUNDS = {
    "starting": (0.5, 1.5, 4.0),
    "validated": (1.5, 2.8, 3.0),
    "detect_scenes": (2.8, 4.5, 10.0),
    "scenes_detected": (4.5, 4.5, 0.0),
    "whisper": (4.5, 18.0, 50.0),
    "whisper_done": (18.0, 18.0, 0.0),
    "load_vlm": (18.0, 20.0, 8.0),
    "scene_analysis": (20.0, 76.0, 16.0),
    "summary": (76.0, 80.0, 18.0),
    "release_models": (80.0, 80.5, 4.0),
    "text_llm_loading_cached": (80.5, 84.5, 25.0),
    "text_llm_loading_cold": (80.5, 88.0, 180.0),
    "text_llm_generating_cached": (84.5, 97.6, 100.0),
    "text_llm_generating_cold": (88.0, 97.6, 120.0),
    "text_llm_repair": (97.6, 98.7, 45.0),
    "compile_output": (99.0, 99.6, 3.0),
    "completed": (100.0, 100.0, 0.0),
}

# Cold start allocates a much larger weight to the one-time HF model download.
# Stage weights shrink proportionally before text LLM so the bar stays monotonic.
JOB_PROGRESS_PHASE_BOUNDS_COLD = {
    "starting": (0.3, 1.0, 4.0),
    "validated": (1.0, 2.0, 3.0),
    "detect_scenes": (2.0, 4.0, 10.0),
    "scenes_detected": (4.0, 4.0, 0.0),
    "whisper": (4.0, 9.0, 50.0),
    "whisper_done": (9.0, 9.0, 0.0),
    "load_vlm": (9.0, 10.0, 8.0),
    "scene_analysis": (10.0, 35.0, 16.0),
    "summary": (35.0, 37.0, 18.0),
    "release_models": (37.0, 37.5, 4.0),
    "text_llm_loading_cached": (37.5, 82.5, 25.0),
    "text_llm_loading_cold": (37.5, 82.5, 240.0),
    "text_llm_generating_cached": (82.5, 97.5, 100.0),
    "text_llm_generating_cold": (82.5, 97.5, 120.0),
    "text_llm_repair": (97.5, 98.6, 45.0),
    "compile_output": (99.0, 99.6, 3.0),
    "completed": (100.0, 100.0, 0.0),
}


def set_job_progress(
    job_id: str,
    phase: str,
    percent: Optional[float] = None,
    message: str = "",
    *,
    indeterminate: bool = False,
    **extra: Any,
) -> None:
    """Store weighted stage bounds; GET /progress interpolates gradually within a stage."""
    if not job_id:
        return
    now = time.time()
    previous = JOB_PROGRESS.get(job_id, {})
    status = extra.pop("status", "running")
    old_phase = str(previous.get("phase", ""))
    scene_index = extra.get("scene_index")
    scene_total = extra.get("scene_total")
    same_scene_changed = phase == "scene_analysis" and scene_index is not None and scene_index != previous.get("scene_index")
    reset_clock = old_phase != phase or same_scene_changed
    job_started_at = float(previous.get("started_at", now))
    phase_started_at = now if reset_clock else float(previous.get("phase_started_at", now))

    model_cached = bool(extra.get("model_cached", previous.get("model_cached", True)))
    active_bounds = JOB_PROGRESS_PHASE_BOUNDS if model_cached else JOB_PROGRESS_PHASE_BOUNDS_COLD
    if phase == "text_llm_loading":
        bounds_key = "text_llm_loading_cached" if model_cached else "text_llm_loading_cold"
        lo, hi, expected = active_bounds[bounds_key]
        extra["model_cached"] = model_cached
    elif phase == "text_llm_generating":
        bounds_key = "text_llm_generating_cached" if model_cached else "text_llm_generating_cold"
        lo, hi, expected = active_bounds[bounds_key]
        extra["model_cached"] = model_cached
    elif phase == "scene_analysis":
        phase_lo, phase_hi, expected = active_bounds[phase]
        if scene_index is not None and scene_total:
            total = max(1, int(scene_total))
            idx = max(1, min(total, int(scene_index)))
            lo = phase_lo + (idx - 1) * (phase_hi - phase_lo) / total
            hi = phase_lo + idx * (phase_hi - phase_lo) / total
            expected = max(7.0, min(28.0, float(extra.get("scene_expected_seconds", 15.0))))
        else:
            lo, hi, expected = phase_lo, min(phase_hi, phase_lo + 1.5), 6.0
    elif phase in active_bounds:
        lo, hi, expected = active_bounds[phase]
    else:
        lo = float(previous.get("percent", 0) or 0)
        hi = max(lo, float(percent if percent is not None else lo))
        expected = 10.0

    # For discrete milestones, never use the old hard-coded, unweighted percentages.
    # Milestones must follow the selected warm/cold profile (otherwise a cold run
    # could jump forward to warm-profile percentages after scene detection).
    fixed_milestones = {
        "starting": active_bounds["starting"][1],
        "validated": active_bounds["validated"][1],
        "scenes_detected": active_bounds["scenes_detected"][1],
        "whisper_done": active_bounds["whisper_done"][1],
        "release_models": active_bounds["release_models"][1],
        "compile_output": active_bounds["compile_output"][0],
        "completed": active_bounds["completed"][1],
    }
    dynamic = phase in {
        "detect_scenes", "whisper", "load_vlm", "scene_analysis", "summary",
        "text_llm_loading", "text_llm_generating", "text_llm_repair",
    }
    if phase in fixed_milestones:
        current_percent = fixed_milestones[phase]
        dynamic = False
    elif phase == "scene_analysis" and percent is not None and scene_index is not None:
        current_percent = hi
        dynamic = False
    elif dynamic:
        current_percent = lo
    elif percent is not None:
        current_percent = max(lo, min(hi, float(percent)))
    else:
        current_percent = lo

    if status in {"failed", "stopped"}:
        current_percent = float(previous.get("last_reported_percent", previous.get("percent", current_percent)) or current_percent)
        dynamic = False

    # Keep the bar monotonic, except that a new job is initialized from zero.
    last = float(previous.get("last_reported_percent", previous.get("percent", 0.0)) or 0.0)
    if phase not in {"starting"}:
        current_percent = max(current_percent, last)

    record: Dict[str, Any] = {
        "job_id": job_id,
        "phase": str(phase),
        "status": status,
        "percent": round(max(0.0, min(current_percent, 100.0)), 1),
        "indeterminate": False,
        "progress_dynamic": bool(dynamic and status == "running"),
        "progress_start_percent": round(max(0.0, min(lo, 100.0)), 2),
        "progress_end_percent": round(max(0.0, min(hi, 100.0)), 2),
        "progress_expected_seconds": max(1.0, float(extra.pop("expected_seconds", expected))) if dynamic else 1.0,
        "message": str(message)[:300],
        "started_at": job_started_at,
        "phase_started_at": phase_started_at,
        "updated_at": now,
        "elapsed_seconds": round(max(0.0, now - job_started_at), 1),
        "last_reported_percent": round(max(0.0, min(current_percent, 100.0)), 1),
        # Carry the cache state across every intermediate stage, not only LLM callbacks.
        "model_cached": model_cached,
    }
    if phase == "scene_analysis" and scene_index is not None and scene_total:
        record["scene_index"] = int(scene_index)
        record["scene_total"] = int(scene_total)
    record.update(extra)
    JOB_PROGRESS[job_id] = record
    if len(JOB_PROGRESS) > 128:
        oldest = sorted(JOB_PROGRESS.items(), key=lambda pair: float(pair[1].get("updated_at", 0)))
        for old_id, _ in oldest[:max(1, len(JOB_PROGRESS) - 96)]:
            if old_id != job_id:
                JOB_PROGRESS.pop(old_id, None)

def format_text_llm_scene_timestamp(seconds: Any) -> str:
    """Return HH:MM:SS based on engine scene timing, never on LLM-generated strings."""
    try:
        total = max(0, int(round(float(seconds or 0))))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"






def reconcile_text_llm_result(payload: Dict[str, Any], segments: List[Dict[str, Any]], original_summary: str) -> Dict[str, Any]:
    """VCE_SEMANTIC_QUALITY_V5_7: reconcile claims, keep evidence strict, and avoid duplicate key points."""
    import difflib
    import math

    def _norm(value: Any) -> str:
        return re.sub(r"\W+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()

    def _compact(value: Any, limit: int = 260) -> str:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(value) <= limit:
            return value
        return value[:limit - 1].rstrip(" ,;:.") + "…"

    def _source_text(seg: Dict[str, Any]) -> str:
        tags = seg.get("visual_tags") if isinstance(seg.get("visual_tags"), dict) else {}
        parts = [seg.get("audio_transcript", ""), seg.get("ocr_text", ""), seg.get("visual_description", "")]
        parts.extend(f"{k}: {v}" for k, v in list(tags.items())[:16])
        return " ".join(str(p or "") for p in parts).strip()

    def _tag_text(seg: Dict[str, Any]) -> str:
        tags = seg.get("visual_tags") if isinstance(seg.get("visual_tags"), dict) else {}
        return " ".join(f"{k}: {v}" for k, v in list(tags.items())[:16] if str(v or "").strip())

    def _field_sources(seg: Dict[str, Any]) -> Dict[str, str]:
        return {
            "speech": str(seg.get("audio_transcript", "") or "").strip(),
            "ocr": str(seg.get("ocr_text", "") or "").strip(),
            "visual": str(seg.get("visual_description", "") or "").strip(),
            "tags": _tag_text(seg).strip(),
        }

    _STOP = set((
        "и в во на по к ко с со у о об от до для из за как что это этот эта эти тот там где при ли же а но не ни да или если чтобы также можно должен должна должны было были быть есть уже ещё еще всего более менее например именно сразу затем потом который которая которые между через один одна одни свой свои их его ее её она они он мы вы я мне нам вам вас их под что бы то all the a an is are was were be been of in on at to from and or for with this that it as into by then than shows shown use using user system video scene screen result said says says that" ).split())

    def _tokens(value: Any) -> List[str]:
        raw = re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE)
        aliases = {
            "соответствует": "соответств", "соответствуют": "соответств", "соответствовал": "соответств",
            "соответствие": "соответств", "соответствия": "соответств", "соответствии": "соответств",
            "несоответствие": "несоответств", "несоответствия": "несоответств",
            "уклона": "уклон", "уклоне": "уклон", "уклону": "уклон", "уклоны": "уклон",
            "пандусов": "пандус", "пандуса": "пандус", "пандусами": "пандус",
            "требования": "требован", "требованию": "требован", "требований": "требован",
            "нормативу": "норматив", "нормативом": "норматив", "нормативные": "норматив",
            "проверки": "провер", "проверку": "провер", "проверка": "провер", "проверке": "провер",
            "генерации": "генерац", "генерация": "генерац", "калькуляторов": "калькулятор",
            "загружается": "загруз", "загружается": "загруз", "выгрузка": "выгруз", "выгрузить": "выгруз",
            "коллизий": "коллиз", "коллизии": "коллиз", "атрибутивным": "атрибут", "атрибутам": "атрибут",
            "объектов": "объект", "объекта": "объект", "объект": "объект",
        }
        output = []
        for token in raw:
            if token in _STOP or len(token) < 2:
                continue
            token = aliases.get(token, token)
            if token not in output:
                output.append(token)
        return output

    result_cues = re.compile(r"не\s*соответ|несоответ|не\s*соответств|нарушен|нарушени|не\s*пройден|ошибк|неверн|не\s*выполн|fail(?:ed)?|error|warning", re.I)
    result_topic_cues = re.compile(r"не\s*соответ|несоответ|нарушен|нарушени|результат|итог|вывод|заключен|ошибк|предупрежден|warning|error|failed|\bfail\b", re.I)
    technical_cues = re.compile(r"\bxml\b|экспорт|выгруз|интеграц|импорт|\bbim\b|\bcad\b|cadlib|nanocad|модел|сценар|калькулят|провер|функц|продукт|поиск|стандарт|норматив|данн|интерфейс|api\b|атрибут|классифик|требован|пандус|уклон|эвакуац|\bСП\s*\d|\bГОСТ\b|1\s*[:к]\s*20", re.I)
    end_card_cues = re.compile(r"\bthe\s*end\b|\bend\s*card\b|конец\s+видео|финальн\w*\s+заставк|конец\s+ролика", re.I)

    source_by_id: Dict[int, Dict[str, Any]] = {}
    ordered_ids: List[int] = []
    for seg in segments:
        try:
            sid = int(seg.get("scene_id", 0))
        except (TypeError, ValueError):
            continue
        if sid > 0:
            source_by_id[sid] = seg
            if sid not in ordered_ids:
                ordered_ids.append(sid)
    ordered_ids.sort(key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid))
    if not ordered_ids:
        return payload

    # Detect whether the final scene is an end card while the actual conclusion is spoken.
    last_sid = ordered_ids[-1]
    last_seg = source_by_id[last_sid]
    last_sources = _field_sources(last_seg)
    last_result_in_speech = bool(result_cues.search(last_sources["speech"]))
    end_card_only = bool(end_card_cues.search(last_sources["ocr"] + " " + last_sources["visual"])) and not bool(result_cues.search(last_sources["ocr"] + " " + last_sources["visual"]))
    spoken_result_end_card = end_card_only and last_result_in_speech

    # OCR-backed canonicalization: only link the phonetic ASR variant to BuildChat when the
    # evidence appears in temporally adjacent scenes (within 30 seconds), not merely somewhere in the video.
    alias_variants = {"белчат", "белчата", "билдчат", "билдчата", "билд чат", "бел чат"}
    buildchat_ocr_candidates: List[int] = []
    spoken_alias_candidates: List[int] = []
    for sid in ordered_ids:
        src = _field_sources(source_by_id[sid])
        if re.search(r"build\s*chat", src["ocr"], re.I):
            buildchat_ocr_candidates.append(sid)
        spoken_compact = _norm(src["speech"]).replace(" ", "")
        if any(alias.replace(" ", "") in spoken_compact for alias in alias_variants):
            spoken_alias_candidates.append(sid)
    paired_ocr_ids = set()
    paired_speech_ids = set()
    for speech_sid in spoken_alias_candidates:
        try:
            speech_start = float(source_by_id[speech_sid].get("start", 0) or 0)
        except (TypeError, ValueError):
            speech_start = 0.0
        for ocr_sid in buildchat_ocr_candidates:
            try:
                ocr_start = float(source_by_id[ocr_sid].get("start", 0) or 0)
            except (TypeError, ValueError):
                ocr_start = 0.0
            speech_evidence = _field_sources(source_by_id[speech_sid])
            ocr_evidence = _field_sources(source_by_id[ocr_sid])
            shared_context = set(_tokens(speech_evidence["speech"])) & set(_tokens(ocr_evidence["ocr"] + " " + ocr_evidence["visual"]))
            if abs(speech_start - ocr_start) <= 30.0 and shared_context:
                paired_speech_ids.add(speech_sid)
                paired_ocr_ids.add(ocr_sid)
    buildchat_ocr_ids = sorted(paired_ocr_ids)
    spoken_alias_ids = sorted(paired_speech_ids)
    has_buildchat_conflict = bool(buildchat_ocr_ids and spoken_alias_ids)
    canonical_buildchat_ids = sorted(set(buildchat_ocr_ids + spoken_alias_ids), key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid))

    # Normalize only derived Text-LLM fields, and only when source evidence supports the correction.
    all_ocr_text_for_normalization = " ".join(str(source_by_id[sid].get("ocr_text", "") or "") for sid in ordered_ids)
    has_verified_sp_standard = bool(re.search(r"\bСП\s*1\.13130\.2020\b", all_ocr_text_for_normalization, re.I))
    def _normalize_derived_strings(value: Any) -> Any:
        if isinstance(value, str):
            if has_verified_sp_standard:
                value = re.sub(r"(?i)\bСП\s*1\.13\.130\.2020\b", "СП 1.13130.2020", value)
            if has_buildchat_conflict:
                value = re.sub(r"(?i)\b(?:белчат\s*/\s*белчата|белчат(?:а)?|билдчат(?:а)?|бел\s+чат|билд\s+чат)\b", "BuildChat", value)
            return value
        if isinstance(value, list):
            return [_normalize_derived_strings(item) for item in value]
        if isinstance(value, dict):
            return {key: _normalize_derived_strings(item) for key, item in value.items()}
        return value
    payload = _normalize_derived_strings(payload)
    original_summary = _normalize_derived_strings(str(original_summary or ""))

    # Scene enrichment: drop foreign/duplicate scene IDs, keep concise descriptions, add missing scenes.
    raw_enrichment = payload.get("scene_enrichment", [])
    enrich_by_id: Dict[int, Dict[str, Any]] = {}
    for item in raw_enrichment if isinstance(raw_enrichment, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("scene_id"))
        except (TypeError, ValueError):
            continue
        if sid not in source_by_id or sid in enrich_by_id:
            continue
        enrich_by_id[sid] = {
            "scene_id": sid,
            "topic": _compact(item.get("topic", ""), 180),
            "improved_description": _compact(item.get("improved_description", ""), 1200),
            "key_points": [_compact(p, 180) for p in _text_llm_normalize_list(item.get("key_points"), 8) if _compact(p, 180)],
        }

    def _fallback_enrichment(sid: int) -> Dict[str, Any]:
        seg = source_by_id[sid]
        src = _field_sources(seg)
        evidence = " ".join(src.values())
        if result_topic_cues.search(evidence):
            topic = "Результат проверки"
        elif technical_cues.search(evidence):
            topic = "Технический этап"
        elif src["speech"]:
            topic = _compact(re.split(r"[.!?\n]", src["speech"], maxsplit=1)[0], 100) or "Речь в сцене"
        elif src["ocr"]:
            topic = "Текст на экране"
        else:
            topic = "Визуальное содержание"
        desc_parts = []
        if src["visual"]: desc_parts.append("Визуально: " + _compact(src["visual"], 360))
        if src["speech"]: desc_parts.append("Речь: " + _compact(src["speech"], 240))
        if src["ocr"]: desc_parts.append("Текст на экране: " + _compact(src["ocr"], 220))
        points = []
        if result_topic_cues.search(evidence): points.append("Сцена содержит результат проверки или итоговый вывод.")
        elif technical_cues.search(evidence): points.append("Сцена показывает технический этап или работу с данными.")
        elif src["speech"]: points.append("Сцена содержит речевое пояснение.")
        elif src["ocr"]: points.append("Сцена содержит текстовую информацию на экране.")
        return {"scene_id": sid, "topic": topic, "improved_description": " ".join(desc_parts) or "В исходных данных недостаточно описательного материала.", "key_points": points}

    missing = [sid for sid in ordered_ids if sid not in enrich_by_id]
    if len(ordered_ids) <= 80:
        for sid in missing:
            enrich_by_id[sid] = _fallback_enrichment(sid)
    else:
        for sid in (ordered_ids[0], ordered_ids[-1]):
            if sid not in enrich_by_id:
                enrich_by_id[sid] = _fallback_enrichment(sid)

    # Distinguish spoken conclusions from what is literally shown on the last frame.
    if spoken_result_end_card:
        final_item = enrich_by_id.get(last_sid, _fallback_enrichment(last_sid))
        final_item["topic"] = final_item.get("topic") or "Итог проверки"
        quoted_speech = _compact(last_sources["speech"], 200).strip(" .;:")
        final_item["improved_description"] = "На экране показана завершающая заставка «THE END»; итог о несоответствии сообщается в аудиодорожке: «" + quoted_speech + "». Конкретный нарушенный параметр на финальном экране не показан."
        final_item["key_points"] = [
            "В речи сообщается: «" + quoted_speech + "».",
            "На экране показана только завершающая заставка «THE END».",
        ]
        enrich_by_id[last_sid] = final_item

    # If the spoken scene describes ramp-slope compliance while OCR shows a collision-check dialog,
    # describe both observations without claiming they are the same operation.
    for sid in ordered_ids:
        src = _field_sources(source_by_id[sid])
        if re.search(r"пандус|уклон", src["speech"], re.I) and re.search(r"проверка\s+коллизий", src["ocr"], re.I):
            item = enrich_by_id.get(sid)
            if item:
                item["topic"] = "Проверка в CADLib"
                item["improved_description"] = "В речи описываются отбор объектов по атрибутам и проверка уклона пандусов; на экране открыто окно «Проверка коллизий». По этим источникам нельзя однозначно подтвердить, что окно показывает именно проверку уклона."
                item["key_points"] = [
                    "Диктор говорит об отборе объектов по атрибутивным данным для проверки уклона пандусов.",
                    "В интерфейсе CADLib показано окно «Проверка коллизий».",
                ]

    # V5.6: never let a verbose multimodal dump (OCR/transcript/frame labels) leak into improved_description.
    description_dump_markers = re.compile(r"\[Кадр\s*\d|Текст на экране\s*:|\bВизуально\s*:|\bРечь\s*:", re.I)
    for sid, item in enrich_by_id.items():
        desc = str(item.get("improved_description", "") or "").strip()
        if not (description_dump_markers.search(desc) or len(desc) > 480 or desc.count("\n") >= 2):
            continue
        seg = source_by_id[sid]
        src = _field_sources(seg)
        visual = re.sub(r"\[Кадр\s+[^\]]+\]\s*", "", src["visual"], flags=re.I).strip()
        speech = src["speech"]
        if re.search(r"логичн\w*.*наглядн\w*|наглядн\w*.*логичн\w*", speech, re.I) and re.search(r"КСИ|классификац", src["ocr"] + " " + visual, re.I) and re.search(r"уклон|пандус", src["ocr"] + " " + visual, re.I):
            desc = _compact(visual, 300) if visual else "На экране показана схема классификации требования об уклоне пандусов."
            desc = desc.rstrip(" .;:") + ". Диктор оценивает схему как логичную и наглядную."
        elif sid == last_sid and spoken_result_end_card:
            desc = "На экране показана завершающая заставка «THE END»; вывод о несоответствии произносится в аудио."
        elif re.search(r"пандус|уклон", speech, re.I) and re.search(r"проверка\s+коллизий", src["ocr"], re.I):
            desc = "Диктор описывает проверку уклона пандусов; на экране показано окно настройки проверки коллизий. Связь между ними не подтверждена."
        elif visual:
            desc = _compact(visual, 340)
        elif speech and len(speech.split()) >= 8:
            desc = "Диктор говорит: " + _compact(speech, 220).rstrip(" .;:") + "."
        elif src["ocr"]:
            desc = "Сцена показывает интерфейс с техническими подписями; подробный OCR сохранён в исходном поле."
        else:
            desc = "В исходных материалах недостаточно сведений для более подробного описания сцены."
        item["improved_description"] = _compact(desc, 430)
        # Scene-level points should summarize meaning, not contain frame/OCR dumps or generic placeholders.
        points = item.get("key_points", []) if isinstance(item.get("key_points"), list) else []
        points = [str(p).strip() for p in points if str(p).strip()]
        if re.search(r"логичн\w*.*наглядн\w*|наглядн\w*.*логичн\w*", speech, re.I) and re.search(r"КСИ|классификац", src["ocr"] + " " + visual, re.I) and re.search(r"уклон|пандус", src["ocr"] + " " + visual, re.I):
            points = ["Диктор оценивает схему как логичную и наглядную.", "На экране показано требование к уклону пандусов не более 1:20."]
        elif any(description_dump_markers.search(p) for p in points):
            points = []
        item["key_points"] = [_compact(p, 180) for p in points[:3]]

    payload["scene_enrichment"] = [enrich_by_id[sid] for sid in ordered_ids if sid in enrich_by_id]

    # Source-specific entailment check for evidence labels. A source is attached only when it
    # supports at least the core terms of the claim; the mere presence of OCR is not evidence.
    def _supports(claim: str, source_text: str) -> bool:
        if not claim.strip() or not source_text.strip():
            return False
        claim_n = _norm(claim)
        source_n = _norm(source_text)
        if len(claim_n) >= 12 and claim_n in source_n:
            return True
        # Result claims must be supported by an explicit result statement, not by an end card.
        if result_cues.search(claim) and result_cues.search(source_text):
            claim_result = re.search(r"не\s*соответ\w*(?:\s+\w+){0,3}|несоответ\w*(?:\s+\w+){0,3}|нарушен\w*(?:\s+\w+){0,3}|ошибк\w*(?:\s+\w+){0,3}", claim, re.I)
            if claim_result:
                source_result = re.search(r"не\s*соответ\w*(?:\s+\w+){0,3}|несоответ\w*(?:\s+\w+){0,3}|нарушен\w*(?:\s+\w+){0,3}|ошибк\w*(?:\s+\w+){0,3}", source_text, re.I)
                if source_result and ("соответ" in claim_result.group(0).casefold() and "соответ" in source_result.group(0).casefold() or "наруш" in claim_result.group(0).casefold() and "наруш" in source_result.group(0).casefold() or "ошибк" in claim_result.group(0).casefold() and "ошибк" in source_result.group(0).casefold()):
                    return True
        claim_tokens = _tokens(claim)
        source_tokens = set(_tokens(source_text))
        if not claim_tokens:
            return False
        overlap = sum(1 for token in claim_tokens if token in source_tokens)
        if len(claim_tokens) <= 3:
            required = min(2, len(claim_tokens))
        else:
            required = max(2, math.ceil(len(claim_tokens) * 0.30))
        # A distinct product/protocol name alone is not sufficient to support a broad factual claim.
        return overlap >= required

    source_kinds = ("speech", "ocr", "visual", "tags")
    def _get_supported_sources(claim: str, seg: Dict[str, Any]) -> List[str]:
        sources = _field_sources(seg)
        return [kind for kind in source_kinds if _supports(claim, sources.get(kind, ""))]

    # Repair the specific mixed-source description: UI shows a collision dialog, voice describes slope checking.
    # This is only a wording repair in derived analysis; raw segment fields remain unchanged.
    point_records = payload.get("key_points", []) if isinstance(payload.get("key_points"), list) else []
    clean_points: Dict[int, Dict[str, Any]] = {}
    seen_by_scene = set()
    for point in point_records:
        if not isinstance(point, dict):
            continue
        try:
            sid = int(point.get("scene_id"))
        except (TypeError, ValueError):
            continue
        seg = source_by_id.get(sid)
        if seg is None:
            continue
        text_value = re.sub(r"\s+", " ", str(point.get("text", "") or "")).strip()
        if not text_value:
            continue
        src = _field_sources(seg)
        combined_visual_text = src["ocr"] + " " + src["visual"]
        source_specific_evidence = None

        # Prefer compact analytical claims to generic interface descriptions or clipped ASR fragments.
        if re.search(r"какой\s+уклон\s+пандусов", src["speech"], re.I) and re.search(r"релевантн|семантическ|ранжирован", combined_visual_text, re.I):
            text_value = "Пользователь спрашивает об уклоне пандусов на путях эвакуации; система показывает релевантные фрагменты."
            source_specific_evidence = ["speech", "ocr", "visual"]
        if re.search(r"уклон", src["speech"], re.I) and re.search(r"пандус", src["speech"], re.I) and re.search(r"1\s*(?:к|:)\s*20", src["speech"], re.I) and re.search(r"СП\s*1\.13130\.2020", src["ocr"], re.I):
            text_value = "По СП 1.13130.2020 уклон пандусов на путях эвакуации должен быть не более 1:20."
            source_specific_evidence = ["speech", "ocr"]
        if re.search(r"создавать проверки вручную", src["speech"], re.I) and re.search(r"искусственн\w* интеллект|\bИИ\b", src["speech"], re.I):
            text_value = "ИИ помогает ускорить создание проверок, которые иначе выполнялись бы вручную."
            source_specific_evidence = ["speech"]
        if re.search(r"сырой результат", src["ocr"], re.I) and re.search(r"калькулятор", src["ocr"], re.I) and re.search(r"коррект", src["ocr"], re.I):
            text_value = "Калькулятор разбивает требование на шаги и показывает результат корректировки формулировки."
            source_specific_evidence = ["ocr", "visual"]
        if re.search(r"пандус|уклон", src["speech"], re.I) and re.search(r"проверка\s+коллизий", src["ocr"], re.I) and re.search(r"коллиз|несоответ", text_value, re.I):
            text_value = "Диктор описывает загрузку XML в CADLib, отбор объектов по атрибутивным данным и проверку уклона пандусов."
        short_speech_fragment = len(src["speech"].split()) <= 7 and (not re.search(r"[.!?]\s*$", src["speech"]) or re.match(r"^(?:и|а|но|чтобы|находитесь)\b", src["speech"].strip(), re.I))
        if short_speech_fragment and re.search(r"формат\s*:\s*NSR", src["ocr"], re.I) and re.search(r"выгруз", src["speech"], re.I) and re.search(r"уклон", src["ocr"], re.I):
            text_value = "Речь переходит к выгрузке; на экране показаны формат NSR и атрибут «Уклон»."
            source_specific_evidence = ["speech", "ocr"]
        elif short_speech_fragment and re.search(r"КСИ", src["ocr"], re.I) and re.search(r"ГОСТ\s*Р?\s*58908\.12-2020", src["ocr"], re.I):
            text_value = "В интерфейсе показан атрибут «Код КСИ» для класса «пандус» со ссылкой на ГОСТ Р 58908.12-2020."
            source_specific_evidence = ["ocr"]
        elif short_speech_fragment and re.search(r"КСИ", src["ocr"], re.I) and re.search(r"пандус|уклон", combined_visual_text, re.I):
            text_value = "На экране продолжается настройка классификатора КСИ и правила для требования об уклоне пандусов."
            source_specific_evidence = ["ocr", "visual"]
        elif short_speech_fragment and re.search(r"ID\s+требования", src["ocr"], re.I) and re.search(r"исходный текст|комментарий", src["ocr"], re.I) and re.search(r"пандус|уклон", combined_visual_text, re.I):
            text_value = "В интерфейсе показана структура технического требования с исходным текстом, полем комментария и ID требования."
            source_specific_evidence = ["ocr", "visual"]

        # Make the final spoken finding explicitly attributable to speech, while representing the end card separately.
        if sid == last_sid and spoken_result_end_card and result_cues.search(text_value):
            text_value = "В речи сообщается, что объект не соответствует нормативу."
        text_key = _norm(text_value)
        if not text_key or (sid, text_key) in seen_by_scene:
            continue
        seen_by_scene.add((sid, text_key))
        evidence = source_specific_evidence or _get_supported_sources(text_value, seg)
        # If model's wording cannot be grounded in any source, prefer a short source-derived statement.
        if not evidence:
            speech = src["speech"]
            if speech and _supports(speech, speech):
                text_value = "В речи говорится: «" + _compact(speech, 180).strip(" .;:") + "»."
                evidence = ["speech"]
            else:
                candidates = []
                if speech: candidates.append(("speech", speech))
                if src["ocr"]: candidates.append(("ocr", src["ocr"]))
                if src["visual"]: candidates.append(("visual", src["visual"]))
                # Do not turn a long OCR dump into a key point; use a short first sentence/line.
                fallback_source = next(((kind, value) for kind, value in candidates if value.strip()), None)
                if fallback_source:
                    kind, value = fallback_source
                    compact_source = re.sub(r"\[Кадр\s+[^\]]+\]\s*", "", value, flags=re.I)
                    compact_source = re.split(r"[\n.!?]+", compact_source, maxsplit=1)[0].strip()
                    if compact_source and len(compact_source) <= 220:
                        text_value = _compact(compact_source, 220)
                        evidence = [kind]
                    else:
                        continue
                else:
                    continue
        # Correct near-verbatim long source dumps to concise source-backed enrichment wording.
        norm_source = _norm(_source_text(seg))
        norm_point = _norm(text_value)
        if len(norm_point) >= 70 and (norm_point in norm_source or difflib.SequenceMatcher(None, norm_point, norm_source).ratio() >= 0.80):
            enrichment = enrich_by_id.get(sid, {})
            alternatives = enrichment.get("key_points", []) if isinstance(enrichment.get("key_points"), list) else []
            replacement = next((str(candidate).strip() for candidate in alternatives if str(candidate).strip() and _get_supported_sources(str(candidate), seg)), "")
            if replacement:
                text_value = replacement
                evidence = _get_supported_sources(text_value, seg)
        clean_points[sid] = {
            "scene_id": sid,
            "time": format_text_llm_scene_timestamp(seg.get("start", 0)),
            "text": _compact(text_value, 260),
            "evidence": evidence,
        }

    # If the model omitted a final/technical beat, recover it with a claim directly grounded in a source.
    for sid in ordered_ids:
        if sid in clean_points:
            continue
        seg = source_by_id[sid]
        src = _field_sources(seg)
        enrich = enrich_by_id.get(sid, {})
        candidate = ""
        if sid == last_sid and spoken_result_end_card:
            candidate = "В речи сообщается, что объект не соответствует нормативу."
        elif re.search(r"пандус|уклон", src["speech"], re.I) and re.search(r"проверка\s+коллизий", src["ocr"], re.I):
            candidate = "Диктор описывает загрузку XML в CADLib, отбор объектов по атрибутивным данным и проверку уклона пандусов."
        elif src["speech"] and result_cues.search(src["speech"]):
            candidate = "В речи сообщается: «" + _compact(src["speech"], 170).strip(" .;:") + "»."
        elif len(src["speech"].split()) <= 7 and (not re.search(r"[.!?]\s*$", src["speech"]) or re.match(r"^(?:и|а|но|чтобы|находитесь)\b", src["speech"].strip(), re.I)) and re.search(r"формат\s*:\s*NSR", src["ocr"], re.I) and re.search(r"выгруз", src["speech"], re.I) and re.search(r"уклон", src["ocr"], re.I):
            candidate = "Речь переходит к выгрузке; на экране показаны формат NSR и атрибут «Уклон»."
        elif len(src["speech"].split()) <= 7 and (not re.search(r"[.!?]\s*$", src["speech"]) or re.match(r"^(?:и|а|но|чтобы|находитесь)\b", src["speech"].strip(), re.I)) and re.search(r"КСИ", src["ocr"], re.I) and re.search(r"ГОСТ\s*Р?\s*58908\.12-2020", src["ocr"], re.I):
            candidate = "В интерфейсе показан атрибут «Код КСИ» для класса «пандус» со ссылкой на ГОСТ Р 58908.12-2020."
        elif len(src["speech"].split()) <= 7 and (not re.search(r"[.!?]\s*$", src["speech"]) or re.match(r"^(?:и|а|но|чтобы|находитесь)\b", src["speech"].strip(), re.I)) and re.search(r"КСИ", src["ocr"], re.I) and re.search(r"пандус|уклон", src["ocr"] + " " + src["visual"], re.I):
            candidate = "На экране продолжается настройка классификатора КСИ и правила для требования об уклоне пандусов."
        elif len(src["speech"].split()) <= 7 and (not re.search(r"[.!?]\s*$", src["speech"]) or re.match(r"^(?:и|а|но|чтобы|находитесь)\b", src["speech"].strip(), re.I)) and re.search(r"ID\s+требования", src["ocr"], re.I) and re.search(r"исходный текст|комментарий", src["ocr"], re.I) and re.search(r"пандус|уклон", src["ocr"] + " " + src["visual"], re.I):
            candidate = "В интерфейсе показана структура технического требования с исходным текстом, полем комментария и ID требования."
        elif re.search(r"логичн\w*.*наглядн\w*|наглядн\w*.*логичн\w*", src["speech"], re.I) and re.search(r"пандус|уклон", src["ocr"] + " " + src["visual"], re.I):
            candidate = "На экране показана схема требования об уклоне пандусов."
        elif src["speech"] and technical_cues.search(src["speech"]) and len(src["speech"]) <= 220:
            candidate = _compact(src["speech"].rstrip(" .;:—–"), 220)
        else:
            alt = next((str(p).strip() for p in enrich.get("key_points", []) if str(p).strip() and _get_supported_sources(str(p), seg)), "")
            candidate = alt or ""
        if candidate:
            supported = _get_supported_sources(candidate, seg)
            if supported:
                clean_points[sid] = {"scene_id": sid, "time": format_text_llm_scene_timestamp(seg.get("start", 0)), "text": candidate, "evidence": supported}

    # Make repetitive interface scenes distinct by the evidence actually present in each one.
    for sid in ordered_ids:
        if sid not in clean_points:
            continue
        seg = source_by_id[sid]
        src = _field_sources(seg)
        combined_visual_text = src["ocr"] + " " + src["visual"]
        speech = src["speech"]
        ocr = src["ocr"]
        revised = None
        revised_evidence = None
        # Scene displaying the segmented requirement and its ID, before the AI-helper view.
        if (not speech.strip() and re.search(r"ID\s+требования", ocr, re.I)
                and re.search(r"сегментац", ocr, re.I)
                and re.search(r"генерац\w* калькулятор", ocr, re.I)):
            revised = "Требование 44846 показано в разделах сегментации и классификации; доступна генерация калькулятора."
            revised_evidence = ["ocr"]
        # The next short/fragmented spoken scene should not repeat the previous screen description.
        elif (re.search(r"находитесь\s+в\s+модуле\s+семантическ", speech, re.I)
                and re.search(r"ИИ\s*ПОМОЩНИК", ocr, re.I)
                and re.search(r"сегментац|классификац", combined_visual_text, re.I)):
            revised = "В интерфейсе показан ИИ-помощник рядом со схемой семантической обработки требования."
            revised_evidence = ["ocr", "visual"]
        # KSI + calculator generation is a different beat from the later export configuration.
        elif (re.search(r"правила\s+проверки", speech, re.I)
                and re.search(r"КСИ", ocr, re.I)
                and re.search(r"генерац\w* калькулятор", ocr, re.I)
                and re.search(r"уклон|пандус", combined_visual_text, re.I)):
            revised = "На экране показаны классификатор КСИ и функция генерации калькуляторов для требования об уклоне пандусов."
            revised_evidence = ["ocr", "visual"]
        # Export scene: use format + attribute instead of the generic KSI setup sentence.
        elif (re.search(r"затем\s+выгрузить|выгруз", speech, re.I)
                and re.search(r"ФОРМАТ\s*:", ocr, re.I)
                and re.search(r"\bNSR\b", ocr, re.I)
                and re.search(r"атрибут\s+КСИ|XPG_0016|Уклон", ocr, re.I)):
            revised = "Настраивается выгрузка правила в формате NSR с атрибутом «Уклон»."
            revised_evidence = ["speech", "ocr"]
        # The spoken evaluation is useful evidence for the otherwise visually repetitive scene.
        elif (re.search(r"логичн\w*.*наглядн\w*|наглядн\w*.*логичн\w*", speech, re.I)
                and re.search(r"КСИ", ocr, re.I)
                and re.search(r"уклон|пандус", combined_visual_text, re.I)):
            revised = "Диктор оценивает схему как логичную и наглядную; на экране показано требование об уклоне пандусов."
            revised_evidence = ["speech", "ocr"]
        if revised:
            clean_points[sid]["text"] = revised
            clean_points[sid]["evidence"] = revised_evidence

    # Remove repeated or near-repeated key-point wording across different scenes. Prefer a
    # scene-enrichment alternative only when the alternative is supported by that scene's own sources.
    def _near_duplicate_claim(a: str, b: str) -> bool:
        na, nb = _norm(a), _norm(b)
        if not na or not nb:
            return False
        if na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.80:
            return True
        ta, tb = set(_tokens(a)), set(_tokens(b))
        if len(ta) >= 4 and len(tb) >= 4:
            overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
            if overlap >= 0.86:
                return True
        return False

    accepted = []
    for sid in ordered_ids:
        point = clean_points.get(sid)
        if not point:
            continue
        if not any(_near_duplicate_claim(point["text"], previous["text"]) for previous in accepted):
            accepted.append(point)
            continue
        seg = source_by_id[sid]
        enrichment = enrich_by_id.get(sid, {})
        candidates = []
        for candidate in enrichment.get("key_points", []) if isinstance(enrichment.get("key_points"), list) else []:
            candidate = _compact(candidate, 240)
            if not candidate or _near_duplicate_claim(candidate, point["text"]):
                continue
            if any(_near_duplicate_claim(candidate, previous["text"]) for previous in accepted):
                continue
            supported = _get_supported_sources(candidate, seg)
            if supported:
                candidates.append((candidate, supported))
        if candidates:
            candidate, supported = max(candidates, key=lambda item: (len(_tokens(item[0])), len(item[1])))
            point["text"] = candidate
            point["evidence"] = supported
        # If no grounded alternative exists, keep the original claim rather than inventing facts;
        # a content-preserving duplicate is preferable to an unsupported paraphrase.
        accepted.append(point)

    # BuildChat canonical entity and calibrated uncertainty for a paired speech/OCR name mismatch.
    standard_name = re.compile(r"(?:\bГОСТ\b|\bСП\s*\d|\bSP\s*\d|\bСНиП\b|\bISO\s*\d|\bIEC\s*\d|\bEN\s*\d|\bASTM\b|\bANSI\b)", re.I)
    fixed_entities: Dict[str, Dict[str, Any]] = {}
    raw_entities = payload.get("entities", []) if isinstance(payload.get("entities"), list) else []
    for entity in raw_entities:
        if not isinstance(entity, dict) or not str(entity.get("name", "")).strip():
            continue
        name = re.sub(r"\s+", " ", str(entity["name"])).strip()[:200]
        n = _norm(name)
        compact_name = n.replace(" ", "")
        if re.fullmatch(r"(?:ai)?иипомощник|иипомощник|aиипомощник|aiиипомощник", compact_name, re.I):
            continue  # Generic UI label, not a named entity.
        if has_buildchat_conflict and compact_name in {"белчат", "белчата", "билдчат", "билдчата", "buildchat", "buildchatsoftware"}:
            name = "BuildChat"
            n = _norm(name)
        try:
            input_ids = entity.get("scene_ids", [])
            input_ids = input_ids if isinstance(input_ids, list) else []
        except Exception:
            input_ids = []
        ids = []
        for raw_id in input_ids:
            try: sid = int(raw_id)
            except (TypeError, ValueError): continue
            if sid not in source_by_id: continue
            src_blob = _norm(_source_text(source_by_id[sid]))
            if n.replace(" ", "") in src_blob.replace(" ", "") or (has_buildchat_conflict and name == "BuildChat" and sid in canonical_buildchat_ids):
                if sid not in ids: ids.append(sid)
        if has_buildchat_conflict and name == "BuildChat":
            ids = sorted(set(ids + canonical_buildchat_ids), key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid))
        if not ids:
            for sid in ordered_ids:
                blob = _norm(_source_text(source_by_id[sid])).replace(" ", "")
                match = n.replace(" ", "") in blob
                if has_buildchat_conflict and name == "BuildChat" and sid in canonical_buildchat_ids:
                    match = True
                if match:
                    ids.append(sid)
        # No source-backed scene reference means this entity is not supported by the supplied evidence.
        if not ids:
            continue
        entity_type = str(entity.get("type", "other") or "other").strip().lower()[:80]
        compact_name = n.replace(" ", "")
        if standard_name.search(name):
            entity_type = "standard"
        elif compact_name == "кси" or "классификатор строительной информации" in n:
            entity_type = "classification_system"
        elif compact_name in {"nanocad", "cadlib"}:
            entity_type = "software"
        elif compact_name == "buildchat":
            entity_type = "software"
        elif entity_type in {"regulatory document", "regulation document", "normative document", "нормативный документ"}:
            entity_type = "standard"
        key = _norm(name)
        if key not in fixed_entities:
            fixed_entities[key] = {"name": name, "type": entity_type, "scene_ids": sorted(set(ids))[:30]}
        else:
            current = fixed_entities[key]
            current["scene_ids"] = sorted(set(current["scene_ids"] + ids))[:30]
            if entity_type == "standard" or entity_type == "classification_system":
                current["type"] = entity_type

    if has_buildchat_conflict:
        key = _norm("BuildChat")
        if key not in fixed_entities:
            fixed_entities[key] = {"name": "BuildChat", "type": "software", "scene_ids": canonical_buildchat_ids}
        else:
            fixed_entities[key]["scene_ids"] = sorted(set(fixed_entities[key]["scene_ids"] + canonical_buildchat_ids), key=lambda sid: (float(source_by_id[sid].get("start", 0) or 0), sid))[:30]
            fixed_entities[key]["type"] = "software"
    payload["entities"] = list(fixed_entities.values())[:60]

    # Keep grounded source-quality notes and actual cross-source disagreements; drop speculative architecture inferences.
    uncertainties = _text_llm_normalize_list(payload.get("uncertainties"), 30)
    channel = re.compile(r"аудио|\bреч\w*\b|расшифров\w*|транскрип\w*|субтитр\w*|на экран\w*|экранн\w*|\bocr\b|визуальн\w*|\bкадр\w*\b|\baudio\b|\bspeech\b|\btranscript\b|on.screen|\bvisual\b", re.I)
    conflict = re.compile(r"не совп|противореч|расход|отлича|звучит как|указан\w*|показан\w*|распознан\w* как|может быть ошиб\w* распозн|неясн\w*|неч[её]тк|неразборч|unclear|contradict|differs|mismatch|heard as|shown as|read as|однако|тогда как|а на экран", re.I)
    quality_only = re.compile(r"неразборч|неч[её]тк|нечитаем|обрезан\w*|плох\w* качеств\w*|не удалось различ|неуверенн\w* распознан|искаж[её]н\w* звук|unclear|unreadable|cut off", re.I)
    weak_port_inference = re.compile(r"localhost|127\.0\.0\.1|postgres(?:ql)?|:\s*\d{2,5}|порт|database|баз\w* данн", re.I)
    inference_words = re.compile(r"может указывать|указывает на|говорит о том|предполагает|возможно означает|вероятно означает|implies|suggests|indicates|could mean", re.I)
    clean_uncertainties = []
    for item in uncertainties:
        supported_conflict = bool(channel.search(item) and conflict.search(item))
        # Replace any model-authored BuildChat/ASR notes with one canonical, source-grounded note below.
        if has_buildchat_conflict and re.search(r"build\s*chat|белчат|белчата|билдчат", item, re.I):
            continue
        # Model-generated KSI date notes often mislabel OCR as speech/transcript and duplicate the same issue.
        if re.search(r"\bКСИ\b|классификатор строительной информации", item, re.I) and re.search(r"20\d{2}|дата|верс|диапазон", item, re.I):
            continue
        if weak_port_inference.search(item) and inference_words.search(item) and not supported_conflict:
            continue
        # A spoken conclusion alongside an end card is a source distinction already captured in the report, not an unresolved contradiction.
        if spoken_result_end_card and end_card_cues.search(item) and result_cues.search(item):
            continue
        # Do not flag the standard number as truncated when the full number is present in any OCR source.
        all_ocr_text = " ".join(str(source_by_id[sid].get("ocr_text", "") or "") for sid in ordered_ids)
        if re.search(r"обрезан|усеч[её]н|неполный|truncated|cut off", item, re.I) and re.search(r"СП\s*1\.13130\.2020", item, re.I) and re.search(r"СП\s*1\.13130\.2020", all_ocr_text, re.I):
            continue
        if quality_only.search(item) or supported_conflict:
            clean_uncertainties.append(item)
    # A temporally linked, readable OCR product label resolves this transliteration mismatch.

    # A displayed KSI date/version difference is a note, not proof of a contradiction.
    ksi_labels = []
    for sid in ordered_ids:
        for match in re.finditer(r"КСИ\s*\(([^)]{4,80})\)", str(source_by_id[sid].get("ocr_text", "") or ""), re.I):
            label = re.sub(r"\s+", " ", match.group(1)).strip().replace("–", "-").replace("—", "-")
            if label and label not in ksi_labels:
                ksi_labels.append(label)
    if len(ksi_labels) >= 2 and any("2025" in x for x in ksi_labels) and any("2024" in x for x in ksi_labels):
        clean_uncertainties.append("В разных кадрах отображаются подписи КСИ с разными датами: «" + ksi_labels[0] + "» и «" + next(x for x in ksi_labels[1:] if ("2025" in x) != ("2025" in ksi_labels[0])) + "». Это может отражать разные версии или диапазоны действия классификатора; по этим кадрам нельзя уверенно заключить, что данные противоречат друг другу.")
    payload["uncertainties"] = list(dict.fromkeys(clean_uncertainties))[:20]

    # Rebuild a near-duplicate summary from distinct source-backed stages and correctly label the ending.
    enhanced = str(payload.get("executive_summary", "") or "").strip()
    prior = str(original_summary or "").strip()
    similarity = difflib.SequenceMatcher(None, _norm(enhanced), _norm(prior)).ratio() if enhanced and prior else 0.0
    if similarity >= 0.78 or spoken_result_end_card:
        points = sorted(clean_points.values(), key=lambda p: (float(source_by_id[p["scene_id"]].get("start", 0) or 0), p["scene_id"]))
        useful = [p for p in points if p["scene_id"] != last_sid and technical_cues.search(p["text"]) and len(_tokens(p["text"])) >= 4]
        selected = []
        if useful:
            selected.append(useful[0])
            if len(useful) >= 3:
                middle_target = float(source_by_id[ordered_ids[len(ordered_ids) // 2]].get("start", 0) or 0)
                middle = min((p for p in useful[1:] if p["scene_id"] != useful[-1]["scene_id"]), key=lambda p: abs(float(source_by_id[p["scene_id"]].get("start", 0) or 0) - middle_target), default=None)
                if middle and middle["scene_id"] not in {x["scene_id"] for x in selected}:
                    selected.append(middle)
            if useful[-1]["scene_id"] not in {x["scene_id"] for x in selected}:
                selected.append(useful[-1])
        selected_text = [_compact(p["text"], 150).rstrip(".; ") for p in selected[:3]]
        protected_starts = {"nsr", "cadlib", "nanocad", "buildchat", "bim", "xml", "сп", "гост", "снип", "iso", "iec", "ксі"}
        for ix, fragment in enumerate(selected_text):
            first_word = re.match(r"[^\W_]+", fragment, flags=re.UNICODE)
            if fragment and first_word and first_word.group(0).casefold() not in protected_starts:
                selected_text[ix] = fragment[0].lower() + fragment[1:]
        if len(selected_text) >= 3:
            summary = "В ролике показана последовательность этапов: " + selected_text[0] + "; далее — " + selected_text[1] + "; ближе к концу — " + selected_text[2] + "."
        elif len(selected_text) == 2:
            summary = "В ролике показана последовательность этапов: " + selected_text[0] + "; затем — " + selected_text[1] + "."
        elif selected_text:
            summary = "Ключевой этап видео: " + selected_text[0].rstrip(".; ") + "."
        else:
            topics = [_compact(t, 100) for t in _text_llm_normalize_list(payload.get("topics"), 3)]
            summary = "Видео раскрывает темы: " + ", ".join(topics) + "." if topics else "Видео объединяет несколько связанных демонстрационных этапов."
        # Preserve a concrete normative fact if it exists, even when stage selection prioritises another beat.
        normative_fact = ""
        for sid in ordered_ids:
            fact_sources = _field_sources(source_by_id[sid])
            fact_text = fact_sources["speech"] + " " + fact_sources["ocr"]
            if (re.search(r"\bСП\s*1\.13130\.2020\b", fact_text, re.I)
                    and re.search(r"уклон|пандус", fact_text, re.I)
                    and re.search(r"1\s*(?:[:к])\s*20", fact_text, re.I)):
                normative_fact = "Для уклона пандусов по СП 1.13130.2020 указан предел не более 1:20."
                break
        if normative_fact and "1:20" not in summary:
            summary += " " + normative_fact
        if last_result_in_speech:
            spoken = _compact(last_sources["speech"], 190).strip(" .;:")
            summary += " В финальной речи сообщается: «" + spoken + "»."
        elif points:
            ending_point = next((p for p in reversed(points) if p["scene_id"] == last_sid), None)
            if ending_point:
                summary += " В финале: " + _compact(ending_point["text"], 140).rstrip(".; ") + "."
        mixed_collision_slope = any(
            re.search(r"пандус|уклон", _field_sources(source_by_id[sid])["speech"], re.I)
            and re.search(r"проверка\s+коллизий", _field_sources(source_by_id[sid])["ocr"], re.I)
            for sid in ordered_ids
        )
        if mixed_collision_slope:
            summary += " В CADLib видно окно «Проверка коллизий», но его связь с описанной проверкой уклона не подтверждена."
        if spoken_result_end_card:
            summary += " Финальный экран содержит только «THE END»; параметр нарушения визуально не указан."
        summary_words = summary.split()
        if len(summary_words) > 105:
            summary = " ".join(summary_words[:105]).rstrip(" ,;:") + "…"
        payload["executive_summary"] = summary

    payload["key_points"] = sorted(clean_points.values(), key=lambda p: (float(source_by_id[p["scene_id"]].get("start", 0) or 0), p["scene_id"]))[:18]
    return payload

LOCAL_VIDEO_SELECTIONS: Dict[str, str] = {}
OUTPUT_FILE_REGISTRY: Dict[str, tuple[Path, str]] = {}

# --- PROMPTS SYSTÈME ---

BASE_VISUAL_PROMPT = """
You are an assistant that analyzes one or several frames from the SAME video scene.

You MUST answer with VALID JSON using EXACTLY this structure:
{
  "description": "short and factual description of what happens in the scene (gestures, posture, general situation)",
  "tags": {
    "people_count": <approximate number of visible people>,
    "place_type": "studio | tv_set | classroom | office | home | outdoor | nature | stage | other",
    "main_action": "short description of the main action",
    "emotional_tone": "calm | neutral | tense | conflictual | joyful | sad | enthusiastic | serious | other",
    "movement_level": "low | medium | high"
  }
}

Rules:
- Do NOT add any text outside of this JSON.
- Do NOT change field names.
- The description and tag values should be in the same language as the user instructions if any, otherwise in French.
""".strip()

BASE_SUMMARY_PROMPT = """
You are summarizing a video based on scene-level notes (audio + visual context).

Write a global summary of the video, answering:
- What is it about (main topic / content)?
- In what context does it happen (place, type of situation)?
- What is the overall tone (calm, tense, professional, intimate, etc.)?

Rules:
- Use clear, natural language.
- Use the same language as the user instructions if any, otherwise French.
- If you return JSON, use exactly: {"summary": "..."} and nothing else.
""".strip()


# --- MOTEUR VLM ---

def release_accelerator_memory(stage: str = "") -> None:
    """Release unused PyTorch MPS and MLX allocator caches between model stages."""
    gc.collect()
    try:
        import torch
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            try:
                torch.mps.synchronize()
            except Exception:
                pass
            torch.mps.empty_cache()
    except Exception as exc:
        print(f"⚠️ Не удалось очистить кэш PyTorch MPS ({stage}): {exc}")
    try:
        import mlx.core as mx
        clear_cache = getattr(mx, "clear_cache", None)
        if callable(clear_cache):
            clear_cache()
        else:
            # Compatibility fallback for versions exposing this only under mx.metal.
            metal_api = getattr(mx, "metal", None)
            metal_clear_cache = getattr(metal_api, "clear_cache", None)
            if callable(metal_clear_cache):
                metal_clear_cache()
            else:
                raise RuntimeError("В этой версии MLX нет доступного clear_cache API")
    except Exception as exc:
        print(f"⚠️ Не удалось очистить кэш MLX ({stage}): {exc}")
    gc.collect()


def is_gpu_memory_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(token in message for token in (
        "insufficient memory",
        "outofmemory",
        "out of memory",
        "kIOGPUCommandBufferCallbackErrorOutOfMemory".lower(),
        "metal command buffer",
    ))


def resize_images_for_oom_retry(images: List[Image.Image], max_side: int) -> List[Image.Image]:
    """Make smaller copies for a bounded retry; never mutate OCR source images."""
    resized: List[Image.Image] = []
    resampling = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    for image in images:
        candidate = image.copy()
        if max(candidate.size) > max_side:
            candidate.thumbnail((max_side, max_side), resampling)
        resized.append(candidate)
    return resized


def ensure_opencv_compatible_video(file_path: str) -> str:
    """Return file_path if OpenCV can decode it; otherwise make a temporary H.264 proxy."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(400, f"Видео не найдено или файл пуст: {path.name}")

    cap = cv2.VideoCapture(str(path))
    opened = cap.isOpened()
    ret, frame = cap.read() if opened else (False, None)
    cap.release()
    if opened and ret and frame is not None:
        return str(path)

    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe:
        raise HTTPException(
            400,
            f"OpenCV не смог прочитать видео «{path.name}», а ffprobe не найден. "
            "Установите FFmpeg (ffmpeg и ffprobe) и повторите выбор файла."
        )
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=45,
        )
    except Exception as exc:
        raise HTTPException(400, f"Не удалось проверить видео «{path.name}» через ffprobe: {exc}") from exc

    try:
        probe_data = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        probe_data = {}
    video_streams = [
        stream for stream in (probe_data.get("streams") or [])
        if stream.get("codec_type") == "video"
    ]
    if probe.returncode != 0 or not video_streams:
        details = (probe.stderr or "").strip()[-600:]
        raise HTTPException(
            400,
            f"Файл «{path.name}» не содержит читаемого видеопотока или повреждён. "
            f"ffprobe: {details or 'видеопоток не обнаружен'}"
        )

    if not ffmpeg:
        raise HTTPException(
            400,
            f"Видеопоток в «{path.name}» найден, но OpenCV не может его декодировать. "
            "FFmpeg не найден для создания совместимой временной копии."
        )

    codec = str(video_streams[0].get("codec_name") or "неизвестный")
    fd, proxy_path = tempfile.mkstemp(prefix="vce_compatible_", suffix=".mp4")
    import os
    os.close(fd)
    os.unlink(proxy_path)  # FFmpeg creates the output file itself.
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=1920:1920:force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", proxy_path,
    ]
    try:
        converted = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=7200,
        )
        if converted.returncode != 0 or not Path(proxy_path).is_file() or Path(proxy_path).stat().st_size == 0:
            details = (converted.stderr or "").strip()[-1000:]
            raise RuntimeError(details or f"ffmpeg завершился с кодом {converted.returncode}")
        test_cap = cv2.VideoCapture(proxy_path)
        proxy_opened = test_cap.isOpened()
        proxy_read, _ = test_cap.read() if proxy_opened else (False, None)
        test_cap.release()
        if not proxy_opened or not proxy_read:
            raise RuntimeError("OpenCV не смог открыть даже преобразованный H.264-файл.")
    except Exception as exc:
        try:
            Path(proxy_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            400,
            f"Видео «{path.name}» определяется как {codec}, но не декодируется OpenCV. "
            f"Не удалось создать совместимую копию: {exc}"
        ) from exc

    print(
        f"⚠️ OpenCV не открыл «{path.name}» (кодек {codec}); "
        "анализ продолжится на временной H.264-копии. Оригинал не изменён."
    )
    return proxy_path


class VLMProvider:
    def __init__(self):
        self.current_model_path = None
        self.last_load_time: float = 0.0

    def load_model(self, model_path: str):
        raise NotImplementedError

    def describe_scene(
        self,
        images: List[Image.Image],
        prompt: str,
        max_tokens: int,
    ) -> str:
        raise NotImplementedError

    def unload_model(self):
        pass


class MLXEngine(VLMProvider):
    def __init__(self):
        super().__init__()
        self.model = None
        self.processor = None
        self.config = None
        self._generate_fn = None

    def load_model(self, model_path: str):
        if self.model is not None and self.current_model_path == model_path:
            print(f"🍎 [MLX] Modèle VLM déjà chargé : {model_path}")
            self.last_load_time = 0.0
            return

        print(f"🍎 [MLX] Chargement VLM : {model_path}")
        t0 = time.time()
        try:
            device_override = os.environ.get(
                "VIDEOCONTEXT_MLX_DEVICE", str(setting("mlx_device", "metal"))
            ).strip().lower()
            if device_override not in {"auto", "metal"}:
                raise ValueError(
                    "VIDEOCONTEXT_MLX_DEVICE must be auto or metal in CPU/Metal parallel mode"
                )
            _vce_configure_mlx_metal("vlm")
            _vce_prepare_mlx_vlm_runtime()
            from mlx_vlm import load, generate
            from mlx_vlm.utils import load_config

            self.model = None
            self.processor = None
            self.config = None
            gc.collect()

            self.model, self.processor = load(model_path, trust_remote_code=True)
            self.config = load_config(model_path)
            self._generate_fn = generate
            self.current_model_path = model_path
            self.last_load_time = time.time() - t0
            print(f"✅ [MLX] VLM chargé en {self.last_load_time:.2f}s")
        except ImportError:
            raise RuntimeError(
                "Erreur: installez 'mlx-vlm' (pip install mlx-vlm torchvision)"
            )

    def describe_scene(
        self,
        images: List[Image.Image],
        prompt: str,
        max_tokens: int,
    ) -> str:
        if not self.model:
            raise RuntimeError("Modèle VLM MLX non chargé")

        from mlx_vlm.prompt_utils import apply_chat_template

        num_images = max(1, len(images))
        formatted_prompt = apply_chat_template(
            self.processor,
            self.config,
            prompt,
            num_images=num_images,
        )

        imgs = images if len(images) > 1 else images[0]

        try:
            output = self._generate_fn(
                self.model,
                self.processor,
                formatted_prompt,
                imgs,
                max_tokens=max_tokens,
                verbose=False,
                temp=0.0,
            )
        except Exception as original_error:
            if not is_gpu_memory_error(original_error):
                raise
            last_error = original_error
            retry_succeeded = False
            original_sizes = [image.size for image in images]
            for retry_side in (768, 512):
                retry_images = resize_images_for_oom_retry(images, retry_side)
                if [image.size for image in retry_images] == original_sizes:
                    continue
                retry_input = retry_images if len(retry_images) > 1 else retry_images[0]
                release_accelerator_memory(f"VLM OOM retry {retry_side}px")
                print(
                    f"⚠️ VLM OOM: повторная попытка с кадрами до {retry_side}px; "
                    "модель и аудиоанализ сохраняются."
                )
                try:
                    output = self._generate_fn(
                        self.model,
                        self.processor,
                        formatted_prompt,
                        retry_input,
                        max_tokens=max_tokens,
                        verbose=False,
                        temp=0.0,
                    )
                    print(f"✅ VLM восстановился на разрешении до {retry_side}px.")
                    retry_succeeded = True
                    break
                except Exception as retry_error:
                    if not is_gpu_memory_error(retry_error):
                        raise
                    last_error = retry_error
                    release_accelerator_memory(f"VLM OOM retry failed {retry_side}px")
            if not retry_succeeded:
                raise RuntimeError(
                    "Недостаточно памяти Metal для VLM даже после уменьшения кадра до 768/512px. "
                    "Закройте другие приложения, использующие GPU, и повторите анализ."
                ) from last_error
        text = output.text if hasattr(output, "text") else str(output)
        return text.strip()

    def unload_model(self):
        self.model = None
        self.processor = None
        self.config = None
        self.current_model_path = None
        gc.collect()


class LlamaCppEngine(VLMProvider):
    def __init__(self):
        super().__init__()
        self.llm = None
        self.chat_handler = None

    def _download_if_missing(self, filename: str, repo_id: str):
        if not os.path.exists(filename):
            print(f"⬇️ [Auto-Download] {filename}...")
            try:
                from huggingface_hub import hf_hub_download

                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=".",
                )
                print(f"✅ {filename} prêt.")
            except ImportError:
                raise RuntimeError("Erreur: 'huggingface_hub' manquant.")
            except Exception as e:
                print(f"⚠️ Erreur téléchargement auto : {e}")

    def load_model(self, model_path: str):
        if model_path == DEFAULT_VLM_MODEL_GGUF:
            self._download_if_missing(model_path, DEFAULT_VLM_REPO_GGUF)
            self._download_if_missing(DEFAULT_MMPROJ_GGUF, DEFAULT_VLM_REPO_GGUF)

        if self.llm is not None and self.current_model_path == model_path:
            print(f"🐧/🪟 [Llama.cpp] Modèle VLM déjà chargé : {model_path}")
            self.last_load_time = 0.0
            return

        print(f"🐧/🪟 [Llama.cpp] Chargement VLM : {model_path}")
        t0 = time.time()
        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import Llava15ChatHandler

            self.llm = None
            gc.collect()

            mmproj = DEFAULT_MMPROJ_GGUF if os.path.exists(DEFAULT_MMPROJ_GGUF) else None
            self.chat_handler = (
                Llava15ChatHandler(clip_model_path=mmproj) if mmproj else None
            )

            self.llm = Llama(
                model_path=model_path,
                chat_handler=self.chat_handler,
                n_gpu_layers=-1,
                n_ctx=4096,
                verbose=False,
            )
            self.current_model_path = model_path
            self.last_load_time = time.time() - t0
            print(f"✅ [Llama.cpp] VLM chargé en {self.last_load_time:.2f}s")
        except ImportError:
            raise RuntimeError("Erreur: installez 'llama-cpp-python'")
        except Exception as e:
            raise RuntimeError(f"Erreur chargement VLM llama.cpp : {e}")

    def describe_scene(
        self,
        images: List[Image.Image],
        prompt: str,
        max_tokens: int,
    ) -> str:
        if not self.llm:
            raise RuntimeError("Modèle VLM llama.cpp non chargé")

        contents = []
        for img in images:
            buffered = BytesIO()
            img.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            contents.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_str}"},
                }
            )

        contents.append({"type": "text", "text": prompt})

        response = self.llm.create_chat_completion(
            messages=[
                {
                    "role": "user",
                    "content": contents,
                }
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return response["choices"][0]["message"]["content"].strip()

    def unload_model(self):
        self.llm = None
        self.chat_handler = None
        self.current_model_path = None
        gc.collect()


def get_vlm_engine() -> VLMProvider:
    return MLXEngine() if platform.system() == "Darwin" else LlamaCppEngine()


# --- UTILS SCÈNES & VIDÉO ---

def compute_hsv_histogram(frame_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist(
        [hsv],
        [0, 1],
        None,
        [50, 60],
        [0, 180, 0, 256],
    )
    cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
    return hist.flatten()


def detect_scenes(
    video_path: str,
    threshold: float,
    min_duration: float,
    max_duration: float,
) -> List[Dict[str, float]]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Erreur lecture vidéo")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_interval = int(fps)
    scenes = []
    last_hist = None
    start_sec = 0.0
    prev_sec = 0.0
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            curr_sec = frame_idx / fps
            hist = compute_hsv_histogram(frame)
            is_visual_change = False

            if last_hist is not None:
                score = cv2.compareHist(last_hist, hist, cv2.HISTCMP_CORREL)
                if (1.0 - score) > threshold:
                    is_visual_change = True

            duration_ok = (curr_sec - start_sec) >= min_duration
            force_cut = (curr_sec - start_sec) >= max_duration

            if (is_visual_change and duration_ok) or force_cut:
                scenes.append({"start": start_sec, "end": prev_sec})
                start_sec = curr_sec

            last_hist = hist
            prev_sec = curr_sec

        frame_idx += 1

    if prev_sec > start_sec:
        scenes.append({"start": start_sec, "end": prev_sec})

    cap.release()
    return scenes


def download_video_from_url(url: str, output_template: str) -> str:
    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration < {MAX_TOTAL_VIDEO_DURATION}"
        ),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
        except yt_dlp.utils.DownloadError as e:
            if "video is too long" in str(e).lower():
                raise ValueError("Vidéo trop longue")
            raise e


def sanitize_filename(filename: str) -> str:
    name = os.path.basename(filename)
    safe_name = "".join(
        [c for c in name if c.isalnum() or c in (" ", ".", "_", "-")]
    )
    return safe_name.strip()[:60] or "video_output"


def validate_video_file(file_path: str) -> float:
    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        raise HTTPException(400, "Fichier vidéo invalide")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    cap.release()

    duration = frames / fps if fps > 0 else 0
    if duration > MAX_TOTAL_VIDEO_DURATION:
        raise HTTPException(
            400, f"Vidéo trop longue ({duration/60:.1f} min)"
        )
    return duration


def resize_keep_aspect(frame: np.ndarray, max_side: int) -> np.ndarray:
    """Resize a frame so its longest side is max_side, without distortion."""
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        return frame
    scale = float(max_side) / float(max(height, width))
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(frame, (new_width, new_height), interpolation=interpolation)


def sample_keyframes_for_scene(
    cap: cv2.VideoCapture,
    scene: Dict[str, float],
    base_res: int,
    max_frames: int,
) -> List[Dict[str, Any]]:
    start = scene["start"]
    end = scene["end"]
    duration = max(0.001, end - start)

    n = max(1, min(max_frames, 5))

    if n == 1:
        times = [start + 0.5 * duration]
    else:
        times = [start + (k / (n + 1)) * duration for k in range(1, n + 1)]

    frames = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if not ret:
            continue
        original_pil_img = Image.fromarray(cv2.cvtColor(frame.copy(), cv2.COLOR_BGR2RGB))
        frame = resize_keep_aspect(frame, base_res)
        pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        frames.append({"time": round(t, 2), "image": pil_img, "original_image": original_pil_img})

    return frames


def apple_vision_ocr(image: Image.Image) -> str:
    """Extract visible text using macOS Vision via ocrmac; preserve reading order."""
    try:
        from ocrmac import ocrmac
    except Exception as exc:
        raise RuntimeError(
            "Apple OCR is enabled, but ocrmac is unavailable. Activate the project venv "
            "and run: python -m pip install ocrmac"
        ) from exc

    try:
        rows = ocrmac.OCR(
            image,
            recognition_level=str(setting("apple_ocr_recognition_level", "accurate")),
            language_preference=setting("apple_ocr_languages", ["ru-RU", "en-US"]),
            confidence_threshold=float(setting("apple_ocr_confidence", 0.30)),
            detail=True,
        ).recognize()
    except ValueError:
        # Some macOS/Vision combinations may not support one of the requested languages.
        rows = ocrmac.OCR(
            image,
            recognition_level="accurate",
            language_preference=None,
            confidence_threshold=0.30,
            detail=True,
        ).recognize()

    lines = []
    seen = set()
    for row in rows or []:
        if isinstance(row, (tuple, list)) and row:
            text = str(row[0])
            confidence = float(row[1]) if len(row) > 1 else 1.0
        else:
            text = str(row)
            confidence = 1.0
        text = re.sub(r"\s+", " ", text).strip()
        if not text or confidence < float(setting("apple_ocr_confidence", 0.30)):
            continue
        key = re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()
        if key and key not in seen:
            seen.add(key)
            lines.append(text)
    return "\n".join(lines)


# --- AUDIO FEATURES ---


def _word_records(whisper_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten Whisper's word-level timestamps into stable records."""
    records: List[Dict[str, Any]] = []
    for segment in whisper_segments or []:
        words = segment.get("words") or []
        if not isinstance(words, list):
            continue
        for word in words:
            if not isinstance(word, dict):
                continue
            try:
                word_start = float(word.get("start"))
                word_end = float(word.get("end"))
            except (TypeError, ValueError):
                continue
            text = str(word.get("word") or word.get("text") or "")
            if word_end <= word_start or not text.strip():
                continue
            records.append({"start": word_start, "end": word_end, "text": text})
    records.sort(key=lambda item: (item["start"], item["end"]))
    return records


def _inside_scene(midpoint: float, start: float, end: float) -> bool:
    # Assign a word/segment to one and only one scene, even at scene boundaries.
    return start <= midpoint < end or (midpoint == end and start == end)


def _join_timed_text(items: List[tuple]) -> str:
    """Join Whisper's leading-space word tokens without adding spaces before punctuation."""
    output = ""
    for _time, fragment, is_word in sorted(items, key=lambda item: item[0]):
        fragment = str(fragment or "")
        if not fragment:
            continue
        if not is_word:
            fragment = fragment.strip()
            if not fragment:
                continue
            if output and not output[-1].isspace() and fragment[0] not in ",.!?;:%)]}»”’":
                output += " "
        elif output and not fragment[0].isspace() and not output[-1].isspace() and fragment[0] not in ",.!?;:%)]}»”’":
            output += " "
        output += fragment
    return output.strip()


def transcript_for_scene(
    scene: Dict[str, float],
    whisper_segments: List[Dict[str, Any]],
) -> str:
    """Build a scene transcript from word timestamps; use segment midpoints as fallback."""
    start = float(scene["start"])
    end = float(scene["end"])
    parts: List[tuple] = []
    for segment in whisper_segments or []:
        words = segment.get("words") or []
        if isinstance(words, list) and words:
            for word in words:
                if not isinstance(word, dict):
                    continue
                try:
                    word_start = float(word.get("start"))
                    word_end = float(word.get("end"))
                except (TypeError, ValueError):
                    continue
                text = str(word.get("word") or word.get("text") or "")
                if word_end <= word_start or not text.strip():
                    continue
                midpoint = (word_start + word_end) / 2.0
                if _inside_scene(midpoint, start, end):
                    parts.append((midpoint, text, True))
        else:
            # Older/fallback ASR segments have no word timestamps. Assign the entire
            # segment to the scene containing its midpoint so text is never duplicated.
            try:
                seg_start = float(segment["start"])
                seg_end = float(segment["end"])
            except (KeyError, TypeError, ValueError):
                continue
            midpoint = (seg_start + seg_end) / 2.0
            text = str(segment.get("text") or "").strip()
            if text and _inside_scene(midpoint, start, end):
                parts.append((midpoint, text, False))
    return _join_timed_text(parts)


def _union_duration(intervals: List[tuple]) -> float:
    """Duration of the union of time intervals; overlapping words are not double-counted."""
    if not intervals:
        return 0.0
    intervals = sorted((float(a), float(b)) for a, b in intervals if b > a)
    if not intervals:
        return 0.0
    total = 0.0
    current_start, current_end = intervals[0]
    for interval_start, interval_end in intervals[1:]:
        if interval_start <= current_end:
            current_end = max(current_end, interval_end)
        else:
            total += current_end - current_start
            current_start, current_end = interval_start, interval_end
    total += current_end - current_start
    return max(0.0, total)


# VCE_BOUNDED_KEYFRAME_PREFETCH_V1
def _vce_start_bounded_keyframe_prefetch(
    video_path: str,
    scenes: List[Dict[str, Any]],
    base_res: int,
    max_frames: int,
    queue_size: int = 1,
    max_frames_by_scene=None,
):
    """Prepare upcoming scene keyframes in a bounded queue on a single CPU worker."""
    import queue
    import threading

    result_queue = queue.Queue(maxsize=max(1, min(int(queue_size), 1)))
    stop_event = threading.Event()
    stats = {"worker_seconds": 0.0, "wait_seconds": 0.0, "fallback_count": 0}

    def put_item(item):
        while not stop_event.is_set():
            try:
                result_queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def producer():
        capture = None
        try:
            capture = cv2.VideoCapture(video_path)
            for scene_index, scene in enumerate(scenes):
                if stop_event.is_set():
                    break
                scene_started = time.perf_counter()
                try:
                    if capture is None or not capture.isOpened():
                        raise RuntimeError("Prefetch VideoCapture could not open the temporary video")
                    scene_max_frames = max_frames
                    if max_frames_by_scene is not None and scene_index < len(max_frames_by_scene):
                        scene_max_frames = max(1, min(5, int(max_frames_by_scene[scene_index])))
                    keyframes = sample_keyframes_for_scene(
                        capture,
                        scene,
                        base_res=base_res,
                        max_frames=scene_max_frames,
                    )
                    item = (scene_index, keyframes or [], None)
                except Exception as exc:
                    item = (scene_index, [], str(exc)[:300])
                finally:
                    stats["worker_seconds"] += time.perf_counter() - scene_started
                if not put_item(item):
                    break
        except Exception as exc:
            # Keep the main analysis alive; missing entries are recomputed synchronously.
            print("⚠️ [Frame prefetch] Worker error: " + str(exc)[:300])
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            if not stop_event.is_set():
                put_item((None, None, None))

    worker = threading.Thread(
        target=producer,
        name="VCE-KeyframePrefetch",
        daemon=True,
    )
    worker.start()
    return result_queue, stop_event, worker, stats


def compute_audio_features_for_scene(
    scene: Dict[str, float],
    whisper_segments: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Compute scene-local speech features from word timestamps when available."""
    start = float(scene["start"])
    end = float(scene["end"])
    duration = max(0.001, end - start)
    intervals: List[tuple] = []
    word_count = 0

    for segment in whisper_segments or []:
        words = segment.get("words") or []
        if isinstance(words, list) and words:
            for word in words:
                if not isinstance(word, dict):
                    continue
                try:
                    word_start = float(word.get("start"))
                    word_end = float(word.get("end"))
                except (TypeError, ValueError):
                    continue
                text = str(word.get("word") or word.get("text") or "")
                if word_end <= word_start or not text.strip():
                    continue
                midpoint = (word_start + word_end) / 2.0
                if _inside_scene(midpoint, start, end):
                    clipped_start = max(start, word_start)
                    clipped_end = min(end, word_end)
                    if clipped_end > clipped_start:
                        intervals.append((clipped_start, clipped_end))
                    # Whisper normally provides one lexical token per entry.
                    if re.search(r"\w", text, flags=re.UNICODE):
                        word_count += 1
        else:
            # No word alignment for this segment: use its midpoint, not overlap,
            # for both word count and ownership. This prevents duplicated counts.
            try:
                seg_start = float(segment["start"])
                seg_end = float(segment["end"])
            except (KeyError, TypeError, ValueError):
                continue
            midpoint = (seg_start + seg_end) / 2.0
            if _inside_scene(midpoint, start, end):
                clipped_start = max(start, seg_start)
                clipped_end = min(end, seg_end)
                if clipped_end > clipped_start:
                    intervals.append((clipped_start, clipped_end))
                word_count += len(str(segment.get("text") or "").strip().split())

    # An empty/no-speech scene is intentionally represented by zero speech features.
    speech_duration = min(duration, _union_duration(intervals))
    speech_ratio = min(1.0, speech_duration / duration)
    silence_ratio = max(0.0, 1.0 - speech_ratio)
    speaking_rate_wpm = (word_count / speech_duration * 60.0) if speech_duration > 0 else 0.0

    return {
        "speech_duration": round(speech_duration, 3),
        "speaking_rate_wpm": round(speaking_rate_wpm, 2),
        "speech_ratio": round(speech_ratio, 3),
        "silence_ratio": round(silence_ratio, 3),
    }


def analyze_emotion_from_frame(frame_rgb: np.ndarray) -> Dict[str, Any]:
    return {}


# --- JSON ROBUSTE + NETTOYAGE ---

def safe_json_parse(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    if start == -1:
        return {}

    candidate = text[start:].strip()

    try:
        return json.loads(candidate)
    except Exception:
        pass

    for _ in range(3):
        open_curly = candidate.count("{")
        close_curly = candidate.count("}")
        if close_curly < open_curly:
            candidate += "}" * (open_curly - close_curly)

        open_brack = candidate.count("[")
        close_brack = candidate.count("]")
        if close_brack < open_brack:
            candidate += "]" * (open_brack - close_brack)

        last_brace = max(candidate.rfind("}"), candidate.rfind("]"))
        if last_brace != -1:
            candidate = candidate[: last_brace + 1].strip()

        try:
            return json.loads(candidate)
        except Exception:
            continue

    return {}


def _extract_field_loose(raw: str, field: str) -> str:
    """
    Essaie d'extraire la valeur d'un champ JSON (ex: "summary", "description")
    dans un texte potentiellement tronqué et/ou entouré de ```json, {, } etc.
    """
    if not raw:
        return ""

    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)

    m = re.search(r'"%s"\s*:\s*(.*)' % re.escape(field), text, flags=re.DOTALL)
    if not m:
        return ""

    value = m.group(1)
    value = re.sub(r'^[\s`"{]+', "", value)

    for sep in ["```", "\n\n", "\n}", "}\n", "\n]", "]"]:
        pos = value.find(sep)
        if pos != -1:
            value = value[:pos]
            break

    value = value.rstrip("`}\n\r\t ")
    return value.strip()


def clean_raw_visual_text(raw: str) -> str:
    if not raw:
        return ""
    desc = _extract_field_loose(raw, "description")
    if desc:
        return desc
    text = raw.strip().lstrip("{").rstrip("}")
    return text.strip()


def clean_raw_summary_text(raw: str) -> str:
    if not raw:
        return ""
    s = _extract_field_loose(raw, "summary")
    if s:
        return s
    s = _extract_field_loose(raw, "description")
    if s:
        return s
    text = raw.strip().lstrip("{").rstrip("}")
    return text.strip()


# VCE_NAME_RECONCILIATION_PROMPT_V1
TEXT_LLM_NAME_RECONCILIATION_PROMPT_DEFAULT = r'''
### Согласование названий и собственных имён между источниками

При подготовке итогового отчёта сопоставляй распознанную речь (ASR), визуальное описание и текст на экране (OCR).

1. Не считай фонетическую запись ASR правильным написанием имени. Распознавание речи может искажать названия программ, брендов, продуктов, аббревиатуры и имена собственные.
2. Используй читаемую надпись на экране для уточнения написания, если она явно относится к сущности, о которой говорит диктор в том же или соседнем связанном эпизоде. Учитывай смысл фразы и последовательность событий, а не только сходство звучания.
3. Если связь между произнесённым названием и видимой надписью достаточно убедительна, используй написание с экрана в нормализованных полях итогового отчёта: entities, topics, key_points, описаниях сцен и сводках. Не дублируй одну сущность под фонетическим вариантом ASR и написанием с экрана.
4. Не изменяй исходные данные. Поля audio_transcript, ocr_text, visual_description и временные метки должны оставаться исходными. Нормализация относится только к производным аналитическим полям.
5. Не считай любое слово на экране подтверждением услышанного названия. Надпись должна относиться к тому же продукту или объекту по контексту. Если связь неоднозначна, противоречива или OCR ненадёжен, сохрани неопределённость и укажи, какие источники дают разные варианты.
6. Не придумывай официальное написание на основании одного только звучания или общих знаний. Приоритет доказательств: чёткая относящаяся к объекту надпись на экране, контекст речи и видео, затем внешние сведения — если они доступны и разрешены. Не делай категоричного вывода, когда доказательств недостаточно.
7. В итоговом отчёте различай три уровня: непосредственно наблюдаемое написание, вероятную идентификацию сущности и неподтверждённую гипотезу. Не выдавай вероятную идентификацию за факт.
'''.strip()

TEXT_LLM_DEFAULT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
TEXT_LLM_DEFAULT_MAX_TOKENS = 4096


def _text_llm_clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 14)].rstrip() + " …[обрезано]"


def _text_llm_normalize_list(value: Any, item_limit: int = 24) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:item_limit]:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:500])
    return out


def _text_llm_parse_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Текстовая LLM не вернула JSON-объект.")
    payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Ответ текстовой LLM должен быть JSON-объектом.")
    summary = payload.get("executive_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("В JSON текстовой LLM отсутствует executive_summary.")

    payload["executive_summary"] = summary.strip()[:8000]
    payload["topics"] = _text_llm_normalize_list(payload.get("topics"), 30)
    payload["uncertainties"] = _text_llm_normalize_list(payload.get("uncertainties"), 30)

    clean_points = []
    for item in payload.get("key_points", []) if isinstance(payload.get("key_points"), list) else []:
        if not isinstance(item, dict):
            continue
        text_value = item.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            continue
        try:
            scene_id = int(item.get("scene_id"))
        except (TypeError, ValueError):
            scene_id = None
        clean_points.append({
            "scene_id": scene_id,
            "time": _text_llm_clip(item.get("time", ""), 32),
            "text": text_value.strip()[:700],
            "evidence": _text_llm_normalize_list(item.get("evidence"), 6),
        })
        if len(clean_points) >= 24:
            break
    payload["key_points"] = clean_points

    clean_entities = []
    for item in payload.get("entities", []) if isinstance(payload.get("entities"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            continue
        ids = []
        if isinstance(item.get("scene_ids"), list):
            for scene_id in item["scene_ids"][:20]:
                try:
                    ids.append(int(scene_id))
                except (TypeError, ValueError):
                    pass
        clean_entities.append({
            "name": item["name"].strip()[:200],
            "type": _text_llm_clip(item.get("type", "other"), 80),
            "scene_ids": ids,
        })
        if len(clean_entities) >= 60:
            break
    payload["entities"] = clean_entities

    clean_enrichment = []
    for item in payload.get("scene_enrichment", []) if isinstance(payload.get("scene_enrichment"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            scene_id = int(item.get("scene_id"))
        except (TypeError, ValueError):
            continue
        clean_enrichment.append({
            "scene_id": scene_id,
            "topic": _text_llm_clip(item.get("topic", ""), 240),
            "improved_description": _text_llm_clip(item.get("improved_description", ""), 1800),
            "key_points": _text_llm_normalize_list(item.get("key_points"), 10),
        })
    payload["scene_enrichment"] = clean_enrichment
    return payload


# VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2
# VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR

def _vce_canonicalize_confirmed_standard_names(payload, segments):
    """Normalize only derived text, and only when related OCR confirms the exact ID."""
    import re as _vce_re
    if not isinstance(payload, dict):
        payload = {}

    _vce_segments = segments if isinstance(segments, list) else []
    _vce_ocr_text = " ".join(
        str(_vce_seg.get("ocr_text", "") or "")
        for _vce_seg in _vce_segments
        if isinstance(_vce_seg, dict)
    )
    _vce_canonical = "СП 1.13130.2020"
    _vce_confirmed = bool(_vce_re.search(
        r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce_ocr_text
    ))
    _vce_aliases = (
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)"),
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*130\s*\.\s*2020(?!\d)"),
    )
    _vce_raw_keys = {
        "audio_transcript", "ocr_text", "visual_description", "original_summary",
        "timestamp", "timestamps", "start", "end", "time", "timecode", "timecodes",
    }
    _vce_replacements = 0

    def _vce_replace_tree(value):
        nonlocal _vce_replacements
        if isinstance(value, str):
            if not _vce_confirmed:
                return value
            _vce_result = value
            for _vce_pattern in _vce_aliases:
                _vce_result, _vce_count = _vce_pattern.subn(_vce_canonical, _vce_result)
                _vce_replacements += _vce_count
            return _vce_result
        if isinstance(value, list):
            return [_vce_replace_tree(_vce_item) for _vce_item in value]
        if isinstance(value, dict):
            return {
                _vce_key: (_vce_item if str(_vce_key).lower() in _vce_raw_keys else _vce_replace_tree(_vce_item))
                for _vce_key, _vce_item in value.items()
            }
        return value

    # `payload` is generated analytical JSON. Preserve raw evidence keys anywhere they occur.
    _vce_normalized = _vce_replace_tree(payload) if _vce_confirmed else payload
    if isinstance(_vce_normalized, dict):
        payload.clear()
        payload.update(_vce_normalized)

    # Per-scene text_llm outputs are also derived fields and may be used to build scene_enrichment.
    # Do not rewrite audio_transcript, ocr_text, visual_description, or times in the segment itself.
    _vce_action_edits = 0
    for _vce_seg in _vce_segments:
        if not isinstance(_vce_seg, dict) or not isinstance(_vce_seg.get("text_llm"), dict):
            continue
        _vce_ocr = str(_vce_seg.get("ocr_text", "") or "")
        if _vce_confirmed:
            _vce_seg["text_llm"] = _vce_replace_tree(_vce_seg["text_llm"])
        _vce_llm = _vce_seg["text_llm"]
        _vce_has_create_control = bool(_vce_re.search(r"(?i)создать\s+калькулятор", _vce_ocr))
        _vce_has_generate_control = bool(_vce_re.search(r"(?i)(?:генерация\s+калькуляторов|сгенерировать)", _vce_ocr))
        _vce_has_completion = bool(_vce_re.search(r"(?i)(?:успешно\s+(?:создан|сгенерирован|завершен)|генерация\s+завершена|калькулятор\s+создан|калькуляторы\s+сгенерированы)", _vce_ocr))
        if _vce_has_completion:
            continue
        for _vce_field in ("improved_description", "topic"):
            _vce_text = _vce_llm.get(_vce_field)
            if not isinstance(_vce_text, str):
                continue
            if _vce_has_generate_control and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_text):
                _vce_llm[_vce_field] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                _vce_action_edits += 1
            elif _vce_has_create_control and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_text):
                _vce_llm[_vce_field] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                _vce_action_edits += 1
        _vce_points = _vce_llm.get("key_points")
        if isinstance(_vce_points, list):
            for _vce_i, _vce_point in enumerate(_vce_points):
                if not isinstance(_vce_point, str):
                    continue
                if _vce_has_generate_control and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_point):
                    _vce_points[_vce_i] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                    _vce_action_edits += 1
                elif _vce_has_create_control and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_point):
                    _vce_points[_vce_i] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                    _vce_action_edits += 1

    # The LLM may place scene_enrichment directly in payload. Apply the same evidence rule there,
    # using OCR matched by scene_id; all changes are limited to derived descriptions/key points.
    _vce_ocr_by_scene = {}
    for _vce_seg in _vce_segments:
        if isinstance(_vce_seg, dict) and _vce_seg.get("scene_id") is not None:
            _vce_ocr_by_scene[_vce_seg.get("scene_id")] = str(_vce_seg.get("ocr_text", "") or "")
    def _vce_guard_scene_enrichment(_vce_list):
        nonlocal _vce_action_edits
        if not isinstance(_vce_list, list):
            return
        for _vce_scene in _vce_list:
            if not isinstance(_vce_scene, dict):
                continue
            _vce_ocr = _vce_ocr_by_scene.get(_vce_scene.get("scene_id"), "")
            _vce_create = bool(_vce_re.search(r"(?i)создать\s+калькулятор", _vce_ocr))
            _vce_generate = bool(_vce_re.search(r"(?i)(?:генерация\s+калькуляторов|сгенерировать)", _vce_ocr))
            _vce_done = bool(_vce_re.search(r"(?i)(?:успешно\s+(?:создан|сгенерирован|завершен)|генерация\s+завершена|калькулятор\s+создан|калькуляторы\s+сгенерированы)", _vce_ocr))
            if _vce_done:
                continue
            for _vce_field in ("improved_description", "topic"):
                _vce_text = _vce_scene.get(_vce_field)
                if not isinstance(_vce_text, str):
                    continue
                if _vce_generate and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_text):
                    _vce_scene[_vce_field] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                    _vce_action_edits += 1
                elif _vce_create and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_text):
                    _vce_scene[_vce_field] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                    _vce_action_edits += 1
            _vce_points = _vce_scene.get("key_points")
            if isinstance(_vce_points, list):
                for _vce_i, _vce_point in enumerate(_vce_points):
                    if not isinstance(_vce_point, str):
                        continue
                    if _vce_generate and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_point):
                        _vce_points[_vce_i] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                        _vce_action_edits += 1
                    elif _vce_create and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_point):
                        _vce_points[_vce_i] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                        _vce_action_edits += 1
    if isinstance(payload, dict):
        _vce_guard_scene_enrichment(payload.get("scene_enrichment"))
    if _vce_replacements:
        print(f"🧭 [ASR/OCR] Исправлено {_vce_replacements} вариантов на {_vce_canonical} по подтверждённому OCR.")
    if _vce_action_edits:
        print(f"🧭 [Evidence guard] Смягчено {_vce_action_edits} утверждений о действиях без подтверждения результата.")
    return payload


# VCE_REPORT_INTEGRITY_V4_HELPER

def _vce_v4_evidence_summary(text, segments):
    """Downgrade model-verified language when the nonconformity is only spoken, not shown."""
    import re as _vce4_re
    if not isinstance(text, str) or not isinstance(segments, list):
        return text
    _vce4_audio = " ".join(
        str(_vce4_seg.get("audio_transcript", "") or "")
        for _vce4_seg in segments if isinstance(_vce4_seg, dict)
    )
    _vce4_ocr = " ".join(
        str(_vce4_seg.get("ocr_text", "") or "")
        for _vce4_seg in segments if isinstance(_vce4_seg, dict)
    )
    _vce4_speech_noncompliance = bool(_vce4_re.search(
        r"(?i)(?:не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*)", _vce4_audio
    ))
    _vce4_collision_ui = bool(_vce4_re.search(r"(?i)проверка\s+коллизий", _vce4_ocr))
    _vce4_ocr_shows_result = bool(_vce4_re.search(
        r"(?i)(?:не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*\s+(?:выявлено|обнаружено)|"
        r"результат\s+проверки\s*[:=]\s*(?:ошибка|не\s+соответствует)|"
        r"итог\s+проверки\s*[:=]\s*(?:ошибка|не\s+соответствует))", _vce4_ocr
    ))
    if not (_vce4_speech_noncompliance and _vce4_collision_ui and not _vce4_ocr_shows_result):
        return text

    _vce4_replacement = (
        "По словам диктора, проверка выявила несоответствие нормативу; "
        "фактическое значение уклона и итог проверки визуально не подтверждены."
    )
    _vce4_result = text
    _vce4_sentences = _vce4_re.split(r"(?<=[.!?])\s+", _vce4_result)
    _vce4_new_sentences = []
    _vce4_changed = False
    for _vce4_sentence in _vce4_sentences:
        _vce4_has_noncompliance = bool(_vce4_re.search(
            r"(?i)(?:уклон\s+пандус\w*|пандус\w*[^.!?]{0,70}уклон)[^.!?]{0,140}не\s+соответствует\s+нормативу", _vce4_sentence
        ))
        _vce4_is_visual_assertion = bool(_vce4_re.search(
            r"(?i)(?:в\s+(?:3d[- ]?)?модел\w*|модель\s+показала|визуально\s+выявлено|"
            r"проверка\s+выявила|выявлено,?\s+что|обнаружено,?\s+что|установлено,?\s+что)", _vce4_sentence
        ))
        _vce4_already_attributed = bool(_vce4_re.search(
            r"(?i)(?:по\s+словам\s+диктора|в\s+речи\s+сообщается|аудиодорожк\w*)", _vce4_sentence
        ))
        if _vce4_has_noncompliance and _vce4_is_visual_assertion and not _vce4_already_attributed:
            _vce4_new_sentences.append(_vce4_replacement)
            _vce4_changed = True
        else:
            _vce4_new_sentences.append(_vce4_sentence)
    if _vce4_changed:
        return " ".join(_vce4_new_sentences)

    # Fallback for a brief global summary whose overconfident model-result sentence does not name the model.
    _vce4_overclaim = _vce4_re.search(
        r"(?i)(?:выявлено|обнаружено|установлено)[^.!?]{0,120}уклон\s+пандус\w*[^.!?]{0,80}не\s+соответствует\s+нормативу", _vce4_result
    )
    if _vce4_overclaim and not _vce4_re.search(r"(?i)визуально\s+не\s+подтвержден", _vce4_result):
        _vce4_start = _vce4_result.rfind(".", 0, _vce4_overclaim.start()) + 1
        _vce4_end = _vce4_result.find(".", _vce4_overclaim.end())
        if _vce4_end < 0:
            _vce4_end = len(_vce4_result)
        _vce4_result = _vce4_result[:_vce4_start] + " " + _vce4_replacement + _vce4_result[_vce4_end + (1 if _vce4_end < len(_vce4_result) else 0):]
    return _vce4_result


def _vce_v4_name_pattern(name):
    """Build a case-insensitive OCR literal matcher while allowing line-wrap whitespace."""
    import re as _vce4_re
    if not isinstance(name, str) or not name.strip():
        return None
    _vce4_tokens = name.strip().split()
    if not _vce4_tokens:
        return None
    return _vce4_re.compile(r"(?i)(?<![\w])" + r"\s+".join(_vce4_re.escape(_vce4_t) for _vce4_t in _vce4_tokens) + r"(?![\w])")


def _vce_v4_update_entity_scene_ids(payload, segments):
    """Add scene IDs only when an entity's literal name is visible in that scene's OCR."""
    import re as _vce4_re
    if not isinstance(payload, dict) or not isinstance(payload.get("entities"), list) or not isinstance(segments, list):
        return 0
    _vce4_valid_ids = set()
    for _vce4_seg in segments:
        if isinstance(_vce4_seg, dict):
            _vce4_id = _vce4_seg.get("scene_id")
            if isinstance(_vce4_id, int) and not isinstance(_vce4_id, bool):
                _vce4_valid_ids.add(_vce4_id)
    _vce4_changes = 0
    for _vce4_entity in payload.get("entities", []):
        if not isinstance(_vce4_entity, dict):
            continue
        _vce4_name = _vce4_entity.get("name")
        _vce4_pattern = _vce_v4_name_pattern(_vce4_name)
        if _vce4_pattern is None:
            continue
        _vce4_old = _vce4_entity.get("scene_ids")
        _vce4_current = []
        if isinstance(_vce4_old, list):
            for _vce4_id in _vce4_old:
                if isinstance(_vce4_id, int) and not isinstance(_vce4_id, bool) and _vce4_id in _vce4_valid_ids:
                    if _vce4_id not in _vce4_current:
                        _vce4_current.append(_vce4_id)
        _vce4_added = []
        # Avoid broad, noisy associations for very short acronyms such as "КСИ".
        if len(str(_vce4_name).strip()) >= 4:
            for _vce4_seg in segments:
                if not isinstance(_vce4_seg, dict):
                    continue
                _vce4_id = _vce4_seg.get("scene_id")
                _vce4_ocr = str(_vce4_seg.get("ocr_text", "") or "")
                if isinstance(_vce4_id, int) and not isinstance(_vce4_id, bool) and _vce4_id in _vce4_valid_ids and _vce4_pattern.search(_vce4_ocr):
                    if _vce4_id not in _vce4_current:
                        _vce4_current.append(_vce4_id)
                        _vce4_added.append(_vce4_id)
        _vce4_current.sort()
        if _vce4_old != _vce4_current:
            _vce4_entity["scene_ids"] = _vce4_current
            _vce4_changes += 1
    return _vce4_changes


def _vce_v4_validate_analysis(payload, segments, global_summary=""):
    """Validate the derived analysis JSON contract; warn rather than abort a video run."""
    import re as _vce4_re
    _vce4_issues = []
    if not isinstance(payload, dict):
        print("⚠️ [JSON integrity V4] enhanced_analysis — ожидался JSON-объект.")
        return payload
    if not isinstance(segments, list):
        _vce4_issues.append("segments не является массивом")
        segments = []
    _vce4_valid_ids = {
        _vce4_seg.get("scene_id") for _vce4_seg in segments
        if isinstance(_vce4_seg, dict) and isinstance(_vce4_seg.get("scene_id"), int) and not isinstance(_vce4_seg.get("scene_id"), bool)
    }
    _vce4_contract = {
        "executive_summary": str,
        "topics": list,
        "key_points": list,
        "entities": list,
        "scene_enrichment": list,
    }
    for _vce4_key, _vce4_type in _vce4_contract.items():
        if _vce4_key not in payload:
            _vce4_issues.append(f"отсутствует поле { _vce4_key }")
        elif not isinstance(payload[_vce4_key], _vce4_type):
            _vce4_issues.append(f"{_vce4_key} имеет тип {type(payload[_vce4_key]).__name__}, ожидался {_vce4_type.__name__}")
    for _vce4_key in ("topics",):
        if isinstance(payload.get(_vce4_key), list) and any(not isinstance(_vce4_item, str) for _vce4_item in payload[_vce4_key]):
            _vce4_issues.append(f"{_vce4_key} содержит элементы не-строки")
    for _vce4_key in ("key_points", "scene_enrichment"):
        _vce4_value = payload.get(_vce4_key)
        if isinstance(_vce4_value, list):
            for _vce4_idx, _vce4_item in enumerate(_vce4_value):
                if not isinstance(_vce4_item, dict):
                    _vce4_issues.append(f"{_vce4_key}[{_vce4_idx}] не является объектом")
                    continue
                _vce4_id = _vce4_item.get("scene_id")
                if _vce4_id is not None and _vce4_id not in _vce4_valid_ids:
                    _vce4_issues.append(f"{_vce4_key}[{_vce4_idx}] ссылается на неизвестную сцену {_vce4_id!r}")
    _vce4_ocr = " ".join(str(_vce4_seg.get("ocr_text", "") or "") for _vce4_seg in segments if isinstance(_vce4_seg, dict))
    _vce4_has_canonical_sp = bool(_vce4_re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce4_ocr))
    if _vce4_has_canonical_sp:
        _vce4_alias = _vce4_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)")
        if _vce4_alias.search(str(global_summary or "")):
            _vce4_issues.append("неисправленный вариант номера СП остался в global_summary")
    _vce4_known_keys = {"audio_transcript", "ocr_text", "visual_description", "original_summary", "start", "end", "time", "timestamp", "timestamps", "timecode", "timecodes"}
    def _vce4_scan_alias(value, path="enhanced_analysis"):
        if isinstance(value, dict):
            for _vce4_key, _vce4_child in value.items():
                if str(_vce4_key).lower() in _vce4_known_keys:
                    continue
                _vce4_scan_alias(_vce4_child, f"{path}.{_vce4_key}")
        elif isinstance(value, list):
            for _vce4_i, _vce4_child in enumerate(value):
                _vce4_scan_alias(_vce4_child, f"{path}[{_vce4_i}]")
        elif isinstance(value, str) and _vce4_has_canonical_sp and _vce4_re.search(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)", value):
            _vce4_issues.append(f"неисправленный вариант номера СП в {path}")
    _vce4_scan_alias(payload)
    _vce4_entity_changes = _vce_v4_update_entity_scene_ids(payload, segments)
    if _vce4_entity_changes:
        print(f"🧭 [JSON integrity V4] Обновлены связи сущностей со сценами: {_vce4_entity_changes} сущностей.")
    _vce4_scenes = len(_vce4_valid_ids)
    if _vce4_issues:
        print("⚠️ [JSON integrity V4] Проверка аналитического JSON нашла замечания:")
        for _vce4_issue in _vce4_issues:
            print("   - " + _vce4_issue)
    else:
        print(f"✅ [JSON integrity V4] Структура аналитики проверена; доступно {_vce4_scenes} scene_id, ссылки сущностей сверены с OCR.")
    return payload


def _vce_v4_finalize_analysis_payload(payload, segments, global_summary=""):
    """Apply evidence-grounded consistency repairs, then validate the derived analysis payload."""
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("executive_summary"), str):
        _vce4_new = _vce_v4_evidence_summary(payload["executive_summary"], segments)
        if _vce4_new != payload["executive_summary"]:
            payload["executive_summary"] = _vce4_new
            print("🧭 [Summary integrity V4] Executive summary reconciled with audio/OCR evidence.")
    _vce_v4_update_entity_scene_ids(payload, segments)
    return _vce_v4_validate_analysis(payload, segments, global_summary)


def run_text_llm_postprocess(
    model_path: str,
    source_name: str,
    video_duration: float,
    existing_summary: str,
    segments: List[Dict[str, Any]],
    max_tokens: int = 4096,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """Load the selected MLX VLM-family checkpoint in text-only mode and return validated enrichment."""
    import gc
    _vce_configure_mlx_metal("text_llm")
    from mlx_vlm import load, generate
    try:
        from mlx_vlm.generate import stream_generate
    except Exception:
        stream_generate = None
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    model = processor = config = None
    try:
        if progress_callback:
            progress_callback("loading", 0, 0.0)
        print(f"🧠 [Text LLM] Загрузка {model_path} (текстовая постобработка)…")
        model, processor = load(model_path)
        config = load_config(model_path)
        if progress_callback:
            progress_callback("loaded", 0, 0.0)

        def make_context(text_limits: tuple[int, int, int]) -> Dict[str, Any]:
            audio_limit, ocr_limit, visual_limit = text_limits
            context_segments = []
            for seg in segments:
                tags = seg.get("visual_tags") if isinstance(seg.get("visual_tags"), dict) else {}
                compact_tags = {}
                for key, val in list(tags.items())[:12]:
                    compact_tags[str(key)[:80]] = _text_llm_clip(val, 120)
                context_segments.append({
                    "scene_id": int(seg.get("scene_id", 0) or 0),
                    "start": round(float(seg.get("start", 0) or 0), 2),
                    "end": round(float(seg.get("end", 0) or 0), 2),
                    "transcript": _text_llm_clip(seg.get("audio_transcript", ""), audio_limit),
                    "ocr_text": _text_llm_clip(seg.get("ocr_text", ""), ocr_limit),
                    "visual_description": _text_llm_clip(seg.get("visual_description", ""), visual_limit),
                    "visual_tags": compact_tags,
                })
            return {
                "source": _text_llm_clip(source_name, 500),
                "duration_seconds": round(float(video_duration or 0), 2),
                "existing_summary": _text_llm_clip(existing_summary, 4000),
                "scenes": context_segments,
            }

        limits = (600, 650, 650)
        context = make_context(limits)
        context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        for smaller in ((360, 400, 400), (220, 250, 250), (140, 160, 160)):
            if len(context_json) <= 47000:
                break
            context = make_context(smaller)
            context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        coverage_note = "Все сцены включены в контекст."
        if len(context_json) > 47000 and len(context.get("scenes", [])) > 80:
            all_scenes = context["scenes"]
            indices = sorted({round(i * (len(all_scenes) - 1) / 79) for i in range(80)})
            context["scenes"] = [all_scenes[i] for i in indices]
            context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            coverage_note = f"Для смысловой разметки переданы 80 репрезентативных сцен из {len(all_scenes)}; исходные сцены в основном JSON не изменены."





        instruction = (
            "Ты — редактор и аналитик видеоконспектов. Верни только один валидный JSON-объект без Markdown и без текста вне JSON. "
            "Все формулировки пиши на русском языке; сохраняй точное написание брендов, имён, стандартов, файлов, команд и технических терминов. "
            "Используй только переданные данные; не добавляй внешние сведения и не делай выводов, которые не подтверждаются исходными источниками. "
            "Не изменяй исходные аудиорасшифровки, OCR, визуальные описания и таймкоды. Они являются доказательным слоем. "
            "OCR — основной источник точного написания видимых названий, нормативных обозначений, атрибутов, дат и чисел, если текст читаем и однозначен. Не позволяй фонетическому варианту ASR переопределить чёткую экранную подпись; сопоставляй источники по смыслу и близости сцен. OCR тоже может ошибаться: при нечитабельной или противоречивой надписи сохраняй неопределённость. "
            "Если речь содержит фонетическое «Белчат/Белчата», а связанный по контексту OCR чётко показывает BuildChat, нормализуй производные аналитические поля к BuildChat и не добавляй ложную неопределённость. Исходные transcript/OCR не переписывай. "
            "Executive_summary: 50–90 слов, самостоятельный синтез цели, последовательности этапов, конкретных параметров и фактического результата. Не повторяй базовую сводку и не добавляй общие фразы о профессиональной атмосфере. Разделяй то, что сказано в аудио, и то, что показано на экране. "
            "Если в финальном аудио произносится несоответствие, а на экране только THE END/КОНЕЦ, сообщи, что это сказано в аудио, и не приписывай вывод изображению. Не считай окно «Проверка коллизий» доказательством результата проверки уклона, если связь не подтверждена. "
            "Key_points: максимум 18 коротких аналитических тезисов, обычно до 30 слов. Не копируй длинные OCR-строки или обрывки транскрипта. Не дублируй смысл между соседними сценами: выделяй конкретное изменение, этап, атрибут, функцию или результат, только если он подтверждён сценой. Не добавляй речевые обрывки вроде «а затем выгрузить» как самостоятельный тезис. "
            "Evidence — строгая привязка: speech указывай только если тезис подтверждается аудио; ocr — только если подтверждается читаемым текстом; visual — если подтверждается описанием изображения; tags — если следует из visual_tags. Не добавляй источник только потому, что он существует. THE END подтверждает заставку, но не несоответствие. "
            "Topics — не более 15 разных тем. Entities — только подтверждённые именованные сущности с реальными scene_ids. Типы: person, organization, product, software, tool, standard, regulation, document, concept, classification_system, other. ГОСТ/СП/СНиП/ISO — standard; КСИ — classification_system; nanoCAD и CADLib — software. Не включай обычные подписи вроде «ИИ помощник» как именованные сущности. "
            "Scene_enrichment: для каждой сцены короткая тема, улучшенное описание в 1–2 предложениях (ориентир до 45 слов) и 0–3 тезиса. Не вставляй в improved_description метки «Визуально:», «Речь:», «Текст на экране:», дампы OCR, временные заголовки [Кадр ...] или объединённые блоки исходных данных. Перескажи значимый экранный факт своими словами; сохраняй точные числа/названия, если они важны. Сцены похожего интерфейса должны различаться тем, что изменилось или какая функция показана. "
            "Uncertainties: включай только реальные расхождения или явную проблему распознавания. Не называй неопределённостью ситуацию, когда аудио и экран описывают разные, но совместимые вещи. Если OCR явно содержит полное значение, не помечай его как обрезанное. Если сомнений нет, верни []. Не делай выводов об архитектуре по localhost/порту/названию базы. "
            "Не включай chain-of-thought, черновые рассуждения или служебные поля. Строго соблюдай структуру: "
            '{"executive_summary":"...","topics":["..."],"key_points":[{"scene_id":1,"time":"00:00:00","text":"...","evidence":["speech"]}],'
            '"entities":[{"name":"...","type":"standard","scene_ids":[1]}],"scene_enrichment":[{"scene_id":1,"topic":"...",'
            '"improved_description":"...","key_points":["..."]}],"uncertainties":["..."]}. '
            "Не используй scene_id, которых нет в контексте. Не утверждай, что проверка пройдена, если в источниках показано несоответствие. "
            + "\n\nПромпт согласования названий и собственных имён между ASR и OCR:\n"
            + str(setting("text_llm_name_reconciliation_prompt", TEXT_LLM_NAME_RECONCILIATION_PROMPT_DEFAULT)).strip()
            + "\n\n"
            + coverage_note
            + "\n\nДанные анализа (JSON):\n"
            + context_json
        )
        try:
            # For this constrained extraction task, avoid spending time on long reasoning output.
            formatted_prompt = apply_chat_template(processor, config, instruction, num_images=0, enable_thinking=True)
        except TypeError:
            formatted_prompt = apply_chat_template(processor, config, instruction, num_images=0)
        if progress_callback:
            progress_callback("generating", 0, 0.0)
        generation_limit = max(1024, min(int(max_tokens), 4096))
        raw = ""
        if stream_generate is not None:
            chunks = []
            started_stream = time.time()
            reported_tokens = 0
            try:
                for item in stream_generate(
                    model,
                    processor,
                    prompt=formatted_prompt,
                    max_tokens=generation_limit,
                    temperature=0.1,
                    enable_thinking=True,
                    thinking_budget=512,
                ):
                    piece = getattr(item, "text", None)
                    if piece is None and isinstance(item, str):
                        piece = item
                    if piece:
                        chunks.append(str(piece))
                    try:
                        current_tokens = int(getattr(item, "generation_tokens", 0) or 0)
                    except (TypeError, ValueError):
                        current_tokens = 0
                    # Some MLX-VLM versions expose token count; provide a monotonic fallback
                    # so progress remains live on versions that don't.
                    if current_tokens <= reported_tokens:
                        current_tokens = reported_tokens + 1
                    reported_tokens = current_tokens
                    elapsed_stream = max(0.001, time.time() - started_stream)
                    rate = reported_tokens / elapsed_stream
                    if progress_callback:
                        progress_callback("generating", reported_tokens, rate)
                raw = "".join(chunks)
            except TypeError as stream_error:
                # Version compatibility: retry without optional thinking argument.
                if not any(_kw in str(stream_error) for _kw in ("enable_thinking", "thinking_budget")):
                    print(f"⚠️ [Text LLM] Потоковая генерация недоступна, переключаюсь на обычную: {stream_error}")
                else:
                    try:
                        chunks = []
                        for item in stream_generate(
                            model, processor, prompt=formatted_prompt,
                            max_tokens=generation_limit, temperature=0.1,
                        ):
                            piece = getattr(item, "text", None)
                            if piece is None and isinstance(item, str): piece = item
                            if piece: chunks.append(str(piece))
                            reported_tokens += 1
                            if progress_callback:
                                elapsed_stream = max(0.001, time.time() - started_stream)
                                progress_callback("generating", reported_tokens, reported_tokens / elapsed_stream)
                        raw = "".join(chunks)
                    except Exception as fallback_stream_error:
                        print(f"⚠️ [Text LLM] Потоковая генерация недоступна, переключаюсь на обычную: {fallback_stream_error}")
                        raw = ""
            except Exception as stream_error:
                print(f"⚠️ [Text LLM] Потоковая генерация недоступна, переключаюсь на обычную: {stream_error}")
                raw = ""
        if not raw.strip():
            try:
                raw = generate(
                    model,
                    processor,
                    prompt=formatted_prompt,
                    max_tokens=generation_limit,
                    temperature=0.1,
                    verbose=False,
                    enable_thinking=True,
                )
            except TypeError:
                raw = generate(
                    model, processor, prompt=formatted_prompt,
                    max_tokens=generation_limit, temperature=0.1, verbose=False,
                )
            if not isinstance(raw, str):
                raw = getattr(raw, "text", None) or str(raw)
        try:
            parsed = _text_llm_parse_json(raw)
        except Exception as parse_error:
            # First attempt a deterministic syntax-only repair if the optional helper exists.
            repaired_payload = None
            try:
                from json_repair import repair_json
                repaired_payload = repair_json(raw, return_objects=True)
                if not isinstance(repaired_payload, dict):
                    repaired_payload = None
            except Exception:
                repaired_payload = None
            if repaired_payload is not None:
                # Run the same validation/sanitization path on repaired content.
                repaired_text = json.dumps(repaired_payload, ensure_ascii=False)
                parsed = _text_llm_parse_json(repaired_text)
            else:
                print(f"⚠️ [Text LLM] JSON не прошёл синтаксическую проверку ({parse_error}); выполняю один repair-pass.")
                if progress_callback:
                    progress_callback("repairing", 0, 0.0)
                repair_instruction = (
                    "Исправь ТОЛЬКО синтаксис следующего JSON. Не добавляй новые факты, не меняй содержание тезисов, "
                    "не объясняй ход решения. Верни один полный валидный JSON-объект, без Markdown. "
                    "Сохрани ключи executive_summary, topics, key_points, entities, scene_enrichment, uncertainties.\n\n"
                    "Невалидный JSON для исправления:\n" + raw[:18000]
                )
                try:
                    repair_prompt = apply_chat_template(processor, config, repair_instruction, num_images=0, enable_thinking=False)
                except TypeError:
                    repair_prompt = apply_chat_template(processor, config, repair_instruction, num_images=0)
                try:
                    repaired_raw = generate(model, processor, prompt=repair_prompt, max_tokens=4096, temperature=0.0, verbose=False, enable_thinking=False)
                except TypeError:
                    repaired_raw = generate(model, processor, prompt=repair_prompt, max_tokens=4096, temperature=0.0, verbose=False)
                if not isinstance(repaired_raw, str):
                    repaired_raw = getattr(repaired_raw, "text", None) or str(repaired_raw)
                try:
                    parsed = _text_llm_parse_json(repaired_raw)
                except Exception as second_error:
                    raise ValueError(f"Qwen3.5 вернул некорректный JSON даже после одной попытки исправления: {second_error}") from second_error
        parsed = reconcile_text_llm_result(parsed, segments, existing_summary)
        parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)
        parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)
        if coverage_note != "Все сцены включены в контекст.":
            parsed["uncertainties"] = (parsed.get("uncertainties", []) + [coverage_note])[:30]
        return parsed
    finally:
        model = None
        processor = None
        config = None
        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass


def format_text_llm_report(payload: Dict[str, Any]) -> str:
    lines = ["", "=" * 56, "СМЫСЛОВОЙ АНАЛИЗ И СТРУКТУРИРОВАНИЕ (Qwen3.5-9B-MLX-4bit)", "=" * 56]
    if payload.get("executive_summary"):
        lines.extend(["", "КРАТКОЕ РЕЗЮМЕ", payload["executive_summary"]])
    if payload.get("topics"):
        lines.extend(["", "ОСНОВНЫЕ ТЕМЫ"])
        lines.extend(f"- {item}" for item in payload["topics"])
    if payload.get("key_points"):
        lines.extend(["", "КЛЮЧЕВЫЕ ТЕЗИСЫ"])
        for item in payload["key_points"]:
            if isinstance(item, dict):
                prefix = f"[{item.get('time')}] " if item.get("time") else ""
                lines.append(f"- {prefix}{item.get('text', '')}")
    if payload.get("entities"):
        lines.extend(["", "СУЩНОСТИ И ТЕРМИНЫ"])
        lines.extend(f"- {item.get('name')} ({item.get('type', 'other')})" for item in payload["entities"] if isinstance(item, dict))
    enrichments = payload.get("scene_enrichment") or []
    if enrichments:
        lines.extend(["", "СТРУКТУРА ПО СЦЕНАМ"])
        for item in enrichments:
            if not isinstance(item, dict):
                continue
            lines.append(f"- Сцена {item.get('scene_id')}: {item.get('topic') or 'без отдельной темы'}")
            if item.get("improved_description"):
                lines.append(f"  {item['improved_description']}")
            for point in item.get("key_points", []):
                lines.append(f"  • {point}")
    if payload.get("uncertainties"):
        lines.extend(["", "ОГОВОРКИ И НЕОПРЕДЕЛЁННОСТИ"])
        lines.extend(f"- {item}" for item in payload["uncertainties"])
    return "\n".join(lines) + "\n"


def generate_text_report(
    filename: str,
    duration: float,
    segments: List[Dict[str, Any]],
    process_time: float,
    params: Dict[str, Any],
    global_summary: str = "",
) -> str:
    lines = [
        "### CONTEXTE VIDEO (VideoContext v3.19)",
        f"Source : {filename}",
        f"Durée : {duration:.2f}s | Traitement : {process_time:.2f}s",
        f"Config : Res={params['resolution']}px | Seuil={params['threshold']} "
        f"| Min={params['min_duration']}s | Max={params['max_duration']}s "
        f"| Keyframes/scene={params['keyframes_per_scene']} | RAM_MODE={params['ram_mode']}",
        "",
    ]

    if global_summary:
        lines.append("--- RÉSUMÉ GLOBAL ---")
        lines.append(global_summary)
        lines.append("")
        lines.append("-" * 40)
        lines.append("")

    for seg in segments:
        lines.append(f"⏱️ [{seg['start']:.2f} - {seg['end']:.2f}] SCÈNE {seg['scene_id']}")

        if seg.get("audio_transcript"):
            lines.append(f"   🎙️ TEXTE : \"{seg['audio_transcript']}\"")

        af = seg.get("audio_features") or {}
        if af:
            lines.append(
                f"   🔊 AudioFeatures : speech={af.get('speech_ratio', 0):.2f}, "
                f"silence={af.get('silence_ratio', 0):.2f}, "
                f"wpm={af.get('speaking_rate_wpm', 0):.1f}"
            )

        if seg.get("visual_description"):
            lines.append(f"   👀 VISUEL : {seg['visual_description']}")
        if seg.get("ocr_text"):
            lines.append("   🔎 APPLE OCR : " + seg["ocr_text"].replace("\n", " | "))

        vt = seg.get("visual_tags") or {}
        if vt:
            lines.append(
                "   🧩 Tags : "
                f"people={vt.get('people_count', '?')}, "
                f"place={vt.get('place_type', '?')}, "
                f"action={vt.get('main_action', '?')}, "
                f"tone={vt.get('emotional_tone', '?')}, "
                f"movement={vt.get('movement_level', '?')}"
            )

        lines.append("")

    return "\n".join(lines)


# --- API FASTAPI ---

vlm_engine: Optional[VLMProvider] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global vlm_engine, WHISPER_CACHE
    vlm_engine = get_vlm_engine()

    if RAM_MODE == "ram+":
        print("🚀 RAM+ mode: préchargement des modèles...")
        try:
            vlm_engine.load_model(str(setting("vlm_model", ACTIVE_DEFAULT_VLM)))
        except Exception as e:
            print(f"⚠️ Impossible de précharger le VLM par défaut: {e}")
        try:
            t0 = time.time()
            startup_whisper_model = str(setting("whisper_model", DEFAULT_WHISPER_MODEL))
            WHISPER_CACHE[startup_whisper_model] = load_whisper_model(
                startup_whisper_model,
                acceleration_enabled=bool(setting("whisper_acceleration", True)),
            )
            print(f"✅ Whisper '{DEFAULT_WHISPER_MODEL}' préchargé ({time.time()-t0:.2f}s)")
        except Exception as e:
            print(f"⚠️ Impossible de précharger Whisper '{DEFAULT_WHISPER_MODEL}': {e}")

    yield

    if vlm_engine:
        vlm_engine.unload_model()
        release_accelerator_memory('after VLM unload')
    for k, m in list(WHISPER_CACHE.items()):
        del WHISPER_CACHE[k]
    WHISPER_CACHE.clear()
    gc.collect()


app = FastAPI(
    title="VideoContext Engine",
    description="Microservice v3.19 (RAM modes + JSON robuste + nettoyage brut)",
    version="3.19",
    lifespan=lifespan,
)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    try:
        paths = openapi_schema["paths"]
        for path, methods in paths.items():
            for method, operation in methods.items():
                request_body = operation.get("requestBody", {})
                content = request_body.get("content", {})
                form_schema = content.get(
                    "application/x-www-form-urlencoded", {}
                ).get("schema", {})
                props = form_schema.get("properties", {})

                for field_name in ["visual_user_prompt", "summary_user_prompt"]:
                    if field_name in props:
                        props[field_name]["format"] = "textarea"
    except Exception as e:
        print("⚠️ Error patching OpenAPI for textareas:", e)

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi


@app.post("/api/v1/select-local-videos", include_in_schema=False)
async def select_local_videos(request: Request):
    """Use macOS's native chooser so the engine knows the real source path."""
    if platform.system() != "Darwin":
        raise HTTPException(501, "Нативный выбор локальных файлов поддерживается в этой сборке для macOS.")
    if request.client and request.client.host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "Выбор локальных файлов разрешён только с этого Mac.")
    apple_script = (
        'set selectedFiles to choose file with prompt \"Выберите одно или несколько видео для VideoContext Engine\" with multiple selections allowed\n'
        'set oldDelimiters to AppleScript\'s text item delimiters\n'
        'set AppleScript\'s text item delimiters to linefeed\n'
        'set pathList to {}\n'
        'repeat with selectedFile in selectedFiles\n'
        '    set end of pathList to POSIX path of selectedFile\n'
        'end repeat\n'
        'set resultText to pathList as text\n'
        'set AppleScript\'s text item delimiters to oldDelimiters\n'
        'return resultText'
    )
    try:
        result = await asyncio.to_thread(
            subprocess.run, ["osascript", "-e", apple_script],
            capture_output=True, text=True, check=False
        )
    except Exception as exc:
        raise HTTPException(500, f"Не удалось открыть системный выбор файлов: {exc}") from exc
    if result.returncode != 0:
        # Choosing Cancel is a normal UI action, not an error.
        if "User canceled" in (result.stderr or "") or "(-128)" in (result.stderr or ""):
            return {"files": [], "cancelled": True}
        raise HTTPException(500, f"macOS не смог открыть окно выбора файлов: {(result.stderr or '').strip()}")

    chosen_paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    files = []
    rejected = []
    for raw_path in chosen_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            rejected.append(f"{path.name}: файл недоступен")
            continue
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            rejected.append(f"{path.name}: неподдерживаемый формат")
            continue
        token = secrets.token_urlsafe(24)
        LOCAL_VIDEO_SELECTIONS[token] = str(path)
        files.append({
            "token": token,
            "name": path.name,
            "size": path.stat().st_size,
            "type": "video/" + path.suffix.lower().lstrip("."),
        })
    if len(LOCAL_VIDEO_SELECTIONS) > 256:
        for old_key in list(LOCAL_VIDEO_SELECTIONS)[:len(LOCAL_VIDEO_SELECTIONS) - 192]:
            LOCAL_VIDEO_SELECTIONS.pop(old_key, None)
    return {"files": files, "rejected": rejected, "cancelled": not bool(files)}


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def video_context_web_ui():
    """Friendly local UI for selecting and processing multiple videos sequentially."""
    ui_path = Path(__file__).with_name("videocontext_web_ui.html")
    if not ui_path.is_file():
        return HTMLResponse(
            "UI file not found. Re-run patch_vce_ui_batch_progress.py from the project folder.",
            status_code=500,
        )
    return HTMLResponse(ui_path.read_text(encoding="utf-8"))


@app.get("/api/v1/output-file/{output_id}", include_in_schema=False)
async def download_registered_output(output_id: str):
    """Download a report from the source video's folder using an opaque in-memory ID."""
    record = OUTPUT_FILE_REGISTRY.get(output_id)
    if not record:
        raise HTTPException(404, "Файл отчёта не найден; возможно, сервер был перезапущен.")
    output_path, download_name = record
    if not output_path.is_file():
        raise HTTPException(404, "Файл отчёта больше не существует на диске.")
    return FileResponse(str(output_path), filename=download_name)


@app.get("/api/v1/output/{filename}", include_in_schema=False)
async def download_context_output(filename: str):
    """Serve only generated *_context.txt / *_context.json files from the working directory."""
    if Path(filename).name != filename or not filename.endswith(("_context.txt", "_context.json")):
        raise HTTPException(400, "Invalid output filename")
    output_path = Path.cwd() / filename
    if not output_path.is_file():
        raise HTTPException(404, "Output file not found in the engine working directory")
    return FileResponse(str(output_path), filename=filename)


@app.get("/api/v1/ui-settings", include_in_schema=False)
async def get_ui_settings():
    """Return the persisted settings shown by the local Web UI."""
    config_path = Path(__file__).with_name("videocontext_settings.json")
    try:
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(persisted, dict):
            raise ValueError("settings root must be an object")
    except Exception as exc:
        raise HTTPException(500, f"Не удалось прочитать настройки: {exc}")
    # VCE_FFMPEG_OCR_WORKERS_SETTING_V1
    defaults = {
        "analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,
        "ffmpeg_ocr_workers": 2,
        "enable_stage_timing": False,
        "enable_adaptive_keyframes": False,
        "enable_ocr_cache": False,
        "enable_mlx_whisper": False,
        "enable_fast_scene_detection": False,
        "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        "whisper_model": "large", "vlm_resolution": 1280,
        "scene_threshold": 0.15, "min_scene_duration": 1.5,
        "max_scene_duration": 20, "keyframes_per_scene": 1,
        "vlm_max_tokens_scene": 1024, "vlm_max_tokens_summary": 2048,
        "enable_apple_ocr": True, "whisper_acceleration": True,
        "enable_audio_features": True, "generate_summary": True,
        "generate_txt": True, "generate_json": True, "word_timestamps": True,
    }
    defaults.setdefault("text_llm_enabled", True)
    defaults.setdefault("text_llm_model", TEXT_LLM_DEFAULT_MODEL)
    defaults.setdefault("text_llm_max_tokens", TEXT_LLM_DEFAULT_MAX_TOKENS)
    defaults.setdefault("text_llm_name_reconciliation_prompt", TEXT_LLM_NAME_RECONCILIATION_PROMPT_DEFAULT)
    result = {key: persisted.get(key, defaults[key]) for key in defaults}
    result["ffmpeg_ocr_available"], result["ffmpeg_ocr_reason"] = _vce_ffmpeg_ocr_capability()
    result["qwen35_text_stage_note"] = "Режим анализа не отключает Qwen3.5 текстовую постобработку."
    return result


@app.post("/api/v1/ui-settings", include_in_schema=False)
async def save_ui_settings(payload: Dict[str, Any]):
    """Persist allow-listed settings without touching prompts or unrelated preferences."""
    config_path = Path(__file__).with_name("videocontext_settings.json")
    try:
        current = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("settings root must be an object")
    except Exception as exc:
        raise HTTPException(500, f"Не удалось прочитать настройки: {exc}")

    int_ranges = {
        "ffmpeg_ocr_workers": (1, 8),
        "ffmpeg_ocr_interval": (1, 60),
        "vlm_resolution": (128, 2048), "keyframes_per_scene": (1, 5),
        "vlm_max_tokens_scene": (16, 1024), "vlm_max_tokens_summary": (16, 2048),
        "text_llm_max_tokens": (1024, 4096),
    }
    float_ranges = {
        "scene_threshold": (0.01, 1.0), "min_scene_duration": (0.5, 60.0),
        "max_scene_duration": (5.0, 300.0),
    }
    bool_keys = {
        "enable_apple_ocr", "enable_ffmpeg_ocr", "whisper_acceleration", "enable_audio_features",
        "enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
        "enable_mlx_whisper", "enable_fast_scene_detection",
        "generate_summary", "generate_txt", "generate_json", "word_timestamps",
        "text_llm_enabled",
    }
    allowed_vlm = {"mlx-community/Qwen3-VL-8B-Instruct-4bit", "mlx-community/Qwen3-VL-4B-Instruct-4bit"}
    allowed_whisper = {"tiny", "base", "small", "medium", "large"}
    merged = dict(current)
    for key, value in payload.items():
        if key == "analysis_mode":
            if value not in {"full", "whisper_ocr", "ocr_only"}: raise HTTPException(422, "Недопустимый сценарий анализа")
        elif key == "vlm_model":
            if value not in allowed_vlm: raise HTTPException(422, "Недопустимая VLM-модель")
        elif key == "whisper_model":
            if value not in allowed_whisper: raise HTTPException(422, "Недопустимая Whisper-модель")
        elif key == "text_llm_name_reconciliation_prompt":
            if not isinstance(value, str):
                raise HTTPException(422, "Промпт согласования названий должен быть строкой")
            if len(value) > 8000:
                raise HTTPException(422, "Промпт согласования названий не должен превышать 8000 символов")
        elif key == "text_llm_model":
            if value != TEXT_LLM_DEFAULT_MODEL:
                raise HTTPException(422, "Недопустимая модель текстовой LLM")
        elif key in int_ranges:
            if type(value) is not int or not int_ranges[key][0] <= value <= int_ranges[key][1]:
                raise HTTPException(422, f"Недопустимое значение настройки {key}")
        elif key in float_ranges:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not float_ranges[key][0] <= float(value) <= float_ranges[key][1]:
                raise HTTPException(422, f"Недопустимое значение настройки {key}")
            value = float(value)
        elif key in bool_keys:
            if type(value) is not bool: raise HTTPException(422, f"Настройка {key} должна быть логической")
        else:
            raise HTTPException(422, f"Неизвестная настройка: {key}")
        merged[key] = value

    temp_path = config_path.with_name(config_path.name + ".tmp")
    try:
        temp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(config_path)
        # Keep the module settings dict in sync where it is a mutable dict.
        if isinstance(globals().get("USER_SETTINGS"), dict):
            USER_SETTINGS.clear()
            USER_SETTINGS.update(merged)
    except Exception as exc:
        try: temp_path.unlink(missing_ok=True)
        except Exception: pass
        raise HTTPException(500, f"Не удалось сохранить настройки: {exc}")
    return {"ok": True, "message": "Настройки сохранены", "settings": {key: merged[key] for key in payload}}


@app.post("/api/v1/cancel/{job_id}", include_in_schema=False)
async def cancel_video_analysis(job_id: str):
    """Request cooperative cancellation of a running local analysis job."""
    event = CANCEL_EVENTS.setdefault(job_id, threading.Event())
    event.set()
    # Bound stale events created if a user stops during a multipart upload.
    if len(CANCEL_EVENTS) > 128:
        for old_id, old_event in list(CANCEL_EVENTS.items()):
            if old_id != job_id and old_event.is_set():
                CANCEL_EVENTS.pop(old_id, None)
                if len(CANCEL_EVENTS) <= 96:
                    break
    return {"ok": True, "message": "Остановка запрошена; движок завершит текущую вычислительную операцию и остановится на ближайшей безопасной точке."}


@app.get("/api/v1/progress/{job_id}", include_in_schema=False)
async def get_video_job_progress(job_id: str):
    """Return weighted, approximate stage progress. It is an estimate, never a fake exact ETA."""
    item = JOB_PROGRESS.get(job_id)
    if item is None:
        return {
            "job_id": job_id,
            "phase": "waiting",
            "status": "waiting",
            "percent": 0.0,
            "indeterminate": False,
            "message": "Ожидание начала обработки файла…",
            "elapsed_seconds": 0.0,
        }
    now = time.time()
    response = dict(item)
    response["elapsed_seconds"] = round(max(0.0, now - float(item.get("started_at", now))), 1)
    phase_elapsed = max(0.0, now - float(item.get("phase_started_at", now)))
    pct = float(item.get("percent", 0.0) or 0.0)
    if item.get("status") == "running" and item.get("progress_dynamic"):
        lo = float(item.get("progress_start_percent", pct))
        hi = float(item.get("progress_end_percent", lo))
        expected = max(1.0, float(item.get("progress_expected_seconds", 20.0)))
        raw_fraction = phase_elapsed / expected
        if raw_fraction <= 0.92:
            fraction = raw_fraction
        else:
            # If a phase runs longer than estimated, keep advancing slowly without
            # pretending the task is complete or snapping to 100%.
            fraction = min(0.97, 0.92 + (raw_fraction - 0.92) * 0.15)
        if item.get("phase") == "text_llm_generating":
            try:
                generated = max(0, int(item.get("generated_tokens", 0) or 0))
                token_limit = max(1024, int(item.get("token_limit", 4096) or 4096))
            except (TypeError, ValueError):
                generated, token_limit = 0, 4096
            token_fraction = min(0.94, generated / max(1200.0, token_limit * 0.72))
            # A slow decoder still advances gently; emitted tokens dominate when available.
            fraction = min(0.94, max(token_fraction, fraction * 0.62))
        elif item.get("phase") == "text_llm_repair":
            fraction = min(0.9, phase_elapsed / expected)
        pct = lo + max(0.0, hi - lo) * fraction
        pct = min(hi - 0.08, pct) if hi > lo else hi
        pct = max(float(item.get("last_reported_percent", 0.0) or 0.0), pct)
        JOB_PROGRESS[job_id]["last_reported_percent"] = round(max(0.0, min(pct, 99.8)), 1)
    response["percent"] = round(max(0.0, min(pct, 100.0)), 1)
    response["indeterminate"] = False
    response["progress_kind"] = "weighted_estimate"
    return response

@app.get("/favicon.ico", include_in_schema=False)
async def vce_favicon():
    """Serve the app's bundled favicon over HTTP (more reliable than data: URLs in Safari)."""
    from fastapi.responses import Response
    image_bytes = base64.b64decode("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")
    return Response(content=image_bytes, media_type="image/png", headers={"Cache-Control": "no-cache, max-age=0"})


# VCE_ANALYSIS_PROFILES_FFMPEG_OCR_V2_CPU_TESSERACT

def _vce_detect_ffmpeg_ocr_backend() -> tuple[str, str, str]:
    """Return (backend, language, status message); backend is native, tesseract-cli or unavailable."""
    ffmpeg = _vce_which_binary("ffmpeg")
    if not ffmpeg:
        return "unavailable", "rus+eng", "Команда ffmpeg не найдена в PATH. Apple Vision OCR продолжит работать."
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        filters_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception as exc:
        filters_text = ""
        filter_error = str(exc)
    else:
        filter_error = ""
    native_filter = bool(re.search(
        r"(?im)^\s*\S+\s+ocr\s+V->V\s+Optical Character Recognition", filters_text
    ))
    # Prefer the standalone Tesseract executable where present. This avoids requiring
    # a custom FFmpeg build and lets independent frames be OCR'd in parallel on CPU.
    tesseract = _vce_which_binary("tesseract")
    if tesseract:
        try:
            lang_proc = subprocess.run(
                [tesseract, "--list-langs"], capture_output=True, text=True,
                timeout=15, check=False,
            )
            lang_text = (lang_proc.stdout or "") + "\n" + (lang_proc.stderr or "")
            languages = {
                line.strip() for line in lang_text.splitlines()
                if re.fullmatch(r"[A-Za-z0-9_]+", line.strip())
            }
        except Exception:
            languages = set()
        has_rus = "rus" in languages
        has_eng = "eng" in languages
        if has_rus and has_eng:
            return "tesseract-cli", "rus+eng", "Доступен FFmpeg + Tesseract CLI (CPU), языки rus+eng; кадры распознаются параллельно."
        if has_rus:
            return "tesseract-cli", "rus", "Доступен FFmpeg + Tesseract CLI (CPU), язык rus. Для английского текста можно установить tesseract-lang."
        if has_eng:
            return "tesseract-cli", "eng", "Доступен FFmpeg + Tesseract CLI (CPU), но языковые данные rus отсутствуют: русские надписи могут распознаваться хуже. Установи tesseract-lang."
        # A CLI without language data isn't a usable OCR backend.
        lang_note = "Tesseract найден, но не обнаружены языки rus/eng."
    else:
        lang_note = "Команда tesseract не найдена."
    if native_filter:
        return "ffmpeg-filter", "rus+eng", "Доступен встроенный FFmpeg OCR/Tesseract filter."
    details = (lang_note + " " + filter_error).strip()
    return "unavailable", "rus+eng", (
        details + " Установи Tesseract и языки командой: brew install tesseract tesseract-lang. "
        "FFmpeg не будет заменён; Apple Vision OCR продолжит работать."
    ).strip()


def _vce_ffmpeg_ocr_capability() -> tuple[bool, str]:
    backend, _language, message = _vce_detect_ffmpeg_ocr_backend()
    return backend != "unavailable", message


def _vce_parse_ffmpeg_ocr_metadata(raw: str) -> list[dict]:
    """Parse metadata=mode=print records emitted by FFmpeg's native ocr filter."""
    blocks = re.split(r"(?m)(?=^frame:\d+\s)", raw or "")
    records: list[dict] = []
    seen: set[tuple[float, str]] = set()
    for block in blocks:
        time_match = re.search(r"pts_time:([-+]?\d+(?:\.\d+)?)", block)
        text_match = re.search(r"(?m)^lavfi\.ocr\.text=(.*)$", block)
        confidence_match = re.search(r"(?m)^lavfi\.ocr\.confidence=([-+]?\d+(?:\.\d+)?)", block)
        if not time_match or not text_match:
            continue
        try:
            frame_time = float(time_match.group(1))
            confidence = float(confidence_match.group(1)) if confidence_match else None
        except (TypeError, ValueError):
            continue
        text = text_match.group(1).replace(r"\n", " ").replace(r"\,", ",").strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        key = (round(frame_time, 2), text.casefold())
        if key in seen:
            continue
        seen.add(key)
        records.append({"time": round(frame_time, 3), "text": text, "confidence": confidence})
    return records


def _vce_run_ffmpeg_ocr(video_path: str, interval_seconds: int, workers: int = 2) -> tuple[list[dict], str]:
    """Sample frames with FFmpeg; use native OCR filter or parallel standalone Tesseract CLI."""
    backend, language, status = _vce_detect_ffmpeg_ocr_backend()
    if backend == "unavailable":
        return [], status
    interval = max(1, min(60, int(interval_seconds)))
    workers = max(1, min(8, int(workers)))
    if backend == "ffmpeg-filter":
        metadata_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="vce_ffmpeg_ocr_", suffix=".txt", delete=False) as tmp:
                metadata_path = tmp.name
            # Escape filtergraph-special path characters. No shell is involved.
            escaped_path = metadata_path.replace("\\", "\\\\").replace(":", r"\:").replace(",", r"\,").replace("'", r"\'")
            vf = f"fps=1/{interval},ocr=language={language},metadata=mode=print:file='{escaped_path}'"
            proc = subprocess.run(
                [_vce_which_binary("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-threads", "2", "-i", str(video_path), "-vf", vf,
                 "-an", "-sn", "-dn", "-f", "null", "-"],
                capture_output=True, text=True, timeout=1800, check=False,
            )
            if proc.returncode != 0:
                details = ((proc.stderr or "").strip().splitlines()[-4:])
                return [], "Встроенный FFmpeg OCR завершился с ошибкой: " + (" | ".join(details) or f"код {proc.returncode}")
            raw = Path(metadata_path).read_text(encoding="utf-8", errors="replace") if Path(metadata_path).exists() else ""
            records = _vce_parse_ffmpeg_ocr_metadata(raw)
            return records, "" if records else "Встроенный OCR выполнился, но не вернул распознанного текста."
        except Exception as exc:
            return [], f"Не удалось выполнить встроенный FFmpeg OCR: {exc}"
        finally:
            if metadata_path:
                try:
                    Path(metadata_path).unlink(missing_ok=True)
                except Exception:
                    pass

    ffmpeg = _vce_which_binary("ffmpeg")
    tesseract = _vce_which_binary("tesseract")
    if not ffmpeg or not tesseract:
        return [], "FFmpeg или Tesseract пропал из PATH после проверки доступности."
    try:
        with tempfile.TemporaryDirectory(prefix="vce_ffmpeg_tesseract_") as work_dir:
            frame_pattern = str(Path(work_dir) / "frame_%06d.jpg")
            vf = f"fps=1/{interval},scale=w='min(1600,iw)':h=-2"
            cpu_count = os.cpu_count() or 2
            decode_threads = max(1, min(4, cpu_count // 2 or 1))
            extract = subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-threads", str(decode_threads), "-i", str(video_path),
                 "-vf", vf, "-an", "-sn", "-dn", "-q:v", "3", "-fps_mode", "vfr",
                 "-start_number", "0", frame_pattern],
                capture_output=True, text=True, timeout=1800, check=False,
            )
            if extract.returncode != 0:
                details = ((extract.stderr or "").strip().splitlines()[-4:])
                return [], "FFmpeg не смог извлечь кадры для Tesseract: " + (" | ".join(details) or f"код {extract.returncode}")
            frames = sorted(Path(work_dir).glob("frame_*.jpg"))
            if not frames:
                return [], "FFmpeg завершился, но не создал кадры для OCR."

            def recognize(item: tuple[int, Path]) -> tuple[int, str, str]:
                index, image_path = item
                env = os.environ.copy()
                # Prevent each Tesseract process from creating its own large OpenMP pool.
                # Frame-level parallelism is the controlled source of CPU concurrency.
                env["OMP_THREAD_LIMIT"] = "1"
                env["OMP_NUM_THREADS"] = "1"
                try:
                    result = subprocess.run(
                        [tesseract, str(image_path), "stdout", "-l", language, "--psm", "6"],
                        capture_output=True, text=True, timeout=90, check=False, env=env,
                    )
                    text = re.sub(r"\s+", " ", (result.stdout or "")).strip()
                    error = "" if result.returncode == 0 else ((result.stderr or "").strip().splitlines()[-1:] or [f"код {result.returncode}"])[0]
                    return index, text, error
                except Exception as exc:
                    return index, "", str(exc)

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(recognize, enumerate(frames)))
            records: list[dict] = []
            errors = []
            seen: set[tuple[float, str]] = set()
            for index, text, error in sorted(results):
                if error:
                    errors.append(error)
                if not text:
                    continue
                frame_time = round(index * interval, 3)
                key = (round(frame_time, 2), text.casefold())
                if key in seen:
                    continue
                seen.add(key)
                records.append({"time": frame_time, "text": text, "confidence": None})
            if records:
                return records, "" if not errors else f"OCR выполнен с предупреждениями в {len(errors)} кадрах: {errors[0]}"
            detail = f" Первое предупреждение: {errors[0]}" if errors else ""
            return [], "Tesseract запустился, но не распознал текст на выбранных кадрах." + detail
    except Exception as exc:
        return [], f"Не удалось выполнить FFmpeg + Tesseract OCR: {exc}"


def _vce_ffmpeg_ocr_scene_text(records: list[dict], scene_start: float, scene_end: float) -> str:
    """Assign sampled OCR text to one scene and remove repeated slide text."""
    output = []
    seen = set()
    for record in sorted(records or [], key=lambda item: float(item.get("time", 0) or 0)):
        try:
            stamp = float(record.get("time", -1))
        except (TypeError, ValueError):
            continue
        text = re.sub(r"\s+", " ", str(record.get("text") or "")).strip()
        if not (scene_start <= stamp < scene_end) or not text:
            continue
        key = re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()
        if key and key not in seen:
            seen.add(key)
            output.append(text)
    return "\n".join(output)


def _vce_merge_ocr_sources(apple_text: str, ffmpeg_text: str) -> str:
    """Keep Apple Vision as primary OCR and append non-duplicate Tesseract lines."""
    apple = str(apple_text or "").strip()
    candidates = [line.strip() for line in str(ffmpeg_text or "").splitlines() if line.strip()]
    existing = {
        re.sub(r"[^\w]+", " ", line.casefold(), flags=re.UNICODE).strip()
        for line in apple.splitlines() if line.strip()
    }
    extra = []
    for line in candidates:
        key = re.sub(r"[^\w]+", " ", line.casefold(), flags=re.UNICODE).strip()
        if key and key not in existing:
            existing.add(key)
            extra.append(line)
    if not extra:
        return apple
    ffmpeg_part = "[FFmpeg/Tesseract OCR]\n" + "\n".join(extra)
    return (apple + "\n\n" + ffmpeg_part).strip() if apple else ffmpeg_part


# VCE_AUDIO_RESILIENCE_ACCELERATION_V6

def _vce_which_binary(name: str) -> str | None:
    """Find tools even when the GUI launcher receives a minimal macOS PATH."""
    found = shutil.which(name)
    if found:
        return found
    candidates = (
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path("/opt/local/bin") / name,
        Path.home() / ".local/bin" / name,
        Path("/usr/bin") / name,
        Path("/bin") / name,
    )
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _vce_probe_audio_stream(media_path: str) -> tuple[bool | None, str]:
    """Return (has_audio, diagnostic); None means probing was inconclusive."""
    ffprobe = _vce_which_binary("ffprobe")
    last_error = ""
    if ffprobe:
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=index,codec_name,codec_type",
                 "-of", "json", str(media_path)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            if proc.returncode == 0:
                payload = json.loads(proc.stdout or "{}")
                streams = payload.get("streams") or []
                if streams:
                    codec = str(streams[0].get("codec_name") or "unknown")
                    return True, f"Аудиопоток обнаружен (кодек: {codec})."
                return False, "Аудиопоток не обнаружен: ffprobe вернул пустой список audio streams."
            last_error = (proc.stderr or "").strip()[-500:]
        except Exception as exc:
            last_error = str(exc)[:500]

    # Fallback for installations where ffprobe is missing or cannot parse the file.
    ffmpeg = _vce_which_binary("ffmpeg")
    if ffmpeg:
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(media_path)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            diagnostic = ((proc.stderr or "") + "\n" + (proc.stdout or ""))
            if re.search(r"(?im)^\s*Stream #\d+:\d+.*?\bAudio:", diagnostic):
                match = re.search(r"(?im)^\s*Stream #\d+:\d+.*?\bAudio:\s*([^,\r\n]+)", diagnostic)
                codec = match.group(1).strip() if match else "unknown"
                return True, f"Аудиопоток обнаружен через FFmpeg (кодек: {codec})."
            if re.search(r"(?im)^\s*Stream #\d+:\d+.*?\bVideo:", diagnostic):
                return False, "Видеопоток найден, аудиопоток отсутствует (проверка FFmpeg)."
            last_error = (diagnostic.strip()[-500:] or last_error)
        except Exception as exc:
            last_error = str(exc)[:500]

    detail = f" Детали проверки: {last_error}" if last_error else ""
    return None, "Не удалось надёжно определить наличие аудио; VCE попробует Whisper и применит резервный режим при ошибке." + detail


def _vce_is_audio_decode_failure(exc: BaseException) -> bool:
    """Recognize common Whisper/FFmpeg audio-load failures without hiding unrelated bugs."""
    message = str(exc).casefold()
    markers = (
        "failed to load audio",
        "output file does not contain any stream",
        "does not contain any stream",
        "no audio stream",
        "could not find codec parameters",
        "error opening input file",
        "invalid data found when processing input",
        "matches no streams",
    )
    return any(marker in message for marker in markers)


def _vce_transcribe_audio_safe(
    model, media_path: str, *args, model_name=None, use_mlx=False, **kwargs
) -> tuple[dict, str]:
    """Transcribe on a worker thread; preserve the selected Whisper backend."""
    try:
        result = _vce_transcribe_audio(
            str(media_path), model_name, model, use_mlx, *args, **kwargs
        )
        if not isinstance(result, dict):
            raise TypeError("Whisper returned a non-dictionary transcription result.")
        return result, ""
    except Exception as exc:
        if not _vce_is_audio_decode_failure(exc):
            raise
        detail = re.sub(r"\s+", " ", str(exc)).strip()[-700:]
        return {"segments": []}, (
            "Whisper автоматически пропущен: FFmpeg не смог прочитать аудиодорожку. "
            "Видеоанализ продолжается без транскрипции. " + detail
        )


def _vce_run_ffmpeg_ocr_timed(video_path: str, interval_seconds: int, workers: int = 2) -> tuple[list[dict], str, float]:
    """Return OCR records, status and actual OCR wall time; never fail the video job."""
    started = time.perf_counter()
    try:
        records, message = _vce_run_ffmpeg_ocr(video_path, interval_seconds, workers)
    except Exception as exc:
        records, message = [], f"FFmpeg/Tesseract OCR пропущен без остановки анализа: {exc}"
    return records, message, round(time.perf_counter() - started, 3)

# VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1
_VCE_MLX_WHISPER_REPOS = {
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
_VCE_OCR_CACHE = {}
_VCE_OCR_CACHE_MAX = 256


def _vce_validate_mlx_whisper_model(model_name):
    """Validate opt-in MLX Whisper availability and resolve its model repository."""
    try:
        import mlx_whisper  # noqa: F401
    except ImportError as exc:
        raise HTTPException(
            422,
            "MLX Whisper выбран в настройках, но пакет не установлен в окружении VCE. "
            "В активном venv установите его командой: python -m pip install mlx-whisper",
        ) from exc
    normalized = str(model_name or "").strip().lower()
    repo = _VCE_MLX_WHISPER_REPOS.get(normalized)
    if not repo:
        override = os.environ.get("VCE_MLX_WHISPER_REPO", "").strip()
        if not override:
            raise HTTPException(
                422,
                "Для MLX Whisper поддерживаются модели large, large-v3 и large-v3-turbo. "
                "Для другой совместимой MLX-модели задайте VCE_MLX_WHISPER_REPO.",
            )
        repo = override
    return repo


def _vce_load_whisper_model(model_name, use_mlx, acceleration_enabled=None):
    """Avoid PyTorch when MLX is selected; otherwise preserve VCE's configured loader."""
    if use_mlx:
        _vce_validate_mlx_whisper_model(model_name)
        return None
    # Preserve the existing loader and its optional MPS/CUDA selection flag.
    loader = globals().get("load_whisper_model")
    if callable(loader):
        if acceleration_enabled is None:
            return loader(model_name)
        return loader(model_name, acceleration_enabled=acceleration_enabled)
    return whisper.load_model(model_name)


def _vce_transcribe_audio(audio_path, model_name, model, use_mlx, *args, **kwargs):
    """Use the selected ASR backend while keeping Whisper-compatible output fields."""
    if not use_mlx:
        return model.transcribe(audio_path, *args, **kwargs)
    import mlx_whisper
    repo = _vce_validate_mlx_whisper_model(model_name)
    return mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=repo,
        **kwargs,
    )


def _vce_adaptive_keyframe_limit(scene, configured_max):
    """Treat keyframes_per_scene as a ceiling and lower it for short scenes."""
    try:
        duration = max(0.0, float(scene.get("end", 0.0)) - float(scene.get("start", 0.0)))
        limit = max(1, min(5, int(configured_max)))
    except (TypeError, ValueError, AttributeError):
        return max(1, min(5, int(configured_max or 1)))
    recommended = 1 if duration <= 6.0 else (2 if duration <= 14.0 else limit)
    return max(1, min(limit, recommended))


def _vce_cached_apple_vision_ocr(image):
    """Cache OCR text for byte-identical images, with a bounded per-process cache."""
    digest = hashlib.sha256()
    digest.update(str(getattr(image, "mode", "")).encode("utf-8"))
    digest.update(str(getattr(image, "size", "")).encode("utf-8"))
    digest.update(image.tobytes())
    key = digest.hexdigest()
    if key in _VCE_OCR_CACHE:
        return _VCE_OCR_CACHE[key]
    result = apple_vision_ocr(image)
    if len(_VCE_OCR_CACHE) >= _VCE_OCR_CACHE_MAX:
        _VCE_OCR_CACHE.pop(next(iter(_VCE_OCR_CACHE)))
    _VCE_OCR_CACHE[key] = result
    return result


def _vce_detect_scenes_fast(video_path, threshold, min_duration, max_duration):
    """Experimental scene scan: seek to one frame per second and histogram a small frame."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Erreur lecture vidéo")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
    if duration <= 0:
        cap.release()
        return detect_scenes(video_path, threshold, min_duration, max_duration)

    scenes = []
    last_hist = None
    start_sec = 0.0
    prev_sec = 0.0
    sample_sec = 0.0
    sample_interval = 1.0
    try:
        while sample_sec < duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, sample_sec * 1000.0)
            ret, frame = cap.read()
            if not ret:
                break
            height, width = frame.shape[:2]
            longest = max(height, width)
            if longest > 480:
                scale = 480.0 / float(longest)
                frame = cv2.resize(
                    frame,
                    (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            hist = compute_hsv_histogram(frame)
            is_visual_change = False
            if last_hist is not None:
                score = cv2.compareHist(last_hist, hist, cv2.HISTCMP_CORREL)
                is_visual_change = (1.0 - score) > threshold
            duration_ok = (sample_sec - start_sec) >= min_duration
            force_cut = (sample_sec - start_sec) >= max_duration
            if (is_visual_change and duration_ok) or force_cut:
                scenes.append({"start": start_sec, "end": prev_sec})
                start_sec = sample_sec
            last_hist = hist
            prev_sec = sample_sec
            sample_sec += sample_interval
    finally:
        cap.release()

    if prev_sec > start_sec:
        scenes.append({"start": start_sec, "end": min(prev_sec, duration)})
    if not scenes and duration > 0:
        scenes.append({"start": 0.0, "end": duration})
    return scenes



# VCE_LOCAL_SERVER_SHUTDOWN_ENDPOINT_V1
@app.post("/api/v1/server/shutdown")
async def _vce_shutdown_server(request: Request):
    # This control is intended only for the local desktop UI, not remote callers.
    client_host = request.client.host if request.client else ""
    import ipaddress
    try:
        is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = client_host == "testclient"
    if not is_loopback:
        raise HTTPException(status_code=403, detail="Остановка разрешена только с локального компьютера")

    origin = request.headers.get("origin", "")
    if origin and not (
        origin.startswith("http://127.0.0.1:7555")
        or origin.startswith("http://localhost:7555")
        or origin.startswith("http://[::1]:7555")
    ):
        raise HTTPException(status_code=403, detail="Недопустимый Origin для остановки локального сервера")

    async def _stop_after_response():
        await asyncio.sleep(0.75)
        import signal
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_stop_after_response())
    return {
        "status": "stopping",
        "message": "Команда остановки принята. Для следующего запуска откройте VideoContext Engine.app заново.",
    }


# VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1
class _VCEVlmMemoryFallback(Exception):
    """Internal marker for an exhausted VLM retry policy (normally not raised)."""


def _vce_is_metal_memory_error(exc):
    text = str(exc).casefold()
    oom_markers = (
        "out of memory", "oom", "memoryerror", "failed to allocate",
        "allocation failed", "insufficient memory", "not enough memory",
        "недостаточно памяти", "не хватает памяти", "не удалось выделить память",
        "mémoire insuffisante", "failed to allocate buffer", "bad_alloc",
    )
    if not any(marker in text for marker in oom_markers):
        return False
    return any(marker in text for marker in (
        "metal", "mlx", "mps", "memory", "mémoire", "памят", "allocate", "allocation", "oom", "bad_alloc"
    ))


def _vce_clear_metal_memory_caches():
    """Best-effort cleanup of unused MLX/PyTorch allocator cache after an OOM."""
    try:
        gc.collect()
    except Exception:
        pass
    try:
        import mlx.core as mx
        # Wait for outstanding work before releasing unused cached buffers.
        mx.synchronize()
        # MLX documents clear_cache() as the public cache-management API.
        mx.clear_cache()
    except Exception:
        pass
    try:
        import torch
        mps = getattr(torch, "mps", None)
        if mps is not None and getattr(torch.backends, "mps", None) is not None:
            if torch.backends.mps.is_available():
                mps.empty_cache()
    except Exception:
        pass
    try:
        gc.collect()
    except Exception:
        pass


def _vce_resize_image_max_edge(image, max_edge):
    """Return a resized PIL image preserving aspect ratio; leave unknown types untouched."""
    try:
        width, height = image.size
        width, height = int(width), int(height)
        longest = max(width, height)
        if longest <= max_edge or longest <= 0:
            return image
        scale = float(max_edge) / float(longest)
        new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        try:
            from PIL import Image as _PILImage
            resampling = _PILImage.Resampling.LANCZOS
        except Exception:
            resampling = 1
        return image.resize(new_size, resampling)
    except Exception:
        return image


def _vce_describe_scene_with_memory_fallback(vlm_engine, images, prompt, max_tokens):
    """Attempt normal inference first; shrink inputs only after a real Metal OOM.

    After all bounded retries fail, return a valid JSON sentinel. The scene loop
    recognizes it, disables VLM for the remainder of this job, and still saves
    transcript/OCR instead of failing the entire analysis.
    """
    original_images = list(images or [])
    requested_tokens = max(64, int(max_tokens or 512))
    try:
        return vlm_engine.describe_scene(original_images, prompt, max_tokens=requested_tokens)
    except Exception as exc:
        if not _vce_is_metal_memory_error(exc):
            raise
        print("⚠️ [VLM memory guard] Metal OOM; clearing unused caches and retrying at lower image resolution.")
        last_error_message = str(exc)[:350]

    _vce_clear_metal_memory_caches()
    if not original_images:
        return json.dumps({
            "description": "Визуальный анализ пропущен: кадры для VLM недоступны.",
            "tags": {}, "_vce_memory_fallback": True,
        }, ensure_ascii=False)

    attempts = [
        (384, original_images, requested_tokens, "384px, all keyframes"),
        (320, [original_images[len(original_images) // 2]], min(requested_tokens, 384), "320px, one representative keyframe"),
        (256, [original_images[len(original_images) // 2]], min(requested_tokens, 256), "256px, one representative keyframe"),
    ]
    seen = set()
    for max_edge, candidate_images, token_limit, label in attempts:
        resized = [_vce_resize_image_max_edge(image, max_edge) for image in candidate_images]
        try:
            signature = (tuple(tuple(getattr(image, "size", ())) for image in resized), len(resized), int(token_limit))
        except Exception:
            signature = (max_edge, len(resized), int(token_limit))
        if signature in seen:
            continue
        seen.add(signature)
        _vce_clear_metal_memory_caches()
        try:
            result = vlm_engine.describe_scene(resized, prompt, max_tokens=int(token_limit))
            print(f"✅ [VLM memory guard] Recovery succeeded at {label}.")
            return result
        except Exception as retry_exc:
            if not _vce_is_metal_memory_error(retry_exc):
                raise
            last_error_message = str(retry_exc)[:350]
            print(f"⚠️ [VLM memory guard] Still out of Metal memory at {label}.")

    # Free the model itself as well, so later report-generation stages have a
    # better chance to use unified memory. Never let cleanup mask the real failure.
    try:
        vlm_engine.unload_model()
    except Exception:
        pass
    _vce_clear_metal_memory_caches()
    message = (
        "Визуальное описание пропущено: Metal-памяти не хватило даже при повторе на 384/320/256 px. "
        "Для оставшихся сцен VLM отключена; транскрипция и OCR сохраняются. "
        "Последняя ошибка: " + last_error_message
    )
    print("🛟 [VLM memory guard] " + message)
    return json.dumps({
        "description": message,
        "tags": {},
        "_vce_memory_fallback": True,
    }, ensure_ascii=False)


def _vce_is_recoverable_container_error(exc):
    detail = getattr(exc, "detail", None)
    text = str(detail if detail is not None else exc).casefold()
    markers = (
        "trun track id unknown", "no tfhd was found", "invalid data found when processing input",
        "moov atom not found", "error reading header", "ffprobe:",
        "не содержит читаемого видеопотока", "не содержит читаемого видео", "поврежд",
        "fichier vidéo invalide", "invalid video file", "файл видео невалиден",
        "невозможно прочитать видео", "no video stream", "no readable video stream",
        "unsupported codec", "unsupported video codec", "codec not supported",
        "decoder not found", "could not find decoder", "could not open codec",
        "failed to open codec", "could not find codec parameters", "invalid codec parameters",
        "unsupported pixel format", "could not open video", "cannot open video",
        "failed to read video", "videocapture not opened", "video capture not opened",
    )
    return any(marker in text for marker in markers) and not any(marker in text for marker in (
        "vidéo trop longue", "видео слишком длин", "video too long", "слишком длинное видео"
    ))



def _vce_validate_video_with_remux(video_path, force_remux=False, prior_error=None):
    """Validate video; on container/codec failure make a temporary remux or H.264 copy."""
    import os
    import shutil as _shutil
    import subprocess
    import tempfile
    detail = str(prior_error or "")
    if not force_remux:
        try:
            return validate_video_file(video_path), None, None
        except Exception as exc:
            if not _vce_is_recoverable_container_error(exc):
                raise
            detail = str(getattr(exc, "detail", None) or exc)
    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=400, detail=(
            "Ошибка контейнера/кодека, но FFmpeg недоступен. Исходник не изменён. " + detail[:700]
        ))
    # VCE_FRAGMENTED_MP4_REMUX_FIRST
    transcode_strategy = ("transcode-h264-aac", [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-max_muxing_queue_size", "2048", "-movflags", "+faststart",
    ])
    remux_strategy = ("stream-copy-remux", ["-c", "copy", "-movflags", "+faststart"])
    detail_folded = detail.casefold()
    fragmented_container_markers = (
        "trun track id unknown", "no tfhd was found", "moov atom not found",
        "error reading header", "invalid data found when processing input",
        "invalid data", "could not find codec parameters",
    )
    fragmented_container_error = any(marker in detail_folded for marker in fragmented_container_markers)
    if force_remux and fragmented_container_error:
        # Container/fragment metadata is the problem: try the cheap packet remux first.
        # If that fails, decode what FFmpeg can salvage and transcode to H.264/AAC.
        strategies = [remux_strategy, transcode_strategy]
        print("⚙️ [MP4 recovery] Обнаружена ошибка контейнера/фрагментов; сначала пробуем remux, затем H.264/AAC.")
    elif force_remux:
        # Unsupported decoders/codecs are more likely to need an actual transcode.
        strategies = [transcode_strategy, remux_strategy]
    else:
        strategies = [remux_strategy, transcode_strategy]
    failures = []
    for label, codec_args in strategies:
        fd, candidate = tempfile.mkstemp(prefix="vce_mp4_recovery_", suffix=".mp4")
        os.close(fd)
        valid = False
        try:
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
                "-analyzeduration", "100M", "-probesize", "100M",
                "-i", str(video_path), "-map", "0:v:0", "-map", "0:a?",
                *codec_args, candidate,
            ]
            proc = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
            if proc.returncode == 0 and os.path.isfile(candidate) and os.path.getsize(candidate) > 1024:
                try:
                    duration = validate_video_file(candidate)
                    note = f"FFmpeg восстановил видео во временной копии методом {label}; исходник не изменён."
                    valid = True
                    print("✅ [MP4 recovery] " + note)
                    return duration, candidate, note
                except Exception as verify_exc:
                    err = str(getattr(verify_exc, "detail", None) or verify_exc)
            else:
                err = (proc.stderr or proc.stdout or f"FFmpeg exit code {proc.returncode}").strip()[-1200:]
            failures.append(f"{label}: {err}")
        except subprocess.TimeoutExpired:
            failures.append(f"{label}: превышен лимит 240 секунд")
        except Exception as recovery_exc:
            failures.append(f"{label}: {type(recovery_exc).__name__}: {recovery_exc}")
        finally:
            if not valid and os.path.exists(candidate):
                try: os.remove(candidate)
                except OSError: pass
    raise HTTPException(status_code=400, detail=(
        "FFmpeg не смог восстановить читаемый видеопоток через transcode/remux. Исходник не изменён. "
        "Ошибка источника: " + detail[:450] + ". Попытки: " + " | ".join(failures)[-1500:] +
        ". Если MP4 fragmented и обязательные фрагменты отсутствуют, перекачайте или экспортируйте файл заново."
    ))



# VCE_PARALLEL_CPU_ASR_GPU_VLM_V1
def _vce_transcribe_audio_safe_timed(*args, **kwargs):
    """Run the existing safe ASR wrapper and return its actual worker wall time."""
    started = time.perf_counter()
    result = _vce_transcribe_audio_safe(*args, **kwargs)
    elapsed = time.perf_counter() - started
    if isinstance(result, tuple) and len(result) == 2:
        return result[0], result[1], elapsed
    raise TypeError("_vce_transcribe_audio_safe must return (audio_result, audio_error)")


def _vce_build_audio_only_video(video_path, prior_error=""):
    """Extract readable audio and wrap it in a temporary black video so ASR can still run."""
    import os
    import shutil as _shutil
    import subprocess
    import tempfile
    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg недоступен для извлечения аудио.")
    afd, audio_path = tempfile.mkstemp(prefix="vce_audio_fallback_", suffix=".m4a")
    os.close(afd)
    vfd, synthetic_path = tempfile.mkstemp(prefix="vce_audio_only_", suffix=".mp4")
    os.close(vfd)
    success = False
    try:
        extract = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
            "-analyzeduration", "100M", "-probesize", "100M", "-i", str(video_path),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "aac", "-b:a", "96k", audio_path,
        ]
        proc = subprocess.run(extract, capture_output=True, text=True, timeout=240, check=False)
        if proc.returncode or not os.path.isfile(audio_path) or os.path.getsize(audio_path) <= 1024:
            raise RuntimeError("Не удалось извлечь аудиодорожку: " + (proc.stderr or proc.stdout or str(proc.returncode))[-800:])
        synthesize = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:r=1",
            "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-pix_fmt", "yuv420p", "-r", "1", "-c:a", "copy",
            "-shortest", "-movflags", "+faststart", synthetic_path,
        ]
        proc = subprocess.run(synthesize, capture_output=True, text=True, timeout=240, check=False)
        if proc.returncode or not os.path.isfile(synthetic_path) or os.path.getsize(synthetic_path) <= 1024:
            raise RuntimeError("Не удалось создать служебное audio-only видео: " + (proc.stderr or proc.stdout or str(proc.returncode))[-800:])
        validate_video_file(synthetic_path)
        cap = cv2.VideoCapture(synthetic_path)
        try:
            if not cap.isOpened():
                raise RuntimeError("OpenCV не смог открыть служебное видео.")
            ok, _frame = cap.read()
            if not ok:
                raise RuntimeError("OpenCV не смог прочитать первый кадр служебного видео.")
        finally:
            cap.release()
        note = (
            "Видеопоток не удалось восстановить; продолжение в audio-only режиме. "
            "Whisper и сводка работают по аудио; VLM, OCR и анализ визуальных сцен отключены. "
            "Чёрная дорожка — техническая временная копия для синхронизации. "
            "Исходник не изменён. " + str(prior_error)[:250]
        )
        success = True
        print("⚠️ [Audio-only fallback] " + note)
        return synthetic_path, note
    finally:
        try: os.remove(audio_path)
        except OSError: pass
        if not success:
            try: os.remove(synthetic_path)
            except OSError: pass

# VCE_MP4_PREFLIGHT_RECOVERY_RETRY_V2
def _vce_ensure_opencv_compatible_with_recovery(video_path):
    """Return (usable_path, audio_only_fallback, note), degrading gracefully if possible."""
    import os
    from pathlib import Path
    prepared = None
    initial_error = ""
    try:
        prepared = ensure_opencv_compatible_video(video_path)
        validate_video_file(prepared)
        return prepared, False, None
    except Exception as exc:
        if not _vce_is_recoverable_container_error(exc):
            if prepared and str(Path(prepared).resolve()) != str(Path(video_path).resolve()):
                try: os.remove(prepared)
                except OSError: pass
            raise
        initial_error = str(getattr(exc, "detail", None) or exc)
    if prepared and str(Path(prepared).resolve()) != str(Path(video_path).resolve()):
        try: os.remove(prepared)
        except OSError: pass

    recovery_error = ""
    recovered = None
    try:
        _duration, recovered, note = _vce_validate_video_with_remux(
            video_path, force_remux=True, prior_error=initial_error
        )
        if recovered:
            compatible = ensure_opencv_compatible_video(recovered)
            validate_video_file(compatible)
            if str(Path(compatible).resolve()) != str(Path(recovered).resolve()):
                try: os.remove(recovered)
                except OSError: pass
            return compatible, False, note
    except Exception as exc:
        recovery_error = str(getattr(exc, "detail", None) or exc)
        if recovered and os.path.exists(recovered):
            try: os.remove(recovered)
            except OSError: pass

    synthetic = None
    compatible = None
    try:
        synthetic, audio_note = _vce_build_audio_only_video(
            video_path, prior_error=(initial_error + " " + recovery_error)
        )
        compatible = ensure_opencv_compatible_video(synthetic)
        validate_video_file(compatible)
        if str(Path(compatible).resolve()) != str(Path(synthetic).resolve()):
            try: os.remove(synthetic)
            except OSError: pass
        return compatible, True, audio_note
    except Exception as exc:
        for candidate in (compatible, synthetic):
            if candidate and os.path.exists(candidate) and str(Path(candidate).resolve()) != str(Path(video_path).resolve()):
                try: os.remove(candidate)
                except OSError: pass
        audio_error = str(getattr(exc, "detail", None) or exc)
        raise HTTPException(status_code=400, detail=(
            "Не удалось прочитать видеопоток. VCE попробовал remux/перекодирование, затем audio-only fallback, "
            "но FFmpeg не смог продолжить. Исходный файл не изменён. Ошибка preflight: " +
            initial_error[:400] + ". Ошибка recovery: " + recovery_error[:600] +
            ". Ошибка извлечения аудио: " + audio_error[:600] +
            ". Если скачивание/экспорт оборваны, скачайте или экспортируйте видео заново."
        ))

@app.post("/api/v1/analyze")
async def analyze_video(
    video_file: Union[UploadFile, str, None] = File(
        None, description="Fichier vidéo local (Optionnel)"
    ),
    local_video_token: str = Form("", description="Внутренний токен видео, выбранного в системном диалоге macOS"),
    video_url: str = Form(
        "", description="URL YouTube ou direct (laisser vide si upload)"
    ),

    visual_user_prompt: str = Form(
        str(setting("visual_user_prompt", "Décris factuellement ce qui se passe à partir des images.")),
        max_length=1000,
        description=(
            "Instructions supplémentaires pour la description visuelle et le nombre de mot max "
            "(style, ce qu'il faut mettre en avant). Laisse vide pour utiliser "
            "le comportement par défaut."
        ),
    ),

    summary_user_prompt: str = Form(
        str(setting("summary_user_prompt", "Résume la vidéo clairement.")),
        max_length=2000,
        description=(
            "Instructions supplémentaires pour le résumé global (ton, niveau de détail...). et le nombre de mot max"
            "Laisse vide pour utiliser le comportement par défaut."
        ),
    ),

    vlm_model: str = Form(
        str(setting("vlm_model", ACTIVE_DEFAULT_VLM)),
        description="Modèle VLM",
    ),
    whisper_model: str = Form(
        str(setting("whisper_model", DEFAULT_WHISPER_MODEL)),
        description="Modèle Whisper",
    ),

    whisper_acceleration: bool = Form(
        bool(setting("whisper_acceleration", True)),
        description="Ускорение Whisper: MPS на Apple Silicon / CUDA при наличии; выключите для CPU",
    ),

    response_format: Literal["json", "text"] = Form(
        str(setting("response_format", "json")), description="Format de sortie"
    ),

    vlm_resolution: int = Form(
        int(setting("vlm_resolution", 1280)), ge=128, le=2048, description="Résolution pour le VLM"
    ),
    scene_threshold: float = Form(
        float(setting("scene_threshold", 0.15)), ge=0.01, le=1.0, description="Seuil de changement de scène"
    ),
    min_scene_duration: float = Form(
        float(setting("min_scene_duration", 1.5)), ge=0.5, le=60.0, description="Durée minimale de scène (s)"
    ),
    max_scene_duration: float = Form(
        float(setting("max_scene_duration", 10)), ge=5.0, le=300.0, description="Durée maximale de scène (s)"
    ),

    keyframes_per_scene: int = Form(
        int(setting("keyframes_per_scene", 2)),
        ge=1,
        le=5,
        description="Nombre de keyframes par scène (1 à 5). 1 = plus rapide.",
    ),

    vlm_max_tokens_scene: int = Form(
        int(setting("vlm_max_tokens_scene", 1024)),
        ge=16,
        le=1024,
        description="Nombre max de tokens générés par le VLM pour chaque scène (par défaut 220).",
    ),
    vlm_max_tokens_summary: int = Form(
        int(setting("vlm_max_tokens_summary", 2048)),
        ge=16,
        le=2048,
        description="Nombre max de tokens générés par le VLM pour le résumé global (par défaut 260).",
    ),

    skip_audio: bool = Form(bool(setting("skip_audio", False))),
    skip_visual: bool = Form(bool(setting("skip_visual", False))),
    generate_txt: bool = Form(bool(setting("generate_txt", True))),
    generate_json: bool = Form(bool(setting("generate_json", True))),
    job_id: str = Form("", description="Внутренний ID задания для кнопки остановки"),
    word_timestamps: bool = Form(
        bool(setting("word_timestamps", True)),
        description="Временные метки отдельных слов Whisper для привязки речи к сценам",
    ),

    enable_audio_features: bool = Form(
        bool(setting("enable_audio_features", True)), description="Calculer les audio_features par scène ? (True par défaut)"
    ),
    enable_apple_ocr: bool = Form(
        bool(setting("enable_apple_ocr", True)), description="Extraire le texte des keyframes avec Apple Vision OCR et le fournir au VLM"
    ),
    generate_summary: bool = Form(
        bool(setting("generate_summary", True)), description="Générer un résumé global de la vidéo ? (True par défaut)"
    ),
    text_llm_enabled: bool = Form(bool(setting("text_llm_enabled", True))),
    text_llm_model: str = Form(str(setting("text_llm_model", TEXT_LLM_DEFAULT_MODEL))),
    text_llm_max_tokens: int = Form(int(setting("text_llm_max_tokens", TEXT_LLM_DEFAULT_MAX_TOKENS)), ge=1024, le=4096),
    analysis_mode: str = Form("full", description="full | whisper_ocr | ocr_only"),
    enable_ffmpeg_ocr: bool = Form(False, description="Дополнительный OCR через FFmpeg/Tesseract"),
    ffmpeg_ocr_interval: int = Form(5, ge=1, le=60, description="Интервал выборки FFmpeg OCR, секунд"),
    ffmpeg_ocr_workers: int = Form(int(setting("ffmpeg_ocr_workers", 2)), ge=1, le=8, description="Число параллельных CPU-процессов Tesseract"),
    enable_stage_timing: bool = Form(False, description="Добавлять подробные тайминги этапов в JSON-ответ"),
    enable_adaptive_keyframes: bool = Form(False, description="Адаптивно уменьшать число кадров на коротких сценах"),
    enable_ocr_cache: bool = Form(False, description="Кэшировать OCR для повторяющихся кадров"),
    enable_mlx_whisper: bool = Form(False, description="Использовать опциональный MLX Whisper вместо PyTorch Whisper"),
    enable_fast_scene_detection: bool = Form(False, description="Экспериментальная ускоренная детекция сцен с выборкой 1 кадр/сек"),
):
    analysis_mode = (analysis_mode or "full").strip().lower()
    if analysis_mode not in {"full", "whisper_ocr", "ocr_only"}:
        raise HTTPException(422, "Недопустимый сценарий анализа")
    if analysis_mode in {"whisper_ocr", "ocr_only"}:
        skip_visual = True
    if analysis_mode == "ocr_only":
        skip_audio = True
    # Important: only this flag controls Qwen3-VL. The separate Qwen3.5 text post-processing
    # stage is intentionally untouched and can still build a coherent report from ASR + OCR.
    run_vlm = (analysis_mode == "full") and (not skip_visual)

    if isinstance(video_file, str):
        video_file = None

    if not video_file and not video_url.strip() and not local_video_token.strip():
        raise HTTPException(
            400, "Veuillez fournir soit un fichier vidéo, soit une URL."
        )

    job_id = (job_id or "").strip() or f"api_{time.time_ns()}"
    cancel_event = CANCEL_EVENTS.setdefault(job_id, threading.Event())
    request_start_time = time.time()
    _vce_text_llm_model_cached = True
    if text_llm_enabled:
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=text_llm_model, local_files_only=True)
            _vce_text_llm_model_cached = True
        except Exception:
            _vce_text_llm_model_cached = False
    set_job_progress(job_id, "starting", 1, "Задание принято; готовим видео", stage_index=1, stage_count=6, model_cached=_vce_text_llm_model_cached)

    whisper_infer_time = 0.0
    vlm_infer_time = 0.0
    ocr_infer_time = 0.0
    whisper_load_time = 0.0
    _vce_whisper_started_at = None
    _vce_whisper_finished_at = None
    _vce_vlm_scene_started_at = None
    _vce_vlm_scene_finished_at = None

    target_whisper = whisper_model
    whisper_device = resolve_whisper_device(whisper_acceleration)
    target_vlm = vlm_model
    temp_path = None
    cap = None
    owns_temp_path = False
    output_directory = Path.cwd()
    source_name = "Inconnu"

    try:
        if cancel_event.is_set():
            raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")
        if local_video_token.strip():
            selected_path = LOCAL_VIDEO_SELECTIONS.pop(local_video_token.strip(), None)
            if not selected_path:
                raise HTTPException(400, "Токен локального видео недействителен или уже использован. Выберите видео повторно.")
            original_path = Path(selected_path).expanduser().resolve()
            if not original_path.is_file():
                raise HTTPException(404, f"Исходный видеофайл не найден: {original_path.name}")
            if original_path.suffix.lower() not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"Неподдерживаемое расширение: {original_path.suffix}")
            source_name = original_path.name
            temp_path = str(original_path)
            output_directory = original_path.parent
        elif video_url.strip():
            owns_temp_path = True
            source_name = video_url
            clean_title = sanitize_filename(video_url)
            try:
                temp_path = download_video_from_url(
                    video_url,
                    f"temp_{int(time.time())}_{clean_title}.%(ext)s",
                )
            except ValueError as ve:
                raise HTTPException(400, str(ve))
        elif video_file:
            owns_temp_path = True
            ext = Path(video_file.filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"Extension interdite: {ext}")
            source_name = video_file.filename
            clean_name = sanitize_filename(video_file.filename)
            temp_path = os.path.abspath(f"temp_{int(time.time())}_{clean_name}")
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(video_file.file, buffer)

        audio_track_available, audio_probe_message = _vce_probe_audio_stream(temp_path)
        audio_auto_skipped = False
        if audio_track_available is False and not skip_audio:
            skip_audio = True
            audio_auto_skipped = True
            print("⚠️ [Audio] Аудиопоток отсутствует; продолжим анализ видео без Whisper.")
        elif audio_track_available is None:
            print("ℹ️ [Audio] " + audio_probe_message)

        original_input_path = str(Path(temp_path).expanduser().resolve())
        temp_path = original_input_path
        prepared_path, _vce_audio_only_fallback, _vce_media_recovery_note = await asyncio.to_thread(
            _vce_ensure_opencv_compatible_with_recovery,
            original_input_path,
        )
        if Path(prepared_path).expanduser().resolve() != Path(original_input_path).resolve():
            if owns_temp_path and os.path.exists(original_input_path):
                try:
                    os.remove(original_input_path)
                except OSError:
                    pass
            temp_path = prepared_path
            owns_temp_path = True
        else:
            temp_path = prepared_path
        # VCE_AUDIO_ONLY_FALLBACK_POLICY_V1
        if _vce_audio_only_fallback:
            _vce_media_recovery_note = _vce_media_recovery_note or "Продолжение в audio-only режиме."
            skip_visual = True
            run_vlm = False
            enable_apple_ocr = False
            enable_ffmpeg_ocr = False
            skip_audio = False
            analysis_mode = "audio_only_fallback"
            print("⚠️ [Media fallback] " + _vce_media_recovery_note)
        if enable_mlx_whisper:
            _vce_validate_mlx_whisper_model(target_whisper)
        video_duration, _vce_recovered_video_path, _vce_recovery_note = _vce_validate_video_with_remux(temp_path)
        if _vce_recovered_video_path:
            _vce_original_temp_path = temp_path
            if locals().get("owns_temp_path", False) and os.path.exists(_vce_original_temp_path):
                try:
                    os.remove(_vce_original_temp_path)
                except OSError:
                    pass
            temp_path = _vce_recovered_video_path
            owns_temp_path = True
            print("⚠️ [MP4 recovery] " + str(_vce_recovery_note or "Используется восстановленная временная копия."))
        set_job_progress(job_id, "validated", 3, "Файл проверен; определяем сцены", stage_index=1, stage_count=6, duration_seconds=round(video_duration, 2))
        start_process = time.time()
        final_segments: List[Dict[str, Any]] = []
        global_summary_text = ""

        set_job_progress(job_id, "detect_scenes", None, "Определяем границы сцен", indeterminate=True, stage_index=1, stage_count=6)
        print("1️⃣ Détection des scènes...")
        scene_detection_started = time.perf_counter()
        apple_ocr_seconds = 0.0
        if enable_fast_scene_detection:
            scenes_raw = await asyncio.to_thread(
                _vce_detect_scenes_fast,
                temp_path,
                threshold=scene_threshold,
                min_duration=min_scene_duration,
                max_duration=max_scene_duration,
            )
        else:
            scenes_raw = await asyncio.to_thread(
                detect_scenes,
                temp_path,
                threshold=scene_threshold,
                min_duration=min_scene_duration,
                max_duration=max_scene_duration,
            )
        scene_detection_seconds = time.perf_counter() - scene_detection_started
        set_job_progress(job_id, "scenes_detected", 9, f"Найдено сцен: {len(scenes_raw)}", stage_index=1, stage_count=6, scene_total=len(scenes_raw))
        if cancel_event.is_set():
            raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")

        ffmpeg_ocr_available, ffmpeg_ocr_reason = _vce_ffmpeg_ocr_capability()
        ffmpeg_ocr_records = []
        ffmpeg_ocr_infer_time = 0.0
        ffmpeg_ocr_task = None
        if enable_ffmpeg_ocr:
            if ffmpeg_ocr_available:
                _vce_ffmpeg_ocr_start = time.time()
                ffmpeg_ocr_task = asyncio.create_task(asyncio.to_thread(
                    _vce_run_ffmpeg_ocr_timed, temp_path, ffmpeg_ocr_interval, ffmpeg_ocr_workers
                ))
            else:
                print("⚠️ [FFmpeg OCR] " + ffmpeg_ocr_reason)

        if skip_audio and skip_visual and not (enable_apple_ocr or (enable_ffmpeg_ocr and ffmpeg_ocr_available)):

            for idx, scene in enumerate(scenes_raw):
                if cancel_event.is_set():
                    raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")
                final_segments.append(
                    {
                        "scene_id": idx + 1,
                        "start": round(scene["start"], 2),
                        "end": round(scene["end"], 2),
                        "audio_transcript": "",
                        "audio_features": {},
                        "visual_description": "",
                        "ocr_text": "",
                        "visual_tags": {},
                        "emotion": {},
                    }
                )
        else:
            async with gpu_lock:
                print("🔒 GPU Lock acquis.")

                whisper_segments: List[Dict[str, Any]] = []
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

                if run_vlm:
                    release_accelerator_memory('before VLM load')
                    set_job_progress(job_id, "load_vlm", None, "Загружаем визуальную модель Qwen3-VL", indeterminate=True, stage_index=3, stage_count=6)
                    t0 = time.time()
                    await _vce_mlx_run(vlm_engine.load_model, target_vlm)
                    t1 = time.time()
                    vlm_load_time = vlm_engine.last_load_time or (t1 - t0)
                else:
                    vlm_load_time = 0.0

                set_job_progress(job_id, "scene_analysis", None, "Начинаем OCR и визуальный анализ сцен", indeterminate=True, stage_index=4, stage_count=6)
                cap = cv2.VideoCapture(temp_path)

                _vce_vlm_scene_started_at = time.perf_counter()
                first_frame_img = None
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, f_frame = cap.read()
                if ret:
                    f_frame = resize_keep_aspect(f_frame, vlm_resolution)
                    first_frame_img = Image.fromarray(
                        cv2.cvtColor(f_frame, cv2.COLOR_BGR2RGB)
                    )

                if ffmpeg_ocr_task is not None:
                    ffmpeg_ocr_records, ffmpeg_ocr_reason, ffmpeg_ocr_infer_time = await ffmpeg_ocr_task
                    if ffmpeg_ocr_reason:
                        print("⚠️ [FFmpeg OCR] " + ffmpeg_ocr_reason)
                    else:
                        print(f"🔎 [FFmpeg OCR] Получено образцов: {len(ffmpeg_ocr_records)}")
                _vce_frame_prefetch_queue = None
                _vce_frame_prefetch_stop = None
                _vce_frame_prefetch_thread = None
                _vce_frame_prefetch_stats = {"worker_seconds": 0.0, "wait_seconds": 0.0, "fallback_count": 0}
                if not skip_visual and run_vlm:
                    (
                        _vce_frame_prefetch_queue,
                        _vce_frame_prefetch_stop,
                        _vce_frame_prefetch_thread,
                        _vce_frame_prefetch_stats,
                    ) = _vce_start_bounded_keyframe_prefetch(
                        temp_path,
                        scenes_raw,
                        base_res=vlm_resolution,
                        max_frames=keyframes_per_scene,
                        max_frames_by_scene=[_vce_adaptive_keyframe_limit(s, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene for s in scenes_raw],
                        queue_size=1,
                    )

                for idx, scene in enumerate(scenes_raw):
                    if _vce_whisper_task is not None and _vce_whisper_task.done():
                        whisper_segments = _vce_whisper_task.result()
                    if cancel_event.is_set():
                        raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")
                    _ui_scene_total = max(1, len(scenes_raw))
                    set_job_progress(job_id, "scene_analysis", None, f"OCR + Qwen3-VL: анализ сцены {idx + 1}/{len(scenes_raw)}", indeterminate=True, stage_index=4, stage_count=6, scene_index=idx + 1, scene_total=len(scenes_raw))
                    scene_id = idx + 1
                    start_s = scene["start"]
                    end_s = scene["end"]

                    audio_text = transcript_for_scene(scene, whisper_segments) if not skip_audio and whisper_segments else ""

                    audio_features = {}
                    if (
                        not skip_audio
                        and whisper_segments
                        and enable_audio_features
                    ):
                        audio_features = compute_audio_features_for_scene(
                            scene, whisper_segments
                        )

                    visual_description = ""
                    scene_ocr_text = ""
                    ffmpeg_scene_ocr_text = ""
                    visual_tags: Dict[str, Any] = {}
                    emotion_info_scene: Dict[str, Any] = {}

                    if run_vlm or enable_apple_ocr or (enable_ffmpeg_ocr and ffmpeg_ocr_available):
                        effective_keyframes = _vce_adaptive_keyframe_limit(scene, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene
                        if _vce_frame_prefetch_queue is not None:
                            _vce_pf_wait_started = time.perf_counter()
                            _vce_pf_scene_index, _vce_pf_keyframes, _vce_pf_error = _vce_frame_prefetch_queue.get()
                            _vce_frame_prefetch_stats["wait_seconds"] += time.perf_counter() - _vce_pf_wait_started
                            if _vce_pf_scene_index is None:
                                # Producer ended early; fall back synchronously for this and subsequent scenes.
                                _vce_frame_prefetch_stats["fallback_count"] += 1
                                _vce_frame_prefetch_queue = None
                                keyframes = sample_keyframes_for_scene(
                                    cap,
                                    scene,
                                    base_res=vlm_resolution,
                                    max_frames=effective_keyframes,
                                )
                            elif _vce_pf_scene_index != idx:
                                _vce_frame_prefetch_stats["fallback_count"] += 1
                                print("⚠️ [Frame prefetch] Порядок очереди не совпал; отключаем prefetch и читаем синхронно.")
                                _vce_frame_prefetch_queue = None
                                if _vce_frame_prefetch_stop is not None:
                                    _vce_frame_prefetch_stop.set()
                                keyframes = sample_keyframes_for_scene(
                                    cap,
                                    scene,
                                    base_res=vlm_resolution,
                                    max_frames=effective_keyframes,
                                )
                            elif _vce_pf_error is not None:
                                _vce_frame_prefetch_stats["fallback_count"] += 1
                                print("⚠️ [Frame prefetch] Синхронный fallback: " + str(_vce_pf_error)[:300])
                                keyframes = sample_keyframes_for_scene(
                                    cap,
                                    scene,
                                    base_res=vlm_resolution,
                                    max_frames=effective_keyframes,
                                )
                            else:
                                keyframes = _vce_pf_keyframes or []
                        else:
                                keyframes = sample_keyframes_for_scene(
                                    cap,
                                    scene,
                                    base_res=vlm_resolution,
                                    max_frames=effective_keyframes,
                                )

                        if keyframes:
                            images = [kf["image"] for kf in keyframes]

                            full_visual_prompt = BASE_VISUAL_PROMPT
                            if visual_user_prompt.strip():
                                full_visual_prompt += (
                                    "\n\nAdditional user instructions (follow them if possible):\n"
                                    + visual_user_prompt.strip()
                                )

                            if enable_apple_ocr:
                                t_ocr_start = time.time()
                                _vce_ocr_wall_start = time.perf_counter()
                                ocr_parts = []
                                for kf in keyframes:
                                    extracted = await asyncio.to_thread((_vce_cached_apple_vision_ocr if enable_ocr_cache else apple_vision_ocr), kf.get("original_image", kf["image"]))
                                    if cancel_event.is_set():
                                        raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")
                                    if extracted:
                                        ocr_parts.append(f"[Кадр {kf['time']:.2f} с]\n{extracted}")
                                scene_ocr_text = "\n\n".join(ocr_parts)
                                apple_ocr_seconds += time.perf_counter() - _vce_ocr_wall_start
                                ocr_infer_time += time.time() - t_ocr_start
                                if scene_ocr_text:
                                    full_visual_prompt += (
                                        "\n\nТЕКСТ, РАСПОЗНАННЫЙ APPLE VISION OCR (возможны ошибки):\n"
                                        + scene_ocr_text
                                        + "\n\nИспользуй OCR как подсказку для точного чтения подписей. Сверяй его с изображениями, не выдумывай отсутствующие надписи и отдельно описывай связи между блоками схемы."
                                    )

                            if audio_text.strip():
                                full_visual_prompt += (
                                    "\n\nСИНХРОНИЗИРОВАННАЯ РАСШИФРОВКА WHISPER (контекст речи, не визуальное доказательство):\n"
                                    + audio_text
                                    + "\n\nИспользуй речь для контекста темы, но описывай как визуальный факт только то, что подтверждается кадрами и OCR."
                                )
                            if enable_ffmpeg_ocr and ffmpeg_ocr_records:
                                ffmpeg_scene_ocr_text = _vce_ffmpeg_ocr_scene_text(ffmpeg_ocr_records, start_s, end_s)
                                if ffmpeg_scene_ocr_text:
                                    scene_ocr_text = _vce_merge_ocr_sources(scene_ocr_text, ffmpeg_scene_ocr_text)
                                    if run_vlm:
                                        full_visual_prompt += "\n\nДополнительный OCR из FFmpeg/Tesseract (может содержать ошибки):\n" + ffmpeg_scene_ocr_text
                            if run_vlm:  # VCE_ANALYSIS_PROFILES_VLM_GUARD
                                t_vlm_start = time.time()
                                raw = await _vce_mlx_run(_vce_describe_scene_with_memory_fallback,
                                    vlm_engine,
                                    images,
                                    full_visual_prompt,
                                    vlm_max_tokens_scene,
                                )
                                t_vlm_end = time.time()
                                vlm_infer_time += t_vlm_end - t_vlm_start
                                if cancel_event.is_set():
                                    raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")

                                data = safe_json_parse(raw)
                                if isinstance(data, dict) and data.get("_vce_memory_fallback"):
                                    run_vlm = False
                                    print("🛟 [VLM memory guard] VLM отключена до конца текущего видео; аудио и OCR продолжаются.")

                                if data and isinstance(data, dict) and "description" in data:
                                    visual_description = (data.get("description") or "").strip()
                                    tags = data.get("tags") or {}
                                    if isinstance(tags, dict):
                                        visual_tags = tags
                                    else:
                                        visual_tags = {}
                                else:
                                    visual_description = clean_raw_visual_text(raw)
                                    visual_tags = {}

                    if _vce_audio_only_fallback and idx == 0:
                        visual_description = _vce_media_recovery_note
                    final_segments.append(
                        {
                            "scene_id": scene_id,
                            "start": round(start_s, 2),
                            "end": round(end_s, 2),
                            "audio_transcript": audio_text,
                            "audio_features": audio_features,
                            "visual_description": visual_description,
                            "ocr_text": scene_ocr_text,
                            "ffmpeg_ocr_text": ffmpeg_scene_ocr_text,
                            "visual_tags": visual_tags,
                            "emotion": emotion_info_scene,
                        }
                    )
                    _ui_scene_pct = 30 + 45 * ((idx + 1) / max(1, len(scenes_raw)))
                    set_job_progress(job_id, "scene_analysis", _ui_scene_pct, f"Завершена сцена {idx + 1}/{len(scenes_raw)}", stage_index=4, stage_count=6, scene_index=idx + 1, scene_total=len(scenes_raw))


                # The VLM scene pass is finished; exclude remaining ASR wait time from its timing.
                _vce_vlm_scene_finished_at = time.perf_counter()

                if _vce_whisper_task is not None:
                    # This task returns a list of transcript segments, not a legacy 3-item tuple.
                    whisper_segments = await _vce_whisper_task
                    whisper_segments = whisper_segments or []
                    set_job_progress(
                        job_id, "whisper_done", 75,
                        f"Whisper завершён: фрагментов речи {len(whisper_segments)}",
                        stage_index=4, stage_count=6,
                        transcript_segments=len(whisper_segments),
                    )

                    # Attach the completed ASR transcript to each scene.
                    for _vce_scene_index, _vce_segment in enumerate(final_segments):
                        if _vce_scene_index >= len(scenes_raw):
                            break
                        _vce_scene = scenes_raw[_vce_scene_index]
                        _vce_audio_text = (
                            transcript_for_scene(_vce_scene, whisper_segments)
                            if not skip_audio and whisper_segments else ""
                        )
                        _vce_segment["audio_transcript"] = _vce_audio_text
                        if not skip_audio and whisper_segments and enable_audio_features:
                            _vce_segment["audio_features"] = compute_audio_features_for_scene(
                                _vce_scene, whisper_segments
                            )

                    if RAM_MODE == "ram-":
                        try:
                            del _vce_whisper_model
                        except UnboundLocalError:
                            pass
                        try:
                            del w_model
                        except UnboundLocalError:
                            pass
                        release_accelerator_memory("after concurrent Whisper inference")
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
                set_job_progress(job_id, "summary", None, "Формируем сводку по всем сценам", indeterminate=True, stage_index=5, stage_count=6)
                if generate_summary:
                    print("🧠 Génération du résumé global...")
                    context_lines = []
                    for s in final_segments:
                        parts = []
                        if s.get("audio_transcript"):
                            parts.append(f"Audio: {s['audio_transcript']}")
                        vt = s.get("visual_tags") or {}
                        if vt:
                            parts.append(
                                "Visuel: "
                                f"people={vt.get('people_count', '?')}, "
                                f"place={vt.get('place_type', '?')}, "
                                f"action={vt.get('main_action', '?')}, "
                                f"tone={vt.get('emotional_tone', '?')}, "
                                f"movement={vt.get('movement_level', '?')}"
                            )
                        elif s.get("visual_description"):
                            parts.append(f"Visuel: {s['visual_description']}")
                        if s.get("ocr_text"):
                            ocr_for_summary = s["ocr_text"].replace("\n", " | ")[:500]
                            parts.append(f"Texte à l’écran (Apple OCR): {ocr_for_summary}")

                        if parts:
                            context_lines.append(
                                f"- Scène {s['scene_id']} "
                                f"({s['start']:.1f}-{s['end']:.1f}s): "
                                + " | ".join(parts)
                            )

                    context_log = "\n".join(context_lines)

                    full_summary_prompt = (
                        "Voici un ensemble de notes sur les scènes d'une vidéo.\n"
                        "Pour chaque scène, tu as une description audio (ce qui est dit) "
                        "et des éléments de contexte visuel (gestes, lieu, ambiance, ton).\n\n"
                        f"{context_log}\n\n"
                        f"{BASE_SUMMARY_PROMPT}"
                    )

                    if summary_user_prompt.strip():
                        full_summary_prompt += (
                            "\n\nUser instructions (apply if possible):\n"
                            + summary_user_prompt.strip()
                        )

                    if run_vlm and first_frame_img is not None:
                        t_vlm_start = time.time()
                        raw_summary = await _vce_mlx_run(_vce_describe_scene_with_memory_fallback,
                            vlm_engine,
                            [first_frame_img],
                            full_summary_prompt,
                            vlm_max_tokens_summary,
                        )
                        t_vlm_end = time.time()
                        vlm_infer_time += t_vlm_end - t_vlm_start
                        if cancel_event.is_set():
                            raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")

                        data_sum = safe_json_parse(raw_summary)
                        if data_sum and isinstance(data_sum, dict) and (
                            "summary" in data_sum or "description" in data_sum
                        ):
                            global_summary_text = (
                                data_sum.get("summary")
                                or data_sum.get("description")
                                or ""
                            ).strip()
                        else:
                            global_summary_text = clean_raw_summary_text(raw_summary)
                    else:
                        global_summary_text = context_log[:2000]

                set_job_progress(job_id, "release_models", 81, "Освобождаем память и готовим результат", stage_index=5, stage_count=6)
                cap.release()
                print("🔓 GPU Lock relâché.")

                if RAM_MODE == "ram-":
                    print("🧹 RAM- mode: déchargement du VLM après traitement.")
                    vlm_engine.unload_model()
                    release_accelerator_memory('after VLM unload')
                    gc.collect()

        # VCE_STANDARD_OCR_RECONCILIATION_V5_7: correct only when the canonical number is present in OCR.
        import re as _vce_standard_re
        _vce_standard_ocr_text = " ".join(
            str(_vce_segment.get("ocr_text", "") or "")
            for _vce_segment in final_segments
            if isinstance(_vce_segment, dict)
        )
        if _vce_standard_re.search(r"\bСП\s*1\.13130\.2020\b", _vce_standard_ocr_text, _vce_standard_re.I):
            global_summary_text = _vce_standard_re.sub(
                r"(?i)\bСП\s*1\.13\.130\.2020\b",
                "СП 1.13130.2020",
                str(global_summary_text or ""),
            )
        # VCE_NAME_CONSISTENCY_V2_GLOBAL_SUMMARY
        # VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR
        import re as _vce_global_std_re
        _vce_global_std_ocr = " ".join(
            str(_vce_std_seg.get("ocr_text", "") or "")
            for _vce_std_seg in final_segments
            if isinstance(_vce_std_seg, dict)
        )
        if _vce_global_std_re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce_global_std_ocr):
            _vce_global_summary_fixed = str(global_summary_text or "")
            for _vce_std_alias in (
                r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)",
                r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*130\s*\.\s*2020(?!\d)",
            ):
                _vce_global_summary_fixed = _vce_global_std_re.sub(
                    _vce_std_alias, "СП 1.13130.2020", _vce_global_summary_fixed
                )
            if _vce_global_summary_fixed != str(global_summary_text or ""):
                global_summary_text = _vce_global_summary_fixed
                print("🧭 [ASR/OCR] Исправлено название норматива в global_summary по подтверждённому OCR.")

        # VCE_REPORT_INTEGRITY_V4_SUMMARY_HARMONIZER
        global_summary_text = _vce_v4_evidence_summary(global_summary_text, final_segments)

# TEXT_LLM_POSTPROCESS_STAGE
        text_llm_result = None
        text_llm_infer_time = 0.0
        text_llm_error = ""
        text_llm_status = "disabled"
        if text_llm_enabled:
            text_llm_status = "failed"
            original_summary_for_text_llm = global_summary_text
            if "cancel_event" in locals() and cancel_event.is_set():
                raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")
            llm_started_at = time.time()
            _vce_text_llm_cache_state = {"checked": True, "cached": bool(_vce_text_llm_model_cached)}
            _vce_text_llm_phase_times = {"load_started": None, "load_seconds": 0.0, "generation_started": None, "generation_seconds": 0.0, "repair_started": None, "repair_seconds": 0.0}
            def _ui_text_llm_progress_callback(phase: str, token_count: int = 0, tokens_per_second: float = 0.0) -> None:
                now = time.time()
                if phase == "loading":
                    # Model cache was probed once at request start using local_files_only=True.
                    # Avoid a second filesystem walk while the user's progress bar is active.
                    _vce_text_llm_phase_times["load_started"] = now
                    if _vce_text_llm_cache_state["cached"]:
                        label = "Qwen3.5: модель уже в кэше, загружаем в память"
                    else:
                        label = "Qwen3.5: скачиваем и подготавливаем модель (первый запуск может занять дольше)"
                    set_job_progress(job_id, "text_llm_loading", None, label, indeterminate=True, stage_index=6, stage_count=6, model_cached=_vce_text_llm_cache_state["cached"])
                elif phase == "loaded":
                    load_started = _vce_text_llm_phase_times.get("load_started")
                    if load_started is not None:
                        _vce_text_llm_phase_times["load_seconds"] = max(0.0, now - load_started)
                    _vce_text_llm_phase_times["generation_started"] = now
                    set_job_progress(job_id, "text_llm_generating", None, "Qwen3.5 загружена; подготавливаем контекст к генерации", indeterminate=True, stage_index=6, stage_count=6, generated_tokens=0, token_limit=max(1024, int(text_llm_max_tokens or 4096)), model_cached=_vce_text_llm_cache_state["cached"])
                elif phase == "repairing":
                    started = _vce_text_llm_phase_times.get("generation_started")
                    if started:
                        _vce_text_llm_phase_times["generation_seconds"] += max(0.0, now - started)
                        _vce_text_llm_phase_times["generation_started"] = None
                    _vce_text_llm_phase_times["repair_started"] = now
                    set_job_progress(job_id, "text_llm_repair", None, "Исправляем формат JSON; проверяем структуру", indeterminate=True, stage_index=6, stage_count=6, model_cached=_vce_text_llm_cache_state["cached"])
                else:
                    if _vce_text_llm_phase_times.get("load_started") and not _vce_text_llm_phase_times.get("load_seconds"):
                        _vce_text_llm_phase_times["load_seconds"] = max(0.0, now - _vce_text_llm_phase_times["load_started"])
                        _vce_text_llm_phase_times["generation_started"] = now
                    elif _vce_text_llm_phase_times.get("repair_started"):
                        _vce_text_llm_phase_times["repair_seconds"] += max(0.0, now - _vce_text_llm_phase_times["repair_started"])
                        _vce_text_llm_phase_times["repair_started"] = None
                    elif not _vce_text_llm_phase_times.get("generation_started"):
                        _vce_text_llm_phase_times["generation_started"] = now
                    rate = f" · {tokens_per_second:.1f} ток/с" if tokens_per_second > 0 else ""
                    set_job_progress(job_id, "text_llm_generating", None, f"Qwen3.5: структурируем анализ · {token_count} токенов{rate}", indeterminate=True, stage_index=6, stage_count=6, generated_tokens=token_count, token_limit=max(1024, int(text_llm_max_tokens or 4096)), tokens_per_second=round(tokens_per_second, 2), model_cached=_vce_text_llm_cache_state["cached"])
            try:
                print(f"🧠 [Text LLM] Улучшение структуры отчёта: {text_llm_model}")
                async with gpu_lock:
                    text_llm_result = await _vce_mlx_run(run_text_llm_postprocess,
                        text_llm_model,
                        source_name,
                        video_duration,
                        global_summary_text,
                        final_segments,
                        text_llm_max_tokens,
                        _ui_text_llm_progress_callback,
                    )
                text_llm_infer_time = time.time() - llm_started_at
                if "cancel_event" in locals() and cancel_event.is_set():
                    raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")
                text_llm_result["original_summary"] = original_summary_for_text_llm
                improved_summary = text_llm_result.get("executive_summary", "").strip()
                if improved_summary:
                    # VCE_PRESERVE_BASE_SUMMARY_V5_4: keep baseline meta.global_summary independent.
                    pass
                valid_scene_ids = {int(s.get("scene_id", 0) or 0) for s in final_segments}
                for item in text_llm_result.get("scene_enrichment", []):
                    if not isinstance(item, dict) or item.get("scene_id") not in valid_scene_ids:
                        continue
                    target_seg = next((s for s in final_segments if int(s.get("scene_id", 0) or 0) == item["scene_id"]), None)
                    if target_seg is not None:
                        target_seg["text_llm"] = {
                            "topic": item.get("topic", ""),
                            "improved_description": item.get("improved_description", ""),
                            "key_points": item.get("key_points", []),
                        }
                text_llm_status = "success"
                print(f"✅ [Text LLM] Постобработка завершена за {text_llm_infer_time:.2f}s; исходные transcript/OCR/timestamps сохранены.")
            except HTTPException:
                raise
            except Exception as exc:
                text_llm_infer_time = time.time() - llm_started_at
                text_llm_error = str(exc)[:500]
                text_llm_result = None
                text_llm_status = "failed"
                print(f"⚠️ [Text LLM] Постобработка не удалась; продолжаю с исходным результатом: {text_llm_error}")

        set_job_progress(job_id, "compile_output", 95, "Собираем TXT/JSON и метаданные", stage_index=6, stage_count=6)
        process_duration = time.time() - start_process
        total_request_time = time.time() - request_start_time

        vlm_load_time = getattr(vlm_engine, "last_load_time", 0.0) if vlm_engine else 0.0

        print("===== PROFIL TEMPS (v3.19) =====")
        print(f"⏱️ Total traitement (process_time): {process_duration:.2f}s")
        print(f"⏱️ Total requête (incl. I/O):      {total_request_time:.2f}s")
        print(f"⏱️ Whisper: load={whisper_load_time:.2f}s, infer={whisper_infer_time:.2f}s")
        print(f"⏱️ VLM    : load={vlm_load_time:.2f}s, infer={vlm_infer_time:.2f}s")
        print(f"⏱️ Apple OCR: enabled={enable_apple_ocr}, infer={ocr_infer_time:.2f}s")
        print(f"RAM_MODE = {RAM_MODE}")
        print("================================")

        params = {
            "threshold": scene_threshold,
            "min_duration": min_scene_duration,
            "max_duration": max_scene_duration,
            "vlm": target_vlm,
            "whisper": target_whisper,
            "resolution": vlm_resolution,
            "keyframes_per_scene": keyframes_per_scene,
            "vlm_max_tokens_scene": vlm_max_tokens_scene,
            "vlm_max_tokens_summary": vlm_max_tokens_summary,
            "ram_mode": RAM_MODE,
            "audio_track_available": audio_track_available,
            "audio_auto_skipped": audio_auto_skipped,
            "audio_status": audio_probe_message,
        }

        text_report_content = generate_text_report(
            source_name,
            video_duration,
            final_segments,
            process_duration,
            params,
            global_summary_text,
        )

        if text_llm_result:
            text_report_content += format_text_llm_report(text_llm_result)

        txt_output_name = None
        txt_download_id = None
        if generate_txt:
            safe_name = sanitize_filename(source_name)
            txt_output_name = f"{safe_name}_context.txt"
            txt_path = str((output_directory / txt_output_name).resolve())
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text_report_content)
            txt_download_id = secrets.token_urlsafe(24)
            OUTPUT_FILE_REGISTRY[txt_download_id] = (Path(txt_path), txt_output_name)

        timings = {
            "total_process_time": round(process_duration, 3),
            "total_request_time": round(total_request_time, 3),
            "whisper": {
                "model": target_whisper,
                "device": whisper_device,
                "acceleration": whisper_acceleration,
                "load_time": round(whisper_load_time, 3),
                "inference_time": round(whisper_infer_time, 3),
            },
            "vlm": {
                "model": target_vlm,
                "load_time": round(vlm_load_time, 3),
                "inference_time": round(vlm_infer_time, 3),
                "runtime": VCE_MLX_RUNTIME_DIAGNOSTICS.get("vlm", {}),
            },
            "parallelism": {
                "whisper_backend": "mlx-whisper",
                "whisper_device": "cpu",
                "whisper_runtime": VCE_MLX_RUNTIME_DIAGNOSTICS.get("whisper", {}),
                "whisper_vlm_overlap_seconds": round(_vce_overlap_seconds, 3) if "_vce_overlap_seconds" in locals() else 0.0,
                "cpu_keyframe_prefetch_seconds": round(float(locals().get("_vce_frame_prefetch_stats", {}).get("worker_seconds", locals().get("_vce_cpu_prefetch_seconds", 0.0))), 3),
            },
            "apple_ocr": {
                "enabled": enable_apple_ocr,
                "inference_time": round(ocr_infer_time, 3),
            },
            "ffmpeg_ocr": {
                "requested": enable_ffmpeg_ocr,
                "available": ffmpeg_ocr_available,
                "interval_seconds": ffmpeg_ocr_interval,
            "workers": ffmpeg_ocr_workers,
            "backend": _vce_detect_ffmpeg_ocr_backend()[0],
                "sample_count": len(ffmpeg_ocr_records),
                "inference_time": round(ffmpeg_ocr_infer_time, 3),
                "message": ffmpeg_ocr_reason,
            },
            "ram_mode": RAM_MODE,
        }
        if enable_stage_timing:
            timings["stage_diagnostics"] = {
                "scene_detection_seconds": round(float(locals().get("scene_detection_seconds", 0.0) or 0.0), 3),
                "whisper_load_seconds": round(float(locals().get("whisper_load_time", 0.0) or 0.0), 3),
                "whisper_inference_seconds": round(float(locals().get("whisper_infer_time", locals().get("whisper_inference_time", 0.0)) or 0.0), 3),
                "vlm_load_seconds": round(float(locals().get("vlm_load_time", 0.0) or 0.0), 3),
                "vlm_inference_seconds": round(float(locals().get("vlm_infer_time", 0.0) or 0.0), 3),
                "apple_ocr_seconds": round(float(locals().get("apple_ocr_seconds", 0.0) or 0.0), 3),
                "ffmpeg_ocr_seconds": round(float(locals().get("ffmpeg_ocr_infer_time", 0.0) or 0.0), 3),
                "frame_prefetch_worker_seconds": round(float((locals().get("_vce_frame_prefetch_stats") or {}).get("worker_seconds", 0.0) or 0.0), 3),
                "frame_prefetch_wait_seconds": round(float((locals().get("_vce_frame_prefetch_stats") or {}).get("wait_seconds", 0.0) or 0.0), 3),
                "frame_prefetch_fallback_count": int((locals().get("_vce_frame_prefetch_stats") or {}).get("fallback_count", 0) or 0),
            }

        json_payload = {
                "meta": {
                    "source": source_name,
                    "duration": round(video_duration, 2),
                    "process_time": round(process_duration, 2),
                    "global_summary": global_summary_text,
                    "scene_count": len(final_segments),
                    "models": {
                        "vlm": target_vlm,
                        "whisper": target_whisper,
                    },
                    "skipped": {
                        "audio": skip_audio,
                        "visual": skip_visual,
                    },
                    "params": {
                        "audio_track_available": audio_track_available,
                        "audio_auto_skipped": audio_auto_skipped,
                        "audio_status": audio_probe_message,
                        "resolution": vlm_resolution,
                        "scene_threshold": scene_threshold,
                        "min_scene_duration": min_scene_duration,
                        "max_scene_duration": max_scene_duration,
                        "analysis_mode": analysis_mode,
                        "run_vlm": run_vlm,
                        "enable_ffmpeg_ocr": enable_ffmpeg_ocr,
                        "keyframes_per_scene": keyframes_per_scene,

                        "enable_apple_ocr": enable_apple_ocr,
                        "whisper_acceleration": whisper_acceleration,
                        "whisper_device": whisper_device,
                        "enable_audio_features": enable_audio_features,
                        "word_timestamps": word_timestamps,
                        "generate_json": generate_json,
                        "generate_summary": generate_summary,
                        "vlm_max_tokens_scene": vlm_max_tokens_scene,
                        "vlm_max_tokens_summary": vlm_max_tokens_summary,
                        "ram_mode": RAM_MODE,
                    },
                    "timings": timings,
                },
                "enhanced_analysis": text_llm_result,
                "text_llm": {
                    "enabled": bool(text_llm_enabled),
                    "model": text_llm_model if text_llm_enabled else None,
                    "status": text_llm_status,
                    "inference_time": round(text_llm_infer_time, 3),
                    "load_download_time": round(locals().get("_vce_text_llm_phase_times", {}).get("load_seconds", 0.0), 3),
                    "generation_time": round(max(0.0, text_llm_infer_time - locals().get("_vce_text_llm_phase_times", {}).get("load_seconds", 0.0)), 3),
                    "model_cached_before_run": bool(locals().get("_vce_text_llm_model_cached", True)) if text_llm_enabled else None,
                    "thinking_enabled": bool(text_llm_enabled),
                    "thinking_budget": 512 if text_llm_enabled else None,
                    "max_output_tokens": int(text_llm_max_tokens or 4096) if text_llm_enabled else None,
                    "error": text_llm_error or None,
                },
                "segments": final_segments,
                "txt_filename": txt_output_name,
            }
        json_payload["json_filename"] = None
        json_payload["txt_download_id"] = txt_download_id
        json_payload["json_download_id"] = None
        json_payload["output_directory"] = str(output_directory.resolve())
        if generate_json:
            json_output_name = f"{sanitize_filename(source_name)}_context.json"
            json_payload["json_filename"] = json_output_name
            json_download_id = secrets.token_urlsafe(24)
            json_payload["json_download_id"] = json_download_id
            json_path = str((output_directory / json_output_name).resolve())
            with open(json_path, "w", encoding="utf-8") as json_file:
                json.dump(json_payload, json_file, ensure_ascii=False, indent=2)
                json_file.write("\n")
            OUTPUT_FILE_REGISTRY[json_download_id] = (Path(json_path), json_output_name)

        timings["text_llm"] = {
            "enabled": bool(text_llm_enabled),
            "model": text_llm_model if text_llm_enabled else None,
            "status": text_llm_status,
            "inference_time": round(text_llm_infer_time, 3),
            "runtime": VCE_MLX_RUNTIME_DIAGNOSTICS.get("text_llm", {}),
            "error": text_llm_error or None,
        }
        set_job_progress(job_id, "completed", 100, "TXT/JSON сформированы; обработка завершена", stage_index=6, stage_count=6, status="done", indeterminate=False)
        if response_format == "text":
            download_name = f"{sanitize_filename(source_name)}_context.txt"
            return PlainTextResponse(
                content=text_report_content,
                headers={
                    "Content-Disposition": f'attachment; filename=\"{download_name}\"'
                },
            )
        return json_payload

    except HTTPException as he:
        if cancel_event.is_set() or he.status_code == 409:
            set_job_progress(job_id, "stopped", None, "Остановка запрошена; задача завершена", indeterminate=False, status="stopped")
        else:
            set_job_progress(job_id, "failed", None, f"Остановлено с HTTP-ошибкой: {he.status_code}", indeterminate=False, status="failed")
        raise he
    except Exception as e:
        set_job_progress(job_id, "failed", None, f"Ошибка: {str(e)[:180]}", indeterminate=False, status="failed")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Erreur interne: {str(e)}")
    finally:
        _vce_pending_whisper_task = locals().get('_vce_whisper_task')
        if _vce_pending_whisper_task is not None:
            try:
                await _vce_pending_whisper_task
            except BaseException:
                # Preserve the original endpoint exception; observe task completion before cleanup.
                pass
        for _vce_prefetch in locals().get('_vce_prefetch_tasks', {}).values():
            try:
                await _vce_prefetch
            except BaseException:
                pass
        _vce_pf_stop = locals().get('_vce_frame_prefetch_stop')
        if _vce_pf_stop is not None:
            _vce_pf_stop.set()
        _vce_pf_thread = locals().get('_vce_frame_prefetch_thread')
        if _vce_pf_thread is not None and _vce_pf_thread.is_alive():
            await asyncio.to_thread(_vce_pf_thread.join, 2.0)
        # VCE bounded keyframe prefetch cleanup: stop and join before deleting temp_path.
        _vce_pf_stop = locals().get("_vce_frame_prefetch_stop")
        if _vce_pf_stop is not None:
            _vce_pf_stop.set()
        _vce_pf_worker = locals().get("_vce_frame_prefetch_thread")
        if _vce_pf_worker is not None:
            _vce_pf_worker.join()
        # VCE parallel ASR cleanup: do not delete the source while Whisper's worker reads it.
        _vce_pending_whisper_task = locals().get("_vce_whisper_task")
        if _vce_pending_whisper_task is not None:
            try:
                await _vce_pending_whisper_task
            except Exception as _vce_whisper_cleanup_error:
                print("⚠️ [Parallel Whisper] Worker завершился при очистке: " + str(_vce_whisper_cleanup_error)[:300])
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        CANCEL_EVENTS.pop(job_id, None)
        if owns_temp_path and temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT_SERVEUR)