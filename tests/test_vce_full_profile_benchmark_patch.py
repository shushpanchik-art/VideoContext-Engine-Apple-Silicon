from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "scripts" / "patch_vce_full_profile_benchmark.py"
SPEC = importlib.util.spec_from_file_location("vce_benchmark_patcher", PATCHER_PATH)
assert SPEC and SPEC.loader
PATCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHER)


class VCEBenchmarkRunnerPatchTests(unittest.TestCase):
    def test_string_path_is_normalized_before_path_open(self):
        source = """import json
def jsonl_append(path, payload):
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload) + "\\n")
"""
        patched = PATCHER.patch_source(source)
        namespace = {}
        exec(compile(patched, "<test-runner>", "exec"), namespace)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "progress.jsonl"
            namespace["jsonl_append"](str(target), {"status": "ok"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "ok"})

    def test_patch_is_idempotent(self):
        source = """def jsonl_append(path, payload):
    with path.open('a', encoding='utf-8') as handle:
        handle.write(str(payload))
"""
        once = PATCHER.patch_source(source)
        twice = PATCHER.patch_source(once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
