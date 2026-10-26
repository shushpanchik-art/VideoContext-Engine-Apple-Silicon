#!/usr/bin/env python3
"""Regression tests for the deterministic source-linked structural-index experiment."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import benchmark_vce_summary_structural_ab as exp  # noqa: E402


class StructuralIndexTests(unittest.TestCase):
    def test_exact_duplicates_are_grouped_but_all_scene_references_remain(self):
        text = "Температура 20 °C; дата 26.10.2026, ГОСТ 1234-2020; объект «Северный поток»."
        rows = [
            {
                "scene_id": 1, "start": 0.0, "end": 2.0,
                "audio": text, "visual": "На экране SP-01.",
                "ocr": "ГОСТ 1234-2020", "visual_tags": {"main_action": "talking"},
            },
            {
                "scene_id": 2, "start": 2.0, "end": 4.0,
                "audio": text, "visual": "на экране SP-01.",
                "ocr": "ГОСТ 1234-2020", "visual_tags": {"main_action": "talking"},
            },
        ]

        index, stats = exp.build_structural_index(rows)

        self.assertEqual(stats["scene_count"], 2)
        self.assertEqual(stats["scene_ids_preserved"], [1, 2])
        self.assertTrue(stats["duplicate_occurrences_collapsed"] >= 3)
        self.assertIn("Температура 20 °C; дата 26.10.2026, ГОСТ 1234-2020; объект «Северный поток».", index)
        self.assertIn('STANDARD "ГОСТ 1234-2020"', index)
        self.assertIn('DATE "26.10.2026"', index)
        self.assertIn('NUMBER "20 °C"', index)
        self.assertIn("A1 AUDIO [", index)
        self.assertIn('QUOTED "«Северный поток»"', index)
        self.assertIn("S1@0.0-2.0s", index)
        self.assertIn("S2@2.0-4.0s", index)

    def test_case_changes_are_not_deduplicated(self):
        rows = [
            {"scene_id": 1, "audio": "ГОСТ 1234", "visual": "", "ocr": "", "visual_tags": {}},
            {"scene_id": 2, "audio": "гост 1234", "visual": "", "ocr": "", "visual_tags": {}},
        ]
        index, stats = exp.build_structural_index(rows)
        self.assertEqual(stats["unique_evidence_strings"], 2)
        self.assertIn("ГОСТ 1234", index)
        self.assertIn("гост 1234", index)

    def test_empty_evidence_still_preserves_scene_timeline(self):
        rows = [
            {"scene_id": "scene-x", "start": None, "end": None,
             "audio": "", "visual": "", "ocr": "", "visual_tags": {}},
        ]
        index, stats = exp.build_structural_index(rows)
        self.assertIn('S"scene-x"@time unknown', index)
        self.assertIn("no text evidence", index)
        self.assertEqual(stats["unique_evidence_strings"], 0)
        self.assertEqual(stats["scene_ids_preserved"], ["scene-x"])

    def test_saved_summary_prompt_adjustment_is_replayed(self):
        text = "Summary instruction " * 20
        adjusted = exp.apply_saved_prompt_adjustment(
            text,
            [{"field": "summary_user_prompt", "original_characters": len(text), "sent_characters": 31}],
        )
        self.assertEqual(adjusted, text[:31])

    def test_missing_prompt_adjustment_keeps_instruction_intact(self):
        text = "Keep every relevant detail."
        self.assertEqual(exp.apply_saved_prompt_adjustment(text, []), text)

    def test_normalization_only_collapses_whitespace(self):
        self.assertEqual(
            exp.normalize_for_dedup("  value \n\t 42  "),
            "value 42",
        )
        self.assertNotEqual(
            exp.normalize_for_dedup("SP 1.13130"),
            exp.normalize_for_dedup("sp 1.13130"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
