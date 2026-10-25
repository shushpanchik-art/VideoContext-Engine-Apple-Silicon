from __future__ import annotations

import ast
import asyncio
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "patch_vce_memory_safety.py"


def load_patch():
    spec = importlib.util.spec_from_file_location("vce_memory_safety_patch", PATCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE = '''import gc
import shutil
import os
import time
import cv2
from PIL import Image
from fastapi import HTTPException
class VLMProvider:
    pass

class MLXEngine(VLMProvider):
    def describe_scene(self, images, prompt, max_tokens):
        num_images = max(1, len(images))
        formatted_prompt = prompt
        imgs = images if len(images) > 1 else images[0]
        output = self._generate_fn(
            self.model,
            self.processor,
            formatted_prompt,
            imgs,
            max_tokens=max_tokens,
            verbose=False,
            temp=0.0,
        )
        return output

def validate_video_file(file_path: str) -> float:
    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        raise HTTPException(400, "Fichier vidéo invalide")
    return 10.0

async def analyze_video():
    temp_path = "/tmp/video.mp4"
    owns_temp_path = False
    video_duration = validate_video_file(temp_path)
    if a:
        if b:
            if c:
                if d:
                    if RAM_MODE == "ram-":
                        del w_model
                        gc.collect()
                if not skip_visual or generate_summary:
                    t0 = time.time()
                    await asyncio.to_thread(vlm_engine.load_model, target_vlm)
'''


class MemorySafetyPatchTests(unittest.TestCase):
    def setUp(self):
        self.patch = load_patch()

    def test_safe_profile_reduces_peak_memory_without_disabling_quality_paths(self):
        original = {
            "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
            "vlm_resolution": 1280,
            "keyframes_per_scene": 2,
            "ram_mode": "ram+",
            "enable_apple_ocr": True,
            "enable_ffmpeg_ocr": True,
            "skip_visual": False,
            "skip_audio": False,
            "generate_json": True,
            "generate_txt": False,
            "visual_user_prompt": "keep technical text verbatim",
            "summary_user_prompt": "do not invent measurements",
            "custom_setting": {"keep": True},
        }
        result = self.patch.safe_profile(original)
        self.assertEqual(result["vlm_resolution"], 768)
        self.assertEqual(result["keyframes_per_scene"], 1)
        self.assertEqual(result["ram_mode"], "ram-")
        self.assertEqual(result["vlm_model"], original["vlm_model"])
        self.assertTrue(result["enable_apple_ocr"])
        self.assertTrue(result["enable_ffmpeg_ocr"])
        self.assertFalse(result["skip_visual"])
        self.assertFalse(result["skip_audio"])
        self.assertTrue(result["generate_json"])
        self.assertFalse(result["generate_txt"])
        self.assertEqual(result["visual_user_prompt"], original["visual_user_prompt"])
        self.assertEqual(result["summary_user_prompt"], original["summary_user_prompt"])
        self.assertEqual(result["custom_setting"], original["custom_setting"])

    def test_safe_profile_is_idempotent_and_does_not_upscale(self):
        result = self.patch.safe_profile({"vlm_resolution": 640, "keyframes_per_scene": 1, "generate_txt": False})
        self.assertEqual(result["vlm_resolution"], 640)
        self.assertEqual(result["keyframes_per_scene"], 1)
        self.assertFalse(result["generate_txt"])
        self.assertEqual(self.patch.safe_profile(result), result)

    def test_engine_patch_adds_oom_retry_cache_cleanup_and_video_proxy(self):
        patched = self.patch.patch_engine(FIXTURE)
        ast.parse(patched)
        self.assertIn("def release_accelerator_memory(", patched)
        self.assertIn("torch.mps.empty_cache()", patched)
        self.assertIn('getattr(mx, "clear_cache", None)', patched)
        self.assertIn('getattr(metal_api, "clear_cache", None)', patched)
        self.assertIn("VLM OOM: повторная попытка", patched)
        self.assertIn("retry_side in (768, 512)", patched)
        self.assertIn("ensure_opencv_compatible_video", patched)
        self.assertIn("original_input_path = str(Path(temp_path).expanduser().resolve())", patched)
        self.assertIn("Path(prepared_path).expanduser().resolve() != Path(original_input_path).resolve()", patched)
        self.assertIn("ffprobe", patched)
        self.assertIn("libx264", patched)
        self.assertIn("release_accelerator_memory('before VLM load')", patched)
        self.assertIn("release_accelerator_memory('after Whisper inference')", patched)

    def test_engine_patch_is_idempotent(self):
        once = self.patch.patch_engine(FIXTURE)
        twice = self.patch.patch_engine(once)
        self.assertEqual(once, twice)

    def test_engine_patch_supports_async_vlm_load(self):
        patched = self.patch.patch_engine(FIXTURE)
        ast.parse(patched)
        self.assertIn("await asyncio.to_thread(vlm_engine.load_model, target_vlm)", patched)
        self.assertIn("release_accelerator_memory('before VLM load')", patched)

    def test_video_preflight_pattern_is_line_bounded(self):
        source = PATCH.read_text(encoding="utf-8")
        self.assertIn(r"video_duration = validate_video_file\(temp_path\)[ \t]*$", source)
        self.assertNotIn(r"video_duration = validate_video_file\(temp_path\)\s*$", source)

    def test_source_patch_file_is_valid_python(self):
        ast.parse(PATCH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
