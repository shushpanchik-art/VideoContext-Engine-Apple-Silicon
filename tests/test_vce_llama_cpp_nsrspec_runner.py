from pathlib import Path
import importlib.util
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
OBSERVER_PATH = ROOT / "scripts/vce_observed_llama_cpp_run.py"
_SPEC = importlib.util.spec_from_file_location("vce_observed_llama_cpp_run", OBSERVER_PATH)
_OBSERVER = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_OBSERVER)


class TestLlamaCppNsrspecRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "scripts/vce_run_nsrspec_ab.command").read_text(encoding="utf-8")
        cls.legacy_launcher = (ROOT / "scripts/vce_run_nsrspec_llama_cpp.command").read_text(encoding="utf-8")
        cls.observer = OBSERVER_PATH.read_text(encoding="utf-8")
        cls.watchdog = (ROOT / "scripts/vce_runtime_watchdog.py").read_text(encoding="utf-8")
        cls.runtime = (ROOT / "VideoContextEngine_v3.19.py").read_text(encoding="utf-8")

    def test_ab_launcher_runs_both_explicit_backends_in_order(self):
        self.assertIn('run_backend "mlx_metal" "$OUT_ROOT/run1_mlx_metal"', self.launcher)
        self.assertIn('run_backend "llama_cpp_metal" "$OUT_ROOT/run2_llama_cpp_metal"', self.launcher)
        self.assertLess(
            self.launcher.index('run_backend "mlx_metal"'),
            self.launcher.index('run_backend "llama_cpp_metal"'),
        )
        self.assertIn("--inference-backend", self.watchdog)
        self.assertIn("required=True", self.observer)
        self.assertIn('choices=SUPPORTED_INFERENCE_BACKENDS', self.observer)
        self.assertIn('open -a "$APP"', self.launcher)
        self.assertNotIn('open "$API', self.launcher)
        self.assertIn("🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4", self.launcher)

    def test_mtmd_installer_is_pinned_downloaded_and_run_before_app_start(self):
        pin_line = next(line for line in self.launcher.splitlines() if line.startswith("TOOLS_COMMIT="))
        self.assertRegex(pin_line, r'^TOOLS_COMMIT="[0-9a-f]{40}"        self.assertIn('curl -fsSL "$BASE_URL/install_vce_mtmd_loader_fix.py"', self.launcher)
        self.assertIn('"$PY" "$TOOLS_DIR/install_vce_mtmd_loader_fix.py"', self.launcher)
        self.assertLess(
            self.launcher.index('install_vce_mtmd_loader_fix.py" --report'),
            self.launcher.index('open -a "$APP"'),
        )
        self.assertIn("import llama_cpp.mtmd_cpp as mtmd_cpp", self.launcher)
        self.assertIn('ctypes.util.find_library("mtmd") is not None', self.launcher)
        self.assertIn("50e91ffabcf2cb6174b068a12eea0b6b909cba5e", self.launcher)
        self.assertIn("55f8f0b561577b90d1525f6c960359e21f74c27b", self.launcher)
        self.assertIn("934739bd0b5f70dc26665adc115a181d365330e0", self.launcher)

    def test_observer_backend_argument_builds_separate_mlx_and_gguf_settings(self):
        mlx = _OBSERVER.build_backend_settings("mlx_metal")
        llama = _OBSERVER.build_backend_settings("llama_cpp_metal")
        self.assertEqual(mlx["inference_backend"], "mlx_metal")
        self.assertEqual(llama["inference_backend"], "llama_cpp_metal")
        self.assertEqual(llama["vlm_model"], "mlx-community/Qwen3-VL-8B-Instruct-4bit")
        self.assertEqual(llama["text_llm_model"], "mlx-community/Qwen3.5-9B-MLX-4bit")
        self.assertEqual(llama["llama_cpp_vlm_model"], "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
        self.assertEqual(llama["llama_cpp_text_llm_model"], "qwen3.5-9b-Q4_K_M.gguf")
        self.assertNotIn("llama_cpp_vlm_model", mlx)
        self.assertNotIn("llama_cpp_text_llm_model", mlx)
        with self.assertRaises(ValueError):
            _OBSERVER.build_backend_settings("unknown")

    def test_runtime_records_mps_device_with_index_as_verified(self):
        self.assertIn('"device_verified": observed_device.split(":")[0] == device.split(":")[0],', self.runtime)

    def test_gguf_ids_are_confined_to_llama_cpp_fields(self):
        self.assertIn('allowed_vlm = {"mlx-community/Qwen3-VL-8B-Instruct-4bit", "mlx-community/Qwen3-VL-4B-Instruct-4bit"}', self.runtime)
        self.assertIn('merged.get("inference_backend") == VCE_INFERENCE_BACKEND_LLAMA_CPP', self.runtime)
        self.assertIn('merged["vlm_model"] = DEFAULT_VLM_MODEL_MLX', self.runtime)
        self.assertIn('merged["text_llm_model"] = TEXT_LLM_DEFAULT_MODEL', self.runtime)

    def test_single_bundle_contains_both_runs_and_verified_private_dropbox_upload(self):
        self.assertIn("20261104_224554_1f098d/VCE_Forensic_Runs", self.launcher)
        self.assertIn('ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$ARCHIVE"', self.launcher)
        self.assertIn('rclone copyto "$ARCHIVE" "$REMOTE_FILE"', self.launcher)
        self.assertIn('rclone size "$REMOTE_FILE" --json', self.launcher)
        self.assertIn("DROPBOX_UPLOAD=SUCCESS", self.launcher)
        self.assertNotIn('rclone copyto "$VIDEO"', self.launcher)
        self.assertIn("RUN1_MLX_METAL_EXIT", self.launcher)
        self.assertIn("RUN2_LLAMA_CPP_METAL_EXIT", self.launcher)
        self.assertIn("settings_restore_report.json", self.launcher)

    def test_legacy_llama_launcher_routes_to_ab_launcher(self):
        self.assertIn('exec "$SCRIPT_DIR/vce_run_nsrspec_ab.command"', self.legacy_launcher)


    def test_real_mmproj_preflight_downloads_pinned_pair_and_initializes_mtmd_context(self):
        self.assertIn("VLM_HF_REPO_REVISION", self.launcher)
        self.assertIn("revision=hf_revision", self.launcher)
        self.assertIn("handler._init_mtmd_context(llm)", self.launcher)
        self.assertIn('"code": "MTMD_CONTEXT_FAILED"', self.launcher)
        self.assertIn('if [[ "$MTMD_PREFLIGHT_EXIT" -ne 0 ]]', self.launcher)
        self.assertIn("Qwen3VLChatHandler.__init__", self.launcher)
        self.assertIn("model_load", self.launcher)
        self.assertIn("mtmd_context_preflight.json", self.launcher)

    def test_pinned_fork_and_hf_revision_are_explicit(self):
        requirements = (ROOT / "requirements-llama-cpp-metal.txt").read_text(encoding="utf-8")
        self.assertIn("1dd04befbaf1277853eb188cc4513d6089a931dd", requirements)
        self.assertIn("f982a07559d4a2f6c8744d840bf6fccab30eea96", self.runtime)
        self.assertIn("revision=DEFAULT_VLM_GGUF_REVISION", self.runtime)
        self.assertIn('"mmproj_revision": DEFAULT_VLM_GGUF_REVISION', self.runtime)

    def test_observer_records_native_versions_and_mtmd_failure_reason(self):
        self.assertIn("def collect_llama_runtime_info", self.observer)
        self.assertIn('"llama_cpp_version"', self.observer)
        self.assertIn('"libmtmd_path"', self.observer)
        self.assertIn("def classify_backend_failure", self.observer)
        verdict = _OBSERVER.classify_backend_failure(
            500,
            {"detail": "Failed to load mtmd context from /tmp/mmproj.gguf"},
            [],
        )
        self.assertEqual(verdict["code"], "MTMD_CONTEXT_FAILED")
        self.assertIn("mmproj.gguf", verdict["error_text"])

    def test_mtmd_hook_keeps_llama_and_mtmd_from_one_venv_bundle(self):
        installer = (ROOT / "scripts/install_vce_mtmd_loader_fix.py").read_text(encoding="utf-8")
        for library_name in ('"mtmd", "llama", "ggml", "ggml-base", "ggml-metal", "ggml-cpu"'):
            self.assertIn(library_name, installer)
        self.assertIn('os.environ.pop("LLAMA_CPP_LIB_PATH", None)', installer)
        self.assertIn("import llama_cpp.llama_cpp as llama_core", installer)
        self.assertIn('llama_core._lib._name', installer)
        self.assertIn('mtmd_cpp._libmtmd._name', installer)
        self.assertIn("expected the same venv bundle directory", installer)
        self.assertIn("does not parse the real mmproj/model weights", installer)


    def test_bash_syntax_for_both_launchers(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not installed")
        for path in (
            ROOT / "scripts/vce_run_nsrspec_ab.command",
            ROOT / "scripts/vce_run_nsrspec_llama_cpp.command",
        ):
            result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")

    def test_runtime_source_compiles_without_importing_platform_dependencies(self):
        compile(self.runtime, str(ROOT / "VideoContextEngine_v3.19.py"), "exec")


if __name__ == "__main__":
    unittest.main()
)
        self.assertIn('curl -fsSL "$BASE_URL/install_vce_mtmd_loader_fix.py"', self.launcher)
        self.assertIn('"$PY" "$TOOLS_DIR/install_vce_mtmd_loader_fix.py"', self.launcher)
        self.assertLess(
            self.launcher.index('install_vce_mtmd_loader_fix.py" --report'),
            self.launcher.index('open -a "$APP"'),
        )
        self.assertIn("import llama_cpp.mtmd_cpp as mtmd_cpp", self.launcher)
        self.assertIn('ctypes.util.find_library("mtmd") is not None', self.launcher)
        self.assertIn("50e91ffabcf2cb6174b068a12eea0b6b909cba5e", self.launcher)
        self.assertIn("13bb85082a5ba9da5a7a555cb847fa52774c8e8b", self.launcher)
        self.assertIn("934739bd0b5f70dc26665adc115a181d365330e0", self.launcher)

    def test_observer_backend_argument_builds_separate_mlx_and_gguf_settings(self):
        mlx = _OBSERVER.build_backend_settings("mlx_metal")
        llama = _OBSERVER.build_backend_settings("llama_cpp_metal")
        self.assertEqual(mlx["inference_backend"], "mlx_metal")
        self.assertEqual(llama["inference_backend"], "llama_cpp_metal")
        self.assertEqual(llama["vlm_model"], "mlx-community/Qwen3-VL-8B-Instruct-4bit")
        self.assertEqual(llama["text_llm_model"], "mlx-community/Qwen3.5-9B-MLX-4bit")
        self.assertEqual(llama["llama_cpp_vlm_model"], "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
        self.assertEqual(llama["llama_cpp_text_llm_model"], "qwen3.5-9b-Q4_K_M.gguf")
        self.assertNotIn("llama_cpp_vlm_model", mlx)
        self.assertNotIn("llama_cpp_text_llm_model", mlx)
        with self.assertRaises(ValueError):
            _OBSERVER.build_backend_settings("unknown")

    def test_runtime_records_mps_device_with_index_as_verified(self):
        self.assertIn('"device_verified": observed_device.split(":")[0] == device.split(":")[0],', self.runtime)

    def test_gguf_ids_are_confined_to_llama_cpp_fields(self):
        self.assertIn('allowed_vlm = {"mlx-community/Qwen3-VL-8B-Instruct-4bit", "mlx-community/Qwen3-VL-4B-Instruct-4bit"}', self.runtime)
        self.assertIn('merged.get("inference_backend") == VCE_INFERENCE_BACKEND_LLAMA_CPP', self.runtime)
        self.assertIn('merged["vlm_model"] = DEFAULT_VLM_MODEL_MLX', self.runtime)
        self.assertIn('merged["text_llm_model"] = TEXT_LLM_DEFAULT_MODEL', self.runtime)

    def test_single_bundle_contains_both_runs_and_verified_private_dropbox_upload(self):
        self.assertIn("20261104_224554_1f098d/VCE_Forensic_Runs", self.launcher)
        self.assertIn('ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$ARCHIVE"', self.launcher)
        self.assertIn('rclone copyto "$ARCHIVE" "$REMOTE_FILE"', self.launcher)
        self.assertIn('rclone size "$REMOTE_FILE" --json', self.launcher)
        self.assertIn("DROPBOX_UPLOAD=SUCCESS", self.launcher)
        self.assertNotIn('rclone copyto "$VIDEO"', self.launcher)
        self.assertIn("RUN1_MLX_METAL_EXIT", self.launcher)
        self.assertIn("RUN2_LLAMA_CPP_METAL_EXIT", self.launcher)
        self.assertIn("settings_restore_report.json", self.launcher)

    def test_legacy_llama_launcher_routes_to_ab_launcher(self):
        self.assertIn('exec "$SCRIPT_DIR/vce_run_nsrspec_ab.command"', self.legacy_launcher)


    def test_real_mmproj_preflight_downloads_pinned_pair_and_initializes_mtmd_context(self):
        self.assertIn("VLM_HF_REPO_REVISION", self.launcher)
        self.assertIn("revision=hf_revision", self.launcher)
        self.assertIn("handler._init_mtmd_context(llm)", self.launcher)
        self.assertIn('"code": "MTMD_CONTEXT_FAILED"', self.launcher)
        self.assertIn('if [[ "$MTMD_PREFLIGHT_EXIT" -ne 0 ]]', self.launcher)
        self.assertIn("Qwen3VLChatHandler.__init__", self.launcher)
        self.assertIn("model_load", self.launcher)
        self.assertIn("mtmd_context_preflight.json", self.launcher)

    def test_pinned_fork_and_hf_revision_are_explicit(self):
        requirements = (ROOT / "requirements-llama-cpp-metal.txt").read_text(encoding="utf-8")
        self.assertIn("1dd04befbaf1277853eb188cc4513d6089a931dd", requirements)
        self.assertIn("f982a07559d4a2f6c8744d840bf6fccab30eea96", self.runtime)
        self.assertIn("revision=DEFAULT_VLM_GGUF_REVISION", self.runtime)
        self.assertIn('"mmproj_revision": DEFAULT_VLM_GGUF_REVISION', self.runtime)

    def test_observer_records_native_versions_and_mtmd_failure_reason(self):
        self.assertIn("def collect_llama_runtime_info", self.observer)
        self.assertIn('"llama_cpp_version"', self.observer)
        self.assertIn('"libmtmd_path"', self.observer)
        self.assertIn("def classify_backend_failure", self.observer)
        verdict = _OBSERVER.classify_backend_failure(
            500,
            {"detail": "Failed to load mtmd context from /tmp/mmproj.gguf"},
            [],
        )
        self.assertEqual(verdict["code"], "MTMD_CONTEXT_FAILED")
        self.assertIn("mmproj.gguf", verdict["error_text"])

    def test_mtmd_hook_keeps_llama_and_mtmd_from_one_venv_bundle(self):
        installer = (ROOT / "scripts/install_vce_mtmd_loader_fix.py").read_text(encoding="utf-8")
        for library_name in ('"mtmd", "llama", "ggml", "ggml-base", "ggml-metal", "ggml-cpu"'):
            self.assertIn(library_name, installer)
        self.assertIn('os.environ.pop("LLAMA_CPP_LIB_PATH", None)', installer)
        self.assertIn("import llama_cpp.llama_cpp as llama_core", installer)
        self.assertIn('llama_core._lib._name', installer)
        self.assertIn('mtmd_cpp._libmtmd._name', installer)
        self.assertIn("expected the same venv bundle directory", installer)
        self.assertIn("does not parse the real mmproj/model weights", installer)


    def test_bash_syntax_for_both_launchers(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not installed")
        for path in (
            ROOT / "scripts/vce_run_nsrspec_ab.command",
            ROOT / "scripts/vce_run_nsrspec_llama_cpp.command",
        ):
            result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")

    def test_runtime_source_compiles_without_importing_platform_dependencies(self):
        compile(self.runtime, str(ROOT / "VideoContextEngine_v3.19.py"), "exec")


if __name__ == "__main__":
    unittest.main()
