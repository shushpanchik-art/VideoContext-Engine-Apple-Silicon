#!/usr/bin/env python3
"""Tests for quality-first Text LLM audit semantics."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "quality_check_text_llm_variants.py"
SPEC = importlib.util.spec_from_file_location("vce_quality_audit", MODULE_PATH)
audit_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(audit_module)


def make_fixture():
    segments = [
        {
            "scene_id": 1, "start": 0.0, "end": 3.0,
            "audio_transcript": "Пользователь ищет требование СП 1.13130.2020.",
            "ocr_text": "NSR Specification",
            "visual_description": "Экран поиска в NSR Specification.",
            "visual_tags": {},
        },
        {
            "scene_id": 2, "start": 3.0, "end": 6.0,
            "audio_transcript": "Нормативный уклон пандуса не более 1:20.",
            "ocr_text": "СП 1.13130.2020",
            "visual_description": "Показаны данные требования.",
            "visual_tags": {},
        },
        {
            "scene_id": 3, "start": 6.0, "end": 9.0,
            "audio_transcript": "Видео завершается.",
            "ocr_text": "THE END",
            "visual_description": "Финальная заставка THE END.",
            "visual_tags": {},
        },
    ]
    evidence = "\n".join(
        str(seg.get(key) or "")
        for seg in segments
        for key in ("audio_transcript", "ocr_text", "visual_description")
    )
    output = {
        "executive_summary": (
            "Видео показывает поиск строительного требования, его проверку и финальную заставку. "
            "Числовое ограничение передаётся как 1:20 только там, где оно подтверждено источником."
        ),
        "topics": ["NSR Specification", "СП 1.13130.2020"],
        "key_points": [
            {
                "scene_id": 1, "time": "00:00:00",
                "text": "Пользователь ищет требование СП 1.13130.2020.",
                "evidence": ["speech", "ocr"],
            },
            {
                "scene_id": 2, "time": "00:00:03",
                "text": "Допустимый уклон пандуса указан как 1:20.",
                "evidence": ["speech", "ocr"],
            },
        ],
        "entities": [
            {"name": "NSR Specification", "type": "product", "scene_ids": [1]},
            {"name": "СП 1.13130.2020", "type": "standard", "scene_ids": [1, 2]},
        ],
        "scene_enrichment": [
            {"scene_id": 1, "topic": "Поиск требования",
             "improved_description": "Поиск строительного требования в NSR Specification.", "key_points": []},
            {"scene_id": 2, "topic": "Нормативное значение",
             "improved_description": "Указан нормативный уклон пандуса 1:20.", "key_points": []},
            {"scene_id": 3, "topic": "Завершение",
             "improved_description": "Финальная заставка с текстом THE END.", "key_points": []},
        ],
        "uncertainties": [],
    }
    return segments, evidence, output


class QualityAuditTests(unittest.TestCase):
    def test_selective_key_points_are_allowed_when_every_scene_is_enriched(self):
        segments, evidence, output = make_fixture()
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["scene_coverage_pct"], 100.0)
        self.assertAlmostEqual(result["key_point_scene_coverage_pct"], 66.7, places=1)
        self.assertEqual(result["missing_scene_ids"], [])
        self.assertNotIn("missing_scene_points:3", result["hard_issues"])

    def test_missing_scene_enrichment_is_a_hard_blocker(self):
        segments, evidence, output = make_fixture()
        output["scene_enrichment"] = output["scene_enrichment"][:2]
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["status"], "HOLD")
        self.assertIn("missing_scene_enrichment:3", result["hard_issues"])
        self.assertEqual(result["scene_coverage_pct"], 66.7)

    def test_garbled_key_point_blocks_candidate(self):
        segments, evidence, output = make_fixture()
        output["key_points"][1]["text"] = "AND JU"
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["status"], "HOLD")
        self.assertIn("garbled_or_truncated_output_text", result["hard_issues"])
        self.assertTrue(result["garbled_output_fragments"])

    def test_garbled_scene_enrichment_also_blocks_candidate(self):
        segments, evidence, output = make_fixture()
        output["scene_enrichment"][2]["improved_description"] = "Заставка D_ NC END"
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["status"], "HOLD")
        self.assertIn("garbled_or_truncated_output_text", result["hard_issues"])

    def test_hyphenated_term_aliases_normalize_equally(self):
        self.assertEqual(
            audit_module.normalize_atom("ИИ помощник"),
            audit_module.normalize_atom("ИИ-помощник"),
        )

    def test_json_repair_and_token_limit_still_block(self):
        segments, evidence, output = make_fixture()
        result = audit_module.quality_run(
            output, segments, [1, 2, 3],
            evidence, {"json_repair_passes": 1, "json_parse_failures": 1,
                       "primary_output_near_token_limit": True},
        )
        self.assertEqual(result["status"], "HOLD")
        self.assertTrue(any(issue.startswith("json_repair_or_parse_failure") for issue in result["hard_issues"]))
        self.assertIn("primary_output_near_token_limit", result["hard_issues"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
