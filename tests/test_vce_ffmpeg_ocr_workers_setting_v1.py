from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "patches" / "patch_vce_ffmpeg_ocr_workers_setting_v1.py"
SPEC = importlib.util.spec_from_file_location("vce_ocr_workers_setting_v1", PATCHER_PATH)
PATCH = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PATCH)

FIXTURE = r'''
import json
from fastapi import Form

def setting(name, fallback):
    return fallback

async def update_ui_settings():
    defaults = {
        "analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,
        "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    }
    int_ranges = {
        "ffmpeg_ocr_interval": (1, 60),
        "text_llm_max_tokens": (1024, 4096),
    }
    float_ranges = {
        "scene_threshold": (0.01, 1.0),
    }
    return defaults, int_ranges, float_ranges

async def analyze_video(
    ffmpeg_ocr_workers: int = Form(2, ge=1, le=8, description="Число параллельных CPU-процессов Tesseract"),
):
    return ffmpeg_ocr_workers
'''


class FfmpegOcrWorkersSettingTests(unittest.TestCase):
    def test_patcher_is_valid_python(self):
        ast.parse(PATCHER_PATH.read_text(encoding="utf-8"))

    def test_adds_default_range_and_form_setting(self):
        updated = PATCH.patch_engine(FIXTURE)
        ast.parse(updated)
        self.assertIn('"ffmpeg_ocr_workers": 2', updated)
        self.assertIn('"ffmpeg_ocr_workers": (1, 8)', updated)
        self.assertIn('Form(int(setting("ffmpeg_ocr_workers", 2)),', updated)
        self.assertIn(PATCH.MARKER, updated)

    def test_is_idempotent(self):
        updated = PATCH.patch_engine(FIXTURE)
        self.assertEqual(PATCH.patch_engine(updated), updated)

    def test_unknown_shape_fails_without_guessing(self):
        with self.assertRaises(ValueError):
            PATCH.patch_engine("async def unrelated():\n    pass\n")


if __name__ == "__main__":
    unittest.main()
