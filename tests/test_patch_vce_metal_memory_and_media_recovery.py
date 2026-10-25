import importlib.util
import json
import gc
import sys
from pathlib import Path

PATCHER = Path(__file__).resolve().parents[1] / "patches" / "patch_vce_metal_memory_and_media_recovery.py"
spec = importlib.util.spec_from_file_location("vce_recovery_patcher", PATCHER)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
assert spec.loader is not None
spec.loader.exec_module(mod)

FIXTURE = '''import asyncio, gc, json, os
from fastapi import FastAPI, HTTPException
app = FastAPI()
def validate_video_file(path):
    return 108.23
@app.post("/api/v1/analyze")
async def analyze_video():
    owns_temp_path = True
    temp_path = "/tmp/source.mp4"
    video_duration = validate_video_file(temp_path)
    run_vlm = True
    vlm_engine = object()
    images = [object()]
    full_visual_prompt = "describe"
    vlm_max_tokens_scene = 512
    if run_vlm:
        raw = await asyncio.to_thread(
            vlm_engine.describe_scene,
            images,
            full_visual_prompt,
            max_tokens=vlm_max_tokens_scene,
        )
        data = safe_json_parse(raw)
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
    return {"duration": video_duration}
'''

patched = mod.patch_engine(FIXTURE)
assert mod.MARKER in patched
assert "_vce_validate_video_with_remux(temp_path)" in patched
assert "_vce_describe_scene_with_memory_fallback," in patched
assert 'if isinstance(data, dict) and data.get("_vce_memory_fallback"):' in patched
assert "run_vlm = False" in patched
assert mod.patch_engine(patched) == patched

settings = json.dumps({
    "whisper_acceleration": False,
    "enable_mlx_whisper": True,
    "enable_stage_timing": False,
    "vlm_resolution": 768,
    "scene_threshold": 0.172,
    "visual_user_prompt": "preserve me",
    "generate_txt": True,
}, ensure_ascii=False)
updated = json.loads(mod.patch_settings(settings))
assert updated["whisper_acceleration"] is True
assert updated["enable_mlx_whisper"] is False
assert updated["enable_stage_timing"] is True
assert updated["vlm_resolution"] == 768
assert updated["scene_threshold"] == 0.172
assert updated["visual_user_prompt"] == "preserve me"
assert updated["generate_txt"] is True

class FakeHTTPException(Exception):
    def __init__(self, status_code=None, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail

runtime = {"gc": gc, "json": json, "HTTPException": FakeHTTPException}
exec(mod.HELPERS, runtime)
assert runtime["_vce_is_recoverable_container_error"](
    Exception("ffprobe: trun track id unknown, no tfhd was found")
)
assert not runtime["_vce_is_recoverable_container_error"](Exception("Video too long"))

class FakeImage:
    def __init__(self, w=768, h=768): self.size = (w, h)
    def resize(self, size, _resampling): return FakeImage(*size)

class RecoversAt384:
    def describe_scene(self, images, prompt, max_tokens):
        if max(max(image.size) for image in images) > 384:
            raise RuntimeError("Недостаточно памяти Metal для VLM даже после уменьшения кадра до 768/512px")
        return '{"description":"recovered at lower resolution","tags":{}}'
    def unload_model(self):
        raise AssertionError("unload should not be needed after recovery")

result = runtime["_vce_describe_scene_with_memory_fallback"](
    RecoversAt384(), [FakeImage()], "prompt", 512
)
assert "recovered at lower resolution" in result

class AlwaysOom:
    def __init__(self): self.unloaded = False
    def describe_scene(self, images, prompt, max_tokens):
        raise RuntimeError("Недостаточно памяти Metal")
    def unload_model(self): self.unloaded = True

failing = AlwaysOom()
result = json.loads(runtime["_vce_describe_scene_with_memory_fallback"](
    failing, [FakeImage()], "prompt", 512
))
assert result["_vce_memory_fallback"] is True
assert failing.unloaded
print("All synthetic patch tests passed.")
