#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the non-experimental Apple-Silicon profile and real PyTorch/MPS Whisper path.

The patch is intentionally narrow and idempotent. It preserves target-Mac local
runtime hardening (including the reviewed Metal latch), creates timestamped
backups, validates all three files before writing, and keeps user prompts and
unrelated settings intact.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

MARKER = "VCE_PYTORCH_WHISPER_MPS_SERIAL_V1"
UI_MARKER = "VCE_FAST_MPS_PROFILE_UI_V1"

PROFILE: dict[str, Any] = {
    "analysis_mode": "full",
    "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    "whisper_model": "large",
    "text_llm_enabled": True,
    "text_llm_model": "mlx-community/Qwen3.5-9B-MLX-4bit",
    "text_llm_max_tokens": 4096,
    "vlm_resolution": 1280,
    "scene_threshold": 0.15,
    "min_scene_duration": 1.5,
    "max_scene_duration": 20,
    "keyframes_per_scene": 1,
    "vlm_max_tokens_scene": 1024,
    "vlm_max_tokens_summary": 2048,
    "whisper_acceleration": True,
    "enable_apple_ocr": True,
    "enable_ffmpeg_ocr": True,
    "ffmpeg_ocr_interval": 5,
    "ffmpeg_ocr_workers": 3,
    "enable_ocr_cache": True,
    "enable_audio_features": True,
    "generate_summary": True,
    "generate_txt": True,
    "generate_json": True,
    "word_timestamps": True,
    "enable_stage_timing": True,
    "skip_audio": False,
    "skip_visual": False,
    "ram_mode": "ram-",
    "mlx_device": "metal",
    "response_format": "json",
}

REMOVED_SETTINGS = {
    "enable_adaptive_keyframes",
    "enable_mlx_whisper",
    "enable_fast_scene_detection",
}

OLD_WHISPER_BLOCK = '''def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Whisper is pinned to MLX CPU stream to overlap with MLX/Metal inference."""
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Return an mlx-whisper adapter; MLX model weights load inside the CPU stream."""
    return _VCEWhisperMLXCPU(model_name)
'''

NEW_WHISPER_BLOCK = '''# VCE_PYTORCH_WHISPER_MPS_SERIAL_V1
def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Choose PyTorch MPS on Apple Silicon; never silently fall back from requested MPS."""
    if not acceleration_enabled:
        return "cpu"
    is_apple_silicon = (
        platform.system() == "Darwin"
        and platform.machine().lower() in {"arm64", "aarch64"}
    )
    if is_apple_silicon:
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        available = bool(mps_backend is not None and mps_backend.is_available())
        if available and hasattr(mps_backend, "is_built"):
            available = bool(mps_backend.is_built())
        if not available:
            raise RuntimeError(
                "PyTorch MPS was requested on Apple Silicon but is unavailable. "
                "Refusing to silently run Whisper on CPU."
            )
        return "mps"
    if bool(getattr(torch, "cuda", None)) and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Load openai-whisper explicitly on the resolved PyTorch device."""
    device = resolve_whisper_device(acceleration_enabled)
    print(f"[VCE Whisper] backend=openai-whisper device={device} model={model_name}", flush=True)
    model = whisper.load_model(model_name, device=device)
    actual_device = getattr(model, "device", None)
    if actual_device is None:
        try:
            actual_device = next(model.parameters()).device
        except Exception as exc:
            raise RuntimeError(
                "Cannot verify the actual device of the loaded openai-whisper model."
            ) from exc
    actual_type = getattr(actual_device, "type", str(actual_device).split(":", 1)[0])
    if str(actual_type) != device:
        raise RuntimeError(
            f"Whisper device mismatch: requested {device}, model reports {actual_type}."
        )
    VCE_MLX_RUNTIME_DIAGNOSTICS["whisper"] = {
        "backend": "openai-whisper",
        "device": device,
        "model": model_name,
        "device_verified": True,
    }
    return model
'''

OLD_FFMPEG_SCENE_BRANCH = '''        if enable_fast_scene_detection:
            scenes_raw = await asyncio.to_thread(
                _vce_detect_scenes_fast,
                temp_path,
                threshold=scene_threshold,
                min_duration=min_scene_duration,
                max_duration=max_scene_duration,
            )
        else:
            scenes_raw = await asyncio.to_thread(
                detect_scenes,
                temp_path,
                threshold=scene_threshold,
                min_duration=min_scene_duration,
                max_duration=max_scene_duration,
            )'''

NEW_FFMPEG_SCENE_BRANCH = '''        scenes_raw = await asyncio.to_thread(
            detect_scenes,
            temp_path,
            threshold=scene_threshold,
            min_duration=min_scene_duration,
            max_duration=max_scene_duration,
        )'''

OLD_WHISPER_CLEANUP = '''                        if RAM_MODE == "ram-":
                            if hasattr(w_model, "release"):
                                await asyncio.to_thread(w_model.release)
                            del w_model
                            gc.collect()'''

NEW_WHISPER_CLEANUP = '''                        # MPS and MLX/Metal share Apple unified memory. Release Whisper
                        # before loading the VLM, even if the previous RAM mode was ram+.
                        if WHISPER_CACHE.get(target_whisper) is w_model:
                            WHISPER_CACHE.pop(target_whisper, None)
                        del w_model
                        gc.collect()
                        if whisper_device == "mps":
                            try:
                                await asyncio.to_thread(torch.mps.synchronize)
                                await asyncio.to_thread(torch.mps.empty_cache)
                            except Exception as release_error:
                                print(
                                    "[VCE Whisper] MPS cache release warning: "
                                    + type(release_error).__name__,
                                    flush=True,
                                )'''

def _line_numbers_containing(source: str, token: str) -> list[int]:
    """Return 1-based source line numbers containing a token, without echoing source text."""
    return [
        line_number
        for line_number, line in enumerate(source.splitlines(), start=1)
        if token in line
    ]


def _replace_exact(source: str, old: str, new: str, label: str, expected: int = 1) -> str:
    count = source.count(old)
    if count != expected:
        raise ValueError(
            f"{label}: expected {expected} exact match(es), found {count}; no files were written."
        )
    return source.replace(old, new, expected)


def _replace_one_of_exact(
    source: str,
    variants: tuple[tuple[str, str], ...],
    label: str,
) -> str:
    """Apply exactly one known source-layout variant; reject absent/ambiguous anchors."""
    match_count = sum(source.count(old) for old, _new in variants)
    if match_count != 1:
        raise ValueError(
            f"{label}: expected exactly 1 supported exact anchor match, found {match_count}; "
            "no files were written."
        )
    for old, new in variants:
        if old in source:
            return source.replace(old, new, 1)
    raise AssertionError("exact anchor count changed unexpectedly")


def _remove_ast_nodes(source: str, names: set[str]) -> str:
    """Remove named top-level functions/classes/assignments without touching adjacent code."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans: list[tuple[int, int]] = []
    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    break
        if name in names:
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno)
            start = offsets[start_line - 1]
            end = offsets[end_line]
            # Consume one following blank line for clean formatting.
            while end < len(source) and source[end] in "\r\n":
                end += 1
            spans.append((start, end))
    if names - {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    } - {
        target.id
        for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }:
        missing = sorted(
            names - {
                node.name for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            } - {
                target.id
                for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(target, ast.Name)
            }
        )
        raise ValueError("Expected obsolete runtime definition(s) missing: " + ", ".join(missing))
    for start, end in sorted(spans, reverse=True):
        source = source[:start] + source[end:]
    return source



def _remove_optional_ast_nodes(source: str, names: set[str]) -> str:
    """Remove optional top-level obsolete definitions, rejecting duplicate/ambiguous names."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    matches: dict[str, list[ast.AST]] = {name: [] for name in names}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in matches:
                matches[node.name].append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in matches:
                    matches[target.id].append(node)
                    break

    ambiguous = sorted(name for name, nodes in matches.items() if len(nodes) > 1)
    if ambiguous:
        raise ValueError(
            "Optional obsolete runtime definition is ambiguous: "
            + ", ".join(ambiguous)
            + "; no files were written."
        )

    spans: list[tuple[int, int]] = []
    for nodes in matches.values():
        if not nodes:
            continue
        node = nodes[0]
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", node.lineno)
        start = offsets[start_line - 1]
        end = offsets[end_line]
        while end < len(source) and source[end] in "\r\n":
            end += 1
        spans.append((start, end))

    for start, end in sorted(spans, reverse=True):
        source = source[:start] + source[end:]
    return source


def _serialize_whisper_before_vlm(source: str) -> str:
    """Await the single Whisper task at its creation site, preserving intervening guards."""
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]+)_vce_whisper_task = "
        r"asyncio\.create_task\(_vce_run_whisper_mps_stage\(\)\)[ \t]*$"
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            "Sequential Whisper-before-VLM scheduling: expected exactly one "
            f"Whisper task creation, found {len(matches)}; no files were written."
        )
    match = matches[0]
    indent = match.group("indent")
    line_start = source.rfind("\n", 0, match.start()) + 1
    prefix = source[:line_start]
    parent_indent = indent[:-4]
    if not re.search(
        r"(?m)^" + re.escape(parent_indent) + r"if not skip_audio:\s*$",
        prefix,
    ):
        raise ValueError(
            "Whisper task creation is not directly governed by the expected "
            "'if not skip_audio' block; refusing to patch."
        )
    insertion = (
        "\n" + indent
        + "# Await ASR before loading Qwen: MPS Whisper and MLX/Metal VLM\n"
        + indent + "# are intentionally serialized on the shared Apple GPU.\n"
        + indent + "whisper_segments = await _vce_whisper_task"
    )
    result = source[:match.end()] + insertion + source[match.end():]
    await_pos = result.find("whisper_segments = await _vce_whisper_task", match.start())
    vlm_pos = result.find("if run_vlm:", await_pos)
    if await_pos < 0 or vlm_pos < 0 or await_pos >= vlm_pos:
        raise ValueError("Whisper await/VLM ordering postcondition failed.")
    ast.parse(result)
    return result

def patch_engine(source: str) -> str:
    if MARKER in source:
        ast.parse(source)
        required = (
            '"backend": "openai-whisper"',
            "await _vce_whisper_task",
            '"whisper_backend": "openai-whisper"',
            "enable_ocr_cache: bool = Form(bool(setting",
        )
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("MPS patch marker exists but required code is incomplete: " + ", ".join(missing))
        return source

    source = _replace_exact(source, OLD_WHISPER_BLOCK, NEW_WHISPER_BLOCK, "Whisper device loader")
    source = _replace_exact(
        source,
        "load_whisper_model, target_whisper, acceleration_enabled=False",
        "load_whisper_model, target_whisper, acceleration_enabled=whisper_acceleration",
        "Whisper loader acceleration (RAM+ / RAM-)",
        expected=2,
    )

    source = source.replace("_vce_run_whisper_cpu_stage", "_vce_run_whisper_mps_stage")
    source = _replace_exact(
        source,
        "Whisper CPU: распознаём речь параллельно с MLX/Metal VLM",
        "Whisper PyTorch: отдельный MPS-этап перед MLX/Metal VLM",
        "Whisper progress label",
    )
    source = _replace_exact(
        source,
        'print("[VCE Whisper] Starting CPU ASR in parallel with the GPU stage.")',
        'print(f"[VCE Whisper] Starting openai-whisper on {whisper_device} before the MLX/Metal VLM stage.")',
        "Whisper start log",
    )
    source = _replace_exact(
        source,
        'f"Whisper CPU завершён: фрагментов речи {len(result)}"',
        'f"Whisper завершён ({whisper_device}): фрагментов речи {len(result)}"',
        "Whisper completion label",
    )
    source = _replace_exact(
        source,
        'print(f"[VCE Whisper] CPU inference: {whisper_infer_time:.2f}s")',
        'print(f"[VCE Whisper] backend=openai-whisper device={whisper_device} inference={whisper_infer_time:.2f}s")',
        "Whisper timing log",
    )
    source = _replace_exact(
        source,
        OLD_WHISPER_CLEANUP,
        NEW_WHISPER_CLEANUP,
        "Whisper memory release",
    )

    # Insert the await immediately after task creation. This deliberately does
    # not replace/rewrite the following VLM guard block: target Macs may carry a
    # local Metal-failure latch between task creation and "if run_vlm".
    source = _serialize_whisper_before_vlm(source)

    source = _replace_exact(
        source,
        'print(f"[VCE parallelism] CPU Whisper / MLX VLM overlap: {_vce_overlap_seconds:.2f}s")',
        'print("[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")',
        "Sequential stage-order log",
    )

    source = _replace_exact(source, OLD_FFMPEG_SCENE_BRANCH, NEW_FFMPEG_SCENE_BRANCH, "Baseline scene detection")
    source = source.replace(
        "max_frames_by_scene=[_vce_adaptive_keyframe_limit(s, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene for s in scenes_raw],",
        "max_frames_by_scene=[keyframes_per_scene for _ in scenes_raw],",
    )
    source = source.replace(
        "effective_keyframes = _vce_adaptive_keyframe_limit(scene, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene",
        "effective_keyframes = keyframes_per_scene",
    )

    source = _replace_exact(
        source,
        '''def _vce_transcribe_audio(audio_path, model_name, model, use_mlx, *args, **kwargs):
    """Use the selected ASR backend while keeping Whisper-compatible output fields."""
    if not use_mlx:
        return model.transcribe(audio_path, *args, **kwargs)
    import mlx_whisper
    repo = _vce_validate_mlx_whisper_model(model_name)
    return mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=repo,
        **kwargs,
    )''',
        '''def _vce_transcribe_audio(audio_path, model_name, model, use_mlx=False, *args, **kwargs):
    """Use only the PyTorch openai-whisper model; MLX Whisper is not a runtime option."""
    if use_mlx:
        raise RuntimeError("MLX Whisper support was removed; use PyTorch Whisper with MPS or CPU.")
    return model.transcribe(audio_path, *args, **kwargs)''',
        "Remove MLX Whisper transcription branch",
    )

    # Remove the runtime/API paths for the other experimental algorithms.
    source = re.sub(
        r'(?m)^[ \t]*enable_(?:adaptive_keyframes|mlx_whisper|fast_scene_detection): bool = Form\([^\n]*\n',
        "",
        source,
    )
    source = _replace_exact(
        source,
        '''        if enable_mlx_whisper:
            _vce_validate_mlx_whisper_model(target_whisper)
''',
        "",
        "Remove MLX Whisper request validation",
    )
    source = _replace_exact(
        source,
        '''        "enable_stage_timing": False,
        "enable_adaptive_keyframes": False,
        "enable_ocr_cache": False,
        "enable_mlx_whisper": False,
        "enable_fast_scene_detection": False,''',
        '''        "enable_stage_timing": True,
        "enable_ocr_cache": True,''',
        "Stable settings defaults",
    )
    source = _replace_exact(
        source,
        '''        "enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
        "enable_mlx_whisper", "enable_fast_scene_detection",''',
        '''        "enable_stage_timing", "enable_ocr_cache",''',
        "Settings allow-list cleanup",
    )
    source = _replace_one_of_exact(
        source,
        (
            (
                '"analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 2,',
                '"analysis_mode": "full", "enable_ffmpeg_ocr": True, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 3,',
            ),
            (
                '"analysis_mode": "full", "recognition_languages": _vce_legacy_apple_languages(persisted.get("apple_ocr_languages", ["ru-RU", "en-US"])), "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 2,',
                '"analysis_mode": "full", "recognition_languages": _vce_legacy_apple_languages(persisted.get("apple_ocr_languages", ["ru-RU", "en-US"])), "enable_ffmpeg_ocr": True, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 3,',
            ),
        ),
        "Settings API OCR defaults",
    )
    source = _replace_exact(
        source,
        'enable_ffmpeg_ocr: bool = Form(False, description="Дополнительный OCR через FFmpeg/Tesseract"),',
        'enable_ffmpeg_ocr: bool = Form(bool(setting("enable_ffmpeg_ocr", True)), description="Дополнительный OCR через FFmpeg/Tesseract"),',
        "FFmpeg OCR request default",
    )
    source = _replace_exact(
        source,
        'ffmpeg_ocr_interval: int = Form(5, ge=1, le=60, description="Интервал выборки FFmpeg OCR, секунд"),',
        'ffmpeg_ocr_interval: int = Form(int(setting("ffmpeg_ocr_interval", 5)), ge=1, le=60, description="Интервал выборки FFmpeg OCR, секунд"),',
        "FFmpeg OCR interval default",
    )
    source = _replace_exact(
        source,
        'ffmpeg_ocr_workers: int = Form(int(setting("ffmpeg_ocr_workers", 2)), ge=1, le=8, description="Число параллельных CPU-процессов Tesseract"),',
        'ffmpeg_ocr_workers: int = Form(int(setting("ffmpeg_ocr_workers", 3)), ge=1, le=8, description="Число параллельных CPU-процессов Tesseract"),',
        "FFmpeg OCR worker default",
    )
    source = _replace_exact(
        source,
        'enable_stage_timing: bool = Form(False, description="Добавлять подробные тайминги этапов в JSON-ответ"),',
        'enable_stage_timing: bool = Form(bool(setting("enable_stage_timing", True)), description="Добавлять подробные тайминги этапов в JSON-ответ"),',
        "Stage timing request default",
    )
    source = _replace_exact(
        source,
        'enable_ocr_cache: bool = Form(False, description="Кэшировать OCR для повторяющихся кадров"),',
        'enable_ocr_cache: bool = Form(bool(setting("enable_ocr_cache", True)), description="Кэшировать OCR для повторяющихся кадров"),',
        "OCR cache request default",
    )

    source = _replace_exact(
        source,
        '''            "whisper": {
                "model": target_whisper,
                "device": whisper_device,''',
        '''            "whisper": {
                "backend": "openai-whisper",
                "model": target_whisper,
                "device": whisper_device,''',
        "Whisper backend timing metadata",
    )
    source = _replace_exact(
        source,
        '''                "whisper_backend": "mlx-whisper",
                "whisper_device": "cpu",''',
        '''                "whisper_backend": "openai-whisper",
                "whisper_device": whisper_device,
                "gpu_stages_serialized": True,''',
        "Whisper runtime provenance metadata",
    )
    source = _replace_exact(
        source,
        '''                        "enable_apple_ocr": enable_apple_ocr,
                        "whisper_acceleration": whisper_acceleration,''',
        '''                        "enable_apple_ocr": enable_apple_ocr,
                        "enable_ocr_cache": enable_ocr_cache,
                        "enable_ffmpeg_ocr": enable_ffmpeg_ocr,
                        "whisper_acceleration": whisper_acceleration,''',
        "OCR cache request provenance",
    )

    source = _replace_exact(
        source,
        "import threading as _vce_threading\n_VCE_MLX_WHISPER_LOCK = _vce_threading.Lock()\n\n",
        "",
        "Remove MLX Whisper lock",
    )

    source = _remove_ast_nodes(
        source,
        {
            "_vce_mlx_whisper_model_id",
            "_VCEWhisperMLXCPU",
            "_VCE_MLX_WHISPER_REPOS",
            "_vce_validate_mlx_whisper_model",
            "_vce_load_whisper_model",
            "_vce_adaptive_keyframe_limit",
            "_vce_detect_scenes_fast",
        },
    )

    # Some user installations carry this additional optional MLX tokenizer helper.
    # Remove it when present, but do not require the upstream baseline to define it.
    source = _remove_optional_ast_nodes(source, {"_vce_whisper_supported_languages"})

    source = source.replace(
        "# VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1",
        "# VCE_FAST_PROFILE_BASELINE_OCR_CACHE_V1",
    )
    source = source.replace(
        "# VCE_MLX_METAL_CPU_PARALLEL_MLXWHISPER_V2",
        "# VCE_MLX_METAL_RUNTIME_DIAGNOSTICS_V2",
    )
    source = source.replace(
        'print(f"[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")',
        'print("[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")',
    )

    # The new runtime intentionally has no active MLX Whisper implementation or
    # switches. Keep the cache, Apple Vision, and FFmpeg/Tesseract stages intact.
    for forbidden in (
        "enable_adaptive_keyframes",
        "enable_mlx_whisper",
        "enable_fast_scene_detection",
        "_VCEWhisperMLXCPU",
        "_vce_validate_mlx_whisper_model",
        "_vce_whisper_supported_languages",
        "mlx_whisper",
        "_vce_detect_scenes_fast",
        "_vce_adaptive_keyframe_limit",
    ):
        if forbidden in source:
            locations = _line_numbers_containing(source, forbidden)
            where = ", ".join(str(number) for number in locations)
            raise ValueError(
                "Experimental runtime reference remains after patch: "
                + forbidden
                + (f" (line(s): {where})" if where else "")
                + "; refusing to write any project files."
            )

    source = source.replace(
        'print("[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")',
        'print("[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")\n# ' + MARKER,
        1,
    )
    ast.parse(source)
    return source


def patch_ui(ui: str) -> str:
    if UI_MARKER in ui:
        for forbidden in (
            'id="cfg-stage-timing"',
            'id="cfg-adaptive-keyframes"',
            'id="cfg-mlx-whisper"',
            'id="cfg-fast-scene-detection"',
        ):
            if forbidden in ui:
                raise ValueError("UI patch marker exists but experimental control remains: " + forbidden)
        return ui

    for key in (
        "enable_stage_timing",
        "enable_adaptive_keyframes",
        "enable_mlx_whisper",
        "enable_fast_scene_detection",
    ):
        ui = re.sub(r"(?m)^[ \t]*" + re.escape(key) + r":[^\n]*\n", "", ui)

    ui = _replace_exact(
        ui,
        "      'cfg-stage-timing':'enable_stage_timing',\n",
        "",
        "UI stage timing control mapping",
    )
    ui = _replace_exact(
        ui,
        "      'cfg-adaptive-keyframes':'enable_adaptive_keyframes',\n",
        "",
        "UI adaptive keyframe control mapping",
    )
    ui = _replace_exact(
        ui,
        "      'cfg-mlx-whisper':'enable_mlx_whisper',\n",
        "",
        "UI MLX Whisper control mapping",
    )
    ui = _replace_exact(
        ui,
        "      'cfg-fast-scene-detection':'enable_fast_scene_detection','cfg-ffmpeg-ocr':'enable_ffmpeg_ocr','cfg-apple-ocr':'enable_apple_ocr','cfg-whisper-acceleration':'whisper_acceleration',\n",
        "      'cfg-ffmpeg-ocr':'enable_ffmpeg_ocr','cfg-apple-ocr':'enable_apple_ocr','cfg-whisper-acceleration':'whisper_acceleration',\n",
        "UI fast scene detection control mapping",
    )

    for control_id in (
        "cfg-stage-timing",
        "cfg-adaptive-keyframes",
        "cfg-mlx-whisper",
        "cfg-fast-scene-detection",
    ):
        pattern = re.compile(
            r'(?m)^[ \t]*<label[^\n]*id="' + re.escape(control_id) + r'"[^\n]*\n'
        )
        ui, count = pattern.subn("", ui)
        if count != 1:
            raise ValueError(f"Expected one UI label for {control_id}, found {count}.")

    ui = _replace_exact(
        ui,
        "Экспериментальные оптимизации (все выключены по умолчанию)",
        "Базовый профиль OCR",
        "UI optimization heading",
    )
    ui = _replace_exact(
        ui,
        "Кэш OCR Apple Vision для повторяющихся кадров",
        "Кэш Apple Vision OCR для повторяющихся кадров",
        "UI OCR cache label",
    )
    ui = _replace_exact(
        ui,
        "Дополнительный OCR через FFmpeg/Tesseract (экспериментально)",
        "Дополнительный OCR через FFmpeg/Tesseract",
        "UI FFmpeg OCR label",
    )

    if 'id="cfg-ocr-cache"' not in ui:
        raise ValueError("Apple Vision OCR cache control was removed unexpectedly.")
    if "enable_ocr_cache: document.getElementById('cfg-ocr-cache').checked" not in ui:
        raise ValueError("Apple Vision OCR cache is no longer wired to the settings payload.")
    for forbidden in (
        'id="cfg-stage-timing"',
        'id="cfg-adaptive-keyframes"',
        'id="cfg-mlx-whisper"',
        'id="cfg-fast-scene-detection"',
        "enable_adaptive_keyframes",
        "enable_mlx_whisper",
        "enable_fast_scene_detection",
    ):
        if forbidden in ui:
            raise ValueError("Experimental UI reference remains after patch: " + forbidden)
    ui = ui.replace("</body>", f"<!-- {UI_MARKER} -->\n</body>", 1)
    return ui


def migrate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    updated = dict(settings)
    for key in REMOVED_SETTINGS:
        updated.pop(key, None)
    updated.update(PROFILE)
    return updated


def _atomic_write(path: Path, text: str, original_mode: int | None) -> None:
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_text(text, encoding="utf-8")
        if original_mode is not None:
            temp.chmod(original_mode)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def apply_project(project: Path) -> list[Path]:
    engine = project / "VideoContextEngine_v3.19.py"
    ui_file = project / "videocontext_web_ui.html"
    settings_file = project / "videocontext_settings.json"
    for required in (engine, ui_file):
        if not required.is_file():
            raise FileNotFoundError(f"Required VCE project file is missing: {required}")

    engine_old = engine.read_text(encoding="utf-8")
    ui_old = ui_file.read_text(encoding="utf-8")
    try:
        settings_old = settings_file.read_text(encoding="utf-8") if settings_file.is_file() else "{}\n"
        settings_data = json.loads(settings_old)
        if not isinstance(settings_data, dict):
            raise ValueError("videocontext_settings.json must contain a JSON object.")
    except Exception as exc:
        raise ValueError(f"Cannot safely read VCE settings: {type(exc).__name__}: {exc}") from exc

    engine_new = patch_engine(engine_old)
    ui_new = patch_ui(ui_old)
    settings_new_data = migrate_settings(settings_data)
    settings_new = json.dumps(settings_new_data, ensure_ascii=False, indent=2) + "\n"
    ast.parse(engine_new, filename=str(engine))

    changed = []
    candidates = (
        (engine, engine_old, engine_new),
        (ui_file, ui_old, ui_new),
        (settings_file, settings_old, settings_new),
    )
    for path, old, new in candidates:
        if old != new:
            changed.append(path)
    if not changed:
        print("Fast MPS profile already applied; no files changed.")
        return []

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backups: dict[Path, Path] = {}
    modes = {path: stat.S_IMODE(path.stat().st_mode) if path.exists() else None for path, _, _ in candidates}
    for path in changed:
        if path.exists():
            backup = path.with_name(path.name + f".backup-fast-mps-{stamp}")
            shutil.copy2(path, backup)
            backups[path] = backup

    try:
        for path, old, new in candidates:
            if old != new:
                _atomic_write(path, new, modes[path])
    except Exception:
        for path in changed:
            if path in backups:
                shutil.copy2(backups[path], path)
            else:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise

    for path in changed:
        print(f"Updated: {path.name}")
        if path in backups:
            print(f"Backup:  {backups[path].name}")
    print("Profile: Whisper large / PyTorch MPS -> MLX VLM; serialized GPU stages.")
    print("OCR: Apple Vision ON; OCR cache ON; FFmpeg/Tesseract ON (5 s, 3 workers).")
    print("Removed UI/API experiments: adaptive keyframes, MLX Whisper, fast scene detection.")
    return list(backups.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        backups = apply_project(args.project_dir.expanduser().resolve())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("No partial profile should be used; inspect any timestamped backups before retrying.", file=sys.stderr)
        return 2
    print(f"Patch complete. Backup files: {len(backups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
