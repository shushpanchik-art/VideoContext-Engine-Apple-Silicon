from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "patches"))
from patch_vce_metal_runtime_latch import MARKER, patch_source


FIXTURE = r'''import asyncio
import functools as _vce_mlx_functools
import json
import os
import threading as _vce_threading
import time
from concurrent.futures import ThreadPoolExecutor

_VCE_MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1)

def _vce_exception_chain_text(exc):
    return str(exc)

def _vce_is_metal_command_timeout(exc):
    return "timeout" in str(exc).lower() or "submissionsignored" in str(exc).lower()

def _vce_mlx_invoke_traced(fn, *args, **kwargs):
    return fn(*args, **kwargs)

async def _vce_mlx_run(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    call = _vce_mlx_functools.partial(_vce_mlx_invoke_traced, fn, *args, **kwargs)
    return await loop.run_in_executor(_VCE_MLX_EXECUTOR, call)

def _vce_configure_mlx_metal(stage: str):
    """Select GPU device."""
    return stage

def _vce_prepare_mlx_vlm_runtime():
    """Prepare mlx-vlm."""
    return None

def _vce_clear_metal_memory_caches():
    return None

def _vce_describe_scene_with_memory_fallback(vlm_engine, images, prompt, max_tokens):
    """Retry allocation OOM only; fail closed on queue timeout."""
    original_images = list(images or [])
    try:
        return vlm_engine.describe_scene(original_images, prompt, max_tokens=max_tokens)
    except Exception as exc:
        if not _vce_is_metal_command_timeout(exc):
            raise
        if _vce_is_metal_command_timeout(exc):
            try:
                vlm_engine.unload_model()
            except Exception:
                pass
            _vce_clear_metal_memory_caches()
            message = (
                "Metal timeout: " + str(exc)
            )
            return message
    return "ok"

async def analyze_video():
    async with gpu_lock:
        async with request_lock:
            async with inner_lock:
                if run_vlm:
                    release_accelerator_memory('before VLM load')
                    await _vce_mlx_run(vlm_engine.load_model, target_vlm)

def run_text_llm_postprocess(model_path, source_name, video_duration, existing_summary, segments):
    """Run the text-only MLX post-processing stage."""
    import gc
    _vce_configure_mlx_metal("text_llm")
    return {}
'''


class MetalLatchPatcherTests(unittest.TestCase):
    def test_patch_parses_and_adds_expected_guards(self):
        patched = patch_source(FIXTURE)
        ast.parse(patched)
        self.assertIn(MARKER, patched)
        self.assertIn('_vce_assert_metal_runtime_healthy("text_llm")', patched)
        self.assertIn('_vce_mark_metal_runtime_failed("vlm_generation", exc)', patched)
        self.assertIn('run_vlm = False', patched)
        self.assertIn('Skipping model unload and cache operations after fatal Metal timeout', patched)

    def test_patch_is_idempotent(self):
        once = patch_source(FIXTURE)
        twice = patch_source(once)
        self.assertEqual(once, twice)

    def test_fail_closed_state_keeps_first_failure(self):
        patched = patch_source(FIXTURE)
        namespace = {
            "_vce_threading": threading,
            "_vce_exception_chain_text": lambda exc: str(exc),
            "os": os,
            "time": time,
            "json": json,
        }
        exec(compile(patched, "fixture.py", "exec"), namespace)
        namespace["_vce_mark_metal_runtime_failed"]("vlm_generation", RuntimeError("GPU Timeout Error"))
        namespace["_vce_mark_metal_runtime_failed"]("text_llm", RuntimeError("later failure"))
        failure = namespace["_vce_metal_runtime_failure"]()
        self.assertEqual(failure["stage"], "vlm_generation")
        self.assertIn("GPU Timeout Error", failure["error"])
        with self.assertRaisesRegex(RuntimeError, "Restart VideoContext Engine"):
            namespace["_vce_assert_metal_runtime_healthy"]("text_llm")

    def test_missing_or_ambiguous_anchor_fails_without_partial_output(self):
        damaged = FIXTURE.replace("release_accelerator_memory('before VLM load')", "release_memory()")
        with self.assertRaisesRegex(ValueError, "analyze_video VLM load guard"):
            patch_source(damaged)

    def test_executor_latches_uncaught_timeout_and_blocks_next_submission(self):
        patched = patch_source(FIXTURE)
        namespace = {
            "_vce_threading": threading,
            "_vce_exception_chain_text": lambda exc: str(exc),
            "os": os,
            "time": time,
            "json": json,
        }
        exec(compile(patched, "fixture.py", "exec"), namespace)
        ran = []

        def fail_with_timeout():
            ran.append("first")
            raise RuntimeError("[METAL] GPU Timeout Error (kIOGPUCommandBufferCallbackErrorTimeout)")

        async def exercise():
            with self.assertRaisesRegex(RuntimeError, "GPU Timeout Error"):
                await namespace["_vce_mlx_run"](fail_with_timeout)
            with self.assertRaisesRegex(RuntimeError, "Restart VideoContext Engine"):
                await namespace["_vce_mlx_run"](lambda: ran.append("second"))

        asyncio.run(exercise())
        self.assertEqual(ran, ["first"])
        self.assertEqual(namespace["_vce_metal_runtime_failure"]()["stage"], "fail_with_timeout")
        namespace["_VCE_MLX_EXECUTOR"].shutdown(wait=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
