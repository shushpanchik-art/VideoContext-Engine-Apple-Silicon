from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "scripts" / "patch_vce_full_profile_benchmark.py"
SPEC = importlib.util.spec_from_file_location("vce_benchmark_patcher", PATCHER_PATH)
assert SPEC and SPEC.loader
PATCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHER)


STUB_RUNNER = """import json
def jsonl_append(path, payload):
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload) + "\\n")

def build_plan():
    return [
        ("baseline", "Baseline", {}),
        ("adaptive_keyframes", "Adaptive", {}),
        ("ocr_cache", "OCR cache", {}),
    ]

def build_base_fields():
    fields = {
        "analysis_mode": "full",
        "skip_audio": False,
        "skip_visual": False,
        "word_timestamps": True,
        "enable_audio_features": True,
        "enable_apple_ocr": True,
        "enable_ffmpeg_ocr": True,
        "text_llm_enabled": True,
        "generate_summary": True,
    }
    missing = []
    return fields, missing
"""


class VCEBenchmarkRunnerPatchTests(unittest.TestCase):
    def _load(self, source: str):
        namespace = {}
        exec(compile(PATCHER.patch_source(source), "<test-runner>", "exec"), namespace)
        return namespace

    def test_string_path_is_normalized_before_path_open(self):
        namespace = self._load(STUB_RUNNER)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "progress.jsonl"
            namespace["jsonl_append"](str(target), {"status": "ok"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "ok"})

    def test_baseline_flag_limits_plan_without_changing_default(self):
        namespace = self._load(STUB_RUNNER)
        self.assertEqual(len(namespace["build_plan"]()), 3)
        with patch.dict(os.environ, {"VCE_BENCH_ONLY_BASELINE": "1"}):
            plan = namespace["build_plan"]()
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0][0], "baseline")

    def test_whisper_only_mode_keeps_speech_and_disables_other_models(self):
        namespace = self._load(STUB_RUNNER)
        with patch.dict(os.environ, {"VCE_BENCH_WHISPER_ONLY": "1"}):
            fields, missing = namespace["build_base_fields"]()
        self.assertEqual(missing, [])
        self.assertEqual(fields["analysis_mode"], "whisper_ocr")
        self.assertFalse(fields["skip_audio"])
        self.assertTrue(fields["word_timestamps"])
        self.assertTrue(fields["enable_audio_features"])
        self.assertTrue(fields["skip_visual"])
        self.assertFalse(fields["enable_apple_ocr"])
        self.assertFalse(fields["enable_ffmpeg_ocr"])
        self.assertFalse(fields["text_llm_enabled"])
        self.assertFalse(fields["generate_summary"])

    def test_patch_is_idempotent(self):
        once = PATCHER.patch_source(STUB_RUNNER)
        twice = PATCHER.patch_source(once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
