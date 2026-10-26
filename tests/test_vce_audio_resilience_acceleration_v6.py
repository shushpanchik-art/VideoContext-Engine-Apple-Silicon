from __future__ import annotations

import ast
import importlib.util
import unittest
import subprocess
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "patches" / "patch_vce_audio_resilience_acceleration_v6.py"
SPEC = importlib.util.spec_from_file_location("vce_audio_resilience_v6", PATCHER_PATH)
PATCH = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PATCH)


ENGINE_FIXTURE = r'''
import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

# VCE_ANALYSIS_PROFILES_FFMPEG_OCR_V2_CPU_TESSERACT
def _vce_detect_ffmpeg_ocr_backend():
    ffmpeg = shutil.which("ffmpeg")
    tesseract = shutil.which("tesseract")
    return ("tesseract-cli", "rus+eng", "ready")

def _vce_ffmpeg_ocr_capability():
    return True, "ready"

def _vce_run_ffmpeg_ocr(video_path, interval_seconds, workers=2):
    return [], ""

@app.post("/api/v1/analyze")
async def analyze_video():
    if isinstance(video_file, str):
        video_file = None
    video_duration = validate_video_file(temp_path)
    ffmpeg_ocr_available, ffmpeg_ocr_reason = _vce_ffmpeg_ocr_capability()
    ffmpeg_ocr_records = []
    ffmpeg_ocr_infer_time = 0.0
    if enable_ffmpeg_ocr:
        if ffmpeg_ocr_available:
            _vce_ffmpeg_ocr_start = time.time()
            ffmpeg_ocr_records, ffmpeg_ocr_reason = await asyncio.to_thread(
                _vce_run_ffmpeg_ocr, temp_path, ffmpeg_ocr_interval, ffmpeg_ocr_workers
            )
            ffmpeg_ocr_infer_time = time.time() - _vce_ffmpeg_ocr_start
            if ffmpeg_ocr_reason:
                print("OCR error: " + ffmpeg_ocr_reason)
            else:
                print(f"OCR count: {len(ffmpeg_ocr_records)}")
        else:
            print("OCR unavailable: " + ffmpeg_ocr_reason)

    if skip_audio and skip_visual:
        pass
    else:
        if not skip_audio:
            t_infer_start = time.time()
            audio_res = await asyncio.to_thread(
                w_model.transcribe,
                temp_path,
                word_timestamps=word_timestamps,
                condition_on_previous_text=False,
                temperature=0.0,
                compression_ratio_threshold=1.8,
            )
            t_infer_end = time.time()
            whisper_infer_time = t_infer_end - t_infer_start
            whisper_segments = audio_res.get("segments", []) or []
        first_frame_img = None
        for idx, scene in enumerate(scenes_raw):
            pass

    params = {
        "ram_mode": RAM_MODE,
    }

    text_report_content = generate_text_report(params)
    return {
        "meta": {
            "params": {
                "ram_mode": RAM_MODE,
            }
        }
    }
'''


class AudioResilienceV6Tests(unittest.TestCase):
    def test_patcher_file_is_valid_python(self):
        ast.parse(PATCHER_PATH.read_text(encoding="utf-8"))

    def test_probe_fallback_and_async_cpu_ocr_are_inserted(self):
        patched = PATCH.patch_engine(ENGINE_FIXTURE)
        ast.parse(patched)
        self.assertIn("_vce_probe_audio_stream(temp_path)", patched)
        self.assertIn("_vce_transcribe_audio_safe", patched)
        self.assertIn("w_model,", patched)
        self.assertIn("word_timestamps=word_timestamps", patched)
        self.assertIn("if whisper_audio_error:", patched)
        self.assertIn("audio_track_available = False", patched)
        self.assertIn("ffmpeg_ocr_task = asyncio.create_task(", patched)
        self.assertIn("await ffmpeg_ocr_task", patched)
        self.assertIn('"audio_status": audio_probe_message', patched)
        # A GUI launch with a minimal PATH can still find Homebrew binaries.
        self.assertIn('_vce_which_binary("ffmpeg")', patched)
        self.assertNotIn('ffmpeg_ocr_records, ffmpeg_ocr_reason = await asyncio.to_thread(\n                _vce_run_ffmpeg_ocr', patched)

    def test_patcher_is_idempotent(self):
        patched = PATCH.patch_engine(ENGINE_FIXTURE)
        self.assertEqual(PATCH.patch_engine(patched), patched)

    def test_unknown_engine_fails_without_guessing(self):
        with self.assertRaises(ValueError):
            PATCH.patch_engine("async def unrelated():\n    pass\n")

    def test_ffprobe_empty_stream_list_detects_silent_video(self):
        namespace = dict(vars(PATCH))
        exec(PATCH.HELPERS, namespace)
        namespace["_vce_which_binary"] = lambda name: "/fake/ffprobe" if name == "ffprobe" else None
        proc = subprocess.CompletedProcess(
            args=["ffprobe"], returncode=0, stdout='{"streams":[]}', stderr=""
        )
        with patch("subprocess.run", return_value=proc):
            has_audio, message = namespace["_vce_probe_audio_stream"]("silent.mp4")
        self.assertIs(has_audio, False)
        self.assertIn("не обнаружен", message)

    def test_ffprobe_audio_stream_is_detected(self):
        namespace = dict(vars(PATCH))
        exec(PATCH.HELPERS, namespace)
        namespace["_vce_which_binary"] = lambda name: "/fake/ffprobe" if name == "ffprobe" else None
        proc = subprocess.CompletedProcess(
            args=["ffprobe"], returncode=0,
            stdout='{"streams":[{"index":1,"codec_name":"aac","codec_type":"audio"}]}',
            stderr="",
        )
        with patch("subprocess.run", return_value=proc):
            has_audio, message = namespace["_vce_probe_audio_stream"]("with-audio.mp4")
        self.assertIs(has_audio, True)
        self.assertIn("aac", message)

    def test_probe_is_inconclusive_if_no_tools_are_available(self):
        namespace = dict(vars(PATCH))
        exec(PATCH.HELPERS, namespace)
        namespace["_vce_which_binary"] = lambda name: None
        has_audio, message = namespace["_vce_probe_audio_stream"]("unknown.mp4")
        self.assertIsNone(has_audio)
        self.assertIn("не удалось надёжно определить", message.casefold())

    def test_known_missing_audio_error_is_recoverable(self):
        namespace = dict(vars(PATCH))
        exec(PATCH.HELPERS, namespace)
        class MissingAudioModel:
            def transcribe(self, *args, **kwargs):
                raise RuntimeError(
                    "Failed to load audio: Output file does not contain any stream"
                )
        result, message = namespace["_vce_transcribe_audio_safe"](
            MissingAudioModel(), "no-audio.mp4"
        )
        self.assertEqual(result, {"segments": []})
        self.assertIn("продолжается", message)
        self.assertTrue(
            namespace["_vce_is_audio_decode_failure"](
                RuntimeError("Failed to load audio: Output file does not contain any stream")
            )
        )

    def test_unrelated_transcription_failure_is_not_silently_swallowed(self):
        namespace = dict(vars(PATCH))
        exec(PATCH.HELPERS, namespace)
        class BrokenModel:
            def transcribe(self, *args, **kwargs):
                raise RuntimeError("CUDA out of memory")
        with self.assertRaisesRegex(RuntimeError, "CUDA out of memory"):
            namespace["_vce_transcribe_audio_safe"](BrokenModel(), "video.mp4")


if __name__ == "__main__":
    unittest.main()
