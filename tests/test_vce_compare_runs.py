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


    def test_report_includes_structured_analysis_and_stage_timings(self):
        payload = {
            "meta": {
                "source": "lecture.mp4", "duration": 60, "process_time": 30,
                "scene_count": 1, "global_summary": "Raw summary",
                "timings": {
                    "whisper": {"inference_time": 4.2},
                    "vlm": {"inference_time": 7.5},
                    "apple_ocr": {"inference_time": 0.8},
                    "ffmpeg_ocr": {"inference_time": 0.0},
                },
            },
            "enhanced_analysis": {
                "executive_summary": "Structured summary",
                "topics": ["Building code"],
                "key_points": [{"scene_id": 1, "time": "00:00:01", "text": "Ramp slope rule", "evidence": ["ocr"]}],
                "entities": [{"name": "SP 1.13130.2020", "type": "standard", "scene_ids": [1]}],
                "uncertainties": ["The final violation is not visually confirmed."],
                "scene_enrichment": [{"scene_id": 1, "topic": "Search", "improved_description": "Answer shown on screen", "key_points": ["The value 1:20 is visible."]}],
            },
            "text_llm": {"status": "success", "model": "Qwen3.5", "inference_time": 3.1},
            "segments": [{"start": 0, "end": 4, "audio_transcript": "Ramp slope", "ocr_text": "1:20", "ffmpeg_ocr_text": "Extra text"}],
        }
        report = COMPARE.make_report("baseline", payload, 31.4)
        for expected in ("Structured summary", "Raw summary", "Building code", "Ramp slope rule",
                         "SP 1.13130.2020", "uncertainties", "3.10", "7.50", "Apple OCR"):
            self.assertIn(expected.casefold(), report.casefold())

    def test_stage_time_extraction_uses_all_available_models(self):
        payload = {
            "meta": {"timings": {"whisper": {"inference_time": 1.0}, "vlm": {"inference_time": 2.0}}},
            "text_llm": {"inference_time": 3.0},
        }
        self.assertEqual(COMPARE._stage_times(payload), [("Whisper", 1.0), ("Qwen3-VL", 2.0), ("Qwen3.5 text", 3.0)])

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
