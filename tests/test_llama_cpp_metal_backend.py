import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LlamaCppMetalBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = (ROOT / "VideoContextEngine_v3.19.py").read_text(encoding="utf-8")
        cls.ui = (ROOT / "videocontext_web_ui.html").read_text(encoding="utf-8")
        cls.config = json.loads((ROOT / "config/videocontext_settings.apple_silicon.example.json").read_text(encoding="utf-8"))

    def test_mlx_is_default_and_llama_is_explicit(self):
        self.assertIn('VCE_INFERENCE_BACKEND_MLX = "mlx_metal"', self.runtime)
        self.assertIn('VCE_INFERENCE_BACKEND_LLAMA_CPP = "llama_cpp_metal"', self.runtime)
        self.assertIn('"inference_backend": VCE_INFERENCE_BACKEND_MLX', self.runtime)
        self.assertEqual(self.config["inference_backend"], "mlx_metal")

    def test_vlm_uses_qwen3_vl_handler_and_8b_projector_pair(self):
        self.assertIn("Qwen3VLChatHandler", self.runtime)
        self.assertNotIn("Llava15ChatHandler", self.runtime)
        self.assertIn("Qwen3VL-8B-Instruct-Q4_K_M.gguf", self.runtime)
        self.assertIn("mmproj-Qwen3VL-8B-Instruct-F16.gguf", self.runtime)

    def test_llama_vlm_context_is_a_validated_request_and_saved_setting(self):
        self.assertIn('"llama_cpp_vlm_context": 8192', self.runtime)
        self.assertIn('"llama_cpp_vlm_context": (4096, 65536)', self.runtime)
        self.assertIn('llama_cpp_vlm_context: int = Form(', self.runtime)
        self.assertIn('await _vce_backend_run(vlm_engine.load_model, target_vlm, llama_cpp_vlm_context)', self.runtime)
        self.assertIn('n_ctx=context_size', self.runtime)
        self.assertEqual(self.config["llama_cpp_vlm_context"], 8192)
        self.assertIn("cfg-llama-vlm-context", self.ui)

    def test_qwen_vl_uses_one_pinned_hf_revision_for_model_and_mmproj(self):
        self.assertIn('DEFAULT_VLM_GGUF_REVISION = "f982a07559d4a2f6c8744d840bf6fccab30eea96"', self.runtime)
        self.assertIn("revision: Optional[str] = None", self.runtime)
        self.assertIn("revision=DEFAULT_VLM_GGUF_REVISION", self.runtime)
        self.assertIn('"mmproj_revision": DEFAULT_VLM_GGUF_REVISION', self.runtime)

    def test_runtime_logs_handler_signature_and_initializes_mtmd_before_scene_inference(self):
        self.assertIn("inspect.signature(Qwen3VLChatHandler.__init__)", self.runtime)
        self.assertIn('"qwen3vl_handler_signature"', self.runtime)
        self.assertIn('init_mtmd_context(self.llm)', self.runtime)
        self.assertIn('"mtmd_context_initialized": True', self.runtime)

    def test_text_llm_has_a_separate_llama_cpp_generation_path(self):
        self.assertIn("DEFAULT_TEXT_LLM_MODEL_GGUF", self.runtime)
        self.assertIn("gguf/Qwen3.5-9B-GGUF", self.runtime)
        self.assertIn("qwen3.5-9b-Q4_K_M.gguf", self.runtime)
        self.assertIn("create_chat_completion", self.runtime)
        self.assertIn("if inference_backend == VCE_INFERENCE_BACKEND_LLAMA_CPP:", self.runtime)
        self.assertIn("from mlx_vlm import load, generate", self.runtime)

    def test_backend_switch_applies_to_both_stages(self):
        self.assertIn('str(setting("llama_cpp_vlm_model", DEFAULT_VLM_MODEL_GGUF))', self.runtime)
        self.assertIn("else vlm_model", self.runtime)
        self.assertIn('text_llm_model = str(setting("llama_cpp_text_llm_model"', self.runtime)
        self.assertIn('"inference_backend": inference_backend', self.runtime)
        self.assertIn('id="cfg-inference-backend"', self.ui)
        self.assertIn("inference_backend: document.getElementById('cfg-inference-backend').value", self.ui)

    def test_ram_mode_is_a_saved_request_setting(self):
        self.assertIn('"ram_mode": "ram-"', self.runtime)
        self.assertIn('ram_mode: str = Form(str(setting("ram_mode", "ram-"))', self.runtime)
        self.assertNotIn('RAM_MODE = os.getenv(', self.runtime)
        self.assertIn("ram_mode: document.getElementById('cfg-ram-mode').value", self.ui)

    def test_metal_is_required_without_silent_cpu_fallback(self):
        self.assertIn("llama_supports_gpu_offload", self.runtime)
        self.assertIn("n_gpu_layers=-1", self.runtime)
        self.assertIn("verbose=True", self.runtime)
        self.assertIn("llama.cpp/Metal доступен только на macOS с Apple Silicon", self.runtime)


if __name__ == "__main__":
    unittest.main()
