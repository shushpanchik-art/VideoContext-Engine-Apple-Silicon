from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vce_compare_runs.py"
SPEC = importlib.util.spec_from_file_location("vce_compare_runs", SCRIPT)
COMPARE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(COMPARE)


class VCECompareRunsTests(unittest.TestCase):
    def test_profile_file_accepts_named_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({
                "profiles": [
                    {"name": "baseline", "settings": {}},
                    {"name": "medium", "settings": {"whisper_model": "medium"}},
                ]
            }), encoding="utf-8")
            profiles = COMPARE.load_profiles(path)
            self.assertEqual([p["name"] for p in profiles], ["baseline", "medium"])
            self.assertEqual(profiles[1]["settings"], {"whisper_model": "medium"})

    def test_rejects_unknown_setting_before_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({
                "profiles": [{"name": "bad", "settings": {"random_switch": True}}]
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "random_switch"):
                COMPARE.load_profiles(path)

    def test_mode_fields_match_web_ui_without_mutating_base_settings(self):
        base = {"analysis_mode": "full", "whisper_model": "large", "generate_txt": True}
        form = COMPARE.effective_settings(base, {"analysis_mode": "ocr_only"})
        self.assertEqual(form["analysis_mode"], "ocr_only")
        self.assertEqual(form["skip_audio"], "true")
        self.assertEqual(form["skip_visual"], "true")
        self.assertEqual(form["generate_txt"], "false")
        self.assertEqual(base["generate_txt"], True)
        self.assertEqual(form["whisper_model"], "large")

    def test_form_booleans_are_lowercase_and_overrides_are_per_run(self):
        base = {"enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5}
        form = COMPARE.effective_settings(base, {"enable_ffmpeg_ocr": True, "ffmpeg_ocr_interval": 2})
        self.assertEqual(form["enable_ffmpeg_ocr"], "true")
        self.assertEqual(form["ffmpeg_ocr_interval"], "2")
        self.assertFalse(base["enable_ffmpeg_ocr"])

    def test_report_extracts_transcript_and_summary(self):
        payload = {
            "meta": {"source": "lecture.mp4", "duration": 60, "process_time": 30,
                     "scene_count": 1, "global_summary": "Итог лекции"},
            "segments": [{"start": 0, "end": 4, "audio_transcript": "Привет, мир",
                          "visual_description": "На экране слайд"}],
        }
        report = COMPARE.make_report("test", payload, 31.4)
        self.assertIn("Итог лекции", report)
        self.assertIn("Речь: Привет, мир", report)
        self.assertIn("Визуально: На экране слайд", report)
        self.assertIn("00:00:00.000", report)


if __name__ == "__main__":
    unittest.main()
