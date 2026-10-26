#!/usr/bin/env python3
"""Regression tests for VCE Runtime Watchdog parsing and evidence classification."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vce_runtime_watchdog", ROOT / "scripts" / "vce_runtime_watchdog.py")
assert SPEC and SPEC.loader
watchdog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = watchdog
SPEC.loader.exec_module(watchdog)


class ProcessParserTests(unittest.TestCase):
    def test_cpu_decimal_comma_and_dot_are_both_accepted(self):
        output = (
            " 2825  1  528,2 1318816 01:00:55 /usr/local/bin/python server.py\n"
            " 2826 2825   41.7 7299696 01:03:10 /usr/local/bin/python worker.py\n"
        )
        rows = watchdog.parse_process_table_output(output)
        self.assertEqual([r["pid"] for r in rows], [2825, 2826])
        self.assertAlmostEqual(rows[0]["cpu_percent"], 528.2)
        self.assertEqual(rows[0]["rss_kb"], 1318816)
        self.assertAlmostEqual(rows[0]["rss_mib"], 1287.9)
        self.assertAlmostEqual(rows[1]["cpu_percent"], 41.7)
        self.assertAlmostEqual(rows[1]["rss_mib"], 7128.6)

    def test_process_rss_summary_uses_max_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "process_samples.jsonl"
            records = [
                {"elapsed_seconds": 1, "processes": [{"pid": 10, "rss_mib": 100.0, "cpu_percent": 1.0, "command": "python server.py"}]},
                {"elapsed_seconds": 2, "processes": [{"pid": 10, "rss_mib": 200.0, "cpu_percent": 2.0, "command": "python server.py"}]},
            ]
            path.write_text("".join(json.dumps(x) + "\n" for x in records), encoding="utf-8")
            summary = watchdog.summarize_process_samples(path)
            self.assertEqual(summary["sample_records"], 2)
            self.assertEqual(summary["max_observed_rss_pid"], 10)
            self.assertEqual(summary["max_observed_rss_mib"], 200.0)


class RuntimeEvidenceTests(unittest.TestCase):
    def test_gpu_stream_setup_is_not_mistaken_for_vlm_gpu_device(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observed = root / "observed"
            observed.mkdir()
            (observed / "stage_timings.json").write_text(json.dumps({
                "vlm": {"runtime": {"backend": "MLX", "accelerator": "Metal", "device": "Device(cpu, 0)", "metal_smoke_test": "passed"}},
                "text_llm": {"runtime": {"backend": "MLX", "accelerator": "Metal", "device": "Device(gpu, 0)"}},
            }), encoding="utf-8")
            console = root / "console.log"
            console.write_text("[VCE MLX] Applied GPU-aware wired_limit and rebound mlx-vlm generation stream on Device(gpu, 0).\n", encoding="utf-8")
            mappings = root / "mappings.jsonl"
            mappings.write_text("", encoding="utf-8")
            power = root / "power.jsonl"
            power.write_text("", encoding="utf-8")
            result = watchdog.runtime_verdict(observed, console, mappings, power)
            self.assertEqual(result["vlm_device"], "Device(cpu, 0)")
            self.assertEqual(result["text_llm_device"], "Device(gpu, 0)")
            self.assertEqual(result["overall_verdict"], "VLM_DEVICE_CPU_CONFLICTS_WITH_GPU_STREAM_SETUP")
            self.assertEqual(result["successful_powermetrics_samples"], 0)

    def test_trace_snapshot_is_loaded_from_server_events(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observed = root / "observed"
            observed.mkdir()
            (observed / "stage_timings.json").write_text(json.dumps({
                "vlm": {"runtime": {"backend": "MLX", "accelerator": "Metal", "device": "Device(cpu, 0)", "metal_smoke_test": "passed"}},
            }), encoding="utf-8")
            snap = {
                "phase": "before", "function": "app.MLXEngine.describe_scene",
                "thread_name": "vce-mlx-metal_0", "thread_ident": 123,
                "mlx_default_device": "Device(gpu, 0)",
                "mlx_vlm.generate.common.generation_stream": {"type": "ThreadLocalStream", "device": "Device(gpu, 0)", "repr": "ThreadLocalStream(gpu, 3)"},
                "mlx_vlm.generate.dispatch.generation_stream": {"type": "ThreadLocalStream", "device": "Device(gpu, 0)", "repr": "ThreadLocalStream(gpu, 3)"},
            }
            (observed / "server_events.jsonl").write_text(json.dumps({"line": "[VCE MLX TRACE] " + json.dumps(snap)}) + "\n", encoding="utf-8")
            console = root / "console.log"
            console.write_text("", encoding="utf-8")
            mappings = root / "mappings.jsonl"
            mappings.write_text("", encoding="utf-8")
            power = root / "power.jsonl"
            power.write_text("", encoding="utf-8")
            result = watchdog.runtime_verdict(observed, console, mappings, power)
            self.assertEqual(result["runtime_trace_snapshot_count"], 1)
            self.assertEqual(result["runtime_trace_snapshots"][0]["thread_name"], "vce-mlx-metal_0")
            self.assertEqual(result["runtime_trace_snapshots"][0]["mlx_default_device"], "Device(gpu, 0)")


if __name__ == "__main__":
    unittest.main()
