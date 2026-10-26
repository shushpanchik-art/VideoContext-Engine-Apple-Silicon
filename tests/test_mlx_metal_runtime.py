#!/usr/bin/env python3
"""Unit tests for MLX/Metal runtime helpers; no Apple hardware required."""
from __future__ import annotations

import sys
import types
import unittest
import threading
from unittest import mock

from scripts import mlx_metal_runtime as runtime


class FakeDevice:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, FakeDevice) and self.name == other.name

    def __str__(self):
        return f"Device({self.name}, 0)"


class FakeArray(list):
    def __mul__(self, value):
        return FakeArray([x * value for x in self])

    def tolist(self):
        return list(self)


class FakeMlxCore(types.ModuleType):
    def __init__(self):
        super().__init__("mlx.core")
        self.gpu = FakeDevice("gpu")
        self._default = FakeDevice("cpu")
        self.metal = types.SimpleNamespace(is_available=lambda: True)

    def is_available(self, device):
        return device == self.gpu

    def set_default_device(self, device):
        self._default = device

    def default_device(self):
        return self._default

    def array(self, values):
        return FakeArray(values)

    def eval(self, _value):
        return None

    def synchronize(self, _device=None):
        return None

    def device_info(self, _device):
        return {"device": "Fake Apple GPU"}


class RuntimeHelperTests(unittest.TestCase):
    def test_configure_selects_gpu_and_runs_probe(self):
        fake_core = FakeMlxCore()
        fake_mlx = types.ModuleType("mlx")
        fake_mlx.core = fake_core
        with (
            mock.patch.dict(sys.modules, {"mlx": fake_mlx, "mlx.core": fake_core}),
            mock.patch.object(runtime.platform, "system", return_value="Darwin"),
            mock.patch.object(runtime.platform, "machine", return_value="arm64"),
        ):
            info = runtime.configure_mlx_gpu()
        self.assertTrue(info["gpu_available"])
        self.assertEqual(info["accelerator"], "Metal")
        self.assertIn("gpu", info["default_device"].lower())
        self.assertEqual(info["gpu_smoke_test"], "passed")

    def test_configure_fails_closed_off_apple_silicon(self):
        with (
            mock.patch.object(runtime.platform, "system", return_value="Darwin"),
            mock.patch.object(runtime.platform, "machine", return_value="x86_64"),
        ):
            with self.assertRaisesRegex(RuntimeError, "native Apple Silicon"):
                runtime.configure_mlx_gpu()

    def test_whisper_loader_forces_cpu_even_if_caller_passes_other_device(self):
        class Model:
            device = "cpu"

        class Whisper:
            calls = []

            @classmethod
            def load_model(cls, name, **kwargs):
                cls.calls.append((name, kwargs))
                return Model()

        result = runtime.load_whisper_cpu(Whisper, "large", device="mps")
        self.assertIsInstance(result, Model)
        self.assertEqual(Whisper.calls, [("large", {"device": "cpu"})])

    def test_whisper_loader_rejects_non_cpu_result(self):
        class Whisper:
            @staticmethod
            def load_model(name, **kwargs):
                return types.SimpleNamespace(device="mps")

        with self.assertRaisesRegex(RuntimeError, "reports device"):
            runtime.load_whisper_cpu(Whisper, "large")

    def test_cpu_asr_overlaps_gpu_stage(self):
        barrier = threading.Barrier(2)

        def asr():
            barrier.wait(timeout=2)
            return "transcript"

        def gpu():
            barrier.wait(timeout=2)
            return "scenes"

        asr_result, gpu_result = runtime.run_whisper_cpu_parallel_with_gpu(asr, gpu)
        self.assertEqual(asr_result, "transcript")
        self.assertEqual(gpu_result, "scenes")


if __name__ == "__main__":
    unittest.main()
