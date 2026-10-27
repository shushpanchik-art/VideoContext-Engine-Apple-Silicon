from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "bench_text_llm_summary.py"
SPEC = importlib.util.spec_from_file_location("bench_text_llm_summary", SCRIPT)
BENCH = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BENCH)


class TextLLMBenchmarkProvenanceTests(unittest.TestCase):
    def test_parser_strips_think_block_and_keeps_only_model_json(self):
        raw = (
            "<think>private reasoning that must not be published</think>\n"
            '{"executive_summary":"A cat appears on screen.","scene_enrichment":[]}'
        )
        parsed = BENCH.parse_primary_model_output(raw)
        self.assertEqual(
            parsed,
            {"executive_summary": "A cat appears on screen.", "scene_enrichment": []},
        )
        self.assertNotIn("private reasoning", str(parsed))

    def test_parser_returns_none_for_non_json_or_non_object(self):
        self.assertIsNone(BENCH.parse_primary_model_output(""))
        self.assertIsNone(BENCH.parse_primary_model_output("not json"))
        self.assertIsNone(BENCH.parse_primary_model_output('["not", "an", "object"]'))

    def test_output_record_keeps_raw_model_and_final_payload_distinct(self):
        raw = {
            "executive_summary": "A cat appears on screen.",
            "scene_enrichment": [],
        }
        finalized = {
            "executive_summary": "A cat appears on screen.",
            "scene_enrichment": [{
                "scene_id": 1,
                "topic": "Visual content",
                "improved_description": "A cat appears on screen.",
                "key_points": [],
            }],
        }
        metrics = {
            "total_seconds": 12.5,
            "output_tokens": 200,
            "decode_tokens_per_second": 16.0,
            "prompt_rewrite_ok": True,
            "generated_scene_enrichment_count": 0,
            "generated_scene_enrichment_empty": True,
        }
        record = BENCH.build_output_record(1, finalized, raw, metrics)
        self.assertEqual(record["output"], finalized)
        self.assertEqual(record["raw_model_output"], raw)
        self.assertNotEqual(
            record["raw_model_output"]["scene_enrichment"],
            record["output"]["scene_enrichment"],
        )
        self.assertEqual(
            record["output_provenance"]["raw_model_output"],
            "parsed_primary_model_json_before_vce_finalize",
        )
        self.assertTrue(record["output_provenance"]["prompt_rewrite_applied"])
        self.assertEqual(record["output_provenance"]["raw_scene_enrichment_count"], 0)


    def test_focused_quality_uses_full_output_baseline_for_4b(self):
        variants = BENCH.benchmark_variants(focused_quality=True)
        chosen = BENCH.comparison_reference_variant("B_fast_summary", focused_quality=True)
        flags = BENCH.flags_for_variant(variants, chosen)

        self.assertEqual(chosen, "baseline")
        self.assertEqual(flags, {})
        self.assertNotIn("fast", flags)

    def test_nonfocused_benchmark_retains_selected_variant_flags(self):
        variants = BENCH.benchmark_variants(focused_quality=False)
        chosen = BENCH.comparison_reference_variant("B_fast_summary", focused_quality=False)
        flags = BENCH.flags_for_variant(variants, chosen)

        self.assertEqual(chosen, "B_fast_summary")
        self.assertTrue(flags.get("fast"))


if __name__ == "__main__":
    unittest.main()
