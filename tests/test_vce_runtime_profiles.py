import ast
import unittest
from pathlib import Path


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

    def test_ui_has_one_authoritative_runtime_selector(self):
        self.assertIn('id="cfg-runtime-profile"', self.ui)
        self.assertIn('value="baseline_mps_sequential"', self.ui)
        self.assertIn('value="mlx_parallel"', self.ui)
        self.assertIn("runtime_profile: document.getElementById('cfg-runtime-profile').value", self.ui)
        self.assertIn('id="cfg-mlx-whisper" type="checkbox" hidden disabled', self.ui)
        self.assertNotIn('id="cfg-mlx-whisper"', self.ui.replace(
            'id="cfg-mlx-whisper" type="checkbox" hidden disabled', ''
        ))


if __name__ == "__main__":
    unittest.main()
