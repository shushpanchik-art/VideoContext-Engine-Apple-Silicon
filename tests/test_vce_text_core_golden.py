"""Golden regression tests for the model-free report post-processing chain.

Unlike the source-text assertions elsewhere in this suite, these tests execute the
real chain on a frozen fixture and compare the *result*. They fail when a change
alters what the report actually contains, which is what "not worse" means here.

The fixture is a small but realistic recording: Russian speech, OCR with a
standard number, product names, an end card, and an LLM payload that carries the
mistakes the chain is supposed to repair.
"""

from __future__ import annotations

import copy
import contextlib
import io
import json
import unittest
from unittest import mock
from pathlib import Path

from vce_core import text_reconcile as core


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "reconcile"

SETTINGS = {
    "text_llm_summary_max_words": 90,
    "text_llm_max_key_points": 18,
    "text_llm_max_topics": 15,
}


def _reader(profile):
    def read(name, fallback):
        if name == "domain_profile":
            return profile
        return SETTINGS.get(name, fallback)
    return read


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TextCoreGoldenTests(unittest.TestCase):
    """Result-level regression coverage for the report post-processing chain."""

    def setUp(self):
        self.segments = _load("segments.json")
        self.payload = _load("llm_payload.json")

    def tearDown(self):
        core.reset_settings_reader()

    def _run(self, profile):
        core.configure_settings_reader(_reader(profile))
        return core.postprocess_text_llm_payload(
            copy.deepcopy(self.payload), copy.deepcopy(self.segments), ""
        )

    def test_generic_profile_matches_frozen_golden_output(self):
        result = self._run("generic")
        expected = _load("expected_generic.json")
        self.assertEqual(
            json.dumps(result, ensure_ascii=False, sort_keys=True),
            json.dumps(expected, ensure_ascii=False, sort_keys=True),
            "generic post-processing output drifted from the frozen golden file",
        )

    def test_nsrspec_profile_matches_frozen_golden_output(self):
        result = self._run("nsrspec")
        expected = _load("expected_nsrspec.json")
        self.assertEqual(
            json.dumps(result, ensure_ascii=False, sort_keys=True),
            json.dumps(expected, ensure_ascii=False, sort_keys=True),
            "nsrspec post-processing output drifted from the frozen golden file",
        )

    # ---- the individual repairs the chain must keep doing ----

    def test_duplicate_key_points_are_collapsed(self):
        result = self._run("generic")
        texts = [str(p.get("text", "")).strip() for p in result["key_points"]]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertLess(len(result["key_points"]), len(self.payload["key_points"]))

    def test_unknown_scene_references_are_dropped(self):
        result = self._run("generic")
        known = {int(s["scene_id"]) for s in self.segments}
        for point in result["key_points"]:
            self.assertIn(int(point["scene_id"]), known)
        for item in result["scene_enrichment"]:
            self.assertIn(int(item["scene_id"]), known)

    def test_entity_not_present_in_sources_is_removed(self):
        result = self._run("generic")
        names = {e.get("name") for e in result["entities"]}
        self.assertNotIn("AutoCAD Plant 3D", names)
        self.assertIn("CADLib", names)
        self.assertIn("BuildChat", names)

    def test_every_scene_ends_up_enriched(self):
        result = self._run("generic")
        covered = {int(i["scene_id"]) for i in result["scene_enrichment"]}
        self.assertEqual(covered, {int(s["scene_id"]) for s in self.segments})

    def test_evidence_never_points_at_a_source_without_the_claim(self):
        result = self._run("generic")
        by_scene = {int(s["scene_id"]): s for s in self.segments}
        for point in result["key_points"]:
            scene = by_scene[int(point["scene_id"])]
            sources = " ".join(
                str(scene.get(k, "")) for k in ("audio_transcript", "ocr_text", "visual_description")
            ).casefold()
            if not str(point.get("text", "")).strip():
                continue
            # An empty evidence list is the correct outcome for an unsupported claim.
            if point.get("evidence"):
                self.assertTrue(
                    sources.strip(),
                    f"scene {point['scene_id']} has evidence {point['evidence']} but no sources",
                )

    def test_standard_number_is_canonicalised_only_under_nsrspec(self):
        generic = self._run("generic")
        nsrspec = self._run("nsrspec")
        self.assertIn("СП 1.13.130.2020", generic["executive_summary"])
        self.assertIn("СП 1.13130.2020", nsrspec["executive_summary"])
        self.assertNotIn("СП 1.13.130.2020", nsrspec["executive_summary"])

    def test_key_point_count_is_exactly_the_expected_number(self):
        """A count check, not just set membership: collapsing or losing points must fail."""
        result = self._run("generic")
        expected = _load("expected_generic.json")
        self.assertEqual(
            len(result["key_points"]), len(expected["key_points"]),
            f"key_points count drifted: {len(result['key_points'])} vs {len(expected['key_points'])}",
        )
        self.assertEqual(len(result["entities"]), len(expected["entities"]))
        self.assertEqual(len(result["topics"]), len(expected["topics"]))
        self.assertEqual(len(result["scene_enrichment"]), len(expected["scene_enrichment"]))

    def test_no_two_key_points_are_identical(self):
        result = self._run("generic")
        pairs = [
            (int(p["scene_id"]), str(p.get("text", "")).strip().casefold())
            for p in result["key_points"]
        ]
        self.assertEqual(len(pairs), len(set(pairs)), "duplicate (scene, text) survived de-duplication")

    def test_near_duplicate_claims_are_collapsed(self):
        """The fixture contains two identical CADLib claims; exactly one may survive."""
        result = self._run("generic")
        cadlib = [
            p for p in result["key_points"]
            if "cadlib" in str(p.get("text", "")).casefold()
        ]
        self.assertEqual(len(cadlib), 1, f"expected one CADLib claim, got {len(cadlib)}")

    def test_key_point_limit_setting_is_respected(self):
        """Lowering the configured limit must visibly reduce the number of key points."""
        core.configure_settings_reader(lambda n, f: (
            1 if n == "text_llm_max_key_points" else
            "generic" if n == "domain_profile" else
            SETTINGS.get(n, f)
        ))
        limited = core.postprocess_text_llm_payload(
            copy.deepcopy(self.payload), copy.deepcopy(self.segments), ""
        )
        self.assertLessEqual(len(limited["key_points"]), 1,
                             "text_llm_max_key_points was ignored")

    def test_near_duplicate_claims_are_never_left_identical(self):
        """Cross-scene near-duplicates must not survive as identical strings.

        The chain rewrites a repeated claim from its scene enrichment when one is
        available, otherwise keeps the scene's own claim. Either way the report must
        not end up carrying the same sentence twice.
        """
        segs = [
            {"scene_id": 1, "start": 0.0, "end": 10.0,
             "audio_transcript": "Проверка коллизий выполняется в CADLib.",
             "ocr_text": "CADLib", "visual_description": "Окно проверки."},
            {"scene_id": 2, "start": 10.0, "end": 20.0,
             "audio_transcript": "Далее открываем результаты проверки.",
             "ocr_text": "Результаты", "visual_description": "Таблица результатов."},
        ]
        pay = {
            "executive_summary": "Проверка коллизий.",
            "topics": ["проверка"],
            "key_points": [
                {"scene_id": 1, "time": "00:00:00", "text": "Проверка коллизий выполняется в CADLib.", "evidence": ["speech"]},
                {"scene_id": 2, "time": "00:00:10", "text": "Проверка коллизий выполняется в CADLib.", "evidence": ["speech"]},
            ],
            "entities": [], "scene_enrichment": [], "uncertainties": [],
        }
        core.configure_settings_reader(lambda n, f: (
            "generic" if n == "domain_profile" else SETTINGS.get(n, f)))
        result = core.postprocess_text_llm_payload(pay, segs, "")
        texts = [str(p.get("text", "")).strip().casefold() for p in result["key_points"]]
        self.assertEqual(len(texts), len(set(texts)),
                         f"identical claim survived twice: {texts}")

    def test_every_report_has_distinct_claims(self):
        result = self._run("generic")
        texts = [str(p.get("text", "")).strip().casefold() for p in result["key_points"]]
        self.assertEqual(len(texts), len(set(texts)), "report contains duplicate claim text")

    def test_chain_is_deterministic(self):
        first = self._run("generic")
        second = self._run("generic")
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )

    def test_chain_does_not_mutate_its_input(self):
        payload_copy = copy.deepcopy(self.payload)
        segments_copy = copy.deepcopy(self.segments)
        self._run("generic")
        self.assertEqual(payload_copy, self.payload)
        self.assertEqual(segments_copy, self.segments)

    def test_requirement_scene_does_not_invent_a_specific_id(self):
        scene = {
            "scene_id": 1,
            "start": 0.0,
            "end": 4.0,
            "ocr_text": "ID требования: 90210. Сегментация. Генерация калькулятора.",
        }
        claim = "Сегментация. Генерация калькулятора."
        payload = {
            "executive_summary": claim,
            "key_points": [{"scene_id": 1, "text": claim, "evidence": ["ocr"]}],
        }
        for profile in ("generic", "nsrspec"):
            with self.subTest(profile=profile):
                core.configure_settings_reader(_reader(profile))
                result = core.postprocess_text_llm_payload(copy.deepcopy(payload), [scene])
                self.assertTrue(result["key_points"])
                self.assertNotIn("44846", json.dumps(result, ensure_ascii=False))

    def test_finalizer_grounds_summary_links_entities_and_reports_validation_issues(self):
        """Exercise finalizer-only repairs that reconcile already performs elsewhere."""
        core.configure_settings_reader(_reader("nsrspec"))
        segments = [{
            "scene_id": 1,
            "start": 0.0,
            "end": 12.0,
            "audio_transcript": "Диктор сообщает, что уклон пандусов не соответствует нормативу.",
            "ocr_text": "CADLib\nПроверка коллизий\nСП 1.13130.2020",
            "visual_description": "Открыто окно проверки коллизий без отображённого результата.",
        }]
        payload = {
            "executive_summary": "В 3D-модели выявлено, что уклон пандусов не соответствует нормативу.",
            "topics": ["проверка"],
            "key_points": [],
            "entities": [{"name": "CADLib", "type": "software", "scene_ids": [99]}],
            "scene_enrichment": [],
            "uncertainties": [],
        }
        # Keep this case focused on finalization: the earlier reconcile stage also
        # repairs entity references and would otherwise mask a broken finalizer.
        with mock.patch.object(core, "reconcile_text_llm_result", side_effect=lambda value, *_: value):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = core.postprocess_text_llm_payload(
                    copy.deepcopy(payload), segments, "Старое резюме. СП 1.13.132.020"
                )

        self.assertIn("По словам диктора", result["executive_summary"])
        self.assertIn("визуально не подтверждены", result["executive_summary"])
        self.assertEqual(result["entities"][0]["scene_ids"], [1])
        self.assertIn(
            "неисправленный вариант номера СП остался в global_summary",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
