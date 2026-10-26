#!/usr/bin/env python3
"""Unit tests for the source-safe MLX/Metal integration patcher."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import apply_vce_mlx_metal_patch as patcher  # noqa: E402


FIXTURE = r'''
import asyncio
import gc
import json
import os
import platform
import time
from typing import Any, Dict, List
import whisper
import torch
import cv2

def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    if not acceleration_enabled:
        return "cpu"
    return "mps"

def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    device = resolve_whisper_device(acceleration_enabled)
    return whisper.load_model(model_name, device=device)

# Optionnel : supprimer le warning HF tokenizers
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

class VLMProvider:
    pass

class MLXEngine(VLMProvider):
    def load_model(self, model_path: str):
        try:
            device_override = os.environ.get("VIDEOCONTEXT_MLX_DEVICE", str(setting("mlx_device", "auto"))).strip().lower()
            if device_override == "cpu":
                import mlx.core as mx
                mx.set_default_device(mx.cpu)
                print("CPU mode")
            elif device_override != "auto":
                raise ValueError("VIDEOCONTEXT_MLX_DEVICE must be auto or cpu")
            from mlx_vlm import load, generate
            return load(model_path)
        except Exception:
            raise

def run_text_llm_postprocess(model_path, source_name, video_duration, existing_summary, segments, max_tokens=4096, progress_callback=None):
    """Load the selected MLX VLM-family checkpoint in text-only mode and return validated enrichment."""
    import gc
    return {}

async def analyze_video():
    whisper_infer_time = 0.0
    whisper_load_time = 0.0

    target_whisper = "large"
    final_segments = []
    try:
        if skip_audio and skip_visual:
            pass
        else:
            async with gpu_lock:
                whisper_segments: List[Dict[str, Any]] = []

                if not skip_audio:
                    set_job_progress(job_id, "whisper", None, "Whisper")
                    w_model = load_whisper_model(target_whisper, acceleration_enabled=whisper_acceleration)
                    audio_res = await asyncio.to_thread(w_model.transcribe, temp_path)
                    whisper_segments = audio_res.get("segments", []) or []
                else:
                    whisper_segments = []

                if not skip_visual or generate_summary:
                    await asyncio.to_thread(vlm_engine.load_model, target_vlm)

                set_job_progress(job_id, "scene_analysis", None, "Начинаем OCR и визуальный анализ сцен", indeterminate=True, stage_index=4, stage_count=6)
                cap = cv2.VideoCapture(temp_path)
                for idx, scene in enumerate(scenes_raw):
                    if cancel_event.is_set():
                        raise RuntimeError("cancelled")
                    audio_text = transcript_for_scene(scene, whisper_segments) if not skip_audio and whisper_segments else ""
                    if not skip_visual:
                        keyframes = sample_keyframes_for_scene(
                            cap,
                            scene,
                            base_res=vlm_resolution,
                            max_frames=keyframes_per_scene,
                        )
                        if keyframes:
                            images = [kf["image"] for kf in keyframes]
                            t_vlm_start = time.time()
                            raw = await asyncio.to_thread(
                                vlm_engine.describe_scene,
                                images,
                                "fixture prompt",
                                128,
                            )
                            t_vlm_end = time.time()
                    final_segments.append({"scene_id": idx + 1, "audio_transcript": audio_text, "audio_features": {}})
                set_job_progress(job_id, "summary", None, "Формируем сводку по всем сценам", indeterminate=True, stage_index=5, stage_count=6)

        timings = {
            "vlm": {
                "model": target_vlm,
                "load_time": round(vlm_load_time, 3),
                "inference_time": round(vlm_infer_time, 3),
            },
        }
        timings["text_llm"] = {
            "enabled": bool(text_llm_enabled),
            "model": text_llm_model if text_llm_enabled else None,
            "status": text_llm_status,
            "inference_time": round(text_llm_infer_time, 3),
            "error": text_llm_error or None,
        }
        return timings
    finally:
        if cap is not None:
            cap.release()
'''


class PatchTests(unittest.TestCase):
    def test_unknown_engine_is_rejected(self):
        with self.assertRaises(patcher.PatchError):
            patcher.patch_engine_text("print('unrecognized source')")

    def test_full_patch_compiles_and_is_idempotent(self):
        patched = patcher.patch_engine_text(FIXTURE)
        compile(patched, "patched_engine.py", "exec")
        self.assertIn('return "cpu"', patched)
        self.assertIn('mlx_whisper.transcribe(', patched)
        self.assertIn('mx.new_thread_local_stream(mx.cpu)', patched)
        self.assertIn('_vce_prepare_scene_keyframes_cpu', patched)
        self.assertIn('cpu_keyframe_prefetch_seconds', patched)
        self.assertIn('"backend": "mlx-whisper"', patched)
        self.assertNotIn('w_model.to, "cpu"', patched)
        self.assertIn('_vce_configure_mlx_metal("vlm")', patched)
        self.assertIn('_vce_configure_mlx_metal("text_llm")', patched)
        self.assertIn('_vce_run_whisper_cpu_stage', patched)
        self.assertIn('await _vce_whisper_task', patched)
        self.assertIn('"whisper_vlm_overlap_seconds"', patched)
        self.assertEqual(patcher.patch_engine_text(patched), patched)

    def test_settings_force_mlx_whisper_cpu_stream_and_metal(self):
        content = json.dumps({"whisper_acceleration": True, "enable_mlx_whisper": False, "mlx_device": "auto", "custom": "keep"})
        result = json.loads(patcher.patch_settings_text(content))
        self.assertFalse(result["whisper_acceleration"])
        self.assertTrue(result["enable_mlx_whisper"])
        self.assertEqual(result["mlx_device"], "metal")
        self.assertEqual(result["custom"], "keep")

    def test_previous_v1_patch_is_not_silently_repatched(self):
        old_patch = FIXTURE + "\n# VCE_MLX_METAL_CPU_PARALLEL_V1\n"
        with self.assertRaisesRegex(patcher.PatchError, "previous CPU/Metal patch version"):
            patcher.patch_engine_text(old_patch)


if __name__ == "__main__":
    unittest.main()
