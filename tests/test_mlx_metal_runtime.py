#!/usr/bin/env python3
"""Unit tests for MLX/Metal runtime helpers; no Apple hardware required."""
from __future__ import annotations

import sys
import types
import unittest
import threading
from contextlib import contextmanager
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mlx_metal_runtime as runtime  # noqa: E402


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
        self.cpu = FakeDevice("cpu")
        self._default = FakeDevice("cpu")
        self._active_stream = None
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

    def new_thread_local_stream(self, device):
        return device

    @contextmanager
    def stream(self, stream):
        old = self._active_stream
        self._active_stream = stream
        try:
            yield
        finally:
            self._active_stream = old


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

    def test_mlx_whisper_uses_cpu_stream_without_changing_gpu_default(self):
        fake_core = FakeMlxCore()
        fake_core._default = fake_core.gpu
        fake_mlx = types.ModuleType("mlx")
        fake_mlx.core = fake_core
        fake_whisper = types.ModuleType("mlx_whisper")
        seen = []

        def transcribe(audio, path_or_hf_repo, **kwargs):
            seen.append((audio, path_or_hf_repo, fake_core._active_stream.name, kwargs))
            return {"text": "hello", "segments": []}

        fake_whisper.transcribe = transcribe
        with mock.patch.dict(sys.modules, {
            "mlx": fake_mlx,
            "mlx.core": fake_core,
            "mlx_whisper": fake_whisper,
        }):
            result = runtime.transcribe_mlx_whisper_cpu(
                "sample.wav", "mlx-community/whisper-large-v3-mlx", temperature=0.0
            )

        self.assertEqual(result["text"], "hello")
        self.assertEqual(seen, [
            ("sample.wav", "mlx-community/whisper-large-v3-mlx", "cpu", {"temperature": 0.0})
        ])
        self.assertEqual(fake_core.default_device(), FakeDevice("gpu"))


if __name__ == "__main__":
    unittest.main()
