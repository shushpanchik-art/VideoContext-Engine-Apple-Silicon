#!/usr/bin/env python3
"""Regression tests for language- and domain-agnostic evidence-overlap diagnostics."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "evaluate_text_llm_grounding.py"
SPEC = importlib.util.spec_from_file_location("vce_text_llm_grounding", MODULE_PATH)
grounding = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(grounding)


def make_docs():
    source = {
        "segments": [
            {
                "scene_id": 1,
                "start": 0,
                "end": 5,
                "audio_transcript": "Докладчик показывает настройки проекта и сохраняет файл.",
                "visual_description": "A photo editing interface shows an export dialog for a TIFF image.",
                "ocr_text": "Export image",
            },
            {
                "scene_id": 2,
                "start": 5,
                "end": 10,
                "audio_transcript": "The rocket engine starts and the rocket launches toward orbit.",
                "visual_description": "A rocket rises above the launch platform.",
            },
        ]
    }
    outputs = {
        "outputs_by_variant": {
            "fixture_variant": [
                {
                    "iteration": 1,
                    "model": "fixture",
                    "output": {
                        "executive_summary": "Докладчик показывает настройки проекта и сохраняет файл.",
                        "topics": ["Photo editing", "Rocket launch"],
                        "key_points": [
                            {"scene_id": 1, "text": "Докладчик показывает настройки проекта."},
                        ],
                        "entities": [{"name": "Export image"}],
                        "scene_enrichment": [
                            {
                                "scene_id": 1,
                                "topic": "Photo editing",
                                "improved_description": "A rocket launches toward orbit.",
                                "key_points": [],
                            },
                            {
                                "scene_id": 2,
                                "topic": "Rocket launch",
                                "improved_description": "The rocket launches toward orbit.",
                                "key_points": [],
                            },
                        ],
                    },
                }
            ]
        }
    }
    return source, outputs


class TextLLMGroundingTests(unittest.TestCase):
    def test_matching_russian_claim_gets_scene_evidence_pointer(self):
        source, outputs = make_docs()
        report = grounding.build_report(source, outputs, 0.25)
        run = report["variants"]["fixture_variant"]["runs"][0]
        claim = next(c for c in run["claims"] if c["output_field"] == "executive_summary")
        self.assertEqual(claim["lexical_evidence"]["claim_token_recall"], 1.0)
        self.assertTrue(claim["manual_review_required"])
        self.assertIn("audio_transcript", claim["lexical_evidence"]["best_evidence"][0]["field_path"])
        self.assertFalse(report["semantic_correctness_evaluated"])
        self.assertIsNone(report["semantic_quality_score"])

    def test_scene_mismatch_is_flagged_even_when_words_appear_elsewhere(self):
        source, outputs = make_docs()
        report = grounding.build_report(source, outputs, 0.25)
        run = report["variants"]["fixture_variant"]["runs"][0]
        claim = next(
            c for c in run["claims"]
            if c["output_field"] == "scene_enrichment[0].improved_description"
        )
        self.assertEqual(claim["scene_id"], 1)
        self.assertEqual(claim["lexical_evidence"]["claim_token_recall"], 0.0)
        self.assertEqual(claim["whole_video_lexical_evidence"]["claim_token_recall"], 1.0)
        self.assertIn("low_lexical_overlap_requires_manual_review", claim["findings"])
        self.assertIn("overlap_found_elsewhere_in_video_not_in_referenced_scene", claim["findings"])

    def test_high_overlap_is_never_automatically_approved(self):
        source, outputs = make_docs()
        report = grounding.build_report(source, outputs)
        run = report["variants"]["fixture_variant"]["runs"][0]
        self.assertTrue(all(claim["manual_review_required"] for claim in run["claims"]))
        self.assertIsNone(report["semantic_quality_score"])
        self.assertIn("lexical_overlap", report["evaluation_type"])

    def test_arbitrary_spanish_text_is_compared_without_domain_vocabulary(self):
        source = {"segments": [
            {"scene_id": 7, "spoken_words": "El presentador abre la configuración del proyecto y guarda el archivo."}
        ]}
        outputs = {"outputs_by_variant": {"spanish_demo": [{
            "iteration": 1,
            "output": {
                "executive_summary": "El presentador abre la configuración del proyecto y guarda el archivo.",
                "topics": [],
                "key_points": [],
                "entities": [],
                "scene_enrichment": [{
                    "scene_id": 7,
                    "topic": "Configuración del proyecto",
                    "improved_description": "El presentador abre la configuración del proyecto y guarda el archivo.",
                    "key_points": [],
                }],
            },
        }]}}
        report = grounding.build_report(source, outputs, 0.2)
        claim = next(
            c for c in report["variants"]["spanish_demo"]["runs"][0]["claims"]
            if c["output_field"] == "executive_summary"
        )
        self.assertEqual(claim["lexical_evidence"]["claim_token_recall"], 1.0)
        self.assertEqual(claim["lexical_evidence"]["best_evidence"][0]["modality"], "audio_transcript")

    def test_empty_source_text_does_not_get_mislabeled_as_supported(self):
        source = {"segments": [{"scene_id": 1, "start": 0, "end": 2}]}
        outputs = {"outputs_by_variant": {"empty_source": [{
            "iteration": 1,
            "output": {"executive_summary": "An unexpected statement about a distant galaxy."},
        }]}}
        report = grounding.build_report(source, outputs)
        claim = report["variants"]["empty_source"]["runs"][0]["claims"][0]
        self.assertIn("no_textual_source_evidence_in_scope", claim["findings"])
        self.assertEqual(claim["lexical_evidence"]["claim_token_recall"], None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
