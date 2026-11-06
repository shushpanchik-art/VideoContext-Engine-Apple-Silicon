from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "scripts" / "patch_vce_full_profile_benchmark.py"
SPEC = importlib.util.spec_from_file_location("vce_benchmark_patcher", PATCHER_PATH)
assert SPEC and SPEC.loader
PATCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHER)


STUB_RUNNER = """import json
def jsonl_append(path, payload):
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload) + "\\n")

def build_plan():
    return [
        ("baseline", "Baseline", {}),
        ("adaptive_keyframes", "Adaptive", {}),
        ("ocr_cache", "OCR cache", {}),
    ]

def build_base_fields():
    fields = {
        "analysis_mode": "full",
        "skip_audio": False,
        "skip_visual": False,
        "word_timestamps": True,
        "enable_audio_features": True,
        "enable_apple_ocr": True,
        "enable_ffmpeg_ocr": True,
        "text_llm_enabled": True,
        "generate_summary": True,
        "enable_adaptive_keyframes": False,
        "enable_mlx_whisper": False,
        "enable_fast_scene_detection": False,
    }
    missing = [
        "enable_adaptive_keyframes",
        "enable_mlx_whisper",
        "enable_fast_scene_detection",
    ]
    return fields, missing

def run_one(run_root, key="baseline", index=1):
    progress_path = "/api/v1/progress/job123"
    jsonl_append(progress_path, {"p": 1})
"""


class VCEBenchmarkRunnerPatchTests(unittest.TestCase):
    def _load(self):
        namespace = {}
        exec(compile(PATCHER.patch_source(STUB_RUNNER), "<test-runner>", "exec"), namespace)
        return namespace

    def _classify(self, namespace, *, fields=None, payload=None, log="", http_status=200, record=None):
        return namespace["_vce_classify_profile_result"](
            record if record is not None else {"http_status": http_status},
            fields or {
                "analysis_mode": "full", "skip_audio": False, "skip_visual": False,
                "text_llm_enabled": True,
            },
            payload if payload is not None else {
                "meta": {
                    "params": {"run_vlm": True, "audio_track_available": True, "audio_auto_skipped": False},
                    "timings": {
                        "vlm": {"inference_time": 12.0},
                        "whisper": {"inference_time": 5.0},
                    },
                },
                "text_llm": {"enabled": True, "status": "success", "inference_time": 3.0},
                "enhanced_analysis": {"executive_summary": "synthetic fixture"},
            },
            log,
        )

    def test_string_path_is_normalized_before_path_open(self):
        namespace = self._load()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "progress.jsonl"
            namespace["jsonl_append"](str(target), {"status": "ok"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "ok"})

    def test_api_progress_endpoint_is_redirected_to_local_trace(self):
        namespace = self._load()
        with tempfile.TemporaryDirectory() as temp:
            namespace["run_one"](temp)
            target = Path(temp) / "01_baseline" / "progress_trace.jsonl"
            self.assertTrue(target.exists())
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"p": 1})

    def test_baseline_flag_limits_plan_without_changing_default(self):
        namespace = self._load()
        self.assertEqual(len(namespace["build_plan"]()), 3)
        with patch.dict(os.environ, {"VCE_BENCH_ONLY_BASELINE": "1"}):
            plan = namespace["build_plan"]()
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0][0], "baseline")

    def test_baseline_only_mode_bypasses_legacy_experiment_field_validation(self):
        namespace = self._load()

        def legacy_planner_must_not_run(*args, **kwargs):
            raise RuntimeError(
                "Локальный API не подтверждает поля ['enable_adaptive_keyframes']"
            )

        namespace["_vce_original_build_plan"] = legacy_planner_must_not_run
        with patch.dict(os.environ, {"VCE_BENCH_ONLY_BASELINE": "1"}):
            plan = namespace["build_plan"](
                {},
                {
                    "enable_ocr_cache": {},
                    # Deliberately absent: removed by the stable MPS profile.
                },
            )
        self.assertEqual(
            plan,
            [("baseline", "Оптимальный базовый профиль", {})],
        )

    def test_fast_profile_removed_experimental_fields_are_filtered_in_baseline_mode(self):
        namespace = self._load()
        fields, missing = namespace["build_base_fields"]()
        self.assertEqual(missing, [])
        for removed in (
            "enable_adaptive_keyframes",
            "enable_mlx_whisper",
            "enable_fast_scene_detection",
        ):
            self.assertNotIn(removed, fields)

    def test_whisper_only_mode_keeps_speech_and_disables_other_models(self):
        namespace = self._load()
        with patch.dict(os.environ, {"VCE_BENCH_WHISPER_ONLY": "1"}):
            fields, missing = namespace["build_base_fields"]()
        self.assertEqual(missing, [])
        self.assertEqual(fields["analysis_mode"], "whisper_ocr")
        self.assertFalse(fields["skip_audio"])
        self.assertTrue(fields["word_timestamps"])
        self.assertTrue(fields["enable_audio_features"])
        self.assertTrue(fields["skip_visual"])
        self.assertFalse(fields["enable_apple_ocr"])
        self.assertFalse(fields["enable_ffmpeg_ocr"])
        self.assertFalse(fields["text_llm_enabled"])
        self.assertFalse(fields["generate_summary"])

    def test_vlm_only_mode_disables_audio_ocr_and_text_llm(self):
        namespace = self._load()
        with patch.dict(os.environ, {"VCE_BENCH_VLM_ONLY": "1"}):
            fields, missing = namespace["build_base_fields"]()
        self.assertEqual(missing, [])
        self.assertTrue(fields["skip_audio"])
        self.assertFalse(fields["word_timestamps"])
        self.assertFalse(fields["enable_audio_features"])
        self.assertFalse(fields["skip_visual"])
        self.assertFalse(fields["enable_apple_ocr"])
        self.assertFalse(fields["enable_ffmpeg_ocr"])
        self.assertFalse(fields["text_llm_enabled"])
        self.assertFalse(fields["generate_summary"])

    def test_conflicting_isolation_modes_are_rejected(self):
        namespace = self._load()
        with patch.dict(os.environ, {"VCE_BENCH_WHISPER_ONLY": "1", "VCE_BENCH_VLM_ONLY": "1"}):
            with self.assertRaisesRegex(RuntimeError, "Choose only one"):
                namespace["build_base_fields"]()

    def test_http_200_with_metal_and_vlm_guard_is_failed(self):
        namespace = self._load()
        result = self._classify(
            namespace,
            log="[VLM memory guard] Metal command-buffer timeout/submissions ignored; disabling VLM for this video.\\n"
                "[METAL] Command buffer execution failed: kIOGPUCommandBufferCallbackErrorSubmissionsIgnored",
        )
        self.assertEqual(result["api_status"], "http_200")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage_status"]["api"]["status"], "http_200")
        self.assertEqual(result["stage_status"]["metal"]["status"], "failed")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "failed")
        self.assertTrue(result["classification_reasons"])

    def test_text_llm_fallback_is_degraded_even_when_http_200(self):
        namespace = self._load()
        payload = {
            "meta": {
                "params": {"run_vlm": True, "audio_track_available": True},
                "timings": {
                    "vlm": {"inference_time": 12.0},
                    "whisper": {"inference_time": 5.0},
                },
            },
            "text_llm": {"enabled": True, "status": "failed", "inference_time": 0.3, "error": "synthetic failure"},
            "enhanced_analysis": None,
        }
        result = self._classify(namespace, payload=payload, log="[Text LLM] Post-processing failed; fallback to original report")
        self.assertEqual(result["api_status"], "http_200")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "completed")
        self.assertEqual(result["stage_status"]["text_llm"]["status"], "failed")
        self.assertEqual(result["status"], "degraded")

    def test_missing_vlm_completion_telemetry_cannot_be_success(self):
        namespace = self._load()
        payload = {
            "meta": {"params": {"run_vlm": True}, "timings": {"vlm": {"inference_time": 0.0}}},
            "text_llm": {"enabled": False, "status": "disabled", "inference_time": 0.0},
        }
        fields = {"analysis_mode": "full", "skip_audio": True, "skip_visual": False, "text_llm_enabled": False}
        result = self._classify(namespace, fields=fields, payload=payload)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "degraded")

    def test_complete_stages_with_unreviewed_runtime_error_are_degraded(self):
        namespace = self._load()
        result = self._classify(
            namespace,
            record={"http_status": 200, "error_count": 1},
        )
        self.assertEqual(result["api_status"], "http_200")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "completed")
        self.assertEqual(result["stage_status"]["whisper"]["status"], "completed")
        self.assertEqual(result["stage_status"]["text_llm"]["status"], "completed")
        self.assertEqual(result["stage_status"]["runtime_errors"]["count"], 1)
        self.assertEqual(result["stage_status"]["runtime_errors"]["status"], "degraded")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("inspect local errors.csv" in reason for reason in result["classification_reasons"]))

    def test_ffmpeg_ocr_availability_banner_is_informational_not_runtime_error(self):
        namespace = self._load()
        info = (
            "FFmpeg OCR: FFmpeg + Tesseract CLI доступны; "
            "используются только выбранные установленные языки: rus+eng"
        )
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "errors.json").write_text(
                json.dumps({
                    "error_count": 1,
                    "errors": [info],
                    "transport_error": None,
                    "http_status": 200,
                    "text_llm_error": None,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (run_dir / "errors.log").write_text(info + "\n", encoding="utf-8")
            record = {
                "http_status": 200,
                "error_count": 1,
                "error_excerpt": [info],
                "result_directory": str(run_dir),
            }

            namespace["_vce_filter_informational_ffmpeg_ocr_diagnostics"](record, run_dir)

            self.assertEqual(record["error_count"], 0)
            self.assertEqual(record["error_excerpt"], [])
            self.assertEqual(record["informational_diagnostics"], [info])
            error_data = json.loads((run_dir / "errors.json").read_text(encoding="utf-8"))
            self.assertEqual(error_data["error_count"], 0)
            self.assertEqual(error_data["errors"], [])
            self.assertEqual(error_data["informational_diagnostics"], [info])
            self.assertIn(info, (run_dir / "diagnostics.log").read_text(encoding="utf-8"))
            self.assertIn("No API/server error markers", (run_dir / "errors.log").read_text(encoding="utf-8"))

            result = self._classify(
                namespace,
                record=record,
                fields={
                    "analysis_mode": "full",
                    "skip_audio": True,
                    "skip_visual": False,
                    "text_llm_enabled": False,
                },
                payload={
                    "meta": {
                        "params": {"run_vlm": True},
                        "timings": {"vlm": {"inference_time": 14.941}},
                    },
                    "text_llm": {"enabled": False, "status": "disabled", "inference_time": 0.0},
                },
            )
            self.assertEqual(result["stage_status"]["runtime_errors"]["count"], 0)
            self.assertEqual(result["status"], "completed")

    def test_informational_ffmpeg_banner_does_not_hide_a_real_error(self):
        namespace = self._load()
        info = (
            "FFmpeg OCR: FFmpeg + Tesseract CLI доступны; "
            "используются только выбранные установленные языки: rus+eng"
        )
        real_error = "FFmpeg OCR worker failed while processing a frame"
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            errors = [info, real_error]
            (run_dir / "errors.json").write_text(
                json.dumps({"error_count": 2, "errors": errors}, ensure_ascii=False),
                encoding="utf-8",
            )
            record = {"error_count": 2, "error_excerpt": errors}
            namespace["_vce_filter_informational_ffmpeg_ocr_diagnostics"](record, run_dir)
            self.assertEqual(record["error_count"], 1)
            self.assertEqual(record["error_excerpt"], [real_error])
            error_data = json.loads((run_dir / "errors.json").read_text(encoding="utf-8"))
            self.assertEqual(error_data["errors"], [real_error])
            self.assertEqual(error_data["informational_diagnostics"], [info])

    def test_informational_filter_fails_closed_when_error_counters_disagree(self):
        namespace = self._load()
        info = (
            "FFmpeg OCR: FFmpeg + Tesseract CLI доступны; "
            "используются только выбранные установленные языки: rus+eng"
        )
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "errors.json").write_text(
                json.dumps({"error_count": 1, "errors": [info]}, ensure_ascii=False),
                encoding="utf-8",
            )
            record = {"error_count": 2, "error_excerpt": [info, "unreviewed"]}
            namespace["_vce_filter_informational_ffmpeg_ocr_diagnostics"](record, run_dir)
            self.assertEqual(record["error_count"], 2)
            self.assertEqual(record["error_excerpt"], [info, "unreviewed"])

    def test_complete_vlm_whisper_and_text_llm_profile(self):
        namespace = self._load()
        result = self._classify(namespace)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["api_status"], "http_200")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "completed")
        self.assertEqual(result["stage_status"]["whisper"]["status"], "completed")
        self.assertEqual(result["stage_status"]["text_llm"]["status"], "completed")

    def test_whisper_only_profile_does_not_require_vlm_or_text_llm(self):
        namespace = self._load()
        fields = {
            "analysis_mode": "whisper_ocr", "skip_audio": False, "skip_visual": True,
            "text_llm_enabled": False,
        }
        payload = {
            "meta": {"params": {"audio_track_available": True}, "timings": {"whisper": {"inference_time": 8.5}}},
            "text_llm": {"enabled": False, "status": "disabled", "inference_time": 0.0},
        }
        result = self._classify(namespace, fields=fields, payload=payload)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "skipped")
        self.assertEqual(result["stage_status"]["text_llm"]["status"], "skipped")
        self.assertEqual(result["stage_status"]["whisper"]["status"], "completed")

    def test_vlm_only_profile_does_not_require_whisper_or_text_llm(self):
        namespace = self._load()
        fields = {
            "analysis_mode": "full", "skip_audio": True, "skip_visual": False,
            "text_llm_enabled": False,
        }
        payload = {
            "meta": {
                "params": {"run_vlm": True, "audio_track_available": True},
                "timings": {"vlm": {"inference_time": 10.0}},
            },
            "text_llm": {"enabled": False, "status": "disabled", "inference_time": 0.0},
        }
        result = self._classify(namespace, fields=fields, payload=payload)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "completed")
        self.assertEqual(result["stage_status"]["whisper"]["status"], "skipped")
        self.assertEqual(result["stage_status"]["text_llm"]["status"], "skipped")

    def test_whisper_only_without_audio_track_is_degraded_not_completed(self):
        namespace = self._load()
        fields = {
            "analysis_mode": "whisper_ocr", "skip_audio": False, "skip_visual": True,
            "text_llm_enabled": False,
        }
        payload = {
            "meta": {
                "params": {"audio_track_available": False, "audio_auto_skipped": True},
                "timings": {"whisper": {"inference_time": 0.0}},
            },
            "text_llm": {"enabled": False, "status": "disabled", "inference_time": 0.0},
        }
        result = self._classify(namespace, fields=fields, payload=payload)
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["stage_status"]["whisper"]["requested"])
        self.assertEqual(result["stage_status"]["whisper"]["status"], "degraded")

    def test_vlm_requested_but_explicitly_skipped_by_runtime_is_failed(self):
        namespace = self._load()
        payload = {
            "meta": {"params": {"run_vlm": False}, "timings": {"vlm": {"inference_time": 0.0}}},
            "text_llm": {"enabled": False, "status": "disabled", "inference_time": 0.0},
        }
        fields = {"analysis_mode": "full", "skip_audio": True, "skip_visual": False, "text_llm_enabled": False}
        result = self._classify(namespace, fields=fields, payload=payload)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage_status"]["vlm"]["status"], "failed")

    def test_non_200_is_failed_independently_of_stage_payload(self):
        namespace = self._load()
        result = self._classify(namespace, http_status=503)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["api_status"], "http_error")

    def test_http_200_without_meta_payload_is_failed(self):
        namespace = self._load()
        result = self._classify(namespace, payload={})
        self.assertEqual(result["api_status"], "http_200")
        self.assertEqual(result["status"], "failed")
        self.assertIn("HTTP response lacks the required non-empty meta payload", result["classification_reasons"])

    def test_vlm_smoke_launcher_passes_explicit_output_root(self):
        launcher = ROOT / "scripts" / "run_vce_vlm_smoke_diagnostic.command"
        source = launcher.read_text(encoding="utf-8")
        self.assertIn('--out-root "$RUN_OUTPUT"', source)
        self.assertNotIn('export VCE_BENCH_RESULT_ROOT=', source)

    def test_patch_is_idempotent(self):
        once = PATCHER.patch_source(STUB_RUNNER)
        self.assertEqual(PATCHER.patch_source(once), once)


if __name__ == "__main__":
    unittest.main(verbosity=2)
