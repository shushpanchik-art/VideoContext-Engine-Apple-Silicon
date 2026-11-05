from __future__ import annotations

import ast
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "patches" / "patch_vce_fast_profile_mps.py"
ENGINE_PATH = ROOT / "VideoContextEngine_v3.19.py"
UI_PATH = ROOT / "videocontext_web_ui.html"


def load_patcher():
    spec = importlib.util.spec_from_file_location("vce_fast_profile_mps_patcher", PATCHER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FastProfileMpsPatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = load_patcher()
        cls.engine = ENGINE_PATH.read_text(encoding="utf-8")
        cls.ui = UI_PATH.read_text(encoding="utf-8")

    def test_engine_switches_to_real_pytorch_mps_and_serializes_gpu_stages(self):
        patched = self.patcher.patch_engine(self.engine)
        ast.parse(patched)
        self.assertIn("VCE_PYTORCH_WHISPER_MPS_SERIAL_V1", patched)
        self.assertIn('print(f"[VCE Whisper] backend=openai-whisper device={device}', patched)
        self.assertIn("whisper.load_model(model_name, device=device)", patched)
        self.assertIn("acceleration_enabled=whisper_acceleration", patched)
        self.assertNotIn("acceleration_enabled=False", patched)
        self.assertIn("_vce_whisper_task = asyncio.create_task(_vce_run_whisper_mps_stage())", patched)
        self.assertIn("whisper_segments = await _vce_whisper_task", patched)
        self.assertIn("GPU inference stages are serialized", patched)
        self.assertIn('"whisper_backend": "openai-whisper"', patched)
        self.assertIn('"gpu_stages_serialized": True', patched)

    def test_engine_removes_optional_local_mlx_whisper_language_helper(self):
        helper = (
            "\\ndef _vce_whisper_supported_languages():\\n"
            "    from mlx_whisper.tokenizer import LANGUAGES\\n"
            "    return LANGUAGES\\n"
        )
        variant = self.engine + helper

        patched = self.patcher.patch_engine(variant)
        ast.parse(patched)
        self.assertNotIn("_vce_whisper_supported_languages", patched)
        self.assertNotIn("mlx_whisper", patched)

    def test_optional_local_mlx_helper_rejects_duplicate_definitions(self):
        duplicate = (
            "def _vce_whisper_supported_languages():\\n"
            "    return {}\\n\\n"
            "def _vce_whisper_supported_languages():\\n"
            "    return {}\\n"
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.patcher._remove_optional_ast_nodes(
                duplicate, {"_vce_whisper_supported_languages"}
            )

    def test_settings_api_ocr_defaults_preserve_recognition_languages_layout(self):
        compact = '"analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 2,'
        with_languages = '"analysis_mode": "full", "recognition_languages": _vce_legacy_apple_languages(persisted.get("apple_ocr_languages", ["ru-RU", "en-US"])), "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 2,'
        self.assertIn(compact, self.engine)
        variant = self.engine.replace(compact, with_languages, 1)

        patched = self.patcher.patch_engine(variant)
        self.assertIn('"recognition_languages": _vce_legacy_apple_languages(persisted.get("apple_ocr_languages", ["ru-RU", "en-US"]))', patched)
        self.assertIn('"enable_ffmpeg_ocr": True, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 3,', patched)

    def test_forbidden_reference_diagnostic_reports_line_numbers_only(self):
        source = "first line\nimport mlx_whisper\nthird line\nmlx_whisper.transcribe()"
        self.assertEqual(self.patcher._line_numbers_containing(source, "mlx_whisper"), [2, 4])

    def test_settings_api_ocr_anchor_rejects_zero_matches(self):
        variants = (("compact-anchor", "replacement"), ("language-anchor", "replacement"))
        with self.assertRaisesRegex(ValueError, "found 0"):
            self.patcher._replace_one_of_exact("unrecognized defaults layout", variants, "OCR defaults")

    def test_settings_api_ocr_anchor_rejects_duplicate_matches(self):
        variants = (("compact-anchor", "replacement"), ("language-anchor", "replacement"))
        with self.assertRaisesRegex(ValueError, "found 2"):
            self.patcher._replace_one_of_exact(
                "compact-anchor then language-anchor", variants, "OCR defaults"
            )

    def test_engine_keeps_apple_ocr_cache_and_ffmpeg_tesseract(self):
        patched = self.patcher.patch_engine(self.engine)
        self.assertIn("_vce_cached_apple_vision_ocr", patched)
        self.assertIn("(_vce_cached_apple_vision_ocr if enable_ocr_cache else apple_vision_ocr)", patched)
        self.assertIn("enable_ocr_cache: bool = Form(bool(setting", patched)
        self.assertIn("enable_apple_ocr: bool = Form(", patched)
        self.assertIn("enable_ffmpeg_ocr: bool = Form(bool(setting", patched)
        self.assertIn("_vce_run_ffmpeg_ocr_timed", patched)
        self.assertIn('ffmpeg_ocr_workers: int = Form(int(setting("ffmpeg_ocr_workers", 3)', patched)
        self.assertIn('"enable_ocr_cache": True', patched)

    def test_other_experimental_runtime_paths_are_removed(self):
        patched = self.patcher.patch_engine(self.engine)
        for item in (
            "enable_adaptive_keyframes",
            "enable_mlx_whisper",
            "enable_fast_scene_detection",
            "_VCEWhisperMLXCPU",
            "_vce_validate_mlx_whisper_model",
            "mlx_whisper",
            "_vce_detect_scenes_fast",
            "_vce_adaptive_keyframe_limit",
        ):
            self.assertNotIn(item, patched)

    def test_engine_patch_is_idempotent(self):
        once = self.patcher.patch_engine(self.engine)
        self.assertEqual(self.patcher.patch_engine(once), once)

    def test_ui_removes_experimental_toggles_but_keeps_ocr_cache(self):
        patched = self.patcher.patch_ui(self.ui)
        for item in (
            'id="cfg-stage-timing"',
            'id="cfg-adaptive-keyframes"',
            'id="cfg-mlx-whisper"',
            'id="cfg-fast-scene-detection"',
            "enable_adaptive_keyframes",
            "enable_mlx_whisper",
            "enable_fast_scene_detection",
        ):
            self.assertNotIn(item, patched)
        self.assertIn('id="cfg-ocr-cache"', patched)
        self.assertIn('id="cfg-apple-ocr"', patched)
        self.assertIn('id="cfg-ffmpeg-ocr"', patched)
        self.assertIn("enable_ocr_cache: document.getElementById('cfg-ocr-cache').checked", patched)
        self.assertIn("VCE_FAST_MPS_PROFILE_UI_V1", patched)

    def test_settings_migration_preserves_prompts_and_removes_experimental_keys(self):
        original = {
            "visual_user_prompt": "keep my visual prompt",
            "summary_user_prompt": "keep my summary prompt",
            "custom_user_key": {"keep": True},
            "enable_adaptive_keyframes": True,
            "enable_mlx_whisper": True,
            "enable_fast_scene_detection": True,
            "enable_ocr_cache": False,
        }
        migrated = self.patcher.migrate_settings(original)
        self.assertEqual(migrated["visual_user_prompt"], original["visual_user_prompt"])
        self.assertEqual(migrated["summary_user_prompt"], original["summary_user_prompt"])
        self.assertEqual(migrated["custom_user_key"], original["custom_user_key"])
        for key in ("enable_adaptive_keyframes", "enable_mlx_whisper", "enable_fast_scene_detection"):
            self.assertNotIn(key, migrated)
        self.assertTrue(migrated["enable_ocr_cache"])
        self.assertTrue(migrated["enable_apple_ocr"])
        self.assertTrue(migrated["enable_ffmpeg_ocr"])
        self.assertEqual(migrated["ffmpeg_ocr_interval"], 5)
        self.assertEqual(migrated["ffmpeg_ocr_workers"], 3)
        self.assertEqual(migrated["whisper_model"], "large")
        self.assertTrue(migrated["whisper_acceleration"])
        self.assertEqual(migrated["ram_mode"], "ram-")


if __name__ == "__main__":
    unittest.main(verbosity=2)
