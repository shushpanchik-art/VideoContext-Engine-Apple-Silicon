#!/usr/bin/env python3
"""Strict MLX/Metal runtime helpers for VCE's Apple Silicon pipeline.

Call configure_mlx_gpu() in every process that loads an MLX model, before
loading the model. Keep Whisper on CPU explicitly and use
run_whisper_cpu_parallel_with_gpu() around the CPU ASR and GPU visual stages.
"""
from __future__ import annotations

import platform
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from typing import Any, Callable


def configure_mlx_gpu() -> dict[str, Any]:
    """Select MLX GPU, execute a small operation, and return diagnostics.

    Raises instead of silently falling back to CPU. On macOS arm64, MLX's
    GPU backend is Metal.
    """
    system = platform.system()
    machine = platform.machine().lower()
    if system != "Darwin" or machine not in {"arm64", "aarch64"}:
        raise RuntimeError(
            f"MLX/Metal requires native Apple Silicon macOS; got {system}/{machine}. "
            "Use a native arm64 Python interpreter, not Rosetta."
        )

    try:
        import mlx.core as mx
    except ImportError as exc:
        raise RuntimeError(
            "MLX is not installed in this Python environment. Install mlx in the "
            "same environment that loads the VLM/LLM."
        ) from exc

    if not mx.is_available(mx.gpu):
        raise RuntimeError("MLX GPU backend is unavailable; refusing a CPU fallback.")

    mx.set_default_device(mx.gpu)
    if mx.default_device() != mx.gpu:
        raise RuntimeError(f"Could not select MLX GPU; default is {mx.default_device()!s}")

    probe = mx.array([1.0, 2.0, 3.0]) * 2
    mx.eval(probe)
    mx.synchronize(mx.gpu)
    if [float(x) for x in probe.tolist()] != [2.0, 4.0, 6.0]:
        raise RuntimeError("MLX GPU smoke operation returned an unexpected result.")

    try:
        mlx_version = metadata.version("mlx")
    except metadata.PackageNotFoundError:
        mlx_version = "unknown"

    try:
        device_info = {str(k): str(v) for k, v in mx.device_info(mx.gpu).items()}
    except Exception:
        device_info = {}

    return {
        "backend": "MLX",
        "accelerator": "Metal",
        "platform": system,
        "architecture": machine,
        "mlx_version": mlx_version,
        "gpu_available": True,
        "default_device": str(mx.default_device()),
        "device_info": device_info,
        "gpu_smoke_test": "passed",
    }


def load_whisper_cpu(whisper_module: Any, model_name: str, **kwargs: Any) -> Any:
    """Load OpenAI Whisper explicitly on CPU and reject unexpected placement."""
    kwargs.pop("device", None)
    model = whisper_module.load_model(model_name, device="cpu", **kwargs)
    actual = str(getattr(model, "device", "cpu")).lower()
    if actual not in {"cpu", "cpu:0"}:
        raise RuntimeError(f"Whisper was requested on CPU but reports device={actual!r}.")
    return model


def run_whisper_cpu_parallel_with_gpu(
    whisper_cpu_fn: Callable[[], Any],
    gpu_fn: Callable[[], Any],
) -> tuple[Any, Any]:
    """Run CPU ASR in a worker while the calling thread performs the GPU stage.

    gpu_fn should run the VLM scene pass (or another MLX GPU stage). Returns
    (whisper_result, gpu_result). Exceptions propagate. Do not use this to run
    two GPU model-generation stages concurrently.
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="vce-whisper-cpu") as pool:
        asr_future = pool.submit(whisper_cpu_fn)
        gpu_result = gpu_fn()
        whisper_result = asr_future.result()
    return whisper_result, gpu_result
