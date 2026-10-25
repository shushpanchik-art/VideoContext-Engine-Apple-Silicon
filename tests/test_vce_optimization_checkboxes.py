import ast
import importlib.util
import json
import unittest
from pathlib import Path

PATCH_PATH = Path(__file__).resolve().parents[1] / "patches" / "patch_vce_optimization_checkboxes.py"
spec = importlib.util.spec_from_file_location("vce_priority_patch", PATCH_PATH)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

ENGINE_FIXTURE = r'''
import os
import re
import time
import cv2
import whisper
from fastapi import Form, HTTPException
app = None
RAM_MODE = "ram-"
WHISPER_CACHE = {}

def compute_hsv_histogram(frame):
    return frame

def detect_scenes(video_path, threshold, min_duration, max_duration):
    return [{"start": 0.0, "end": 10.0}]

def sample_keyframes_for_scene(cap, scene, base_res, max_frames):
    return [{"time": 1.0, "image": "frame"}]

def apple_vision_ocr(image):
    return "OCR text"

def validate_video_file(path):
    return 10.0

@app.post("/api/v1/analyze")
async def analyze_video(
    video_file: str = Form(""),
    temp_path: str = Form(""),
    target_whisper: str = Form("large"),
    cap: str = Form(""),
    vlm_resolution: int = Form(1280),
    keyframes_per_scene: int = Form(2),
    scene_threshold: float = Form(0.15),
    min_scene_duration: float = Form(1.5),
    max_scene_duration: float = Form(20.0),
    enable_apple_ocr: bool = Form(True),
    enable_ffmpeg_ocr: bool = Form(False),
):
    video_duration = validate_video_file(temp_path)
    scenes_raw = detect_scenes(
        temp_path,
        threshold=scene_threshold,
        min_duration=min_scene_duration,
        max_duration=max_scene_duration,
    )
    if RAM_MODE == "ram+":
        if target_whisper in WHISPER_CACHE:
            w_model = WHISPER_CACHE[target_whisper]
        else:
            w_model = whisper.load_model(target_whisper)
            WHISPER_CACHE[target_whisper] = w_model
    else:
        w_model = whisper.load_model(target_whisper)
    audio_res = w_model.transcribe(
        temp_path,
        condition_on_previous_text=False,
        temperature=0.0,
        compression_ratio_threshold=1.8,
    )
    whisper_load_time = 0.1
    whisper_infer_time = 0.2
    vlm_load_time = 0.3
    vlm_infer_time = 0.4
    for scene_id, scene in enumerate(scenes_raw):
        keyframes = sample_keyframes_for_scene(
            cap,
            scene,
            vlm_resolution,
            keyframes_per_scene,
        )
        if enable_apple_ocr and keyframes:
            scene_ocr_text = apple_vision_ocr(keyframes[0]["image"])
    timings = {
        "total_process_time": 1.0,
        "whisper": {"load_time": whisper_load_time, "inference_time": whisper_infer_time},
        "vlm": {"load_time": vlm_load_time, "inference_time": vlm_infer_time},
    }
    return timings

@app.get("/api/v1/ui-settings")
def get_ui_settings():
    defaults = {
        "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        "enable_apple_ocr": True,
        "enable_ffmpeg_ocr": False,
    }
    persisted = {}
    return {key: persisted.get(key, defaults[key]) for key in defaults}

@app.post("/api/v1/ui-settings")
def post_ui_settings():
    bool_keys = {
        "enable_apple_ocr", "enable_ffmpeg_ocr", "whisper_acceleration", "enable_audio_features",
    }
    return bool_keys
'''


UI_FIXTURE = r'''<section id="settings-panel">
  <label><input id="cfg-apple-ocr" type="checkbox" checked> Apple Vision OCR</label>
  <button id="save-settings">Save</button>
</section>
<script>
  const readSettings = () => ({
    enable_apple_ocr: document.getElementById('cfg-apple-ocr').checked,
  });
  function fillSettings(s) {
    const checks = {'cfg-apple-ocr':'enable_apple_ocr',};
    for (const [id,key] of Object.entries(checks)) if (s[key] !== undefined) document.getElementById(id).checked = Boolean(s[key]);
  }
  async function processOne(item) {
    const cfg = readSettings();
    for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));
  }
</script>'''


class TestOptimizationCheckboxPatch(unittest.TestCase):
    def test_engine_patch_parses_and_adds_five_real_hooks(self):
        updated = patch.patch_engine(ENGINE_FIXTURE)
        ast.parse(updated)
        for key in (
            "enable_stage_timing",
            "enable_adaptive_keyframes",
            "enable_ocr_cache",
            "enable_mlx_whisper",
            "enable_fast_scene_detection",
        ):
            self.assertIn(key, updated)
        self.assertIn("_vce_detect_scenes_fast(", updated)
        self.assertIn("_vce_cached_apple_vision_ocr(", updated)
        self.assertIn("_vce_transcribe_audio(", updated)
        self.assertIn('timings["stage_diagnostics"]', updated)

    def test_engine_patch_is_idempotent(self):
        once = patch.patch_engine(ENGINE_FIXTURE)
        twice = patch.patch_engine(once)
        self.assertEqual(once, twice)

    def test_ui_has_five_persisted_checkboxes_and_is_idempotent(self):
        once = patch.patch_ui(UI_FIXTURE)
        twice = patch.patch_ui(once)
        self.assertEqual(once, twice)
        for item in (
            'id="cfg-stage-timing"',
            'id="cfg-adaptive-keyframes"',
            'id="cfg-ocr-cache"',
            'id="cfg-mlx-whisper"',
            'id="cfg-fast-scene-detection"',
        ):
            self.assertIn(item, once)
        for key in (
            "enable_stage_timing",
            "enable_adaptive_keyframes",
            "enable_ocr_cache",
            "enable_mlx_whisper",
            "enable_fast_scene_detection",
        ):
            self.assertIn(key, once)

    def test_settings_default_off_and_preserve_txt_and_prompts(self):
        before = {
            "generate_txt": False,
            "visual_user_prompt": "keep this",
            "summary_user_prompt": "also keep this",
        }
        after = json.loads(patch.patch_settings(json.dumps(before)))
        self.assertFalse(after["generate_txt"])
        self.assertEqual(after["visual_user_prompt"], "keep this")
        self.assertEqual(after["summary_user_prompt"], "also keep this")
        for key in (
            "enable_stage_timing",
            "enable_adaptive_keyframes",
            "enable_ocr_cache",
            "enable_mlx_whisper",
            "enable_fast_scene_detection",
        ):
            self.assertIs(after[key], False)


if __name__ == "__main__":
    unittest.main()
