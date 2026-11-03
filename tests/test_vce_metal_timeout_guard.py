from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "VideoContextEngine_v3.19.py"


def load_runtime_helpers():
    source = RUNTIME.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RUNTIME))
    names = {
        "is_gpu_memory_error",
        "_vce_exception_chain_text",
        "_vce_is_metal_command_timeout",
        "_vce_is_metal_memory_error",
        "_vce_describe_scene_with_memory_fallback",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    found = {node.name for node in nodes}
    if found != names:
        raise AssertionError(f"Missing runtime helpers in source: {sorted(names - found)}")
    namespace = {"json": json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(RUNTIME), "exec"), namespace)
    namespace["_vce_clear_metal_memory_caches"] = lambda: None
    return namespace


class FakeVLM:
    def __init__(self, error):
        self.error = error
        self.describe_calls = 0
        self.unload_calls = 0

    def describe_scene(self, images, prompt, max_tokens):
        self.describe_calls += 1
        raise self.error

    def unload_model(self):
        self.unload_calls += 1


class MetalTimeoutGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_runtime_helpers()

    def test_recognizes_exact_macos_gpu_timeout_message(self):
        error = RuntimeError(
            "[METAL] Command buffer execution failed: Caused GPU Timeout Error "
            "(00000002:kIOGPUCommandBufferCallbackErrorTimeout)"
        )
        self.assertTrue(self.helpers["is_gpu_memory_error"](error))
        self.assertTrue(self.helpers["_vce_is_metal_command_timeout"](error))
        self.assertTrue(self.helpers["_vce_is_metal_memory_error"](error))

    def test_recognizes_submissions_ignored_after_prior_gpu_error(self):
        error = RuntimeError(
            "[METAL] Command buffer execution failed: Ignored for prior/excessive GPU errors "
            "(00000004:kIOGPUCommandBufferCallbackErrorSubmissionsIgnored)"
        )
        self.assertTrue(self.helpers["is_gpu_memory_error"](error))
        self.assertTrue(self.helpers["_vce_is_metal_command_timeout"](error))
        self.assertTrue(self.helpers["_vce_is_metal_memory_error"](error))

    def test_follows_wrapped_native_exception_cause(self):
        original = RuntimeError(
            "[METAL] Command buffer execution failed: "
            "kIOGPUCommandBufferCallbackErrorSubmissionsIgnored"
        )
        wrapped = RuntimeError("Недостаточно памяти Metal для VLM после повторов")
        wrapped.__cause__ = original
        self.assertTrue(self.helpers["_vce_is_metal_command_timeout"](wrapped))
        self.assertTrue(self.helpers["_vce_is_metal_memory_error"](wrapped))

    def test_timeout_disables_only_vlm_and_returns_fallback_sentinel(self):
        error = RuntimeError(
            "[METAL] Command buffer execution failed: Caused GPU Timeout Error "
            "(kIOGPUCommandBufferCallbackErrorTimeout)"
        )
        model = FakeVLM(error)
        result = json.loads(
            self.helpers["_vce_describe_scene_with_memory_fallback"](
                model, [object()], "prompt", 512
            )
        )
        self.assertTrue(result["_vce_memory_fallback"])
        self.assertIn("Whisper", result["description"])
        self.assertIn("OCR", result["description"])
        self.assertEqual(model.describe_calls, 1)
        self.assertEqual(model.unload_calls, 1)

    def test_ordinary_allocation_oom_keeps_bounded_resize_recovery(self):
        error = RuntimeError("Metal out of memory: insufficient memory")
        self.assertTrue(self.helpers["_vce_is_metal_memory_error"](error))
        self.assertFalse(self.helpers["_vce_is_metal_command_timeout"](error))


if __name__ == "__main__":
    unittest.main(verbosity=2)
