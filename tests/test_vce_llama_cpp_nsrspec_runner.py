import ast
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import tempfile
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
        cls.patch_installer = (ROOT / "scripts/vce_patch_test_run_nsrspec_json.command").read_text(encoding="utf-8")
        cls.legacy_patch_installer = (ROOT / "scripts/vce_patch_test_run_nsrspec_ab.command").read_text(encoding="utf-8")

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
        self.assertIn('ROOT="${VCE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"', self.launcher)
        self.assertIn('VIDEO="${VCE_VIDEO:-$HOME/Downloads/NSR Specification/', self.launcher)
        self.assertIn('--video) [[ $# -ge 2 ]] || fail "--video требует путь"; VIDEO="$2"; shift 2 ;;', self.launcher)
        self.assertNotIn("video_path = input(", self.launcher)
        self.assertIn('"video_path": video_path', self.launcher)

    def test_pinned_tool_hashes_match_repository_blobs(self):
        expected = {"installer": "934739bd0b5f70dc26665adc115a181d365330e0"}
        actual = {"installer": git_blob_sha(ROOT / "scripts/install_vce_mtmd_loader_fix.py")}
        self.assertEqual(actual, expected)
        for sha in expected.values():
            self.assertIn(sha, self.launcher)
        self.assertNotIn("RUNNER_BLOB_SHA", self.launcher)
        self.assertNotIn('hash-object "$ROOT/scripts/vce_observed_llama_cpp_run.py"', self.launcher)
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
        check = self.launcher.index('git hash-object "$TOOLS_DIR/install_vce_mtmd_loader_fix.py"')
        server_guard = self.launcher.index('API_PIDS=')
        installer = self.launcher.index('install_vce_mtmd_loader_fix.py" --report')
        app_start = self.launcher.index('open -a "$APP"')
        self.assertLess(check, server_guard)
        self.assertLess(server_guard, installer)
        self.assertLess(installer, app_start)
        self.assertIn('WATCHDOG="$ROOT/scripts/vce_runtime_watchdog.py"', self.launcher)
        self.assertNotIn('hash-object "$TOOLS_DIR/vce_runtime_watchdog.py"', self.launcher)
        self.assertIn("lsof -nP -tiTCP:7555", self.launcher)
        self.assertIn("pgrep -f", self.launcher)
        self.assertIn("дождись закрытия API на порту 7555", self.launcher)
        self.assertIn("printf 'API listener PID(s): %s", self.launcher)

    def test_observer_backend_argument_builds_separate_mlx_and_gguf_settings(self):
        mlx = _OBSERVER.build_backend_settings("mlx_metal", "nsrspec")
        llama = _OBSERVER.build_backend_settings("llama_cpp_metal", "nsrspec")
        self.assertEqual(mlx["inference_backend"], "mlx_metal")
        self.assertEqual(llama["inference_backend"], "llama_cpp_metal")
        # 2026-10-25 documented baseline: Qwen3-VL + Qwen3.5 use MLX/Metal;
        # Whisper remains openai-whisper / PyTorch-MPS in both A/B passes.
        for settings in (mlx, llama):
            self.assertEqual(settings["runtime_profile"], "baseline_mps_sequential")
            self.assertFalse(settings["enable_mlx_whisper"])
            self.assertIs(settings["enable_ocr_cache"], False)
            self.assertEqual(settings["whisper_model"], "large")
            self.assertEqual(settings["vlm_model"], "mlx-community/Qwen3-VL-8B-Instruct-4bit")
            self.assertEqual(settings["text_llm_model"], "mlx-community/Qwen3.5-9B-MLX-4bit")
        self.assertEqual(llama["vlm_model"], "mlx-community/Qwen3-VL-8B-Instruct-4bit")
        self.assertEqual(llama["text_llm_model"], "mlx-community/Qwen3.5-9B-MLX-4bit")
        self.assertEqual(llama["llama_cpp_vlm_model"], "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
        self.assertEqual(llama["llama_cpp_text_llm_model"], "qwen3.5-9b-Q4_K_M.gguf")
        current = _OBSERVER.build_backend_settings("mlx_metal", "current")
        self.assertEqual(current, {"inference_backend": "mlx_metal"})
        self.assertFalse(set(_OBSERVER.NSRSPEC_REQUEST_OVERRIDES) & set(_OBSERVER.COMMON_BACKEND_SETTINGS))
        self.assertNotIn("llama_cpp_vlm_model", mlx)
        self.assertNotIn("llama_cpp_text_llm_model", mlx)
        with self.assertRaises(ValueError):
            _OBSERVER.build_backend_settings("unknown")

    def test_observer_uses_active_ocr_cache_value_in_effective_multipart_fields(self):
        properties = {
            "enable_ocr_cache": {"type": "boolean"},
            "response_format": {"type": "string"},
            "generate_json": {"type": "boolean"},
        }
        fields_off, _ = _OBSERVER.resolve_form_fields(
            {"enable_ocr_cache": False, "response_format": "json", "generate_json": True}, properties, "current"
        )
        fields_on, _ = _OBSERVER.resolve_form_fields(
            {"enable_ocr_cache": True, "response_format": "json", "generate_json": True}, properties, "current"
        )
        self.assertIs(fields_off["enable_ocr_cache"], False)
        self.assertIs(fields_on["enable_ocr_cache"], True)
        self.assertNotIn("enable_ocr_cache", _OBSERVER.NSRSPEC_REQUEST_OVERRIDES)
        self.assertIs(fields_on["generate_json"], True)
        nsrspec_fields, _ = _OBSERVER.resolve_form_fields(
            {"enable_ocr_cache": False, "response_format": "json", "generate_json": True}, properties, "nsrspec"
        )
        self.assertIs(nsrspec_fields["generate_json"], False)
        self.assertIn('if active_settings.get("enable_ocr_cache") is not False:', self.observer)
        self.assertIn("A/B requires enable_ocr_cache=false for both passes", self.observer)
        self.assertIn('if profile == "nsrspec":', self.observer)
        self.assertIn('fields, origins = resolve_form_fields(active_settings, properties, args.profile)', self.observer)
        self.assertIn('parser.add_argument("--profile", choices=("current", "nsrspec"), default="current"', self.observer)

    def test_context_and_result_limits_are_settings_backed(self):
        for key in ("text_llm_context_max_chars", "text_llm_context_max_scenes",
                    "text_llm_summary_min_words", "text_llm_summary_max_words",
                    "text_llm_max_key_points", "text_llm_max_topics"):
            self.assertIn(key, self.runtime)
        self.assertIn('defaults.setdefault("text_llm_summary_max_words", 90)', self.runtime)
        self.assertIn('if context_max_scenes > 0 and len(context.get("scenes", [])) > context_max_scenes:', self.runtime)

    def test_observer_uses_explicit_nsrspec_profile(self):
        self.assertIn('"domain_profile": "nsrspec"', self.observer)

    def test_nsrspec_evidence_rewrites_are_profile_gated(self):
        self.assertIn('if str(setting("domain_profile", "generic") or "generic").strip().lower() != "nsrspec":', self.runtime)
        self.assertIn('_vce4_is_nsrspec = str(setting("domain_profile", "generic")', self.runtime)

    def test_generic_technical_cues_do_not_name_nsrspec_products(self):
        line = next(x for x in self.runtime.splitlines() if "technical_cues = re.compile" in x)
        self.assertNotIn("cadlib", line.lower())
        self.assertNotIn("nanocad", line.lower())
        self.assertNotIn("пандус", line.lower())

    def test_generic_entity_scene_matching_is_not_profile_gated(self):
        self.assertIn('if n.replace(" ", "") in src_blob.replace(" ", "") or (nsrspec_profile and has_buildchat_conflict', self.runtime)
        self.assertIn('elif nsrspec_profile and (compact_name == "кси" or', self.runtime)

    def test_observer_checks_only_required_backend_settings(self):
        self.assertIn("required_experiment_mismatches", self.observer)
        self.assertIn("for key, value in backend_settings.items()", self.observer)
        self.assertNotIn("if differences:", self.observer)

    def test_nsrspec_profile_is_explicit_and_generic_is_default(self):
        self.assertIn('setting("domain_profile", "generic")', self.runtime)
        self.assertIn('from domain_profiles.nsrspec import PROMPT_INSTRUCTIONS', self.runtime)
        self.assertIn('"domain_profile": "nsrspec"', self.observer)

    def test_patch_installers_share_one_pinned_manifest(self):
        self.assertRegex(self.patch_installer, r'PATCH_COMMIT="[0-9a-f]{40}"')
        for entry in (
            "VideoContextEngine_v3.19.py 6485d05d5be0b5dce4bbed15657ef10b4b216945",
            "scripts/vce_observed_llama_cpp_run.py 481f13e7e9f58fc7d440fa4fffc85ddfc9c152fd",
            "scripts/vce_run_nsrspec_ab.command 0ef0cbd7034299f63cb972d76d733e6c6741fda0",
            "scripts/vce_runtime_watchdog.py 4cf0c6db9c9ab855532c704acd75d5f880095a4a",
            "domain_profiles/nsrspec.py aada467c7060b44487e9d2e3ee038c1b67b7f098",
        ):
            self.assertIn(entry, self.patch_installer)
        self.assertIn('exec /bin/bash "$ROOT/scripts/vce_patch_test_run_nsrspec_json.command" "$@"', self.legacy_patch_installer)
        self.assertNotIn("PATCH_COMMIT=", self.legacy_patch_installer)
        self.assertNotIn("/Users/", self.patch_installer)
        self.assertIn('if ! command -v rclone >/dev/null 2>&1; then', self.patch_installer)
        self.assertIn("validation archive kept locally", self.patch_installer)
        self.assertIn('if [[ "$SKIP_AB" == "1" ]]; then', self.patch_installer)

    def test_patch_installer_uses_full_pytest_suite_and_profiles(self):
        self.assertIn('"$PY" -m pytest -q tests', self.patch_installer)
        self.assertIn('"$ROOT/domain_profiles/nsrspec.py"', self.patch_installer)
        self.assertIn('export VCE_ROOT="$ROOT" VCE_VIDEO="$VIDEO" VCE_APP="$APP" VCE_API_URL="$API"', self.patch_installer)

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

    def test_empty_out_root_aborts_before_archive_or_dropbox_upload(self):
        self.assertIn('local file_count', self.launcher)
        self.assertIn('find "$OUT_ROOT" -type f ! -name "SHA256SUMS.txt"', self.launcher)
        self.assertIn('OUT_ROOT пуст; архив не создаю, upload отменён.', self.launcher)
        self.assertLess(
            self.launcher.index('OUT_ROOT пуст; архив не создаю, upload отменён.'),
            self.launcher.index('/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$archive"'),
        )
        self.assertLess(
            self.launcher.index('OUT_ROOT пуст; архив не создаю, upload отменён.'),
            self.launcher.index('rclone copyto "$archive" "$remote_file" --progress'),
        )

    def test_dropbox_is_optional_and_local_zip_is_a_successful_fallback(self):
        self.assertIn('if [[ "$DROPBOX_AVAILABLE" != "1" ]]; then', self.launcher)
        self.assertIn("forensic ZIP сохранён локально", self.launcher)
        self.assertNotIn('command -v rclone >/dev/null 2>&1 || fail', self.launcher)

    def test_single_bundle_contains_both_runs_and_verified_private_dropbox_upload(self):
        self.assertIn("20261104_224554_1f098d/VCE_forensic_runs_NSRSPEC_AB", self.launcher)
        self.assertIn('/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$archive"', self.launcher)
        self.assertIn('rclone copyto "$archive" "$remote_file" --progress', self.launcher)
        self.assertIn('rclone size "$remote_file" --json', self.launcher)
        self.assertIn("DROPBOX_UPLOAD=SUCCESS", self.launcher)
        self.assertNotIn('rclone copyto "$VIDEO"', self.launcher)
        self.assertIn("RUN1_MLX_METAL_EXIT", self.launcher)
        self.assertIn("RUN2_LLAMA_CPP_METAL_EXIT", self.launcher)
        self.assertIn("--observer-profile nsrspec", self.launcher)
        self.assertIn("--profile", self.watchdog)
        self.assertIn("settings_restore_report.json", self.launcher)


    def test_exit_trap_collects_external_log_and_uploads_after_restore_failure(self):
        finish = self.launcher[
            self.launcher.index("finish() {"):
            self.launcher.index("trap finish EXIT")
        ]
        self.assertIn("restore_settings", finish)
        self.assertIn("capture_external_logs", finish)
        self.assertIn("upload_forensic_bundle", finish)
        self.assertLess(finish.index("restore_settings"), finish.index("upload_forensic_bundle"))
        self.assertIn('local server_log="$HOME/Library/Logs/VideoContext Engine/server.log"', self.launcher)
        self.assertIn('/bin/cp -p "$server_log" "$OUT_ROOT/server.log"', self.launcher)
        self.assertIn("SHA256SUMS.txt", self.launcher)
        self.assertIn('/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$archive"', self.launcher)
        self.assertIn('rclone copyto "$archive" "$remote_file" --progress', self.launcher)
        self.assertIn('rclone size "$remote_file" --json', self.launcher)
        self.assertIn('trap finish EXIT', self.launcher)
        self.assertNotIn('rclone copyto "$VIDEO"', self.launcher)
        self.assertIn('20261104_224554_1f098d/VCE_forensic_runs_NSRSPEC_AB', self.launcher)

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
        self.assertIn('"baseline_gate_for_llama_cpp": not diagnostic_only', self.launcher)
        self.assertIn('"comparison_result_valid": preflight_exit_code == 0 and run1_exit == 0 and run2_exit == 0 and not diagnostic_only', self.launcher)
        self.assertIn("--diagnostic-llama-cpp", self.launcher)
        self.assertIn('"role": "diagnostic-only" if diagnostic_only else "comparison backend"', self.launcher)
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


    def test_qwen35_llama_completion_uses_exact_system_user_kwargs_and_captures_stream_metadata(self):
        tree = ast.parse(self.runtime)
        helper_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_vce_llama_json_completion"
        )
        namespace = {"VCE_TEXT_LLM_GENERATION_DIAGNOSTICS": {}}
        exec(
            compile(ast.Module(body=[helper_node], type_ignores=[]),
                    "VideoContextEngine_v3.19.py", "exec"),
            namespace,
        )
        complete = namespace["_vce_llama_json_completion"]
        diagnostics = namespace["VCE_TEXT_LLM_GENERATION_DIAGNOSTICS"]

        class FakeLlama:
            def __init__(self):
                self.calls = []

            def create_chat_completion(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs["stream"]:
                    return iter([
                        {"choices": [{"delta": {"content": "{"}, "finish_reason": None}]},
                        {"choices": [{"delta": {"content": '"ok":true}'}, "finish_reason": "stop"}]},
                    ])
                return {
                    "choices": [{
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4},
                }

        fake = FakeLlama()
        normal = complete(fake, "только тестовые данные", 512, 0.0, False)
        streamed = list(complete(fake, "только тестовые данные", 256, 0.0, True))
        self.assertEqual(normal["choices"][0]["finish_reason"], "stop")
        self.assertEqual(len(streamed), 2)
        self.assertEqual([call["stream"] for call in fake.calls], [False, True])
        for call in fake.calls:
            self.assertEqual(set(call), {"messages", "max_tokens", "temperature", "stream"})
            self.assertEqual([message["role"] for message in call["messages"]], ["system", "user"])
            self.assertIn("JSON", call["messages"][0]["content"])
            self.assertEqual(call["messages"][1]["content"], "только тестовые данные")
            self.assertNotIn("response_format", call)
            self.assertNotIn("reasoning_budget", call)
            self.assertNotIn("reasoning_start", call)
            self.assertNotIn("reasoning_end", call)
            self.assertNotIn("reasoning_start_in_prompt", call)
            self.assertNotIn("chat_template_kwargs", call)
        self.assertEqual(diagnostics["finish_reason"], "stop")
        self.assertEqual(diagnostics["stream_chunk_count"], 2)
        self.assertEqual(diagnostics["requested_max_tokens"], 256)
        self.assertTrue(diagnostics["streaming"])
        self.assertIn("stream_text_characters", diagnostics)

    def test_json_candidate_finder_ignores_unrelated_braces_in_prose(self):
        helper_node = next(
            node for node in ast.parse(self.runtime).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_text_llm_find_json_candidate_offset"
        )
        namespace = {"re": re}
        exec(
            compile(ast.Module(body=[helper_node], type_ignores=[]),
                    "VideoContextEngine_v3.19.py", "exec"),
            namespace,
        )
        find_candidate = namespace["_text_llm_find_json_candidate_offset"]
        self.assertEqual(find_candidate("Reasoning with {an example} but no JSON follows"), -1)
        raw = 'Reasoning {not a schema object}; final: {"executive_summary":'
        self.assertEqual(find_candidate(raw), raw.index('{"executive_summary"'))
        self.assertEqual(
            find_candidate('<think>example {"executive_summary":"not final"}'),
            -1,
        )

    def test_text_llm_generation_cap_is_consistent_at_8192(self):
        self.assertIn("TEXT_LLM_DEFAULT_MAX_TOKENS = 8192", self.runtime)
        self.assertIn("min(int(max_tokens), 8192)", self.runtime)
        self.assertIn('"text_llm_max_tokens": (1024, 8192)', self.runtime)
        self.assertIn("le=8192", self.runtime)
        self.assertIn("max_tokens=generation_limit", self.runtime)

    def test_qwen35_no_brace_generation_skips_repair_and_records_tail_diagnostics(self):
        no_brace_gate = self.runtime.index("if first_brace_offset < 0:")
        repair_import = self.runtime.index("from json_repair import repair_json")
        self.assertLess(no_brace_gate, repair_import)
        self.assertIn('failure.first_brace_offset = -1', self.runtime)
        self.assertIn('failure.raw_length = len(raw)', self.runtime)
        self.assertIn('failure.raw_tail_excerpt = _text_llm_clip(raw[-1500:], 1500)', self.runtime)
        self.assertIn('failure.generation_diagnostics["repair_skipped"] = True', self.runtime)

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
                    "generation_diagnostics": {
                        "raw_length": 4096,
                        "first_brace_offset": -1,
                        "raw_tail_excerpt": "tail without a brace",
                        "finish_reason": "length",
                        "requested_max_tokens": 4096,
                        "streaming": True,
                        "repair_skipped": True,
                    },
                }
            }
            _OBSERVER.save_timing_outputs(run_dir, {"meta": {"timings": timings}})
            artifact = run_dir / "text_llm_generation_diagnostics.txt"
            self.assertTrue(artifact.exists())
            saved = artifact.read_text(encoding="utf-8")
            self.assertIn("Primary generation", saved)
            self.assertIn("Repair-pass generation", saved)
            self.assertIn("invalid repair output", saved)
            self.assertIn("raw_length: 4096", saved)
            self.assertIn("first_brace_offset: -1", saved)
            self.assertIn("finish_reason: length", saved)
            self.assertIn("tail without a brace", saved)
            self.assertEqual(timings["text_llm"]["raw_diagnostic_file"], "text_llm_generation_diagnostics.txt")

    def test_text_llm_raw_diagnostics_are_bounded_and_exposed_in_api_timing(self):
        self.assertIn('failure.raw_excerpt = _text_llm_clip(raw, 2000)', self.runtime)
        self.assertIn('failure.raw_tail_excerpt = _text_llm_clip(raw[-1500:], 1500)', self.runtime)
        self.assertIn('"raw_length": len(raw)', self.runtime)
        self.assertIn('"first_brace_offset": first_brace_offset', self.runtime)
        self.assertIn('failure.repair_raw_excerpt = _text_llm_clip(repaired_raw, 2000)', self.runtime)
        self.assertIn('"raw_excerpt": text_llm_raw_excerpt or None', self.runtime)
        self.assertIn('"repair_raw_excerpt": text_llm_repair_raw_excerpt or None', self.runtime)
        self.assertIn('"generation_diagnostics": text_llm_generation_diagnostics', self.runtime)

    def test_text_llm_reasoning_metadata_matches_selected_backend(self):
        self.assertIn(
            '"thinking_enabled": bool(text_llm_enabled and inference_backend == VCE_INFERENCE_BACKEND_MLX)',
            self.runtime,
        )
        self.assertIn('512 if inference_backend == VCE_INFERENCE_BACKEND_MLX else 0', self.runtime)

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
