import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "VideoContextEngine_v3.19.py"
UI = ROOT / "videocontext_web_ui.html"
CONFIG = ROOT / "config/videocontext_settings.apple_silicon.example.json"


class TestSeparateLlamaCppMetalBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ENGINE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.ui = UI.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))

    def function_source(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.get_source_segment(self.source, node)
        self.fail(f"Function not found: {name}")

    def test_mlx_is_the_default_and_backend_is_independent_of_whisper_profile(self):
        self.assertIn('VCE_INFERENCE_BACKEND_MLX = "mlx_metal"', self.source)
        self.assertIn('VCE_INFERENCE_BACKEND_LLAMA_CPP = "llama_cpp_metal"', self.source)
        self.assertIn('setting("inference_backend", VCE_INFERENCE_BACKEND_MLX)', self.source)
        self.assertEqual(self.config["inference_backend"], "mlx_metal")
        self.assertEqual(self.config["runtime_profile"], "baseline_mps_sequential")

    def test_vlm_factory_selects_separate_provider(self):
        factory = self.function_source("get_vlm_engine")
        self.assertIn("LlamaCppEngine()", factory)
        self.assertIn("MLXEngine()", factory)
        self.assertIn("_vce_normalize_inference_backend", factory)

    def test_llama_vlm_requires_qwen3vl_handler_and_mmproj(self):
        loader = self.function_source("load_model")
        self.assertIn("Qwen3VLChatHandler", self.source)
        self.assertIn('setting("vlm_mmproj_model"', self.source)
        self.assertIn("llama_supports_gpu_offload", self.source)
        self.assertIn("n_gpu_layers=-1", self.source)
        self.assertNotIn("Llava15ChatHandler", loader)

    def test_text_llm_uses_llama_cpp_chat_completion_when_selected(self):
        fn = self.function_source("run_text_llm_postprocess")
        self.assertIn("VCE_INFERENCE_BACKEND_LLAMA_CPP", fn)
        self.assertIn("create_chat_completion", fn)
        self.assertIn("stream=True", fn)
        self.assertIn("_vce_configure_mlx_metal", fn)

    def test_llama_backend_has_its_own_worker_and_does_not_use_mlx_trace(self):
        fn = self.function_source("_vce_inference_run")
        self.assertIn("_VCE_LLAMA_CPP_EXECUTOR", self.source)
        self.assertIn("_vce_mlx_run", fn)
        self.assertIn("run_in_executor(_VCE_LLAMA_CPP_EXECUTOR", fn)
        self.assertIn("await _vce_inference_run(vlm_engine.load_model", self.source)
        self.assertEqual(self.source.count("await _vce_inference_run(_vce_describe_scene_with_memory_fallback"), 2)
        self.assertIn("await _vce_inference_run(run_text_llm_postprocess", self.source)
        self.assertNotIn("await _vce_mlx_run(_vce_describe_scene_with_memory_fallback", self.source)

    def test_api_rejects_request_backend_that_differs_from_loaded_server(self):
        self.assertIn("configured_backend = _vce_normalize_inference_backend()", self.source)
        self.assertIn("if inference_backend != configured_backend:", self.source)
        self.assertIn("перезапустите VideoContext Engine", self.source)

    def test_settings_require_gguf_paths_and_sequential_mps_for_llama_architecture(self):
        self.assertIn('("vlm_model", "vlm_mmproj_model", "text_llm_model")', self.source)
        self.assertIn("Для llama.cpp/Metal укажите локальный GGUF-файл", self.source)
        self.assertIn('value.lower().endswith(".gguf")', self.source)
        self.assertIn("llama.cpp/Metal A/B-профиль требует baseline_mps_sequential", self.source)
        self.assertIn("llama.cpp/Metal требует baseline_mps_sequential", self.source)

    def test_ui_exposes_two_independent_selectors_and_model_paths(self):
        self.assertIn('id="cfg-runtime-profile"', self.ui)
        self.assertIn('id="cfg-inference-backend"', self.ui)
        self.assertIn('value="mlx_metal"', self.ui)
        self.assertIn('value="llama_cpp_metal"', self.ui)
        self.assertIn('id="cfg-vlm-mmproj-model"', self.ui)
        self.assertIn("inference_backend: document.getElementById('cfg-inference-backend').value", self.ui)

    def test_documentation_preserves_baseline_and_separates_architectures(self):
        docs = (ROOT / "docs/RUNTIME_PROFILES.md").read_text(encoding="utf-8")
        self.assertIn("The `inference_backend` setting is orthogonal", docs)
        self.assertIn("`baseline_mps_sequential`", docs)
        self.assertIn("There is no automatic fallback", docs)


if __name__ == "__main__":
    unittest.main()
