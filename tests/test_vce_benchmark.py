from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vce_benchmark.py"
SPEC = importlib.util.spec_from_file_location("vce_benchmark", SCRIPT)
BENCH = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BENCH)


class VCEBenchmarkTests(unittest.TestCase):
    def test_edit_distance_and_russian_word_normalization(self):
        self.assertEqual(BENCH.edit_distance(["я", "вижу", "экран"], ["я", "вижу", "слайд"]), 1)
        self.assertEqual(BENCH.normalize_words("Привет, МИР!  СП 1.13130.2020"), ["привет", "мир", "сп", "1", "13130", "2020"])

    def test_cer_preserves_punctuation_and_unicode_normalization(self):
        reference = "Ёж. СП 1.13130.2020"
        hypothesis = "ёж СП 1.13130.2020"
        self.assertEqual(BENCH.normalize_text(reference), "ёж. сп 1.13130.2020")
        self.assertGreater(BENCH.edit_distance(list(BENCH.normalize_text(reference)), list(BENCH.normalize_text(hypothesis))), 0)

    def test_manifest_text_files_scoring_and_speed_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ref.txt").write_text("Привет, мир. СП 1.13130.2020", encoding="utf-8")
            (root / "hyp.txt").write_text("Привет мир. СП 1.13130.2020", encoding="utf-8")
            record = {
                "case_id": "ru-01", "run_id": "baseline",
                "reference_text_path": "ref.txt", "hypothesis_text_path": "hyp.txt",
                "video_seconds": 100, "wall_seconds": 125,
                "stage_seconds": {"whisper": 20, "ocr": 5},
                "expected_terms": ["СП 1.13130.2020", "Привет"],
            }
            (root / "manifest.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            records = BENCH.load_manifest(root / "manifest.jsonl")
            item = records[0]
            self.assertEqual(item["case_id"], "ru-01")
            self.assertAlmostEqual(item["rtf"], 1.25)
            self.assertEqual(item["term_recall"], 1.0)
            self.assertLess(item["wer"], 0.5)
            summary = BENCH.summarize(records, baseline="baseline")
            self.assertEqual(summary["runs"]["baseline"]["case_count"], 1)
            out = root / "results"
            BENCH.write_outputs(records, summary, out)
            self.assertTrue((out / "per_case.csv").is_file())
            self.assertIn("baseline", (out / "summary.md").read_text(encoding="utf-8"))

    def test_json_pointer_wildcard_and_paired_run_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ref.txt").write_text("один два", encoding="utf-8")
            (root / "baseline.txt").write_text("один два", encoding="utf-8")
            (root / "candidate.txt").write_text("один три", encoding="utf-8")
            payload = {"audio_transcript": {"segments": [{"text": "один"}, {"text": "два"}]}}
            (root / "result.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            baseline = {
                "case_id": "json-01", "run_id": "baseline",
                "reference_text_path": "ref.txt", "hypothesis_json_path": "result.json",
                "hypothesis_json_pointer": "/audio_transcript/segments/*/text",
                "video_seconds": 100, "wall_seconds": 80,
            }
            candidate = {
                "case_id": "json-01", "run_id": "candidate",
                "reference_text_path": "ref.txt", "hypothesis_text_path": "candidate.txt",
                "video_seconds": 100, "wall_seconds": 60,
            }
            (root / "manifest.jsonl").write_text(
                json.dumps(baseline, ensure_ascii=False) + "\n" + json.dumps(candidate, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            records = BENCH.load_manifest(root / "manifest.jsonl")
            self.assertEqual(records[0]["hypothesis"], "один два")
            summary = BENCH.summarize(records, baseline="baseline")
            self.assertEqual(summary["comparisons"]["candidate"]["paired_case_count"], 1)
            self.assertGreater(summary["comparisons"]["candidate"]["mean_wer_delta_candidate_minus_baseline"], 0)
            self.assertEqual(summary["comparisons"]["candidate"]["rtf_change_percent"], -25.0)

    def test_empty_reference_is_not_reported_as_zero_error_rate(self):
        item = {
            "case_id": "silent", "run_id": "default", "reference": "", "hypothesis": "текст",
            "reference_source": "ref", "hypothesis_source": "hyp",
            "video_seconds": None, "wall_seconds": None, "stage_seconds": {}, "expected_terms": [],
        }
        score = BENCH._score_record(item)
        self.assertIsNone(score["wer"])
        self.assertIsNone(score["cer"])
        self.assertIsNone(score["rtf"])

    def test_duplicate_run_case_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ref.txt").write_text("тест", encoding="utf-8")
            (root / "hyp.txt").write_text("тест", encoding="utf-8")
            record = {"case_id": "same", "run_id": "v1", "reference_text_path": "ref.txt", "hypothesis_text_path": "hyp.txt"}
            (root / "manifest.jsonl").write_text(
                json.dumps(record) + "\n" + json.dumps(record) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "Duplicate case_id"):
                BENCH.load_manifest(root / "manifest.jsonl")


if __name__ == "__main__":
    unittest.main()
