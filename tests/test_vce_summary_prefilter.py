#!/usr/bin/env python3
"""Regression tests for the CPU-summary-prefilter feasibility benchmark."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import benchmark_vce_summary_prefilter as exp  # noqa: E402


class SummaryPrefilterTests(unittest.TestCase):
    @patch.object(exp.subprocess, "run")
    def test_worker_environment_probe_reports_missing_mlx_lm(self, mocked_run):
        mocked_run.return_value = type("Probe", (), {
            "returncode": 1, "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'mlx_lm'\n"
        })()
        ok, detail = exp.check_worker_environment("/tmp/python")
        self.assertFalse(ok)
        self.assertIn("mlx_lm", detail)

    @patch.object(exp.subprocess, "run")
    def test_worker_environment_probe_accepts_successful_imports(self, mocked_run):
        mocked_run.return_value = type("Probe", (), {
            "returncode": 0, "stdout": "MLX_LM_IMPORT_OK\n", "stderr": ""
        })()
        ok, detail = exp.check_worker_environment("/tmp/python")
        self.assertTrue(ok)
        self.assertEqual(detail, "")

    def test_scene_records_keep_raw_evidence_but_drop_derived_fields(self):
        payload = {
            "meta": {"source": "/private/video.mp4", "global_summary": "A summary"},
            "segments": [{
                "scene_id": 7, "start": 2.0, "end": 4.5,
                "audio_transcript": "Speaker explains the process.",
                "visual_description": "A diagram is shown.",
                "ocr_text": "SP 1.13130.2020",
                "visual_tags": {"main_action": "explaining"},
                "text_llm": {"improved_description": "Derived text should not enter source evidence."},
            }],
        }
        records, truncated = exp.scene_records(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scene_id"], 7)
        self.assertEqual(records[0]["audio"], "Speaker explains the process.")
        self.assertEqual(records[0]["ocr"], "SP 1.13130.2020")
        self.assertNotIn("text_llm", records[0])
        self.assertNotIn("/private/video.mp4", json.dumps(records))
        self.assertEqual(truncated, {"audio": 0, "visual": 0, "ocr": 0})

    def test_scene_evidence_fields_are_capped_and_counted(self):
        payload = {"segments": [{
            "scene_id": 1, "audio_transcript": "A" * 20,
            "visual_description": "B" * 4, "ocr_text": "C" * 13,
        }]}
        records, truncated = exp.scene_records(payload, max_field_chars=10)
        self.assertEqual(len(records[0]["audio"]), 10 + len(" …[truncated]"))
        self.assertEqual(truncated["audio"], 10)
        self.assertEqual(truncated["visual"], 0)
        self.assertEqual(truncated["ocr"], 3)

    def test_chunking_preserves_all_scene_ids_in_order(self):
        records = [{"scene_id": n} for n in range(1, 14)]
        chunks = exp.chunk_records(records, 5)
        self.assertEqual([len(chunk) for chunk in chunks], [5, 5, 3])
        self.assertEqual([r["scene_id"] for chunk in chunks for r in chunk], list(range(1, 14)))

    def test_context_includes_time_and_distinct_evidence_types(self):
        rows = [{
            "scene_id": 3, "start": 1.25, "end": 7.0,
            "audio": "Spoken fact", "visual": "Visible chart",
            "visual_tags": {"main_action": "explaining"}, "ocr": "Value: 42",
        }]
        context = exp.full_evidence_context(rows)
        self.assertIn("SCENE 3 (1.2-7.0s)", context)
        self.assertIn("SPEECH: Spoken fact", context)
        self.assertIn("VISUAL: Visible chart", context)
        self.assertIn("OCR: Value: 42", context)

    def test_prefilter_prompt_demands_scene_citations_and_json(self):
        prompt = exp.prefilter_prompt([{
            "scene_id": 9, "start": 0, "end": 1, "audio": "fact",
            "visual": "", "visual_tags": {}, "ocr": "",
        }], 1)
        self.assertIn('"scene_ids"', prompt)
        self.assertIn("валидный JSON", prompt)
        self.assertIn("Не добавляй знаний от себя", prompt)

    def test_json_parser_handles_fenced_or_explanatory_output(self):
        value = exp.parse_json_fragment('Here is JSON: {"facts":[{"claim":"x"}]}')
        self.assertEqual(value["facts"][0]["claim"], "x")

    def test_summary_prompt_preserves_custom_instruction(self):
        prompt = exp.summary_prompt("scene evidence", "Keep standards exact.")
        self.assertIn("scene evidence", prompt)
        self.assertIn("Keep standards exact.", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
