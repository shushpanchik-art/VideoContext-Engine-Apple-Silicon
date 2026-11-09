"""Check MLX device reporting without importing the runtime or touching the GPU."""

import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class MlxDeviceDiagnosticTests(unittest.TestCase):
    def test_warns_only_when_observed_device_type_is_not_gpu(self):
        path = Path(__file__).resolve().parents[1] / "VideoContextEngine_v3.19.py"
        module = ast.parse(path.read_text(encoding="utf-8"))
        prepare = next(node for node in module.body if getattr(node, "name", None) == "_vce_configure_mlx_metal")
        import json
        namespace = {
            "Dict": dict,
            "Any": object,
            "json": json,
            "platform": types.SimpleNamespace(system=lambda: "Darwin", machine=lambda: "arm64"),
            "VCE_MLX_RUNTIME_DIAGNOSTICS": {},
            "VCE_BACKEND_DIAGNOSTICS": {},
            "_vce_request_diagnostics": lambda diagnostics: diagnostics,
        }
        exec(compile(ast.Module(body=[prepare], type_ignores=[]), str(path), "exec"), namespace)
        mx = types.ModuleType("mlx.core")
        mx.gpu = "DeviceType.gpu"
        mx.metal = types.SimpleNamespace(is_available=lambda: True)
        mx.is_available = lambda device: True
        mx.set_default_device = lambda device: None
        mx.default_stream = lambda device: "gpu stream"
        mx.arange = lambda *args, **kwargs: None
        mx.multiply = lambda *args, **kwargs: types.SimpleNamespace(tolist=lambda: [2, 4, 6])
        mx.eval = lambda value: None
        mx.synchronize = lambda stream: None
        mx.device_info = lambda device: {}
        mlx = types.ModuleType("mlx")
        mlx.core = mx

        class Device:
            def __init__(self, kind):
                self.kind = kind
                self.type = f"DeviceType.{kind}"

            def __str__(self):
                return f"Device({self.kind}, 0)"

        with patch.dict(sys.modules, {"mlx": mlx, "mlx.core": mx}):
            for kind in ("gpu", "cpu"):
                with self.subTest(kind=kind), redirect_stdout(io.StringIO()) as stdout:
                    mx.default_device = lambda: Device(kind)
                    info = namespace["_vce_configure_mlx_metal"]("text_llm")
                    self.assertEqual(info["default_device_observed"], f"Device({kind}, 0)")
                    self.assertEqual(info["device_verified"], True)
                    self.assertEqual("[VCE warning]" in stdout.getvalue(), kind == "cpu")
