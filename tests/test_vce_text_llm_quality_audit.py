#!/usr/bin/env python3
"""Tests for universal-default Text LLM quality auditing."""
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
            "audio_transcript": "A speaker introduces the subject and defines the first term.",
            "ocr_text": "Document title 101",
            "visual_description": "A title slide with a document name.",
            "visual_tags": {},
        },
        {
            "scene_id": 2, "start": 3.0, "end": 6.0,
            "audio_transcript": "The speaker explains the process and mentions value 12.5.",
            "ocr_text": "Step 2",
            "visual_description": "A software interface with one selected item.",
            "visual_tags": {},
        },
        {
            "scene_id": 3, "start": 6.0, "end": 9.0,
            "audio_transcript": "The video ends.",
            "ocr_text": "THE END",
            "visual_description": "A closing frame with a title.",
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
            "The video introduces a subject, demonstrates a process in a software interface, "
            "and ends with a closing frame. Numeric values are included only when relevant to the explanation."
        ),
        "topics": ["Document overview", "Process demonstration"],
        "key_points": [
            {"scene_id": 1, "time": "00:00:00", "text": "The speaker introduces the subject.", "evidence": ["speech"]},
            {"scene_id": 2, "time": "00:00:03", "text": "The process is demonstrated in the interface.", "evidence": ["visual"]},
        ],
        "entities": [{"name": "Document", "type": "other", "scene_ids": [1]}],
        "scene_enrichment": [
            {"scene_id": 1, "topic": "Introduction", "improved_description": "The speaker introduces the subject.", "key_points": []},
            {"scene_id": 2, "topic": "Demonstration", "improved_description": "The interface shows the selected process.", "key_points": []},
            {"scene_id": 3, "topic": "Conclusion", "improved_description": "The video ends with a closing frame.", "key_points": []},
        ],
        "uncertainties": [],
    }
    return segments, evidence, output


class QualityAuditTests(unittest.TestCase):
    def test_generic_profile_accepts_selective_key_points_when_all_scenes_are_enriched(self):
        segments, evidence, output = make_fixture()
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["audit_profile"], "generic")
        self.assertEqual(result["quality_score_scope"], "structure_and_text_integrity_only")
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["scene_coverage_pct"], 100.0)
        self.assertAlmostEqual(result["key_point_scene_coverage_pct"], 66.7, places=1)
        self.assertEqual(result["missing_scene_ids"], [])
        self.assertIsNone(result["technical_atom_coverage_pct"])
        self.assertFalse(result["domain_specific_checks_enabled"])

    def test_empty_optional_arrays_are_valid_when_the_schema_field_exists(self):
        segments, evidence, output = make_fixture()
        output["topics"] = []
        output["entities"] = []
        output["uncertainties"] = []
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertNotIn("missing_required_fields:topics,entities,uncertainties", result["hard_issues"])
        self.assertNotIn("missing_required_fields", " ".join(result["hard_issues"]))

    def test_missing_scene_enrichment_is_a_hard_blocker_for_any_video(self):
        segments, evidence, output = make_fixture()
        output["scene_enrichment"] = output["scene_enrichment"][:2]
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["status"], "HOLD")
        self.assertIn("missing_scene_enrichment:3", result["hard_issues"])
        self.assertEqual(result["scene_coverage_pct"], 66.7)

    def test_invalid_and_duplicate_scene_ids_are_generic_blockers(self):
        segments, evidence, output = make_fixture()
        output["scene_enrichment"][1]["scene_id"] = 1
        output["key_points"][0]["scene_id"] = 999
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertIn("duplicate_scene_enrichment_ids:1", result["hard_issues"])
        self.assertIn("unexpected_key_point_scene_ids:999", result["hard_issues"])
        self.assertEqual(result["status"], "HOLD")

    def test_generic_profile_does_not_apply_video_specific_keywords(self):
        segments, evidence, output = make_fixture()
        output["executive_summary"] = (
            "The video shows a closing frame. A speaker mentions a collision and a mismatch during the earlier discussion. "
            "The visible ending is a title card, not proof of the earlier result."
        )
        output["topics"].extend(["АИ ИИ ПОМОЩНИК", "КСИ", "HDB_V5"])
        output["key_points"][0]["text"] = "AND JU"
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["audit_profile"], "generic")
        self.assertFalse(result["domain_specific_checks_enabled"])
        self.assertEqual(result["closing_title_evidence"], None)
        self.assertNotIn("closing_title_conflicts_with_summary_visual_result_claim", result["hard_issues"])
        self.assertNotIn("summary_result_claim_not_attributed_to_speech", result["hard_issues"])
        self.assertFalse(any(issue.startswith("suspected_ocr_garbled_topic") for issue in result["hard_issues"]))
        self.assertNotIn("garbled_or_truncated_output_text", result["hard_issues"])

    def test_generic_profile_detects_encoding_and_control_corruption(self):
        segments, evidence, output = make_fixture()
        output["topics"].append("Broken text \ufffd")
        result = audit_module.quality_run(output, segments, [1, 2, 3], evidence, {})
        self.assertEqual(result["status"], "HOLD")
        self.assertIn("garbled_or_truncated_output_text", result["hard_issues"])

    def test_sample_diagnostic_profile_is_opt_in(self):
        segments, evidence, output = make_fixture()
        output["executive_summary"] = (
            "The ending screen visibly confirms a collision and mismatch in the process result. "
            "The last frame is a closing title, so this needs source-specific review."
        )
        result = audit_module.quality_run(
            output, segments, [1, 2, 3], evidence, {}, profile="nsr-case-study"
        )
        self.assertEqual(result["audit_profile"], "nsr-case-study")
        self.assertTrue(result["domain_specific_checks_enabled"])
        # This opt-in profile is intentionally sample-oriented; generic mode above never applies it.
        self.assertIsNotNone(result["closing_title_evidence"])

    def test_known_sample_corruption_signatures_are_profile_scoped(self):
        for value in ("КСИ", "HDB_V5", "THE END", "АИ ИИ ПОМОЩНИК"):
            self.assertFalse(audit_module.is_garbled(value, profile="generic"))
        self.assertFalse(audit_module.is_garbled("КСИ", profile="nsr-case-study"))
        self.assertTrue(audit_module.is_garbled("AND JU", profile="nsr-case-study"))
        self.assertTrue(audit_module.is_garbled("D_", profile="nsr-case-study"))
        self.assertTrue(audit_module.is_garbled("Text \ufffd", profile="generic"))

    def test_hyphenated_term_aliases_normalize_equally(self):
        self.assertEqual(
            audit_module.normalize_atom("term helper"),
            audit_module.normalize_atom("term-helper"),
        )

    def test_json_repair_and_token_limit_still_block_generically(self):
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
