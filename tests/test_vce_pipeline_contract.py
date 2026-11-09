"""Contract tests for the engine <-> core boundary and for report invariants.

Two separate things are pinned here:

1. Engine wiring. The engine must import the model-free core and hand it its own
   request-aware ``setting()``. A missing import or a reader configured before the
   import would be a startup-time NameError, so the ordering is asserted as well.

2. Report invariants. These hold for *any* recording and any LLM output, so they
   catch qualitative regressions that string-matching tests cannot see: NSR
   material leaking into a generic report, evidence pointing at sources that do
   not contain the claim, scenes left unenriched, or output silently truncated.
"""

from __future__ import annotations

import ast
import copy
import json
import unittest
from pathlib import Path

import vce_core.text_reconcile as core


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "VideoContextEngine_v3.19.py"
FIXTURES = ROOT / "tests" / "fixtures" / "reconcile"

NSR_MARKERS = ("пандус", "кси", "1.13130", "buildchat", "cadlib", "nanocad")

SETTINGS = {
    "text_llm_summary_max_words": 90,
    "text_llm_max_key_points": 18,
    "text_llm_max_topics": 15,
    "text_llm_context_max_scenes": 80,
}


class EngineCoreWiringTests(unittest.TestCase):
    """The engine must expose the core and enable its settings bridge in order."""

    @classmethod
    def setUpClass(cls):
        cls.source = ENGINE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_engine_imports_the_core_instead_of_defining_it(self):
        self.assertIn("from vce_core.text_reconcile import", self.source)
        for name in (
            "reconcile_text_llm_result",
            "_vce_canonicalize_confirmed_standard_names",
            "_finalize_analysis_payload",
        ):
            self.assertNotIn(
                f"\ndef {name}(",
                self.source,
                f"{name} must live in vce_core, not be redefined in the engine",
            )

    def test_reader_is_configured_after_both_setting_and_import(self):
        idx_setting = self.source.index("def setting(")
        idx_import = self.source.index("from vce_core.text_reconcile import")
        idx_configure = self.source.index("_vce_core_configure_settings(setting)")
        self.assertLess(idx_setting, idx_configure, "reader wired before setting() exists")
        self.assertLess(idx_import, idx_configure, "reader wired before the import exists")

    def test_engine_uses_the_core_pipeline_entry_point(self):
        run_function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run_text_llm_postprocess"
        )
        calls = [
            node.func.id
            for node in ast.walk(run_function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertEqual(
            calls.count("postprocess_text_llm_payload"),
            1,
            "run_text_llm_postprocess must call the shared core pipeline exactly once",
        )
        self.assertFalse(
            {"reconcile_text_llm_result", "_vce_canonicalize_confirmed_standard_names", "_finalize_analysis_payload"}
            & set(calls),
            "the engine must not re-orchestrate individual core stages",
        )

    def test_core_imports_no_ml_or_web_dependency(self):
        core_src = (ROOT / "vce_core" / "text_reconcile.py").read_text(encoding="utf-8")
        tree = ast.parse(core_src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {
            "fastapi", "torch", "whisper", "cv2", "numpy", "yt_dlp",
            "mlx", "mlx_vlm", "llama_cpp", "PIL", "uvicorn",
        }
        self.assertEqual(imported & forbidden, set(), f"core gained heavy imports: {imported & forbidden}")

    def test_core_has_no_module_level_side_effects(self):
        core_src = (ROOT / "vce_core" / "text_reconcile.py").read_text(encoding="utf-8")
        tree = ast.parse(core_src)
        for node in tree.body:
            self.assertIsInstance(
                node,
                (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
                 ast.Assign, ast.AnnAssign, ast.Expr, ast.ClassDef),
                f"unexpected module-level node: {type(node).__name__}",
            )


class ReportInvariantTests(unittest.TestCase):
    """Invariants that must hold for any recording, checked on the fixture."""

    def setUp(self):
        self.segments = json.loads((FIXTURES / "segments.json").read_text(encoding="utf-8"))
        self.payload = json.loads((FIXTURES / "llm_payload.json").read_text(encoding="utf-8"))

    def tearDown(self):
        core.reset_settings_reader()

    def _run(self, profile="generic"):
        def reader(name, fallback):
            if name == "domain_profile":
                return profile
            return SETTINGS.get(name, fallback)
        core.configure_settings_reader(reader)
        return core.postprocess_text_llm_payload(
            copy.deepcopy(self.payload), copy.deepcopy(self.segments), ""
        )

    def _all_text(self, result):
        parts = [str(result.get("executive_summary", ""))]
        parts += [str(t) for t in result.get("topics", [])]
        for point in result.get("key_points", []):
            parts.append(str(point.get("text", "")))
        for item in result.get("scene_enrichment", []):
            parts.append(str(item.get("topic", "")))
            parts.append(str(item.get("improved_description", "")))
            parts += [str(p) for p in item.get("key_points", [])]
        return " ".join(parts).casefold()

    def test_generic_report_never_uses_canned_nsrspec_scenarios(self):
        """A generic report must not contain the hardcoded NSR scenario sentences.

        Product names such as CADLib or BuildChat are legitimate when the video
        itself shows them, so absence is not the invariant. What must never appear
        is the canned narration the NSR profile substitutes when speech mentions a
        ramp or a collision check.
        """
        result = self._run("generic")
        blob = self._all_text(result)
        canned = (
            "описывает загрузку xml в cadlib",
            "отбор объектов по атрибутивным данным",
            "проверка уклона пандусов",
            "схема требования об уклоне пандусов",
            "атрибут «код кси»",
        )
        found = [c for c in canned if c in blob]
        self.assertEqual(found, [], f"canned NSR narration in a generic report: {found}")

    def test_generic_report_has_no_ramp_vocabulary_unless_spoken(self):
        result = self._run("generic")
        blob = self._all_text(result)
        source_text = " ".join(
            str(s.get(k, "")) for s in self.segments
            for k in ("audio_transcript", "ocr_text", "visual_description")
        ).casefold()
        for marker in ("пандус", "кси"):
            if marker in blob:
                self.assertIn(
                    marker, source_text,
                    f"generic report mentions {marker!r} but no source contains it",
                )

    def test_any_product_mention_in_generic_report_is_grounded(self):
        """Product names may appear, but only when the sources actually show them."""
        result = self._run("generic")
        blob = self._all_text(result)
        source_text = " ".join(
            str(s.get(k, "")) for s in self.segments
            for k in ("audio_transcript", "ocr_text", "visual_description")
        ).casefold()
        for marker in ("cadlib", "buildchat", "nanocad"):
            if marker in blob:
                self.assertIn(
                    marker, source_text,
                    f"generic report mentions {marker!r} with no support in the sources",
                )

    def test_report_structure_is_complete(self):
        result = self._run("generic")
        for key in ("executive_summary", "topics", "key_points", "entities",
                    "scene_enrichment", "uncertainties"):
            self.assertIn(key, result, f"missing report section: {key}")
        self.assertTrue(str(result["executive_summary"]).strip())
        self.assertTrue(result["key_points"])
        self.assertTrue(result["scene_enrichment"])

    def test_every_emitted_scene_id_exists_in_input(self):
        result = self._run("generic")
        known = {int(s["scene_id"]) for s in self.segments}
        for section in ("key_points", "scene_enrichment"):
            for item in result[section]:
                self.assertIn(int(item["scene_id"]), known, f"{section} references unknown scene")

    def test_no_section_is_silently_empty_when_input_had_content(self):
        result = self._run("generic")
        self.assertTrue(result["topics"], "topics emptied out")
        self.assertTrue(result["entities"], "all entities were dropped")
        self.assertTrue(result["scene_enrichment"], "all enrichment was dropped")

    def test_summary_is_not_truncated_mid_word(self):
        result = self._run("generic")
        text = str(result["executive_summary"])
        self.assertFalse(text.endswith(" "), "summary ends with a dangling space")
        self.assertGreater(len(text.split()), 10, "summary collapsed to a stub")

    def test_key_points_are_bounded_by_the_configured_limit(self):
        result = self._run("generic")
        self.assertLessEqual(len(result["key_points"]), SETTINGS["text_llm_max_key_points"])
        self.assertLessEqual(len(result["topics"]), SETTINGS["text_llm_max_topics"])

    def test_profile_switch_does_not_change_structure(self):
        generic = self._run("generic")
        nsrspec = self._run("nsrspec")
        self.assertEqual(
            sorted(generic.keys()), sorted(nsrspec.keys()),
            "profile switch changed the report schema",
        )
        self.assertEqual(
            len(generic["key_points"]), len(nsrspec["key_points"]),
            "profile switch changed the number of key points",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
