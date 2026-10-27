#!/usr/bin/env python3
"""Tests for universal non-mutating VCE output integrity validation."""
from __future__ import annotations

import copy
import importlib.util
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


guard_module = load_module("vce_text_llm_integrity_guard", REPO_ROOT / "patches" / "patch_vce_text_llm_quality_guard.py")
bench_module = load_module("vce_text_llm_benchmark", REPO_ROOT / "tools" / "bench_text_llm_summary.py")


def fixture():
    payload = {
        "executive_summary": "A video describes a process and its outcome.",
        "topics": ["КСИ", "HDB_V5", "THE END", "АИ ИИ ПОМОЩНИК"],
        "entities": [{"name": "Example item", "scene_ids": [1]}],
        "uncertainties": [],
        "key_points": [
            {"scene_id": 1, "text": "A speaker introduces the item.", "evidence": ["speech"]},
        ],
        "scene_enrichment": [
            {"scene_id": 1, "topic": "Introduction", "improved_description": "The video introduces the item.", "key_points": []},
            {"scene_id": 2, "topic": "Conclusion", "improved_description": "The video ends with a title card.", "key_points": []},
        ],
    }
    segments = [
        {"scene_id": 1, "start": 0, "end": 3, "audio_transcript": "A speaker introduces the item."},
        {"scene_id": 2, "start": 3, "end": 6, "audio_transcript": "The video ends.", "ocr_text": "THE END"},
    ]
    return payload, segments


class UniversalIntegrityGuardTests(unittest.TestCase):
    def test_both_guards_are_non_mutating_for_any_domain_language_or_title(self):
        payload, segments = fixture()
        original = copy.deepcopy(payload)
        result = guard_module.validate_text_llm_payload(payload, segments)
        self.assertEqual(result, original)
        self.assertEqual(payload, original)

        payload2, segments2 = fixture()
        state = types.SimpleNamespace(quality_guard_flags=0)
        result2 = bench_module.validate_text_llm_payload(payload2, segments2, state=state)
        self.assertEqual(result2, payload2)
        self.assertEqual(payload2, original)
        self.assertEqual(state.quality_guard_flags, 0)

    def test_missing_scene_enrichment_is_flagged_but_not_rewritten(self):
        payload, segments = fixture()
        payload["scene_enrichment"] = payload["scene_enrichment"][:1]
        before = copy.deepcopy(payload)
        state = types.SimpleNamespace(quality_guard_flags=0)
        result = bench_module.validate_text_llm_payload(payload, segments, state=state)
        self.assertEqual(result, before)
        self.assertGreater(state.quality_guard_flags, 0)

    def test_invalid_scene_ids_are_flagged_but_content_is_preserved(self):
        payload, segments = fixture()
        payload["scene_enrichment"][0]["scene_id"] = 99
        before = copy.deepcopy(payload)
        state = types.SimpleNamespace(quality_guard_flags=0)
        result = bench_module.validate_text_llm_payload(payload, segments, state=state)
        self.assertEqual(result, before)
        self.assertGreater(state.quality_guard_flags, 0)

    def test_unicode_replacement_character_is_flagged_without_rewriting(self):
        payload, segments = fixture()
        payload["executive_summary"] = "Broken text \ufffd"
        before = copy.deepcopy(payload)
        state = types.SimpleNamespace(quality_guard_flags=0)
        result = bench_module.validate_text_llm_payload(payload, segments, state=state)
        self.assertEqual(result, before)
        self.assertGreater(state.quality_guard_flags, 0)

    def test_focused_quality_benchmark_selects_baseline_b_and_4b_path(self):
        focused = bench_module.benchmark_variants(focused_quality=True)
        self.assertEqual([name for name, _model, _flags in focused], ["baseline", "B_fast_summary"])
        full = bench_module.benchmark_variants(focused_quality=False)
        self.assertEqual([name for name, _model, _flags in full],
                         ["baseline", "A_thinking_off", "B_fast_summary", "C_compact_context"])

    def test_benchmark_and_installed_engine_validators_match(self):
        payload, segments = fixture()
        payload_a = copy.deepcopy(payload)
        payload_b = copy.deepcopy(payload)
        engine_result = guard_module.validate_text_llm_payload(payload_a, segments)
        state = types.SimpleNamespace(quality_guard_flags=0)
        bench_result = bench_module.validate_text_llm_payload(payload_b, segments, state=state)
        self.assertEqual(engine_result, bench_result)
        self.assertEqual(payload_a, payload_b)

    def test_patch_source_compiles_and_is_idempotent(self):
        source = (
            "def run_text_llm_postprocess():\n"
            "    parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)\n"
            "    return parsed\n\n"
            "def format_text_llm_report(payload):\n"
            "    return str(payload)\n"
        )
        patched, changed = guard_module.patch_source(source)
        self.assertTrue(changed)
        compile(patched, "fixture.py", "exec")
        self.assertIn(
            "parsed = _vce_v6_validate_text_llm_payload(parsed, segments)", patched
        )
        patched_again, changed_again = guard_module.patch_source(patched)
        self.assertFalse(changed_again)
        self.assertEqual(patched_again, patched)

    def test_refuses_partial_patch_state(self):
        source = (
            "def _vce_v6_validate_text_llm_payload(payload, segments):\n"
            "    return payload\n\n"
            "def run_text_llm_postprocess():\n"
            "    return {}\n"
        )
        with self.assertRaises(ValueError):
            guard_module.patch_source(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
