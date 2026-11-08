import ast
import contextvars
import unittest
from pathlib import Path


class _TestHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "VideoContextEngine_v3.19.py"
UI = ROOT / "videocontext_web_ui.html"


class TestRuntimeProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ENGINE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.ui = UI.read_text(encoding="utf-8")

    def function_source(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.get_source_segment(self.source, node)
        self.fail(f"Function not found: {name}")

    def test_baseline_is_the_default_and_profiles_are_allowlisted(self):
        self.assertIn('VCE_RUNTIME_PROFILE_BASELINE = "baseline_mps_sequential"', self.source)
        self.assertIn('VCE_RUNTIME_PROFILE_MLX_PARALLEL = "mlx_parallel"', self.source)
        self.assertIn('"runtime_profile": VCE_RUNTIME_PROFILE_BASELINE', self.source)
        self.assertIn('if key == "runtime_profile":', self.source)
        self.assertIn("if value not in VCE_RUNTIME_PROFILES:", self.source)
        normalize = self.function_source("_vce_normalize_runtime_profile")
        self.assertNotIn("enable_mlx_whisper", normalize)
        analyze = self.function_source("analyze_video")
        self.assertNotIn("if enable_mlx_whisper:", analyze)

    def test_explicit_legacy_whisper_flag_must_match_runtime_profile(self):
        helper = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_vce_validate_legacy_mlx_whisper_flag"
        )
        namespace = {
            "Optional": __import__("typing").Optional,
            "HTTPException": _TestHTTPException,
            "VCE_RUNTIME_PROFILE_MLX_PARALLEL": "mlx_parallel",
        }
        module = ast.Module(body=[helper], type_ignores=[])
        exec(compile(module, str(ENGINE), "exec"), namespace)
        validate = namespace["_vce_validate_legacy_mlx_whisper_flag"]
        validate(None, "baseline_mps_sequential")
        validate(False, "baseline_mps_sequential")
        validate(True, "mlx_parallel")
        for legacy_flag, profile in ((True, "baseline_mps_sequential"), (False, "mlx_parallel")):
            with self.assertRaises(_TestHTTPException) as raised:
                validate(legacy_flag, profile)
            self.assertEqual(raised.exception.status_code, 422)
        self.assertIn('enable_mlx_whisper: Optional[bool] = Form(None', self.source)

    def test_whisper_loader_selects_pytorch_mps_or_mlx(self):
        resolver = self.function_source("resolve_whisper_device")
        loader = self.function_source("load_whisper_model")
        self.assertIn("torch.backends.mps.is_available()", resolver)
        self.assertIn('return "mps"', resolver)
        self.assertIn('return "cpu"', resolver)
        self.assertIn("_VCEWhisperMLXCPU(model_name)", loader)
        self.assertIn("whisper.load_model(model_name, device=device)", loader)

    def test_sequential_profile_awaits_asr_before_vlm(self):
        self.assertIn(
            "if runtime_profile == VCE_RUNTIME_PROFILE_MLX_PARALLEL:\n"
            "                        _vce_whisper_task = asyncio.create_task(_vce_run_whisper_cpu_stage())\n"
            "                    else:\n"
            "                        # Reproducible baseline: complete PyTorch Whisper/MPS before VLM touches Metal.\n"
            "                        whisper_segments = await _vce_run_whisper_cpu_stage()",
            self.source,
        )
        self.assertIn("Disabled for baseline_mps_sequential; Whisper completed before VLM.", self.source)

    def test_measurement_output_identifies_the_selected_profile_and_backend(self):
        self.assertIn('"runtime_profile": runtime_profile', self.source)
        self.assertIn('"backend": whisper_backend', self.source)
        self.assertIn('"whisper_backend": whisper_backend', self.source)
        self.assertIn('"whisper_vlm_overlap_seconds":', self.source)

    def test_baseline_releases_pytorch_mps_cache_and_reports_zero_overlap(self):
        stage = self.function_source("_vce_run_whisper_cpu_stage") if False else self.source
        self.assertIn("await asyncio.to_thread(torch.mps.synchronize)", stage)
        self.assertIn("release_accelerator_memory,", stage)
        self.assertIn("if runtime_profile == VCE_RUNTIME_PROFILE_MLX_PARALLEL and all(v is not None", stage)
        self.assertNotIn("def _vce_load_whisper_model(", stage)

    def test_example_resolution_matches_documented_baseline(self):
        import json
        config = json.loads((ROOT / "config/videocontext_settings.apple_silicon.example.json").read_text())
        self.assertEqual(config["vlm_resolution"], 1280)

    def test_optional_form_defaults_are_loaded_from_settings(self):
        for key in (
            "analysis_mode", "enable_ffmpeg_ocr", "ffmpeg_ocr_interval",
            "enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
            "runtime_profile", "enable_fast_scene_detection",
        ):
            self.assertIn(f'setting("{key}",', self.source)
        self.assertIn('setting("ram_mode", "ram-")', self.source)
        self.assertIn('setting("llama_cpp_vlm_context", 8192)', self.source)
        self.assertIn('enable_mlx_whisper: Optional[bool] = Form(None', self.source)

    def test_obsolete_default_constants_are_removed(self):
        for name in ("DEFAULT_MAX_DURATION", "DEFAULT_RESOLUTION", "DEFAULT_KEYFRAMES_PER_SCENE"):
            self.assertNotIn(name, self.source)

    def test_ui_has_one_authoritative_runtime_selector(self):
        self.assertIn('id="cfg-runtime-profile"', self.ui)
        self.assertIn('value="baseline_mps_sequential"', self.ui)
        self.assertIn('value="mlx_parallel"', self.ui)
        self.assertIn("runtime_profile: document.getElementById('cfg-runtime-profile').value", self.ui)
        self.assertNotIn('id="cfg-mlx-whisper"', self.ui)
        self.assertIn('id="cfg-ram-mode"', self.ui)

    def test_domain_profile_form_overrides_persisted_setting_per_request(self):
        setting_node = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "setting"
        )
        namespace = {
            "USER_SETTINGS": {"domain_profile": "nsrspec", "other": "stored"},
            "VCE_REQUEST_DOMAIN_PROFILE": contextvars.ContextVar("test_domain_profile", default=None),
        }
        module = ast.Module(body=[setting_node], type_ignores=[])
        exec(compile(module, str(ENGINE), "exec"), namespace)
        get_setting = namespace["setting"]
        self.assertEqual(get_setting("domain_profile", "generic"), "nsrspec")
        token = namespace["VCE_REQUEST_DOMAIN_PROFILE"].set("generic")
        try:
            self.assertEqual(get_setting("domain_profile", "nsrspec"), "generic")
            self.assertEqual(get_setting("other", "fallback"), "stored")
        finally:
            namespace["VCE_REQUEST_DOMAIN_PROFILE"].reset(token)
        self.assertEqual(get_setting("domain_profile", "generic"), "nsrspec")

        analyze = self.function_source("analyze_video")
        self.assertIn('domain_profile: str = Form(str(setting("domain_profile", "generic"))', analyze)
        self.assertIn('VCE_REQUEST_DOMAIN_PROFILE.set(domain_profile)', analyze)
        self.assertIn('VCE_REQUEST_DOMAIN_PROFILE.reset(_vce_domain_profile_token)', analyze)
        self.assertIn('if domain_profile not in {"generic", "nsrspec"}', analyze)
        self.assertNotIn('enable_mlx_whisper = runtime_profile ==', analyze)


if __name__ == "__main__":
    unittest.main()
