#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply a reversible VLM-preserving performance profile to an existing VCE config.

This patch changes only videocontext_settings.json. It does not edit the engine,
disable Qwen3-VL, change model weights, or overwrite user prompts / unknown keys.

Profiles:
  fast-vlm     1 keyframe per scene; intended for slides and mostly-static screen recordings
  balanced-vlm 2 keyframes per scene; safer for fast UI transitions

Run --dry-run first. A timestamped backup is made before writes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

def _default_project() -> Path:
    """Find the common local checkout locations without assuming Downloads."""
    candidates = (
        Path.home() / "Downloads" / "VideoContext-Engine",
        Path.home() / "VideoContext-Engine",
    )
    for candidate in candidates:
        if (candidate / ENGINE_NAME).is_file():
            return candidate
    return candidates[0]


DEFAULT_PROJECT = _default_project()
ENGINE_NAME = "VideoContextEngine_v3.19.py"
CONFIG_NAME = "videocontext_settings.json"
DEFAULT_VLM = "mlx-community/Qwen3-VL-8B-Instruct-4bit"


def _positive_number(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(maximum, max(minimum, number))


def optimize_settings(current: dict[str, Any], profile: str = "fast-vlm") -> dict[str, Any]:
    """Return an optimized copy while preserving model choice, prompts and extra keys."""
    if not isinstance(current, dict):
        raise ValueError("videocontext_settings.json must contain a JSON object")
    if profile not in {"fast-vlm", "balanced-vlm"}:
        raise ValueError(f"Unknown profile: {profile}")

    updated = dict(current)

    # Keep an actual VLM selected and running. Preserve a user's chosen model (4B, 8B, etc.).
    if not isinstance(updated.get("vlm_model"), str) or not updated["vlm_model"].strip():
        updated["vlm_model"] = DEFAULT_VLM
    updated["analysis_mode"] = "full"
    updated["skip_visual"] = False
    updated["skip_audio"] = False

    # The local benchmark and historical project plan support 1280 px as a useful
    # speed/quality compromise. Never upscale a lower-resolution configuration.
    resolution = _positive_number(updated.get("vlm_resolution", 1280), 1280, 128, 2048)
    updated["vlm_resolution"] = min(resolution, 1280)

    updated["keyframes_per_scene"] = 1 if profile == "fast-vlm" else 2

    # Keep short-scene settings for dynamic videos; only clamp unusually long scenes.
    try:
        max_scene = float(updated.get("max_scene_duration", 20))
    except (TypeError, ValueError):
        max_scene = 20.0
    if max_scene <= 0:
        max_scene = 20.0
    updated["max_scene_duration"] = min(max_scene, 20.0)

    # These limits are above the generated descriptions in the supplied 14-scene sample
    # (about 250-390 characters). They cap accidental verbose output but preserve detail.
    updated["vlm_max_tokens_scene"] = min(
        _positive_number(updated.get("vlm_max_tokens_scene", 512), 512, 16, 1024), 512
    )
    updated["vlm_max_tokens_summary"] = min(
        _positive_number(updated.get("vlm_max_tokens_summary", 1024), 1024, 16, 2048), 1024
    )

    # OCR remains available. The extra FFmpeg/Tesseract pass is expensive at 1 fps;
    # move it to at least 5 seconds when enabled, without turning it off.
    interval = _positive_number(updated.get("ffmpeg_ocr_interval", 5), 5, 1, 60)
    minimum_interval = 5 if profile == "fast-vlm" else 2
    updated["ffmpeg_ocr_interval"] = max(minimum_interval, interval)
    updated["enable_apple_ocr"] = True
    updated["whisper_acceleration"] = True

    # Do not touch generate_summary, word_timestamps, prompts, model selection, or custom keys.
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT,
                        help=f"VCE project folder (default: {DEFAULT_PROJECT})")
    parser.add_argument("--profile", choices=("fast-vlm", "balanced-vlm"), default="fast-vlm",
                        help="fast-vlm uses one frame per scene; balanced-vlm uses two")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing files")
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    engine_path = project / ENGINE_NAME
    config_path = project / CONFIG_NAME
    if not engine_path.is_file():
        print(f"ERROR: engine not found: {engine_path}", file=sys.stderr)
        print("Set --project to the folder containing VideoContextEngine_v3.19.py.", file=sys.stderr)
        return 2
    if not config_path.is_file():
        print(f"ERROR: settings not found: {config_path}", file=sys.stderr)
        return 2

    try:
        current = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(current, dict):
            raise ValueError("settings root must be a JSON object")
        updated = optimize_settings(current, args.profile)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: cannot prepare performance profile: {exc}", file=sys.stderr)
        return 3

    changed = [key for key in updated if current.get(key) != updated.get(key)]
    added = [key for key in updated if key not in current]
    if not changed and not added:
        print(f"Already configured for {args.profile}; nothing to change.")
        print(f"VLM remains enabled: model={updated['vlm_model']}, analysis_mode=full.")
        return 0

    print(f"Profile: {args.profile}")
    print("VLM stays enabled: analysis_mode=full, skip_visual=false.")
    print(f"Model: {updated['vlm_model']}")
    print(f"Resolution: {current.get('vlm_resolution', '(default)')} -> {updated['vlm_resolution']}")
    print(f"Keyframes per scene: {current.get('keyframes_per_scene', '(default)')} -> {updated['keyframes_per_scene']}")
    print(f"FFmpeg OCR interval: {current.get('ffmpeg_ocr_interval', '(default)')} -> {updated['ffmpeg_ocr_interval']} s (OCR is not disabled)")
    print("Prompts, summary generation, model selection, and custom settings are preserved.")

    if args.dry_run:
        print("DRY RUN: no files changed.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = config_path.with_name(f"{config_path.name}.before_speed_profile_{stamp}.bak")
    try:
        shutil.copy2(config_path, backup)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(config_path.parent),
            prefix=f".{config_path.name}.", suffix=".tmp", delete=False
        ) as temp:
            temp.write(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
            temp_path = Path(temp.name)
        temp_path.replace(config_path)
    except Exception as exc:
        try:
            if "temp_path" in locals():
                temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        print(f"ERROR: failed to update settings: {exc}", file=sys.stderr)
        return 4

    # Ensure the saved JSON is readable before reporting success.
    try:
        json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        shutil.copy2(backup, config_path)
        print(f"ERROR: settings validation failed; restored backup: {exc}", file=sys.stderr)
        return 5

    print("Performance profile applied.")
    print(f"Backup: {backup}")
    print(f"Settings: {config_path}")
    print("Restart VideoContext Engine before the next analysis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
