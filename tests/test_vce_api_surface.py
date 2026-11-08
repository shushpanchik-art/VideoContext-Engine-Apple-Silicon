import ast
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "VideoContextEngine_v3.19.py"
UI = ROOT / "videocontext_web_ui.html"
WHISPER_PATCH = ROOT / "patches" / "patch_vce_whisper_ocr_fullres_fixed.py"
_PATCH_SPEC = importlib.util.spec_from_file_location("vce_whisper_patch_under_test", WHISPER_PATCH)
_WHISPER_PATCH_MODULE = importlib.util.module_from_spec(_PATCH_SPEC)
_PATCH_SPEC.loader.exec_module(_WHISPER_PATCH_MODULE)


class TestApiSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ENGINE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.ui = UI.read_text(encoding="utf-8")

    def test_docs_are_opt_in_and_openapi_schema_stays_available(self):
        self.assertIn('docs_url="/docs" if _vce_dev_docs_enabled() else None', self.source)
        self.assertIn('redoc_url="/redoc" if _vce_dev_docs_enabled() else None', self.source)
        self.assertIn("app.openapi = custom_openapi", self.source)
        self.assertNotIn('openapi_url=None', self.source)

    def test_dev_docs_environment_uses_explicit_truthy_values(self):
        function = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_vce_dev_docs_enabled"
        )
        namespace = {"os": os}
        module = ast.Module(body=[function], type_ignores=[])
        exec(compile(module, str(ENGINE), "exec"), namespace)
        is_enabled = namespace["_vce_dev_docs_enabled"]
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_enabled())
        for value in ("", "0", "false", "no", "off", "disabled"):
            with patch.dict(os.environ, {"VCE_DEV_DOCS": value}, clear=True):
                self.assertFalse(is_enabled(), value)
        for value in ("1", "true", "YES", "On"):
            with patch.dict(os.environ, {"VCE_DEV_DOCS": value}, clear=True):
                self.assertTrue(is_enabled(), value)

    def test_ffmpeg_conversion_timeout_is_a_bounded_saved_setting(self):
        helper = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_vce_media_conversion_timeout_seconds"
        )
        self.assertIn('"media_conversion_timeout": 3600', self.source)
        self.assertIn('"media_conversion_timeout": (60, 7200)', self.source)
        self.assertIn('cfg-media-conversion-timeout', self.ui)
        self.assertIn('value="3600"', self.ui)
        self.assertIn("timeout=conversion_timeout", self.source)
        namespace = {"setting": lambda _name, fallback: fallback}
        module = ast.Module(body=[helper], type_ignores=[])
        exec(compile(module, str(ENGINE), "exec"), namespace)
        for configured, expected in ((1, 60), (1800, 1800), (9000, 7200), ("invalid", 3600)):
            namespace["setting"] = lambda _name, _fallback, value=configured: value
            self.assertEqual(namespace["_vce_media_conversion_timeout_seconds"](), expected)

    def test_whisper_patcher_checks_form_and_runtime_use_with_ast(self):
        valid = '''
from fastapi import Form
async def analyze_video(whisper_acceleration: bool = Form(True)):
    whisper_device = resolve_whisper_device(whisper_acceleration)
    model = load_whisper_model("large", acceleration_enabled=whisper_acceleration)
'''
        marker_only = '''
async def analyze_video(whisper_acceleration: bool):
    return None
'''
        self.assertTrue(_WHISPER_PATCH_MODULE.analyze_form_parameter_exists(valid, "whisper_acceleration"))
        self.assertTrue(_WHISPER_PATCH_MODULE.analyze_uses_whisper_acceleration(valid))
        self.assertFalse(_WHISPER_PATCH_MODULE.analyze_form_parameter_exists(marker_only, "whisper_acceleration"))
        self.assertFalse(_WHISPER_PATCH_MODULE.analyze_uses_whisper_acceleration(marker_only))

    def test_shutdown_is_not_exposed_in_openapi(self):
        shutdown = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_vce_shutdown_server"
        )
        decorator = shutdown.decorator_list[0]
        self.assertTrue(any(
            isinstance(keyword.value, ast.Constant)
            and keyword.arg == "include_in_schema"
            and keyword.value.value is False
            for keyword in decorator.keywords
        ))

    def test_root_redirects_to_ui_and_legacy_output_route_is_removed(self):
        root = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "vce_root"
        )
        self.assertIn("RedirectResponse", ast.unparse(root))
        self.assertIn("RedirectResponse('/ui')", ast.unparse(root))
        self.assertNotIn('@app.get("/api/v1/output/{filename}"', self.source)
        self.assertNotIn("/api/v1/output/", self.ui)

    def test_ui_uses_only_registered_output_download_ids(self):
        self.assertNotIn('href="/docs"', self.ui)
        self.assertIn("data.txt_filename && data.txt_download_id", self.ui)
        self.assertIn("data.json_filename && data.json_download_id", self.ui)
        self.assertIn("/api/v1/output-file/", self.ui)

    def test_fast_scene_mode_is_documented_as_a_supported_tradeoff(self):
        self.assertNotIn("Экспериментальная ускоренная детекция сцен", self.source)
        self.assertNotIn("Экспериментальные оптимизации", self.ui)
        self.assertIn("возможно пропустить короткие переходы", self.ui)


if __name__ == "__main__":
    unittest.main()
