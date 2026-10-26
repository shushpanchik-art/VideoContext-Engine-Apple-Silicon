import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "patches" / "patch_vce_mp4_recovery_retry.py"
spec = importlib.util.spec_from_file_location("vce_mp4_recovery_retry", PATCHER)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
assert spec.loader is not None
spec.loader.exec_module(mod)

FIXTURE = r'''
import asyncio, os
from fastapi import FastAPI, HTTPException
app = FastAPI()
# VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1

def _vce_is_recoverable_container_error(exc):
    return "trun" in str(exc)

def validate_video_file(path):
    return 42.0

def ensure_opencv_compatible_video(path):
    return path

def _vce_validate_video_with_remux(video_path):
    return validate_video_file(video_path), None, None

@app.post("/api/v1/analyze")
async def analyze_video(temp_path, enable_mlx_whisper=False):
    owns_temp_path = True
    original_input_path = temp_path
    prepared_path = await asyncio.to_thread(ensure_opencv_compatible_video, original_input_path)
    if prepared_path != original_input_path:
        temp_path = prepared_path
    if enable_mlx_whisper:
        pass
    run_vlm = True
    skip_visual = False
    skip_audio = False
    enable_apple_ocr = True
    enable_ffmpeg_ocr = True
    scenes_raw = [{"start": 0, "end": 1}]
    final_segments = []
    for idx, scene in enumerate(scenes_raw):
        scene_id = idx + 1
        visual_description = ""
        final_segments.append(
            {
                "scene_id": scene_id,
                "start": scene["start"],
                "end": scene["end"],
                "audio_transcript": "",
                "visual_description": visual_description,
            }
        )
    return final_segments
'''

def test_patch_is_valid_and_idempotent():
    patched = mod.patch_engine(FIXTURE)
    ast.parse(patched)
    assert patched.count("def _vce_is_recoverable_container_error(") == 1
    assert patched.count("def _vce_validate_video_with_remux(") == 1
    assert patched.count("def _vce_ensure_opencv_compatible_with_recovery(") == 1
    assert patched.count("def _vce_build_audio_only_video(") == 1
    assert "prepared_path, _vce_audio_only_fallback, _vce_media_recovery_note" in patched
    assert "VCE_AUDIO_ONLY_FALLBACK_POLICY_V1" in patched
    assert "analysis_mode = \"audio_only_fallback\"" in patched
    assert "transcode-h264-aac" in patched
    assert "0:a:0" in patched
    assert mod.patch_engine(patched) == patched

if __name__ == "__main__":
    test_patch_is_valid_and_idempotent()
    print("MP4 recovery patch fixture tests passed.")
