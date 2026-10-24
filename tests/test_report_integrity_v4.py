from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

PATCH_PATH = Path(__file__).resolve().parents[1] / "patches" / "patch_vce_report_integrity_v4.py"
spec = importlib.util.spec_from_file_location("vce_report_integrity_v4", PATCH_PATH)
patch = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(patch)
HELPERS = {}
exec(compile(patch.HELPER_SOURCE, "<VCE V4 helper source>", "exec"), HELPERS)


class ReportIntegrityV4Tests(unittest.TestCase):
    def setUp(self):
        self.segments = [
            {"scene_id": 2, "start": 7.0, "end": 19.0, "audio_transcript": "Какой уклон пандусов?",
             "ocr_text": "СП 1.13130.2020 Системы противопожарной защиты; уклон не более 1:20",
             "visual_description": "Результаты поиска.",
             "text_llm": {"topic": "Семантический поиск", "improved_description": "Показан поиск."}},
            {"scene_id": 3, "start": 20.0, "end": 39.0,
             "audio_transcript": "1.13.132.020, системы противопожарной защиты.",
             "ocr_text": "BuildChat: Согласно СП 1.13130.2020 уклон пандусов не более 1:20",
             "visual_description": "Ответ BuildChat.",
             "text_llm": {"topic": "Ответ BuildChat", "improved_description": "СП 1.13130.2020"}},
            {"scene_id": 13, "start": 85.0, "end": 99.0,
             "audio_transcript": "В этой проверке оцениваем уклон.",
             "ocr_text": "CADLib Проверка коллизий [object1.#XPG_0016]>1/20",
             "visual_description": "Открыто окно проверки коллизий.",
             "text_llm": {"topic": "Проверка в CADLib",
                          "improved_description": "В 3D-модели выявлено, что уклон пандуса не соответствует нормативу."}},
            {"scene_id": 14, "start": 100.0, "end": 108.0,
             "audio_transcript": "не соответствует нормативу.", "ocr_text": "THE END",
             "visual_description": "Финальный экран.",
             "text_llm": {"topic": "Финал", "improved_description": "В речи сообщается о несоответствии."}},
        ]
        self.analysis = {
            "executive_summary": "В финальной речи сообщается: не соответствует нормативу. В CADLib видно окно «Проверка коллизий», но связь с проверкой не подтверждена; параметр нарушения визуально не указан.",
            "topics": ["СП 1.13130.2020", "CADLib"],
            "key_points": [{"scene_id": 3, "text": "По СП 1.13130.2020 предел 1:20."}],
            "entities": [{"name": "СП 1.13130.2020", "type": "standard", "scene_ids": [3]}],
            "scene_enrichment": [{"scene_id": 3, "topic": "Норматив", "improved_description": "СП 1.13130.2020", "key_points": []}],
            "original_summary": "Исходный пример: СП 1.13.132.020",
        }

    def test_summary_harmonizer_attributes_claim_to_audio(self):
        source = ("Видео показывает процесс проверки. В ходе проверки в 3D-модели выявлено, "
                  "что уклон пандуса не соответствует нормативу. Видео завершается экраном THE END.")
        result = HELPERS["_vce_v4_evidence_summary"](source, self.segments)
        self.assertIn("По словам диктора", result)
        self.assertIn("визуально не подтверждены", result)
        self.assertNotIn("В ходе проверки в 3D-модели выявлено", result)

    def test_summary_not_downgraded_when_ocr_contains_result(self):
        segments = [dict(s) for s in self.segments]
        segments[2]["ocr_text"] += " Результат проверки: не соответствует нормативу"
        source = "В ходе проверки в 3D-модели выявлено, что уклон пандуса не соответствует нормативу."
        self.assertEqual(HELPERS["_vce_v4_evidence_summary"](source, segments), source)

    def test_entity_links_and_raw_fields(self):
        transcript = self.segments[1]["audio_transcript"]
        summary = self.analysis["original_summary"]
        HELPERS["_vce_v4_finalize_analysis_payload"](self.analysis, self.segments, "summary")
        self.assertEqual(self.analysis["entities"][0]["scene_ids"], [2, 3])
        self.assertEqual(self.segments[1]["audio_transcript"], transcript)
        self.assertEqual(self.analysis["original_summary"], summary)

    def test_validator_detects_dangling_scene_reference(self):
        report = {
            "meta": {"scene_count": 4, "global_summary": "По словам диктора, результат визуально не подтверждён."},
            "enhanced_analysis": self.analysis, "segments": self.segments,
        }
        report["enhanced_analysis"]["key_points"].append({"scene_id": 999, "text": "invalid"})
        issues = patch._report_issues(report)
        self.assertTrue(any("неизвестную сцену" in i or "отсутствует в segments" in i for i in issues))

    def test_installer_upgrade_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            engine = project / "VideoContextEngine_v3.19.py"
            settings = project / "videocontext_settings.json"
            engine.write_text(
                "# VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR\n"
                "def _vce_canonicalize_confirmed_standard_names(payload, segments):\n    return payload\n\n"
                "def reconcile_text_llm_result(parsed, segments, existing_summary):\n    return parsed\n\n"
                "def run_text_llm_postprocess(parsed, segments, existing_summary):\n"
                "        parsed = reconcile_text_llm_result(parsed, segments, existing_summary)\n"
                "        parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)\n"
                "        return parsed\n\n"
                "def run_pipeline(final_segments, global_summary_text):\n"
                "        # VCE_NAME_CONSISTENCY_V3_GLOBAL_SUMMARY\n"
                "        # TEXT_LLM_POSTPROCESS_STAGE\n"
                "        return global_summary_text\n", encoding="utf-8")
            settings.write_text(json.dumps({"text_llm_name_reconciliation_prompt": "existing"}, indent=2) + "\n", encoding="utf-8")
            changes, _, _, _ = patch._prepare(project)
            self.assertIn(engine, changes)
            compile(changes[engine], str(engine), "exec")
            engine.write_text(changes[engine], encoding="utf-8")
            settings.write_text(json.dumps({"text_llm_name_reconciliation_prompt": "existing" + patch.PROMPT_ADDENDUM},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changes2, _, _, _ = patch._prepare(project)
            self.assertFalse(changes2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
