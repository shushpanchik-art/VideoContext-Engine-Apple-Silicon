from __future__ import annotations

import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_GZIP = ROOT / "scripts" / "vce_full_profile_benchmark.py.gz"
with tempfile.TemporaryDirectory() as _module_temp:
    SCRIPT = Path(_module_temp) / "vce_full_profile_benchmark.py"
    SCRIPT.write_bytes(gzip.decompress(SCRIPT_GZIP.read_bytes()))
    SPEC = importlib.util.spec_from_file_location("vce_full_profile_benchmark", SCRIPT)
    assert SPEC and SPEC.loader
    BENCH = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(BENCH)


class VCEFullProfileBenchmarkTests(unittest.TestCase):
    def test_quality_profile_is_hardware_aware_and_keeps_ocr(self):
        keys = {
            "analysis_mode", "skip_audio", "skip_visual", "vlm_model", "whisper_model",
            "vlm_resolution", "scene_threshold", "min_scene_duration", "max_scene_duration",
            "keyframes_per_scene", "vlm_max_tokens_scene", "vlm_max_tokens_summary",
            "enable_apple_ocr", "enable_ffmpeg_ocr", "ffmpeg_ocr_interval", "ffmpeg_ocr_workers",
            "whisper_acceleration", "enable_audio_features", "generate_summary", "generate_txt",
            "generate_json", "word_timestamps", "text_llm_enabled", "enable_stage_timing",
            "enable_adaptive_keyframes", "enable_ocr_cache", "enable_mlx_whisper",
            "enable_fast_scene_detection", "response_format", "text_llm_max_tokens", "job_id",
        }
        properties = {key: {} for key in keys}
        active = {"ffmpeg_ocr_available": True}
        fields, missing = BENCH.build_base_fields(properties, active, 32 * 1024**3)
        self.assertEqual(missing, [])
        self.assertEqual(fields["vlm_resolution"], 1280)
        self.assertEqual(fields["keyframes_per_scene"], 1)
        self.assertTrue(fields["enable_apple_ocr"])
        self.assertTrue(fields["enable_ffmpeg_ocr"])
        self.assertEqual(fields["ffmpeg_ocr_workers"], 3)
        self.assertTrue(fields["enable_stage_timing"])
        self.assertFalse(fields["generate_txt"])
        self.assertFalse(fields["generate_json"])

    def test_smaller_memory_uses_conservative_vlm_resolution(self):
        properties = {key: {} for key in ("vlm_resolution", "enable_ffmpeg_ocr")}
        fields, _ = BENCH.build_base_fields(properties, {"ffmpeg_ocr_available": False}, 16 * 1024**3)
        self.assertEqual(fields["vlm_resolution"], 768)
        self.assertFalse(fields["enable_ffmpeg_ocr"])

    def test_experiments_are_one_factor_at_a_time_then_combined(self):
        properties = {key: {} for key in (
            "enable_adaptive_keyframes", "keyframes_per_scene", "enable_ocr_cache",
            "enable_mlx_whisper", "enable_fast_scene_detection",
        )}
        plan = BENCH.build_plan({}, properties)
        self.assertEqual([item[0] for item in plan], [
            "baseline", "adaptive_keyframes", "ocr_cache", "mlx_whisper_flag",
            "fast_scene_detection", "all_experiments",
        ])
        self.assertEqual(plan[1][2], {"enable_adaptive_keyframes": True, "keyframes_per_scene": 2})
        self.assertEqual(plan[2][2], {"enable_ocr_cache": True})
        self.assertEqual(plan[3][2], {"enable_mlx_whisper": True})
        self.assertEqual(plan[4][2], {"enable_fast_scene_detection": True})

    def test_multipart_content_length_matches_file_and_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "video.mp4"
            video.write_bytes(b"sample-video-bytes")
            prefix, closing, length = BENCH.build_multipart({"skip_audio": False}, video, "boundary", "job")
            self.assertIn(b'name="skip_audio"', prefix)
            self.assertIn(b'name="video_file"', prefix)
            self.assertEqual(length, len(prefix) + video.stat().st_size + len(closing))
            self.assertTrue(closing.endswith(b"--boundary--\r\n"))

    def test_stage_metrics_expose_cpu_gpu_overlap(self):
        payload = {
            "meta": {
                "process_time": 15,
                "scene_count": 4,
                "timings": {
                    "whisper": {"load_time": 1, "inference_time": 4},
                    "vlm": {"load_time": 2, "inference_time": 8},
                    "parallelism": {"whisper_vlm_overlap_seconds": 3.5},
                    "stage_diagnostics": {"scene_detection_seconds": 0.5},
                },
            },
            "text_llm": {"inference_time": 2.5},
        }
        stages, _ = BENCH.stage_metrics(payload)
        self.assertEqual(stages["scene_detection"], 0.5)
        self.assertEqual(stages["whisper_inference"], 4.0)
        self.assertEqual(stages["vlm_inference"], 8.0)
        self.assertEqual(stages["cpu_whisper_gpu_vlm_overlap"], 3.5)
        self.assertEqual(stages["text_llm"], 2.5)

    def test_transcript_word_count_is_a_coverage_metric(self):
        payload = {"segments": [{"audio_transcript": "Тестовый русский текст"}, {"audio_transcript": "ещё слова"}]}
        self.assertEqual(BENCH.count_transcript_words(payload), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
