from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestLlamaCppNsrspecRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "scripts/vce_run_nsrspec_llama_cpp.command").read_text(encoding="utf-8")
        cls.observer = (ROOT / "scripts/vce_observed_llama_cpp_run.py").read_text(encoding="utf-8")
        cls.watchdog = (ROOT / "scripts/vce_runtime_watchdog.py").read_text(encoding="utf-8")

    def test_launcher_uses_fixed_video_and_native_app_only(self):
        self.assertIn("🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4", self.launcher)
        self.assertIn('open -a "$APP"', self.launcher)
        self.assertNotIn('open "$API', self.launcher)
        self.assertIn("TOOLS_COMMIT=", self.launcher)
        self.assertIn("git hash-object", self.launcher)

    def test_launcher_requires_runtime_profile_mps_and_qwen3vl_metal(self):
        self.assertIn('"runtime_profile" not in props', self.launcher)
        self.assertIn("x.cpu().double().numpy()", self.launcher)
        self.assertIn("Qwen3VLChatHandler", self.launcher)
        self.assertIn("llama_supports_gpu_offload", self.launcher)

    def test_observer_selects_llama_for_both_llm_stages_and_keeps_whisper_mps(self):
        self.assertIn('"inference_backend": "llama_cpp_metal"', self.observer)
        self.assertIn('"llama_cpp_vlm_model": "Qwen3-VL-8B-Instruct-Q4_K_M.gguf"', self.observer)
        self.assertIn('"llama_cpp_text_llm_model": "Qwen3.5-9B-Q4_K_M.gguf"', self.observer)
        self.assertIn('"runtime_profile": "baseline_mps_sequential"', self.observer)
        self.assertIn('"enable_mlx_whisper": False', self.observer)
        self.assertIn('"enable_ocr_cache": True', self.observer)
        self.assertIn('"ffmpeg_ocr_interval": 5', self.observer)
        self.assertIn('"ffmpeg_ocr_workers": 3', self.observer)
        self.assertIn("post_json(", self.observer)

    def test_private_dropbox_upload_is_verified_and_video_is_not_uploaded(self):
        self.assertIn("20261104_224554_1f098d/VCE_Forensic_Runs", self.launcher)
        self.assertIn('rclone copyto "$ARCHIVE" "$REMOTE_FILE"', self.launcher)
        self.assertIn('rclone size "$REMOTE_FILE" --json', self.launcher)
        self.assertIn("DROPBOX_UPLOAD=SUCCESS", self.launcher)
        self.assertIn("no video was submitted", self.launcher)
        self.assertNotIn("rclone copyto \"$VIDEO\"", self.launcher)
        self.assertIn("does not upload video/results", self.watchdog)


if __name__ == "__main__":
    unittest.main()
