#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add bounded background prefetch of scene keyframes to VideoContext Engine v3.19.

A dedicated OpenCV VideoCapture in a single producer thread prepares images for
up to two upcoming scenes while the main thread runs VLM/OCR on the current
scene. Scene order is preserved. The worker is stopped and joined before the
temporary video can be deleted.

Requires the previously installed VCE parallel ASR/VLM patch.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import re
import shutil
import textwrap
from datetime import datetime
from pathlib import Path

ENGINE_NAME = "VideoContextEngine_v3.19.py"
MARKER = "# VCE_BOUNDED_KEYFRAME_PREFETCH_V1"
PARALLEL_MARKER = "VCE_PARALLEL_CPU_ASR_GPU_VLM_V1"
SETTINGS_NAME = "videocontext_settings.json"

HELPER = r'''# VCE_BOUNDED_KEYFRAME_PREFETCH_V1
def _vce_start_bounded_keyframe_prefetch(
    video_path: str,
    scenes: List[Dict[str, Any]],
    base_res: int,
    max_frames: int,
    queue_size: int = 1,
    max_frames_by_scene=None,
):
    """Prepare upcoming scene keyframes in a bounded queue on a single CPU worker."""
    import queue
    import threading

    result_queue = queue.Queue(maxsize=max(1, min(int(queue_size), 1)))
    stop_event = threading.Event()
    stats = {"worker_seconds": 0.0, "wait_seconds": 0.0, "fallback_count": 0}

    def put_item(item):
        while not stop_event.is_set():
            try:
                result_queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def producer():
        capture = None
        try:
            capture = cv2.VideoCapture(video_path)
            for scene_index, scene in enumerate(scenes):
                if stop_event.is_set():
                    break
                scene_started = time.perf_counter()
                try:
                    if capture is None or not capture.isOpened():
                        raise RuntimeError("Prefetch VideoCapture could not open the temporary video")
                    scene_max_frames = max_frames
                    if max_frames_by_scene is not None and scene_index < len(max_frames_by_scene):
                        scene_max_frames = max(1, min(5, int(max_frames_by_scene[scene_index])))
                    keyframes = sample_keyframes_for_scene(
                        capture,
                        scene,
                        base_res=base_res,
                        max_frames=scene_max_frames,
                    )
                    item = (scene_index, keyframes or [], None)
                except Exception as exc:
                    item = (scene_index, [], str(exc)[:300])
                finally:
                    stats["worker_seconds"] += time.perf_counter() - scene_started
                if not put_item(item):
                    break
        except Exception as exc:
            # Keep the main analysis alive; missing entries are recomputed synchronously.
            print("⚠️ [Frame prefetch] Worker error: " + str(exc)[:300])
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            if not stop_event.is_set():
                put_item((None, None, None))

    worker = threading.Thread(
        target=producer,
        name="VCE-KeyframePrefetch",
        daemon=True,
    )
    worker.start()
    return result_queue, stop_event, worker, stats


'''

def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {label}; found {count}. No files changed.")
    return source.replace(old, new, 1)


def patch_helper(source: str) -> str:
    if MARKER in source:
        if "_vce_start_bounded_keyframe_prefetch" not in source:
            raise ValueError("Partial bounded-prefetch patch detected; refusing to continue.")
        return source
    pattern = re.compile(r"(?m)^def compute_audio_features_for_scene\(")
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            "Could not uniquely find compute_audio_features_for_scene() to place prefetch helper; "
            f"found {len(matches)}. No files changed."
        )
    match = matches[0]
    return source[:match.start()] + HELPER + source[match.start():]


def patch_scene_loop(source: str) -> str:
    # Start the prefetch producer immediately before the main scene loop, after
    # the first-frame image has already been read from the existing capture.
    pattern = re.compile(r"(?m)^(?P<i>[ \t]*)for idx, scene in enumerate\(scenes_raw\):[ \t]*$")
    matches = list(pattern.finditer(source))
    if len(matches) < 2:
        raise ValueError(
            "Expected both the skip-mode and normal scene loops; "
            f"found {len(matches)} occurrences. No files changed."
        )
    match = matches[-1]
    indent = match.group("i")
    child = indent + "    "
    insert = (
        indent + "_vce_frame_prefetch_queue = None\n"
        + indent + "_vce_frame_prefetch_stop = None\n"
        + indent + "_vce_frame_prefetch_thread = None\n"
        + indent + "_vce_frame_prefetch_stats = {\"worker_seconds\": 0.0, \"wait_seconds\": 0.0, \"fallback_count\": 0}\n"
        + indent + "if not skip_visual and run_vlm:\n"
        + child + "(\n"
        + child + "    _vce_frame_prefetch_queue,\n"
        + child + "    _vce_frame_prefetch_stop,\n"
        + child + "    _vce_frame_prefetch_thread,\n"
        + child + "    _vce_frame_prefetch_stats,\n"
        + child + ") = _vce_start_bounded_keyframe_prefetch(\n"
        + child + "    temp_path,\n"
        + child + "    scenes_raw,\n"
        + child + "    base_res=vlm_resolution,\n"
        + child + "    max_frames=keyframes_per_scene,\n"
        + child + "    max_frames_by_scene=[_vce_adaptive_keyframe_limit(s, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene for s in scenes_raw],\n"
        + child + "    queue_size=1,\n"
        + child + ")\n"
        + "\n"
    )
    source = source[:match.start()] + insert + source[match.start():]

    call_pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)keyframes = sample_keyframes_for_scene\(\s*\n"
        r"(?P<j>[ \t]*)cap,\s*\n"
        r"(?P=j)scene,\s*\n"
        r"(?P=j)base_res=vlm_resolution,\s*\n"
        r"(?P=j)max_frames=(?P<maxexpr>keyframes_per_scene|effective_keyframes),\s*\n"
        r"(?P=i)\)[ \t]*$"
    )
    call_matches = list(call_pattern.finditer(source))
    if len(call_matches) != 1:
        raise ValueError(
            "Could not uniquely find the scene keyframe extraction call; "
            f"found {len(call_matches)}. No files changed."
        )
    call = call_matches[0]
    i = call.group("i")
    j = call.group("j")
    maxexpr = call.group("maxexpr")
    replacement = (
        i + "if _vce_frame_prefetch_queue is not None:\n"
        + j + "_vce_pf_wait_started = time.perf_counter()\n"
        + j + "_vce_pf_scene_index, _vce_pf_keyframes, _vce_pf_error = _vce_frame_prefetch_queue.get()\n"
        + j + "_vce_frame_prefetch_stats[\"wait_seconds\"] += time.perf_counter() - _vce_pf_wait_started\n"
        + j + "if _vce_pf_scene_index is None:\n"
        + j + "    # Producer ended early; fall back synchronously for this and subsequent scenes.\n"
        + j + "    _vce_frame_prefetch_stats[\"fallback_count\"] += 1\n"
        + j + "    _vce_frame_prefetch_queue = None\n"
        + j + "    keyframes = sample_keyframes_for_scene(\n"
        + j + "        cap,\n"
        + j + "        scene,\n"
        + j + "        base_res=vlm_resolution,\n"
        + j + f"        max_frames={maxexpr},\n"
        + j + "    )\n"
        + j + "elif _vce_pf_scene_index != idx:\n"
        + j + "    raise RuntimeError(\n"
        + j + "        f\"Bounded keyframe prefetch order mismatch: expected scene {idx}, \"\n"
        + j + "        f\"received scene {_vce_pf_scene_index}\"\n"
        + j + "    )\n"
        + j + "elif _vce_pf_error is not None:\n"
        + j + "    _vce_frame_prefetch_stats[\"fallback_count\"] += 1\n"
        + j + "    print(\"⚠️ [Frame prefetch] Синхронный fallback: \" + str(_vce_pf_error)[:300])\n"
        + j + "    keyframes = sample_keyframes_for_scene(\n"
        + j + "        cap,\n"
        + j + "        scene,\n"
        + j + "        base_res=vlm_resolution,\n"
        + j + f"        max_frames={maxexpr},\n"
        + j + "    )\n"
        + j + "else:\n"
        + j + "    keyframes = _vce_pf_keyframes or []\n"
        + i + "else:\n"
        + j + "    keyframes = sample_keyframes_for_scene(\n"
        + j + "        cap,\n"
        + j + "        scene,\n"
        + j + "        base_res=vlm_resolution,\n"
        + j + f"        max_frames={maxexpr},\n"
        + j + "    )"
    )
    return source[:call.start()] + replacement + source[call.end():]


def patch_cleanup(source: str) -> str:
    marker = "        # VCE parallel ASR cleanup: do not delete the source while Whisper's worker reads it.\n"
    if "VCE bounded keyframe prefetch cleanup" in source:
        return source
    cleanup = (
        "        # VCE bounded keyframe prefetch cleanup: stop and join before deleting temp_path.\n"
        "        _vce_pf_stop = locals().get(\"_vce_frame_prefetch_stop\")\n"
        "        if _vce_pf_stop is not None:\n"
        "            _vce_pf_stop.set()\n"
        "        _vce_pf_worker = locals().get(\"_vce_frame_prefetch_thread\")\n"
        "        if _vce_pf_worker is not None:\n"
        "            _vce_pf_worker.join()\n"
    )
    return replace_once(source, marker, cleanup + marker, "parallel ASR cleanup marker")




def patch_diagnostics(source: str) -> str:
    if "frame_prefetch_worker_seconds" in source:
        return source
    metric = (
        '                "ffmpeg_ocr_seconds": round(float(locals().get("ffmpeg_ocr_infer_time", 0.0) or 0.0), 3),\n'
    )
    addition = (
        '                "frame_prefetch_worker_seconds": round(float((locals().get("_vce_frame_prefetch_stats") or {}).get("worker_seconds", 0.0) or 0.0), 3),\n'
        '                "frame_prefetch_wait_seconds": round(float((locals().get("_vce_frame_prefetch_stats") or {}).get("wait_seconds", 0.0) or 0.0), 3),\n'
        '                "frame_prefetch_fallback_count": int((locals().get("_vce_frame_prefetch_stats") or {}).get("fallback_count", 0) or 0),\n'
    )
    return replace_once(source, metric, metric + addition, "stage timing diagnostics")


def patch_engine(source: str) -> str:
    ast.parse(source, filename=ENGINE_NAME)
    if MARKER in source:
        required = (
            "_vce_start_bounded_keyframe_prefetch",
            "_vce_frame_prefetch_queue.get()",
            "VCE bounded keyframe prefetch cleanup",
        )
        missing = [term for term in required if term not in source]
        if missing:
            raise ValueError("Partial bounded-prefetch patch; missing: " + ", ".join(missing))
        return source
    if PARALLEL_MARKER not in source:
        raise ValueError(
            "Install the parallel CPU-ASR/GPU-VLM patch first; no files changed."
        )
    if "VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1" not in source:
        raise ValueError(
            "The expected Metal-memory/media-recovery patch is not present; no files changed."
        )
    source = patch_helper(source)
    source = patch_scene_loop(source)
    source = patch_cleanup(source)
    source = patch_diagnostics(source)
    ast.parse(source, filename=ENGINE_NAME)
    return source


def prepare(project: Path):
    engine_path = project / ENGINE_NAME
    settings_path = project / SETTINGS_NAME
    if not engine_path.is_file() or not settings_path.is_file():
        missing = [str(path) for path in (engine_path, settings_path) if not path.is_file()]
        raise FileNotFoundError("Missing required file(s): " + ", ".join(missing))
    old_engine = engine_path.read_text(encoding="utf-8")
    # Validate settings too, but deliberately leave user preferences unchanged.
    import json
    json.loads(settings_path.read_text(encoding="utf-8"))
    new_engine = patch_engine(old_engine)
    ast.parse(new_engine, filename=str(engine_path))
    return engine_path, old_engine, new_engine


def atomic_write(path: Path, content: str) -> None:
    temp = path.with_name(path.name + ".bounded_prefetch.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true", help="Show diff without writing")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()

    try:
        engine_path, old_engine, new_engine = prepare(project)
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("No files changed.")
        return 2

    if old_engine == new_engine:
        print("Bounded keyframe prefetch patch is already installed.")
        return 0

    diff_lines = list(difflib.unified_diff(
        old_engine.splitlines(keepends=True),
        new_engine.splitlines(keepends=True),
        fromfile=str(engine_path),
        tofile=str(engine_path) + " (bounded prefetch)",
    ))
    print("".join(diff_lines[:320]))
    if len(diff_lines) > 320:
        print(f"... ({len(diff_lines) - 320} additional diff lines omitted)")
    print("\nQueue depth: 2 scenes maximum; one producer thread; order-preserving fallback.")

    if args.dry_run:
        print("\nDRY RUN: one file prepared; nothing written.")
        return 0

    backup = engine_path.with_name(
        engine_path.name + ".before_bounded_prefetch_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
    )
    try:
        shutil.copy2(engine_path, backup)
        atomic_write(engine_path, new_engine)
        ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
    except Exception:
        engine_path.write_text(old_engine, encoding="utf-8")
        print("ERROR after writing: original engine restored; backup retained.")
        raise

    print("\n✅ Bounded keyframe prefetch installed.")
    print(f"Backup: {backup}")
    print("The prefetch worker will be stopped and joined before temporary video cleanup.")
    print("Restart VideoContext Engine before testing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
