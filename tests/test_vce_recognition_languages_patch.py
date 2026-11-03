import ast
import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_PATH = ROOT / "patches" / "vce-recognition-languages-patcher.tar.gz"
with tempfile.TemporaryDirectory(prefix="vce-recognition-patcher-test-") as _patcher_dir:
    with tarfile.open(BUNDLE_PATH, "r:gz") as archive:
        member = archive.getmember("patch_vce_recognition_languages.py")
        archive.extract(member, _patcher_dir)
    PATCH_PATH = Path(_patcher_dir) / "patch_vce_recognition_languages.py"
    spec = importlib.util.spec_from_file_location("vce_recognition_languages_patch", PATCH_PATH)
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)


class TestVCERecognitionLanguagesPatch(unittest.TestCase):
    def test_current_engine_is_patchable_parseable_and_idempotent(self):
        source = (ROOT / "VideoContextEngine_v3.19.py").read_text(encoding="utf-8")
        updated = patch.patch_engine(source)
        ast.parse(updated)
        self.assertIn("_vce_transcribe_allowed_language_chunks", updated)
        self.assertIn("recognition_languages_json", updated)
        self.assertIn("_vce_effective_vision_locales", updated)
        self.assertIn("_vce_tesseract_installed_languages", updated)
        self.assertIn("_vce_language_policy_prompt(recognition_languages)", updated)
        self.assertNotIn("language_preference=None", updated)
        self.assertEqual(patch.patch_engine(updated), updated)

    def test_current_web_ui_has_multiple_language_checkboxes_and_is_idempotent(self):
        source = (ROOT / "videocontext_web_ui.html").read_text(encoding="utf-8")
        updated = patch.patch_ui(source)
        self.assertIn('id="cfg-recognition-languages"', updated)
        self.assertIn('value="ru"', updated)
        self.assertIn('value="en"', updated)
        self.assertIn("recognition_languages_json", updated)
        self.assertIn("fillRecognitionLanguageChecks(s)", updated)
        self.assertEqual(patch.patch_ui(updated), updated)

    def test_config_migrates_legacy_apple_vision_locales_and_preserves_other_keys(self):
        source = json.dumps({
            "apple_ocr_languages": ["ru-RU", "en-US"],
            "visual_user_prompt": "keep this",
            "custom_setting": 17,
        })
        updated = json.loads(patch.patch_config(source))
        self.assertEqual(updated["recognition_languages"], ["ru", "en"])
        self.assertEqual(updated["visual_user_prompt"], "keep this")
        self.assertEqual(updated["custom_setting"], 17)


if __name__ == "__main__":
    unittest.main()
