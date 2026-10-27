#!/usr/bin/env python3
"""Regression tests for the evidence-gated VCE final-scene quality guard."""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "patches" / "patch_vce_text_llm_quality_guard.py"
SPEC = importlib.util.spec_from_file_location("vce_text_llm_quality_guard", MODULE_PATH)
guard_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(guard_module)

BENCH_PATH = REPO_ROOT / "tools" / "bench_text_llm_summary.py"
BENCH_SPEC = importlib.util.spec_from_file_location("vce_text_llm_benchmark", BENCH_PATH)
bench_module = importlib.util.module_from_spec(BENCH_SPEC)
assert BENCH_SPEC and BENCH_SPEC.loader
BENCH_SPEC.loader.exec_module(bench_module)


class FinalSceneQualityGuardTests(unittest.TestCase):
    def test_repairs_final_scene_only_when_end_title_is_in_source_evidence(self):
        payload = {
            "executive_summary": "Отчёт остаётся без изменений.",
            "key_points": [
                {"scene_id": 1, "text": "AND JU", "evidence": ["visual"]},
                {"scene_id": 2, "text": "AND JU", "evidence": ["visual"]},
            ],
            "scene_enrichment": [
                {"scene_id": 1, "topic": "Не трогать", "improved_description": "AND JU", "key_points": []},
                {"scene_id": 2, "topic": "Результат проверки", "improved_description": "Заставка D_ NC END",
                 "key_points": ["Сцена содержит результат проверки."]},
            ],
        }
        segments = [
            {"scene_id": 1, "start": 0, "end": 4, "ocr_text": "AND JU",
             "visual_description": "Технический интерфейс."},
            {"scene_id": 2, "start": 4, "end": 8, "ocr_text": "THE END",
             "visual_description": "На чёрном фоне титр THE END."},
        ]

        result = guard_module.repair_final_scene_artifacts(payload, segments)
        self.assertEqual(result["key_points"][0]["text"], "AND JU",
                         "non-final scenes must not be edited")
        self.assertEqual(result["key_points"][1]["text"], "Финальная заставка с текстом «THE END».")
        self.assertEqual(result["key_points"][1]["evidence"], ["ocr", "visual"])
        self.assertEqual(result["scene_enrichment"][1]["topic"], "Завершение видео")
        self.assertEqual(result["scene_enrichment"][1]["improved_description"],
                         "Финальная заставка с текстом «THE END».")
        self.assertEqual(result["scene_enrichment"][1]["key_points"],
                         ["Видео завершается финальной заставкой «THE END»."])
        self.assertEqual(result["scene_enrichment"][0]["improved_description"], "AND JU")

    def test_semantic_result_conflict_is_rewritten_when_audio_does_not_support_it(self):
        payload = {
            "key_points": [{"scene_id": 7, "text": "В модели обнаружена коллизия.", "evidence": ["visual"]}],
            "scene_enrichment": [{"scene_id": 7, "topic": "Результат проверки",
                                  "improved_description": "В модели обнаружена коллизия: уклон не соответствует нормативу.",
                                  "key_points": ["Выявлено несоответствие норме."]}],
        }
        segments = [{"scene_id": 7, "start": 10, "end": 12, "audio_transcript": "",
                     "ocr_text": "THE END", "visual_description": "Финальная заставка THE END."}]
        result = guard_module.repair_final_scene_artifacts(payload, segments)
        self.assertEqual(result["key_points"][0]["text"], "Финальная заставка с текстом «THE END».")
        self.assertEqual(result["scene_enrichment"][0]["improved_description"],
                         "Финальная заставка с текстом «THE END».")
        self.assertEqual(result["scene_enrichment"][0]["key_points"],
                         ["Видео завершается финальной заставкой «THE END»."])

    def test_spoken_result_is_preserved_with_explicit_audio_attribution(self):
        payload = {
            "key_points": [{"scene_id": 7, "text": "В модели обнаружена коллизия.", "evidence": ["speech", "visual"]}],
            "scene_enrichment": [{"scene_id": 7, "topic": "Результат проверки",
                                  "improved_description": "В модели обнаружена коллизия: уклон не соответствует нормативу.",
                                  "key_points": ["Выявлено несоответствие норме."]}],
        }
        audio = "Диктор сообщает: проверка выявила несоответствие уклона пандуса нормативу."
        segments = [{"scene_id": 7, "start": 10, "end": 12, "audio_transcript": audio,
                     "ocr_text": "THE END", "visual_description": "Финальная заставка THE END."}]
        result = guard_module.repair_final_scene_artifacts(payload, segments)
        self.assertIn("В аудио сообщается:", result["scene_enrichment"][0]["improved_description"])
        self.assertIn(audio, result["scene_enrichment"][0]["improved_description"])
        self.assertIn("Визуально показана финальная заставка «THE END».",
                      result["scene_enrichment"][0]["improved_description"])
        self.assertIn("speech", result["key_points"][0]["evidence"])
        self.assertIn("В аудио сообщается:", result["key_points"][0]["text"])

    def test_no_repair_without_explicit_end_title_evidence(self):
        payload = {
            "key_points": [{"scene_id": 7, "text": "AND JU", "evidence": ["visual"]}],
            "scene_enrichment": [{"scene_id": 7, "topic": "Завершение", "improved_description": "D_ NC END",
                                  "key_points": []}],
        }
        segments = [{"scene_id": 7, "start": 0, "end": 2,
                     "ocr_text": "Unclear",
                     "visual_description": "Чёрный экран, читаемый текст отсутствует."}]
        before = repr(payload)
        result = guard_module.repair_final_scene_artifacts(payload, segments)
        self.assertEqual(repr(result), before)

    def test_focused_quality_benchmark_selects_baseline_b_and_4b_path(self):
        focused = bench_module.benchmark_variants(focused_quality=True)
        self.assertEqual([name for name, _model, _flags in focused], ["baseline", "B_fast_summary"])
        full = bench_module.benchmark_variants(focused_quality=False)
        self.assertEqual([name for name, _model, _flags in full],
                         ["baseline", "A_thinking_off", "B_fast_summary", "C_compact_context"])

    def test_benchmark_and_installed_engine_guard_match(self):
        payload = {
            "key_points": [{"scene_id": 2, "text": "AND JU", "evidence": ["visual"]}],
            "scene_enrichment": [{"scene_id": 2, "topic": "Результат проверки",
                                  "improved_description": "D_ NC END", "key_points": []}],
        }
        segments = [{"scene_id": 2, "start": 4, "end": 8, "ocr_text": "THE END",
                     "visual_description": "На чёрном фоне титр THE END."}]
        import copy
        engine_result = guard_module.repair_final_scene_artifacts(copy.deepcopy(payload), segments)
        bench_result = bench_module.repair_final_scene_artifacts(copy.deepcopy(payload), segments)
        self.assertEqual(engine_result, bench_result)

    def test_patch_source_compiles_and_is_idempotent(self):
        source = (
            "import re\n\n"
            "def run_text_llm_postprocess():\n"
            "    try:\n"
            "        parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)\n"
            "        return parsed\n"
            "    finally:\n"
            "        pass\n\n"
            "def format_text_llm_report(payload):\n"
            "    return str(payload)\n"
        )
        patched, changed = guard_module.patch_source(source)
        self.assertTrue(changed)
        compile(patched, "fixture.py", "exec")
        self.assertIn("parsed = _vce_v5_repair_final_scene_artifacts(parsed, segments)", patched)
        patched_again, changed_again = guard_module.patch_source(patched)
        self.assertFalse(changed_again)
        self.assertEqual(patched_again, patched)

    def test_refuses_partial_patch_state(self):
        source = (
            "def _vce_v5_repair_final_scene_artifacts(payload, segments):\n"
            "    return payload\n\n"
            "def run_text_llm_postprocess():\n"
            "    return {}\n"
        )
        with self.assertRaises(ValueError):
            guard_module.patch_source(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
