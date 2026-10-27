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

    def test_final_title_blocks_summary_claim_that_collision_was_shown(self):
        segments, evidence, output = make_fixture()
        output["executive_summary"] = (
            "Видео показывает поиск норматива и использование CADLib для проверки модели. "
            "В конце показано, что проверка выявила коллизию и уклон пандуса не соответствует нормативу. "
            "Финальная сцена служит подтверждением результата."
        )
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertTrue(result["closing_title_evidence"])
        self.assertTrue(result["summary_visual_overclaim"])
        self.assertIn("closing_title_conflicts_with_summary_visual_result_claim", result["hard_issues"])
        self.assertEqual(result["status"], "HOLD")

    def test_result_claim_supported_by_audio_requires_explicit_attribution(self):
        segments, evidence, output = make_fixture()
        segments[1]["audio_transcript"] = "Диктор сообщает: проверка выявила несоответствие уклона пандуса нормативу."
        output["executive_summary"] = (
            "Видео описывает поиск норматива и проверку модели. Обнаружено несоответствие уклона пандуса нормативу. "
            "Это относится к результату проверки и завершает демонстрацию."
        )
        evidence = "\\n".join(
            str(seg.get(key) or "") for seg in segments
            for key in ("audio_transcript", "ocr_text", "visual_description")
        )
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertTrue(result["summary_has_result_claim"])
        self.assertFalse(result["summary_has_audio_attribution"])
        self.assertIn("summary_result_claim_not_attributed_to_speech", result["hard_issues"])

    def test_result_claim_without_audio_support_is_a_hard_blocker(self):
        segments, evidence, output = make_fixture()
        output["executive_summary"] = (
            "Видео показывает поиск норматива и работу с CADLib. Обнаружено несоответствие уклона пандуса нормативу. "
            "В исходной речи нет такого утверждения, а последний кадр содержит только финальную заставку."
        )
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertIn("summary_result_claim_not_found_in_audio_despite_closing_title", result["hard_issues"])

    def test_suspicious_ocr_topic_blocks_candidate(self):
        segments, evidence, output = make_fixture()
        output["topics"].append("АИ ИИ ПОМОЩНИК")
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertIn("suspected_ocr_garbled_topic:АИ ИИ ПОМОЩНИК", result["hard_issues"])

    def test_legitimate_acronyms_and_identifiers_are_not_garbled(self):
        self.assertFalse(audit_module.is_garbled("КСИ"))
        self.assertFalse(audit_module.is_garbled("HDB_V5"))
        self.assertFalse(audit_module.is_garbled("THE END"))
        self.assertTrue(audit_module.is_garbled("AND JU"))
        self.assertTrue(audit_module.is_garbled("D_"))

    def test_audio_attributed_result_and_closing_title_can_coexist(self):
        segments, evidence, output = make_fixture()
        segments[1]["audio_transcript"] = (
            "По словам диктора, проверка выявила несоответствие уклона пандуса нормативу."
        )
        output["executive_summary"] = (
            "По словам диктора, проверка выявила несоответствие уклона пандуса нормативу. "
            "Визуально финальная сцена представляет собой заставку THE END."
        )
        evidence = "\\n".join(
            str(seg.get(key) or "") for seg in segments
            for key in ("audio_transcript", "ocr_text", "visual_description")
        )
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertTrue(result["closing_title_evidence"])
        self.assertTrue(result["summary_has_audio_attribution"])
        self.assertFalse(result["summary_visual_overclaim"])
        self.assertNotIn("summary_result_claim_not_attributed_to_speech", result["hard_issues"])
        self.assertNotIn("summary_result_claim_not_found_in_audio_despite_closing_title", result["hard_issues"])

    def test_final_scene_enrichment_cannot_mislabel_closing_title_as_result(self):
        segments, evidence, output = make_fixture()
        output["scene_enrichment"][2]["topic"] = "Результат проверки"
        output["scene_enrichment"][2]["improved_description"] = (
            "В модели обнаружена коллизия: уклон пандуса не соответствует нормативу."
        )
        output["scene_enrichment"][2]["key_points"] = ["Обнаружено несоответствие нормативу."]
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertIn("final_scene_enrichment_claims_result_without_audio_attribution", result["hard_issues"])
        self.assertEqual(result["status"], "HOLD")

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
