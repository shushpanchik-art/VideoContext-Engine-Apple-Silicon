import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "VideoContextEngine_v3.19.py"
UI = ROOT / "videocontext_web_ui.html"


class TestApiSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ENGINE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.ui = UI.read_text(encoding="utf-8")

    def test_docs_are_opt_in_and_openapi_schema_stays_available(self):
        self.assertIn('docs_url="/docs" if os.getenv("VCE_DEV_DOCS") else None', self.source)
        self.assertIn('redoc_url="/redoc" if os.getenv("VCE_DEV_DOCS") else None', self.source)
        self.assertIn("app.openapi = custom_openapi", self.source)
        self.assertNotIn('openapi_url=None', self.source)

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


if __name__ == "__main__":
    unittest.main()
