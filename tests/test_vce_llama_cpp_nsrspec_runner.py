import ast
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import tempfile
import re
import shutil
import subprocess
import unittest
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
OBSERVER_PATH = ROOT / "scripts/vce_observed_llama_cpp_run.py"
_SPEC = importlib.util.spec_from_file_location("vce_observed_llama_cpp_run", OBSERVER_PATH)
_OBSERVER = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_OBSERVER)


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


class TestLlamaCppNsrspecRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher_path = ROOT / "scripts/vce_run_nsrspec_ab.command"
        cls.launcher = cls.launcher_path.read_text(encoding="utf-8")
        cls.legacy_launcher = (ROOT / "scripts/vce_run_nsrspec_llama_cpp.command").read_text(encoding="utf-8")
        cls.observer = OBSERVER_PATH.read_text(encoding="utf-8")
        cls.watchdog = (ROOT / "scripts/vce_runtime_watchdog.py").read_text(encoding="utf-8")
        cls.runtime = (ROOT / "VideoContextEngine_v3.19.py").read_text(encoding="utf-8")
        cls.installer = (ROOT / "scripts/install_vce_mtmd_loader_fix.py").read_text(encoding="utf-8")

    def test_ab_launcher_runs_both_explicit_backends_in_order(self):
        mlx = 'run_backend "mlx_metal" "$OUT_ROOT/run1_mlx_metal"'
        llama = 'run_backend "llama_cpp_metal" "$OUT_ROOT/run2_llama_cpp_metal"'
        self.assertIn(mlx, self.launcher)
        self.assertIn(llama, self.launcher)
        self.assertLess(self.launcher.index(mlx), self.launcher.index(llama))
        self.assertIn("--inference-backend", self.watchdog)
        self.assertIn("required=True", self.observer)
        self.assertIn("choices=SUPPORTED_INFERENCE_BACKENDS", self.observer)
        self.assertIn('open -a "$APP"', self.launcher)
        self.assertNotIn('open "$API', self.launcher)
        self.assertIn('VIDEO="/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"', self.launcher)
        self.assertNotIn("video_path = input(", self.launcher)
        self.assertIn('"video_path": video_path', self.launcher)

    def test_pinned_tool_hashes_match_repository_blobs(self):
        expected = {
            "watchdog": "50e91ffabcf2cb6174b068a12eea0b6b909cba5e",
            "installer": "934739bd0b5f70dc26665adc115a181d365330e0",
        }
        actual = {
            "watchdog": git_blob_sha(ROOT / "scripts/vce_runtime_watchdog.py"),
            "installer": git_blob_sha(ROOT / "scripts/install_vce_mtmd_loader_fix.py"),
        }
        self.assertEqual(actual, expected)
        for sha in expected.values():
            self.assertIn(sha, self.launcher)
        self.assertIn('RUNNER_BLOB_SHA="${RUNNER_BLOB_SHA:-${VCE_RUNNER_BLOB_SHA:-39fff57545c50500c3ce241fcb10ae3004204c0c}}"', self.launcher)
        self.assertNotIn("13bb85082a5ba9da5a7a555cb847fa52774c8e8b", self.launcher)

    def test_mtmd_installer_is_pinned_and_runs_before_app_start(self):
        pin_line = next(line for line in self.launcher.splitlines() if line.startswith("TOOLS_COMMIT="))
        self.assertRegex(pin_line, r'^TOOLS_COMMIT="[0-9a-f]{40}"$')
        self.assertIn('curl -fsSL "$BASE_URL/install_vce_mtmd_loader_fix.py"', self.launcher)
        self.assertIn('"$PY" "$TOOLS_DIR/install_vce_mtmd_loader_fix.py"', self.launcher)
        self.assertLess(
            self.launcher.index('install_vce_mtmd_loader_fix.py" --report'),
            self.launcher.index('open -a "$APP"'),
        )
        self.assertIn("import llama_cpp.mtmd_cpp as mtmd_cpp", self.launcher)
        self.assertIn('for name in ("mtmd", "llama", "ggml", "ggml-base")', self.launcher)
        self.assertIn("resolved = ctypes.util.find_library(name)", self.launcher)
        self.assertIn("if resolved is not None:", self.launcher)

    def test_tool_integrity_is_checked_before_live_server_guard(self):
        check = self.launcher.index('git -C "$ROOT" hash-object "$ROOT/scripts/vce_observed_llama_cpp_run.py"')
        server_guard = self.launcher.index('API_PIDS=')
        installer = self.launcher.index('install_vce_mtmd_loader_fix.py" --report')
        app_start = self.launcher.index('open -a "$APP"')
        self.assertLess(check, server_guard)
        self.assertLess(server_guard, installer)
        self.assertLess(installer, app_start)
        self.assertIn("lsof -nP -tiTCP:7555", self.launcher)
        self.assertIn("pgrep -f", self.launcher)
        self.assertIn("дождись закрытия API на порту 7555", self.launcher)
        self.assertIn("printf 'API listener PID(s): %s", self.launcher)

    def test_observer_backend_argument_builds_separate_mlx_and_gguf_settings(self):
        mlx = _OBSERVER.build_backend_settings("mlx_metal")
        llama = _OBSERVER.build_backend_settings("llama_cpp_metal")
        self.assertEqual(mlx["inference_backend"], "mlx_metal")
        self.assertEqual(llama["inference_backend"], "llama_cpp_metal")
        # 2026-10-25 documented baseline: Qwen3-VL + Qwen3.5 use MLX/Metal;
        # Whisper remains openai-whisper / PyTorch-MPS in both A/B passes.
        for settings in (mlx, llama):
            self.assertEqual(settings["runtime_profile"], "baseline_mps_sequential")
            self.assertFalse(settings["enable_mlx_whisper"])
            self.assertEqual(settings["whisper_model"], "large")
            self.assertEqual(settings["vlm_model"], "mlx-community/Qwen3-VL-8B-Instruct-4bit")
            self.assertEqual(settings["text_llm_model"], "mlx-community/Qwen3.5-9B-MLX-4bit")
        self.assertEqual(llama["vlm_model"], "mlx-community/Qwen3-VL-8B-Instruct-4bit")
        self.assertEqual(llama["text_llm_model"], "mlx-community/Qwen3.5-9B-MLX-4bit")
        self.assertEqual(llama["llama_cpp_vlm_model"], "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
        self.assertEqual(llama["llama_cpp_text_llm_model"], "qwen3.5-9b-Q4_K_M.gguf")
        self.assertNotIn("llama_cpp_vlm_model", mlx)
        self.assertNotIn("llama_cpp_text_llm_model", mlx)
        with self.assertRaises(ValueError):
            _OBSERVER.build_backend_settings("unknown")

    def test_runtime_records_mps_device_with_index_as_verified(self):
        self.assertIn("def _vce_device_type_name(value: Any) -> str:", self.runtime)
        self.assertIn("_vce_device_type_name(observed_device) == _vce_device_type_name(device)", self.runtime)
        self.assertIn('return str(device_type).strip().lower().split(":", 1)[0]', self.runtime)

    def test_text_llm_backend_diagnostic_matches_selected_backend(self):
        self.assertIn('"llama.cpp"', self.runtime)
        self.assertIn('"backend": text_llm_backend if text_llm_enabled else None', self.runtime)
        self.assertNotIn('"backend": "mlx-vlm" if text_llm_enabled else None', self.runtime)

    def test_text_llm_failure_logs_full_traceback(self):
        self.assertIn("traceback.print_exc()", self.runtime)
        self.assertIn('text_llm_status = "failed"', self.runtime)
        self.assertIn("text_llm_error = str(exc)[:500]", self.runtime)

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
        self.assertIn('HF_HUB_DOWNLOAD_TIMEOUT="${HF_HUB_DOWNLOAD_TIMEOUT:-120}"', self.launcher)
        self.assertIn("HF_PREFLIGHT: resolving pinned Qwen3-VL Q4 model", self.launcher)
        self.assertIn("HF_PREFLIGHT: model ready", self.launcher)
        self.assertIn("HF_PREFLIGHT: model and mmproj files are present", self.launcher)
        self.assertIn("inspect.Parameter.VAR_KEYWORD", self.launcher)
        self.assertIn('report["qwen3vl_handler_accepts_kwargs"]', self.launcher)
        self.assertIn('init_keyword = "mmproj_path"', self.launcher)
        self.assertIn("Qwen3VLChatHandler accepts neither mmproj_path nor **kwargs", self.launcher)
        self.assertIn('report["mtmd_cpp_module_path"]', self.launcher)
        self.assertIn('for name in mtmd_required_symbols', self.launcher)
        self.assertIn('"mtmd_init_from_file", "mtmd_get_video_config"', self.launcher)
        self.assertIn('report["mtmd_cpp_native_symbols"]', self.launcher)
        self.assertIn('handler._init_mtmd_context(llm)', self.launcher)
        self.assertIn('report["video_capability"]', self.launcher)
        self.assertIn('if not report["video_capability"]:', self.launcher)

    def test_qwen3vl_kwargs_constructor_accepts_canonical_mmproj_path(self):
        # The pinned fork reports (self, force_reasoning=False, add_vision_id=True, **kwargs).
        # Qwen3VLChatHandler forwards these kwargs to MTMDBaseHandler, where mmproj_path
        # is explicit. A direct-parameter-only signature check is therefore a false negative.
        self.assertIn("signature_parameters = signature.parameters", self.launcher)
        self.assertIn("inspect.Parameter.VAR_KEYWORD", self.launcher)
        self.assertIn("accepts_arbitrary_kwargs = any(", self.launcher)
        self.assertIn('elif accepts_arbitrary_kwargs:', self.launcher)
        self.assertIn('init_keyword = "mmproj_path"', self.launcher)
        self.assertIn('report["handler_constructor"] = "passed"', self.launcher)

    def test_llama_cpp_pass_is_gated_by_successful_mlx_baseline(self):
        baseline = self.launcher.index('run_backend "mlx_metal" "$OUT_ROOT/run1_mlx_metal"')
        gate = self.launcher.index('if [[ "$RUN1_EXIT" -ne 0 ]]; then')
        llama = self.launcher.index('run_backend "llama_cpp_metal" "$OUT_ROOT/run2_llama_cpp_metal"')
        self.assertLess(baseline, gate)
        self.assertLess(gate, llama)
        self.assertIn("RUN2_EXIT=125", self.launcher[gate:llama])
        self.assertIn("MLX baseline не завершился успешно", self.launcher[gate:llama])
        self.assertIn('"baseline_gate_for_llama_cpp": True', self.launcher)
        self.assertIn('"skipped_reason": run2_skipped_reason', self.launcher)

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
        for library_name in ('"mtmd", "llama", "ggml", "ggml-base", "ggml-metal", "ggml-cpu"'):
            self.assertIn(library_name, self.installer)
        self.assertIn('os.environ.pop("LLAMA_CPP_LIB_PATH", None)', self.installer)
        self.assertIn("import llama_cpp.llama_cpp as llama_core", self.installer)
        self.assertIn("llama_core._lib._name", self.installer)
        self.assertIn("mtmd_cpp._libmtmd._name", self.installer)
        self.assertIn("expected the same venv bundle directory", self.installer)
        self.assertIn("does not parse the real mmproj/model weights", self.installer)

    def test_bash_syntax_for_both_launchers(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not installed")
        for path in (
            self.launcher_path,
            ROOT / "scripts/vce_run_nsrspec_llama_cpp.command",
        ):
            result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")

    def test_production_qwen3vl_constructor_contract_accepts_forwarded_kwargs(self):
        tree = ast.parse(self.runtime)
        resolver_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_vce_qwen3vl_mmproj_init_keyword"
        )
        namespace = {"Any": Any}
        exec(
            compile(
                ast.Module(body=[resolver_node], type_ignores=[]),
                "VideoContextEngine_v3.19.py",
                "exec",
            ),
            namespace,
        )
        resolve = namespace["_vce_qwen3vl_mmproj_init_keyword"]

        class VariadicQwenHandler:
            def __init__(self, force_reasoning=False, add_vision_id=True, **kwargs):
                pass

        class ExplicitMmprojHandler:
            def __init__(self, mmproj_path=None):
                pass

        class LegacyClipHandler:
            def __init__(self, clip_model_path=None):
                pass

        class UnsupportedHandler:
            def __init__(self, force_reasoning=False):
                pass

        self.assertEqual(resolve(VariadicQwenHandler), "mmproj_path")
        self.assertEqual(resolve(ExplicitMmprojHandler), "mmproj_path")
        self.assertEqual(resolve(LegacyClipHandler), "clip_model_path")
        with self.assertRaises(RuntimeError):
            resolve(UnsupportedHandler)
        self.assertIn(
            "_vce_qwen3vl_mmproj_init_keyword(Qwen3VLChatHandler)",
            self.runtime,
        )

    def test_runtime_and_observer_source_compile(self):
        compile(self.runtime, str(ROOT / "VideoContextEngine_v3.19.py"), "exec")
        compile(self.observer, str(OBSERVER_PATH), "exec")


    def test_qwen35_llama_completion_uses_native_json_grammar_and_zero_reasoning_budget(self):
        tree = ast.parse(self.runtime)
        helper_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_vce_llama_json_completion"
        )
        namespace = {}
        exec(
            compile(ast.Module(body=[helper_node], type_ignores=[]),
                    "VideoContextEngine_v3.19.py", "exec"),
            namespace,
        )
        complete = namespace["_vce_llama_json_completion"]

        class FakeLlama:
            def __init__(self):
                self.calls = []

            def create_chat_completion(self, **kwargs):
                self.calls.append(kwargs)
                return {"choices": []}

        fake = FakeLlama()
        for stream in (False, True):
            complete(fake, "prompt", 2048, 0.0, stream)
        self.assertEqual([call["stream"] for call in fake.calls], [False, True])
        for call in fake.calls:
            self.assertEqual(call["response_format"], {"type": "json_object"})
            self.assertEqual(call["reasoning_budget"], 0)
            self.assertEqual(call["reasoning_start"], "<think>")
            self.assertEqual(call["reasoning_end"], "</think>")
            self.assertTrue(call["reasoning_start_in_prompt"])
        self.assertIn("_vce_llama_json_completion(instance, prompt, max_tokens, temperature, stream)", self.runtime)

    def test_qwen35_json_parser_skips_prose_and_closed_thinking_blocks(self):
        tree = ast.parse(self.runtime)
        helper_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_text_llm_extract_json_object"
        )
        namespace = {"Any": Any, "Dict": Dict, "json": json, "re": re}
        exec(
            compile(ast.Module(body=[helper_node], type_ignores=[]),
                    "VideoContextEngine_v3.19.py", "exec"),
            namespace,
        )
        extract = namespace["_text_llm_extract_json_object"]
        result = extract(
            "<think>Scratch {not JSON}</think>\n"
            "I will now answer:\n"
            '{"executive_summary":"валидный результат","topics":[]}'
        )
        self.assertEqual(result["executive_summary"], "валидный результат")

    def test_qwen35_json_parser_rejects_json_like_text_inside_unterminated_thinking(self):
        tree = ast.parse(self.runtime)
        helper_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_text_llm_extract_json_object"
        )
        namespace = {"Any": Any, "Dict": Dict, "json": json, "re": re}
        exec(
            compile(ast.Module(body=[helper_node], type_ignores=[]),
                    "VideoContextEngine_v3.19.py", "exec"),
            namespace,
        )
        extract = namespace["_text_llm_extract_json_object"]
        with self.assertRaises(ValueError):
            extract('<think>unfinished thoughts {"executive_summary":"not final"}')

    def test_observer_saves_bounded_text_llm_raw_diagnostic_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            timings = {
                "text_llm": {
                    "raw_excerpt": '{"executive_summary":',
                    "repair_raw_excerpt": "{'executive_summary':",
                    "primary_parse_error": "invalid first output",
                    "repair_parse_error": "invalid repair output",
                }
            }
            _OBSERVER.save_timing_outputs(run_dir, {"meta": {"timings": timings}})
            artifact = run_dir / "text_llm_raw.txt"
            self.assertTrue(artifact.exists())
            saved = artifact.read_text(encoding="utf-8")
            self.assertIn("Primary generation", saved)
            self.assertIn("Repair-pass generation", saved)
            self.assertIn("invalid repair output", saved)
            self.assertEqual(timings["text_llm"]["raw_diagnostic_file"], "text_llm_raw.txt")

    def test_text_llm_raw_diagnostics_are_bounded_and_exposed_in_api_timing(self):
        self.assertIn('failure.raw_excerpt = _text_llm_clip(raw, 2000)', self.runtime)
        self.assertIn('failure.repair_raw_excerpt = _text_llm_clip(repaired_raw, 2000)', self.runtime)
        self.assertIn('"raw_excerpt": text_llm_raw_excerpt or None', self.runtime)
        self.assertIn('"repair_raw_excerpt": text_llm_repair_raw_excerpt or None', self.runtime)

    def test_http_200_with_failed_text_llm_is_not_a_pass(self):
        verdict = _OBSERVER.classify_backend_failure(
            200,
            {"text_llm": {"enabled": True, "status": "failed", "error": "invalid JSON after retry"}},
            ["Traceback (most recent call last):", "ValueError: invalid JSON"],
        )
        self.assertEqual(verdict["code"], "TEXT_LLM_FAILED")
        self.assertEqual(verdict["text_llm_status"], "failed")
        self.assertIn("invalid JSON", verdict["error_text"])
        self.assertTrue(any("Traceback" in line for line in verdict["evidence_lines"]))

    def test_observer_returns_nonzero_for_text_llm_failure(self):
        self.assertIn('if failure_verdict["code"] != "PASS":', self.observer)
        self.assertIn('print("VERDICT=" + failure_verdict["code"])', self.observer)
        self.assertIn('print("TEXT_LLM_ERROR:"', self.observer)
        self.assertIn("return 3", self.observer)

if __name__ == "__main__":
    unittest.main()
