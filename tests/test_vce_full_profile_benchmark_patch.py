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
    plan = ["baseline", "adaptive", "cache"]
    return plan

def build_base_fields():
    fields = {
        "skip_audio": False,
        "enable_audio_features": True,
        "word_timestamps": True,
    }
    missing = []
    return fields, missing
"""


class VCEBenchmarkRunnerPatchTests(unittest.TestCase):
    def test_string_path_is_normalized_before_path_open(self):
        patched = PATCHER.patch_source(STUB_RUNNER)
        namespace = {}
        exec(compile(patched, "<test-runner>", "exec"), namespace)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "progress.jsonl"
            namespace["jsonl_append"](str(target), {"status": "ok"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "ok"})

    def test_diagnostic_switches_only_change_requested_run(self):
        patched = PATCHER.patch_source(STUB_RUNNER)
        namespace = {}
        exec(compile(patched, "<test-runner>", "exec"), namespace)
        with patch.dict(os.environ, {
            "VCE_BENCH_ONLY_BASELINE": "1",
            "VCE_BENCH_SKIP_AUDIO": "1",
        }):
            self.assertEqual(namespace["build_plan"](), ["baseline"])
            fields, missing = namespace["build_base_fields"]()
        self.assertEqual(missing, [])
        self.assertTrue(fields["skip_audio"])
        self.assertFalse(fields["enable_audio_features"])
        self.assertFalse(fields["word_timestamps"])

    def test_patch_is_idempotent(self):
        once = PATCHER.patch_source(STUB_RUNNER)
        twice = PATCHER.patch_source(once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
