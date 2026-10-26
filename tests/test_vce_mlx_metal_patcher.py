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

    def test_whisper_block_with_custom_initializers_is_supported(self):
        old = "                whisper_segments: List[Dict[str, Any]] = []\n\n                if not skip_audio:"
        new = (
            "                whisper_segments: List[Dict[str, Any]] = []\n"
            "                _vce_whisper_task = None\n"
            "                _vce_whisper_model = None\n"
            "                whisper_audio_error = None\n"
            "                whisper_infer_time = 0.0\n\n"
            "                if not skip_audio:"
        )
        variant = FIXTURE.replace(old, new)
        self.assertNotEqual(variant, FIXTURE)
        patched = patcher.patch_engine_text(variant)
        compile(patched, "patched_custom_whisper_init.py", "exec")
        self.assertIn("async def _vce_run_whisper_cpu_stage", patched)
        self.assertIn("_vce_whisper_model = None", patched)
        self.assertIn("whisper_audio_error = None", patched)

    def test_custom_visual_condition_without_legacy_anchor_is_supported(self):
        variant = FIXTURE.replace(
            "if not skip_visual or generate_summary:",
            "if not skip_visual:",
        )
        self.assertNotIn("if not skip_visual or generate_summary:", variant)
        patched = patcher.patch_engine_text(variant)
        compile(patched, "patched_custom_engine.py", "exec")
        self.assertIn("if not skip_visual:", patched)
        self.assertIn('_vce_configure_mlx_metal("vlm")', patched)
        self.assertIn("_vce_prepare_scene_keyframes_cpu", patched)

    def test_cleanup_anchor_allows_blank_lines(self):
        variant = FIXTURE.replace(
            "    finally:\n        if cap is not None:",
            "    finally:\n\n        if cap is not None:",
        )
        self.assertIn("    finally:\n\n        if cap is not None:", variant)
        patched = patcher.patch_engine_text(variant)
        compile(patched, "patched_blank_cleanup_engine.py", "exec")
        self.assertIn("_vce_pending_whisper_task", patched)

    def test_existing_bounded_prefetch_is_preserved(self):
        variant = FIXTURE.replace(
            "                cap = cv2.VideoCapture(temp_path)\n"
            "                for idx, scene in enumerate(scenes_raw):",
            "                cap = cv2.VideoCapture(temp_path)\n"
            "                _vce_frame_prefetch_queue = None\n"
            "                _vce_frame_prefetch_stats = dict(worker_seconds=0.1)\n"
            "                # _vce_start_bounded_keyframe_prefetch already exists in this engine variant\n"
            "                for idx, scene in enumerate(scenes_raw):",
        )
        variant = variant.replace(
            "                        keyframes = sample_keyframes_for_scene(\n"
            "                            cap,\n"
            "                            scene,\n"
            "                            base_res=vlm_resolution,\n"
            "                            max_frames=keyframes_per_scene,\n"
            "                        )",
            "                        if _vce_frame_prefetch_queue is not None:\n"
            "                            keyframes = []\n"
            "                        else:\n"
            "                            keyframes = sample_keyframes_for_scene(\n"
            "                                cap,\n"
            "                                scene,\n"
            "                                base_res=vlm_resolution,\n"
            "                                max_frames=keyframes_per_scene,\n"
            "                            )",
        )
        self.assertIn("_vce_start_bounded_keyframe_prefetch", variant)
        patched = patcher.patch_engine_text(variant)
        compile(patched, "patched_existing_prefetch_engine.py", "exec")
        self.assertIn("_vce_frame_prefetch_queue is not None", patched)
        self.assertNotIn("_vce_prepare_scene_keyframes_cpu", patched)
        self.assertIn('locals().get("_vce_frame_prefetch_stats", {})', patched)

    def test_mlx_vlm_load_and_generation_use_same_dedicated_thread(self):
        patched = patcher.patch_engine_text(FIXTURE)
        compile(patched, "patched_thread_affinity_engine.py", "exec")
        self.assertIn(patcher.MLX_THREAD_AFFINITY_MARKER, patched)
        self.assertIn("_VCE_MLX_EXECUTOR", patched)
        self.assertIn('max_workers=1, thread_name_prefix="vce-mlx-metal"', patched)
        self.assertIn("await _vce_mlx_run(vlm_engine.load_model, target_vlm)", patched)
        self.assertIn("await _vce_mlx_run(vlm_engine.describe_scene,", patched)
        self.assertNotIn("await asyncio.to_thread(vlm_engine.load_model", patched)
        self.assertNotIn("await asyncio.to_thread(vlm_engine.describe_scene", patched)

    def test_custom_memory_fallback_call_uses_dedicated_executor(self):
        variant = FIXTURE.replace(
            "vlm_engine.describe_scene,",
            "_vce_describe_scene_with_memory_fallback,",
        )
        patched = patcher.patch_engine_text(variant)
        compile(patched, "patched_memory_fallback_thread_engine.py", "exec")
        self.assertIn(
            "await _vce_mlx_run(_vce_describe_scene_with_memory_fallback,",
            patched,
        )

    def test_existing_v2_patch_is_upgraded_to_thread_affinity(self):
        fully_patched = patcher.patch_engine_text(FIXTURE)
        legacy = fully_patched.replace(patcher.MLX_THREAD_RUNTIME_HELPERS, "")
        legacy = legacy.replace(
            "await _vce_mlx_run(vlm_engine.load_model, target_vlm)",
            "await asyncio.to_thread(vlm_engine.load_model, target_vlm)",
        )
        legacy = legacy.replace(
            "await _vce_mlx_run(vlm_engine.describe_scene,",
            "await asyncio.to_thread(\n                                vlm_engine.describe_scene,",
        )
        self.assertNotIn(patcher.MLX_THREAD_AFFINITY_MARKER, legacy)
        upgraded = patcher.patch_engine_text(legacy)
        compile(upgraded, "upgraded_v2_engine.py", "exec")
        self.assertIn(patcher.MLX_THREAD_AFFINITY_MARKER, upgraded)
        self.assertIn("await _vce_mlx_run(vlm_engine.load_model, target_vlm)", upgraded)

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
