"""Exercise the MLX adapter's cleanup without loading models or touching Metal."""

import ast
import contextlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class MlxWiredLimitTests(unittest.TestCase):
    def test_restores_limit_when_gpu_synchronization_fails(self):
        engine = Path(__file__).resolve().parents[1] / "VideoContextEngine_v3.19.py"
        tree = ast.parse(engine.read_text(encoding="utf-8"))
        prepare = next(node for node in tree.body if getattr(node, "name", None) == "_vce_prepare_mlx_vlm_runtime")
        namespace = {"_vce_mlx_contextlib": contextlib}
        exec(compile(ast.Module(body=[prepare], type_ignores=[]), str(engine), "exec"), namespace)

        class MetalSyncError(RuntimeError):
            pass

        for streams in (None, ["explicit-gpu-stream"]):
            with self.subTest(streams=streams):
                limits = []
                synchronized = []
                error = MetalSyncError("simulated Metal failure")

                def set_wired_limit(value):
                    limits.append(value)
                    return 7

                def synchronize(*args):
                    synchronized.append(args)
                    raise error

                mx = types.ModuleType("mlx.core")
                mx.gpu = "gpu"
                mx.metal = types.SimpleNamespace(is_available=lambda: True)
                mx.is_available = lambda device: True
                mx.set_default_device = lambda device: None
                mx.default_device = lambda: "gpu"
                mx.new_thread_local_stream = lambda device: object()
                mx.device_info = lambda device: {"max_recommended_working_set_size": 99}
                mx.set_wired_limit = set_wired_limit
                mx.synchronize = synchronize
                mlx = types.ModuleType("mlx")
                mlx.core = mx
                common = types.SimpleNamespace(wired_limit=None, tree_reduce=lambda *args: 0)
                dispatch = types.SimpleNamespace()
                autoregressive = types.SimpleNamespace(generation_stream="cpu")
                with patch.dict(sys.modules, {
                    "mlx": mlx,
                    "mlx.core": mx,
                    "mlx_vlm.generate.common": common,
                    "mlx_vlm.generate.dispatch": dispatch,
                    "mlx_vlm.generate.ar": autoregressive,
                }):
                    namespace["_vce_prepare_mlx_vlm_runtime"]()
                    self.assertIs(dispatch.wired_limit, common.wired_limit)
                    self.assertIs(autoregressive.generation_stream, common.generation_stream)
                    with self.assertRaises(MetalSyncError) as caught:
                        with common.wired_limit(object(), streams):
                            self.assertEqual(limits, [99])
                    self.assertIs(caught.exception, error)
                    self.assertEqual(limits, [99, 7])
                    self.assertEqual(synchronized, [tuple(streams or [])])
