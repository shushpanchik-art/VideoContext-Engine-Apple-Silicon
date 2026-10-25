from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "patch_vce_speed_profile.py"


def load_patch():
    spec = importlib.util.spec_from_file_location("vce_speed_profile_patch", PATCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpeedProfileTests(unittest.TestCase):
    def setUp(self):
        self.patch = load_patch()

    def test_fast_profile_keeps_vlm_and_audio_path_enabled(self):
        source = {
            "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
            "analysis_mode": "whisper_ocr",
            "skip_visual": True,
            "skip_audio": False,
            "vlm_resolution": 1440,
            "keyframes_per_scene": 2,
            "enable_ffmpeg_ocr": True,
            "ffmpeg_ocr_interval": 1,
            "visual_user_prompt": "custom visual prompt",
            "summary_user_prompt": "custom summary prompt",
            "custom_key": {"keep": True},
        }
        result = self.patch.optimize_settings(source, "fast-vlm")
        self.assertEqual(result["analysis_mode"], "full")
        self.assertFalse(result["skip_visual"])
        self.assertFalse(result["skip_audio"])
        self.assertEqual(result["vlm_model"], source["vlm_model"])
        self.assertEqual(result["vlm_resolution"], 1280)
        self.assertEqual(result["keyframes_per_scene"], 1)
        self.assertTrue(result["enable_ffmpeg_ocr"])
        self.assertEqual(result["ffmpeg_ocr_interval"], 5)
        self.assertEqual(result["visual_user_prompt"], "custom visual prompt")
        self.assertEqual(result["summary_user_prompt"], "custom summary prompt")
        self.assertEqual(result["custom_key"], {"keep": True})

    def test_balanced_profile_uses_two_frames_and_keeps_vlm(self):
        result = self.patch.optimize_settings(
            {"vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
             "vlm_resolution": 1080,
             "keyframes_per_scene": 3,
             "max_scene_duration": 10,
             "enable_ffmpeg_ocr": False,
             "ffmpeg_ocr_interval": 1},
            "balanced-vlm",
        )
        self.assertEqual(result["analysis_mode"], "full")
        self.assertFalse(result["skip_visual"])
        self.assertEqual(result["vlm_resolution"], 1080)
        self.assertEqual(result["keyframes_per_scene"], 2)
        self.assertEqual(result["max_scene_duration"], 10)
        self.assertFalse(result["enable_ffmpeg_ocr"])
        self.assertEqual(result["ffmpeg_ocr_interval"], 2)

    def test_idempotent_and_non_destructive_to_model_selection(self):
        source = {
            "vlm_model": "mlx-community/Qwen3-VL-4B-Instruct-4bit",
            "vlm_resolution": 1280,
            "keyframes_per_scene": 1,
            "max_scene_duration": 20,
            "ffmpeg_ocr_interval": 5,
            "enable_ffmpeg_ocr": False,
            "visual_user_prompt": "user",
        }
        once = self.patch.optimize_settings(source, "fast-vlm")
        twice = self.patch.optimize_settings(once, "fast-vlm")
        self.assertEqual(once, twice)
        self.assertEqual(once["vlm_model"], source["vlm_model"])
        self.assertEqual(once["visual_user_prompt"], "user")

    def test_patch_syntax_and_menu_bar_bundle_metadata(self):
        import ast
        ast.parse(PATCH.read_text(encoding="utf-8"))
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "macos" / "install_video_context_mac.command")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plist_path = ROOT / "macos" / "VideoContext Engine.app" / "Contents" / "Info.plist"
        with plist_path.open("rb") as stream:
            plist = plistlib.load(stream)
        self.assertEqual(plist["CFBundleExecutable"], "VideoContextStatusBar")
        self.assertFalse(plist["LSUIElement"])
        self.assertEqual(plist["CFBundleIconFile"], "VideoContextEngine")
        swift = (plist_path.parent / "Resources" / "VideoContextStatusBar.swift").read_text(encoding="utf-8")
        self.assertIn("NSStatusBar.system.statusItem", swift)
        self.assertIn("start_server.command", swift)
        self.assertIn('updated["skip_visual"] = False', PATCH.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
