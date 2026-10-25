import ast
import importlib.util
import json
import tempfile
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

    def test_mlx_selected_skips_torch_model_device_transfer(self):
        fixture = ENGINE_FIXTURE.replace(
            "    audio_res = w_model.transcribe(\n",
            '    w_model = w_model.to("mps")\n    audio_res = w_model.transcribe(\n',
            1,
        )
        updated = patch.patch_engine(fixture)
        ast.parse(updated)
        self.assertIn(
            '    if not enable_mlx_whisper:\n        w_model = w_model.to("mps")',
            updated,
        )
        self.assertIn("VCE_LOCAL_SERVER_SHUTDOWN_ENDPOINT_V1", updated)
        self.assertIn('@app.post("/api/v1/server/shutdown")', updated)


    def test_mlx_selected_skips_standalone_torch_model_device_transfer(self):
        fixture = ENGINE_FIXTURE.replace(
            "    audio_res = w_model.transcribe(\n",
            '    w_model.to("mps")\n    audio_res = w_model.transcribe(\n',
            1,
        )
        updated = patch.patch_engine(fixture)
        ast.parse(updated)
        self.assertIn(
            '    if not enable_mlx_whisper:\n        w_model.to("mps")',
            updated,
        )

    def test_scene_detector_supports_custom_variable_and_single_line_call(self):
        fixture = ENGINE_FIXTURE.replace(
            "scenes_raw",
            "scene_candidates",
        ).replace(
            "scenes_raw = detect_scenes(\n        temp_path,\n        threshold=scene_threshold,\n        min_duration=min_scene_duration,\n        max_duration=max_scene_duration,\n    )",
            "scene_candidates = detect_scenes(temp_path, threshold=scene_threshold, min_duration=min_scene_duration, max_duration=max_scene_duration)",
        )
        updated = patch.patch_engine(fixture)
        ast.parse(updated)
        self.assertIn("scene_candidates = _vce_detect_scenes_fast(", updated)
        self.assertIn("scene_candidates = detect_scenes(", updated)

    def test_scene_detector_supports_asyncio_to_thread(self):
        fixture = ENGINE_FIXTURE.replace(
            "scenes_raw = detect_scenes(\n",
            "scenes_raw = await asyncio.to_thread(\n        detect_scenes,\n",
            1,
        )
        updated = patch.patch_engine(fixture)
        ast.parse(updated)
        self.assertIn("scenes_raw = await asyncio.to_thread(\n            _vce_detect_scenes_fast,", updated)
        self.assertIn("scenes_raw = await asyncio.to_thread(\n            detect_scenes,", updated)

    def test_threaded_whisper_and_custom_loader_are_patched(self):
        fixture = ENGINE_FIXTURE.replace(
            "def validate_video_file(path):",
            "def load_whisper_model(model_name, acceleration_enabled=True):\n    return whisper.load_model(model_name)\n\ndef validate_video_file(path):",
            1,
        ).replace(
            "enable_ffmpeg_ocr: bool = Form(False),",
            "enable_ffmpeg_ocr: bool = Form(False),\n    whisper_acceleration: bool = Form(True),",
            1,
        ).replace(
            "w_model = whisper.load_model(target_whisper)",
            "w_model = load_whisper_model(target_whisper, acceleration_enabled=whisper_acceleration)",
        ).replace(
            "audio_res = w_model.transcribe(\n        temp_path,\n",
            "audio_res = await asyncio.to_thread(\n        w_model.transcribe,\n        temp_path,\n",
            1,
        )
        updated = patch.patch_engine(fixture)
        ast.parse(updated)
        self.assertIn(
            "audio_res = await asyncio.to_thread(\n        _vce_transcribe_audio,\n        temp_path, target_whisper, w_model, enable_mlx_whisper,",
            updated,
        )
        self.assertIn(
            "_vce_load_whisper_model(target_whisper, enable_mlx_whisper, whisper_acceleration)",
            updated,
        )

    def test_whisper_transcription_result_variable_can_differ(self):
        fixture = ENGINE_FIXTURE.replace(
            "audio_res = w_model.transcribe(\n        temp_path,\n",
            "speech_result = await asyncio.to_thread(\n        w_model.transcribe,\n        temp_path,\n",
            1,
        )
        updated = patch.patch_engine(fixture)
        ast.parse(updated)
        self.assertIn(
            "speech_result = await asyncio.to_thread(\n        _vce_transcribe_audio,\n        temp_path, target_whisper, w_model, enable_mlx_whisper,",
            updated,
        )

    def test_async_multi_keyframe_ocr_loop_is_patched(self):
        fixture = ENGINE_FIXTURE.replace(
            "whisper_infer_time = 0.2\n",
            "whisper_infer_time = 0.2\n    ocr_infer_time = 0.0\n",
            1,
        ).replace(
            '            scene_ocr_text = apple_vision_ocr(keyframes[0]["image"])',
            r'''            t_ocr_start = time.time()
            ocr_parts = []
            for kf in keyframes:
                extracted = await asyncio.to_thread(apple_vision_ocr, kf.get("original_image", kf["image"]))
                if extracted:
                    ocr_parts.append(extracted)
            scene_ocr_text = "\\n\\n".join(ocr_parts)
            ocr_infer_time += time.time() - t_ocr_start''',
            1,
        )
        updated = patch.patch_engine(fixture)
        ast.parse(updated)
        self.assertIn(
            "asyncio.to_thread((_vce_cached_apple_vision_ocr if enable_ocr_cache else apple_vision_ocr),",
            updated,
        )
        self.assertIn(
            "apple_ocr_seconds += time.perf_counter() - _vce_ocr_wall_start",
            updated,
        )

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
        self.assertIn('id="vce-stop-server"', once)
        self.assertIn("VCE_LOCAL_SERVER_SHUTDOWN_BUTTON_HANDLER_V1", once)
        self.assertIn("fetch('/api/v1/server/shutdown'", once)

    def test_ui_works_without_save_settings_button(self):
        fixture = UI_FIXTURE.replace('  <button id="save-settings">Save</button>\n', "")
        updated = patch.patch_ui(fixture)
        for element in (
            'id="cfg-stage-timing"',
            'id="cfg-adaptive-keyframes"',
            'id="cfg-ocr-cache"',
            'id="cfg-mlx-whisper"',
            'id="cfg-fast-scene-detection"',
        ):
            self.assertIn(element, updated)
        self.assertLess(updated.index("id=\"cfg-fast-scene-detection\""), updated.index('id="cfg-apple-ocr"'))
        self.assertIn("enable_fast_scene_detection:", updated)
        self.assertIn("'cfg-fast-scene-detection':'enable_fast_scene_detection'", updated)

    def test_upgrade_preserves_existing_user_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            # Simulate an engine/UI that already has the previous checkbox patch.
            engine = patch.patch_engine(ENGINE_FIXTURE)
            ui = patch.patch_ui(UI_FIXTURE)
            settings = {
                "vlm_resolution": 768,
                "scene_threshold": 0.172,
                "generate_txt": False,
                "visual_user_prompt": "custom visual prompt",
                "summary_user_prompt": "custom summary prompt",
                "enable_mlx_whisper": True,
            }
            (project / "VideoContextEngine_v3.19.py").write_text(engine, encoding="utf-8")
            (project / "videocontext_web_ui.html").write_text(ui, encoding="utf-8")
            settings_path = project / "videocontext_settings.json"
            settings_path.write_text(json.dumps(settings), encoding="utf-8")

            changes, originals = patch.prepare(project)

            self.assertEqual(settings_path.read_text(encoding="utf-8"), originals[settings_path])
            self.assertNotIn(settings_path, changes)
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8"))["vlm_resolution"], 768)
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8"))["scene_threshold"], 0.172)

    def test_settings_default_off_and_preserve_txt_and_prompts(self):
        before = {
            "generate_txt": False,
            "visual_user_prompt": "keep this",
            "summary_user_prompt": "also keep this",
            "vlm_resolution": 1280,
            "scene_threshold": 0.27,
            "ffmpeg_ocr_workers": 6,
            "enable_ffmpeg_ocr": False,
        }
        after = json.loads(patch.patch_settings(json.dumps(before)))
        self.assertFalse(after["generate_txt"])
        self.assertEqual(after["visual_user_prompt"], "keep this")
        self.assertEqual(after["summary_user_prompt"], "also keep this")
        self.assertEqual(after["vlm_resolution"], 384)
        self.assertEqual(after["scene_threshold"], 0.15)
        self.assertEqual(after["min_scene_duration"], 1.5)
        self.assertEqual(after["max_scene_duration"], 20)
        self.assertEqual(after["keyframes_per_scene"], 1)
        self.assertTrue(after["enable_apple_ocr"])
        self.assertTrue(after["enable_ffmpeg_ocr"])
        self.assertEqual(after["ffmpeg_ocr_interval"], 5)
        self.assertEqual(after["ffmpeg_ocr_workers"], 3)
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
