from __future__ import annotations

import importlib.util
from pathlib import Path

PATCHER = Path(__file__).resolve().parents[1] / "patches" / "patch_vce_fast_profile_mps.py"
spec = importlib.util.spec_from_file_location("vce_fast_mps_patcher", PATCHER)
patcher = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(patcher)


def target_shape() -> str:
    return '''async def process():
                if not skip_audio:
                    _vce_whisper_task = asyncio.create_task(_vce_run_whisper_cpu_stage())

                if run_vlm and _vce_metal_runtime_failure() is not None:
                    run_vlm = False
                    print("[VCE MLX] Skipping VLM because Metal failed", flush=True)
                if run_vlm:
                    release_accelerator_memory('before VLM load')
                    await _vce_mlx_run(vlm_engine.load_model, target_vlm)
'''


def test_scheduler_inserts_await_and_preserves_metal_guard():
    source = target_shape().replace(
        "_vce_run_whisper_cpu_stage", "_vce_run_whisper_mps_stage"
    )
    patched = patcher._serialize_whisper_before_vlm(source)
    assert patched.index("_vce_whisper_task = asyncio.create_task") < patched.index(
        "whisper_segments = await _vce_whisper_task"
    ) < patched.index("_vce_metal_runtime_failure()") < patched.index("if run_vlm:")
    assert 'print("[VCE MLX] Skipping VLM because Metal failed", flush=True)' in patched


def test_scheduler_rejects_missing_task():
    try:
        patcher._serialize_whisper_before_vlm("async def process():\n    if run_vlm:\n        pass\n")
    except ValueError as exc:
        assert "expected exactly one Whisper task creation" in str(exc)
    else:
        raise AssertionError("missing task anchor must fail closed")


def test_scheduler_rejects_duplicate_task_sites():
    source = target_shape().replace(
        "_vce_run_whisper_cpu_stage", "_vce_run_whisper_mps_stage"
    )
    duplicate = source + source
    try:
        patcher._serialize_whisper_before_vlm(duplicate)
    except ValueError as exc:
        assert "found 2" in str(exc)
    else:
        raise AssertionError("duplicate task anchors must fail closed")
