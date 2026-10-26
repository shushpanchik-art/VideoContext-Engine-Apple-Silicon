#!/usr/bin/env python3
"""Regression tests for the local VCE benchmark helper."""
from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import benchmark_vce_macos as bench  # noqa: E402


class BenchmarkPrivacyAndSchemaTests(unittest.TestCase):
    def test_form_prompt_is_trimmed_to_api_limit_without_mutating_settings(self):
        prompt = ("Пример технического промпта с длинным содержимым. " * 60)
        settings = {"visual_user_prompt": prompt}
        adaptations = []
        fields = bench.build_form_fields(
            settings,
            {"visual_user_prompt": {"maxLength": 1000}},
            adaptations,
        )
        submitted = dict(fields)["visual_user_prompt"]

        self.assertLessEqual(len(submitted), 1000)
        self.assertEqual(settings["visual_user_prompt"], prompt)
        self.assertEqual(adaptations[0]["field"], "visual_user_prompt")
        self.assertEqual(adaptations[0]["original_characters"], len(prompt))
        self.assertEqual(adaptations[0]["sent_characters"], len(submitted))
        self.assertNotIn(prompt, json.dumps(adaptations, ensure_ascii=False))

    def test_known_prompt_limit_applies_when_openapi_omits_max_length(self):
        prompt = "X" * 1400
        adaptations = []
        fields = bench.build_form_fields(
            {"visual_user_prompt": prompt},
            {"visual_user_prompt": {"type": "string"}},
            adaptations,
        )
        self.assertLessEqual(len(dict(fields)["visual_user_prompt"]), 1000)
        self.assertEqual(adaptations[0]["api_maxLength"], 1000)

    def test_http_validation_error_never_returns_submitted_prompt(self):
        secret_prompt = "THIS IS A PRIVATE CUSTOM PROMPT"
        body = json.dumps({
            "detail": [{
                "loc": ["body", "visual_user_prompt"],
                "msg": "String should have at most 1000 characters",
                "type": "string_too_long",
                "input": secret_prompt,
            }]
        }).encode("utf-8")

        message = bench.safe_http_error(422, body)
        self.assertIn("visual_user_prompt", message)
        self.assertIn("string_too_long", message)
        self.assertNotIn(secret_prompt, message)

    def test_unstructured_http_error_body_is_omitted(self):
        secret_text = b"private video filename and content"
        message = bench.safe_http_error(500, secret_text)
        self.assertIn("body omitted for privacy", message)
        self.assertNotIn(secret_text.decode("utf-8"), message)

    @patch.object(bench, "command_output")
    def test_process_sampler_accepts_comma_decimal_cpu_and_descendants(self, mocked_command):
        # Some localized ps output uses a comma as the decimal separator.
        mocked_command.return_value = (
            "123 1 20,5 102400\\n"
            "124 123 1,5 204800\\n"
            "125 1 50,0 51200\\n"
        )
        cpu, rss_mb, process_count = bench.ps_process_tree(123)
        self.assertAlmostEqual(cpu, 22.0)
        self.assertAlmostEqual(rss_mb, 300.0)
        self.assertEqual(process_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
