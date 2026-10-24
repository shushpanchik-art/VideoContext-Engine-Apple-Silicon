#!/usr/bin/env python3
"""Regression tests for the V4.1 installer anchor fix."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "patch_vce_report_integrity_v4_1.py"
SPEC = importlib.util.spec_from_file_location("vce_v4_1", PATCH)
patch = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(patch)


def make_project(root: Path, marker_prefix: str = "") -> Path:
    project = root / "VideoContext-Engine"
    project.mkdir(parents=True, exist_ok=True)
    source = project / "VideoContextEngine_v3.19.py"
    source.write_text(
        "# VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR\n"
        "def _vce_canonicalize_confirmed_standard_names(parsed, segments):\n"
        "    return parsed\n\n"
        "def run_text_llm_postprocess(parsed, segments, existing_summary, global_summary_text, final_segments):\n"
        "    if True:\n"
        "        parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)\n"
        + marker_prefix + "# TEXT_LLM_POSTPROCESS_STAGE\n"
        "        return parsed\n",
        encoding="utf-8",
    )
    (project / "videocontext_settings.json").write_text(
        json.dumps({"text_llm_name_reconciliation_prompt": "existing prompt"}),
        encoding="utf-8",
    )
    return project


class V41InstallerTests(unittest.TestCase):
    def check_marker(self, indent: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp), indent)
            changes, _, source, settings = patch._prepare(project)
            self.assertIn(source, changes)
            updated = changes[source]
            compile(updated, str(source), "exec")
            self.assertEqual(updated.count(patch.STAGE_MARKER), 1)
            self.assertIn(patch.SUMMARY_MARKER, updated)
            self.assertIn(
                "_vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)",
                updated,
            )
            self.assertIn(patch.V4_MARKER, changes[settings])

    def test_accepts_unindented_marker_from_v3_patch(self) -> None:
        self.check_marker("")

    def test_accepts_normally_indented_marker(self) -> None:
        self.check_marker("        ")

    def test_prepare_is_idempotent_after_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp))
            changes, _, source, settings = patch._prepare(project)
            source.write_text(changes[source], encoding="utf-8")
            settings.write_text(changes[settings], encoding="utf-8")
            second_changes, *_ = patch._prepare(project)
            self.assertEqual(second_changes, {})

    def test_rejects_marker_embedded_in_code_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp), "        x = 1  ")
            with self.assertRaises(RuntimeError):
                patch._prepare(project)


if __name__ == "__main__":
    unittest.main(verbosity=2)
