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
        self.assertEqual(claim["best_other_scene_lexical_evidence"]["scene_id"], 2)
        markdown = grounding.build_markdown(report)
        self.assertIn("scene 2:", markdown)

    def test_distributed_generic_words_do_not_create_a_scene_mismatch(self):
        source = {"segments": [
            {"scene_id": 1, "audio_transcript": "A person opens a blank notebook and writes a single line."},
            {"scene_id": 2, "audio_transcript": "Scene footage was archived safely."},
            {"scene_id": 3, "audio_transcript": "Data was stored separately."},
        ]}
        outputs = {"outputs_by_variant": {"generic_claim": [{
            "iteration": 1,
            "output": {
                "scene_enrichment": [{
                    "scene_id": 1,
                    "topic": "General overview",
                    "improved_description": "This scene shows general technical work with data.",
                    "key_points": [],
                }],
            },
        }]}}
        report = grounding.build_report(source, outputs, min_token_recall=0.15)
        run = report["variants"]["generic_claim"]["runs"][0]
        claim = next(c for c in run["claims"]
                     if c["output_field"] == "scene_enrichment[0].improved_description")

        # "scene" and "data" appear somewhere in the video, but only in
        # separate scenes; the union must not be mistaken for one other scene
        # supporting this description.
        self.assertGreaterEqual(
            claim["whole_video_lexical_evidence"]["claim_token_recall"], 0.15
        )
        self.assertNotIn(
            "overlap_found_elsewhere_in_video_not_in_referenced_scene",
            claim["findings"],
        )
        self.assertEqual(claim["diagnostic_status"], "low_overlap_review_cue")

    def test_split_tokens_across_fields_in_one_other_scene_do_not_create_a_scene_mismatch(self):
        source = {"segments": [
            {
                "scene_id": 1,
                "audio_transcript": "Докладчик открывает пустой документ и ожидает.",
            },
            {
                "scene_id": 2,
                "visual_description": "Результат отображается поверх окна.",
                "ocr_text": "Проверки завершены.",
            },
        ]}
        outputs = {"outputs_by_variant": {"split_field_claim": [{
            "iteration": 1,
            "output": {
                "scene_enrichment": [{
                    "scene_id": 1,
                    "topic": "Результат проверки",
                    "key_points": [],
                }],
            },
        }]}}
        report = grounding.build_report(source, outputs, min_token_recall=0.15)
        claim = report["variants"]["split_field_claim"]["runs"][0]["claims"][0]

        # The whole other scene contains both words, but each independent source
        # field contains only one. That is too weak to infer scene misattribution.
        self.assertEqual(claim["lexical_evidence"]["claim_token_recall"], 0.0)
        self.assertEqual(claim["whole_video_lexical_evidence"]["claim_token_recall"], 1.0)
        self.assertEqual(claim["best_other_scene_lexical_evidence"]["scene_id"], 2)
        self.assertNotIn(
            "overlap_found_elsewhere_in_video_not_in_referenced_scene",
            claim["findings"],
        )
        self.assertIn("low_lexical_overlap_requires_manual_review", claim["findings"])

    def test_individual_evidence_match_wins_over_split_field_union(self):
        source = {"segments": [
            {
                "scene_id": 1,
                "audio_transcript": "Докладчик открывает пустой документ и ожидает.",
            },
            {
                "scene_id": 2,
                "visual_description": "Результат отображается поверх окна.",
                "ocr_text": "Проверки завершены.",
            },
            {
                "scene_id": 3,
                "audio_transcript": "Результат проверки подтверждён.",
            },
        ]}
        outputs = {"outputs_by_variant": {"candidate_selection": [{
            "iteration": 1,
            "output": {
                "scene_enrichment": [{
                    "scene_id": 1,
                    "topic": "Результат проверки",
                    "key_points": [],
                }],
            },
        }]}}
        report = grounding.build_report(source, outputs, min_token_recall=0.15)
        claim = report["variants"]["candidate_selection"]["runs"][0]["claims"][0]

        # Scene 2 aggregates both words across two weak fields; scene 3 has
        # one independent field that supports both. Prefer the latter pointer.
        self.assertIn(
            "overlap_found_elsewhere_in_video_not_in_referenced_scene",
            claim["findings"],
        )
        self.assertEqual(claim["best_other_scene_lexical_evidence"]["scene_id"], 3)
        self.assertEqual(
            claim["best_other_scene_lexical_evidence"]["best_evidence"][0]["matched_claim_token_count"],
            2,
        )

    def test_raw_model_output_is_a_separate_audit_stage(self):
        source = {"segments": [
            {"scene_id": 1, "audio_transcript": "A cat appears on screen."}
        ]}
        outputs = {"outputs_by_variant": {"fast_summary": [{
            "iteration": 1,
            "output": {
                "executive_summary": "A cat appears on screen.",
                "scene_enrichment": [{
                    "scene_id": 1,
                    "topic": "Technical stage",
                    "improved_description": "Generic work with data.",
                    "key_points": ["This scene shows technical work with data."],
                }],
            },
            "raw_model_output": {
                "executive_summary": "A cat appears on screen.",
                "scene_enrichment": [],
            },
            "output_provenance": {
                "output": "vce_finalized_payload_after_postprocess",
                "raw_model_output": "parsed_primary_model_json_before_vce_finalize",
                "prompt_rewrite_applied": True,
                "raw_scene_enrichment_count": 0,
                "raw_scene_enrichment_empty": True,
            },
        }]}}
        report = grounding.build_report(source, outputs)
        final_run = report["variants"]["fast_summary"]["runs"][0]
        raw_run = report["variants"]["fast_summary [raw model output]"]["runs"][0]

        self.assertEqual(final_run["output_stage"], "finalized_output")
        self.assertEqual(raw_run["output_stage"], "raw_model_output")
        self.assertTrue(any(
            claim["output_field"] == "scene_enrichment[0].topic"
            for claim in final_run["claims"]
        ))
        self.assertFalse(any(
            claim["output_field"].startswith("scene_enrichment[")
            for claim in raw_run["claims"]
        ))
        self.assertTrue(all(
            claim["output_stage"] == "raw_model_output"
            for claim in raw_run["claims"]
        ))
        markdown = grounding.build_markdown(report)
        self.assertIn("fast_summary [raw model output]", markdown)
        self.assertIn("deterministic fallback text", markdown)

    def test_unparseable_raw_model_text_emits_provenance_warning(self):
        source = {"segments": [
            {"scene_id": 1, "audio_transcript": "A cat appears on screen."}
        ]}
        outputs = {"outputs_by_variant": {"4b_candidate": [{
            "iteration": 2,
            "output": {"executive_summary": "A cat appears on screen."},
            "raw_model_output": None,
            "raw_model_output_text": '{"executive_summary":"unfinished"',
            "raw_model_output_parse_status": "invalid_json",
            "raw_model_output_parse_diagnostic": "JSONDecodeError line 1 column 35",
        }]}}
        report = grounding.build_report(source, outputs)
        warnings = report["output_provenance_warnings"]

        self.assertTrue(any(
            "sanitized primary model text was retained" in warning
            and "JSON parsing status is invalid_json" in warning
            for warning in warnings
        ))
        markdown = grounding.build_markdown(report)
        self.assertIn("Output provenance warnings", markdown)
        self.assertIn("claim-level raw-output audit is unavailable", markdown)

    def test_legacy_metrics_warn_when_raw_scene_enrichment_is_empty(self):
        source = {"segments": [
            {"scene_id": 1, "audio_transcript": "A cat appears on screen."}
        ]}
        outputs = {"outputs_by_variant": {"fast_summary": [{
            "iteration": 1,
            "output": {
                "executive_summary": "A cat appears on screen.",
                "scene_enrichment": [{
                    "scene_id": 1,
                    "topic": "Technical stage",
                    "improved_description": "Generic work with data.",
                    "key_points": ["This scene shows technical work with data."],
                }],
            },
        }]}}
        metrics = {"runs": [{
            "variant": "fast_summary",
            "iteration": 1,
            "prompt_rewrite_ok": True,
            "generated_scene_enrichment_count": 0,
            "generated_scene_enrichment_empty": True,
        }]}
        report = grounding.build_report(source, outputs, metrics_doc=metrics)
        run = report["variants"]["fast_summary"]["runs"][0]
        self.assertFalse(run["output_provenance"]["raw_model_output_retained"])
        self.assertTrue(run["output_provenance"]["raw_scene_enrichment_empty"])
        self.assertTrue(any(
            "must not be attributed directly to the LLM" in warning
            for warning in report["output_provenance_warnings"]
        ))
        markdown = grounding.build_markdown(report)
        self.assertIn("Output provenance warnings", markdown)
        self.assertIn("legacy artifact did not retain raw model JSON", markdown)

    def test_high_overlap_is_never_automatically_approved_or_reported_as_a_cue(self):
        source, outputs = make_docs()
        report = grounding.build_report(source, outputs)
        run = report["variants"]["fixture_variant"]["runs"][0]
        self.assertTrue(all(claim["manual_review_required"] for claim in run["claims"]))
        claim = next(c for c in run["claims"] if c["output_field"] == "executive_summary")
        self.assertEqual(claim["findings"], [])
        self.assertEqual(claim["diagnostic_status"], "lexical_overlap_observed_semantics_unverified")
        self.assertIsNone(report["semantic_quality_score"])
        self.assertIn("lexical_overlap", report["evaluation_type"])

        compact = grounding.build_markdown(report)
        self.assertNotIn("| executive_summary | whole video |", compact)
        self.assertIn("remain semantically unverified", compact)
        full = grounding.build_markdown(report, include_all_claims=True)
        self.assertIn("TEXT OVERLAP — SEMANTICS UNVERIFIED", full)
        self.assertIn("| executive_summary | whole video |", full)
        self.assertNotIn("lexical_overlap_found_manual_review_still_required", full)

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

    def test_generated_text_llm_and_global_summary_are_never_source_evidence(self):
        source = {
            "meta": {"global_summary": "Violet asteroid destroys research station."},
            "segments": [{
                "scene_id": 1,
                "audio_transcript": "Operator opens settings window and saves project.",
                "visual_description": "A simple software settings form.",
                "text_llm": {
                    "topic": "Violet asteroid",
                    "improved_description": "Violet asteroid destroys research station.",
                    "key_points": ["Violet asteroid destroys research station."],
                },
            }],
        }
        outputs = {"outputs_by_variant": {"self_match": [{
            "iteration": 1,
            "output": {"executive_summary": "Violet asteroid destroys research station."},
        }]}}
        report = grounding.build_report(source, outputs, min_token_recall=0.5)
        claim = report["variants"]["self_match"]["runs"][0]["claims"][0]

        # The generated phrase exists verbatim in excluded model-derived fields,
        # but not in independent transcript/OCR/visual evidence.
        self.assertEqual(claim["lexical_evidence"]["claim_token_recall"], 0.0)
        self.assertIn("low_lexical_overlap_requires_manual_review", claim["findings"])
        field_paths = [item["field_path"] for item in grounding.build_source_index(source)[1]]
        self.assertTrue(field_paths)
        self.assertFalse(any("text_llm" in path for path in field_paths))
        self.assertFalse(any("global_summary" in path for path in field_paths))
        self.assertIn("text_llm", report["excluded_derived_source_fields"])
        self.assertIn("global_summary", report["excluded_derived_source_fields"])

    def test_all_generated_descriptions_are_excluded_even_if_they_are_long_strings(self):
        source = {
            "segments": [{
                "scene_id": 4,
                "audio_transcript": "An operator opens a drawing.",
                "visual_description": "A desktop application with an open drawing.",
                "text_llm": {
                    "improved_description": "The quantum reactor failed after a blue meteor strike.",
                    "additional_context": "The quantum reactor failed after a blue meteor strike.",
                },
                "generated_summary": "The quantum reactor failed after a blue meteor strike.",
            }]
        }
        _, evidence = grounding.build_source_index(source)
        joined = " ".join(item["text"] for item in evidence)
        self.assertNotIn("quantum reactor", joined)
        self.assertNotIn("blue meteor", joined)
        self.assertTrue(any("audio_transcript" in item["field_path"] for item in evidence))
        self.assertTrue(any("visual_description" in item["field_path"] for item in evidence))

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
