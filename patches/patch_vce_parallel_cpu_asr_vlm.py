#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch VCE v3.19 to overlap CPU Whisper inference with GPU VLM scene analysis.

The previous implementation awaited the complete Whisper transcription before
loading VLM. This patch schedules Whisper inference in a worker thread, lets VLM
load and analyze scenes while ASR runs, then attaches the completed transcript and
audio features to scene records before summary generation. Audio transcript is not
added to per-scene VLM prompts in this mode because it may not yet be ready; the
full transcript is retained in each scene and in the final summary context.

The patch also routes the summary-image inference through the existing VLM Metal
memory fallback and waits for a pending ASR worker in analyze_video's cleanup path
before a temporary video file may be removed.

Usage:
  python3 patch_vce_parallel_cpu_asr_vlm.py --project ~/VideoContext-Engine --dry-run
  python3 patch_vce_parallel_cpu_asr_vlm.py --project ~/VideoContext-Engine
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import shutil
import textwrap
from datetime import datetime
from pathlib import Path

ENGINE_NAME = "VideoContextEngine_v3.19.py"
SETTINGS_NAME = "videocontext_settings.json"
MARKER = "# VCE_PARALLEL_CPU_ASR_GPU_VLM_V1"
ENDPOINT = '@app.post("/api/v1/analyze")'

HELPER = r'''# VCE_PARALLEL_CPU_ASR_GPU_VLM_V1
def _vce_transcribe_audio_safe_timed(*args, **kwargs):
    """Run the existing safe ASR wrapper and return its actual worker wall time."""
    started = time.perf_counter()
    result = _vce_transcribe_audio_safe(*args, **kwargs)
    elapsed = time.perf_counter() - started
    if isinstance(result, tuple) and len(result) == 2:
        return result[0], result[1], elapsed
    raise TypeError("_vce_transcribe_audio_safe must return (audio_result, audio_error)")


'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {label}; found {count}. No files changed.")
    return source.replace(old, new, 1)


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _patch_whisper_block(source: str) -> str:
    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Could not find async def analyze_video().")
    # Match the full current ASR block up to, but not including, the VLM-load branch.
    pattern = re.compile(
        r"(?ms)^(?P<i>[ \t]*)whisper_segments: List\[Dict\[str, Any\]\] = \[\][ \t]*\n"
        r"(?P<body>.*?)^(?P=i)if run_vlm:[ \t]*$"
    )
    matches = [m for m in pattern.finditer(source, route_start)]
    if len(matches) != 1:
        raise ValueError(
            "Could not uniquely identify the Whisper-to-VLM boundary in analyze_video(); "
            f"found {len(matches)} candidates. No files changed."
        )
    m = matches[0]
    old_block = m.group(0)
    if "_vce_whisper_task" in old_block:
        raise ValueError("A Whisper background task is already present but the patch marker is missing.")
    i = m.group("i")
    d = i + "    "
    dd = d + "    "
    ddd = dd + "    "
    block = (
        i + "whisper_segments: List[Dict[str, Any]] = []\n"
        + i + "_vce_whisper_task = None\n"
        + i + "_vce_whisper_model = None\n"
        + i + "whisper_audio_error = None\n"
        + i + "whisper_infer_time = 0.0\n\n"
        + i + "if not skip_audio:\n"
        + d + 'if enable_mlx_whisper or whisper_acceleration or str(whisper_device).casefold() != "cpu":\n'
        + dd + 'print("⚙️ [Parallel ASR/VLM] Для параллельного режима Whisper принудительно переводится на CPU; MLX Whisper/MPS выключены для этой задачи.")\n'
        + d + "enable_mlx_whisper = False\n"
        + d + "whisper_acceleration = False\n"
        + d + 'whisper_device = "cpu"\n'
        + d + 'set_job_progress(job_id, "whisper", None, "Whisper: распознаём речь и временные метки (параллельно с VLM)", indeterminate=True, stage_index=2, stage_count=6)\n'
        + d + 'print(f"🧮 Whisper device: {whisper_device} (acceleration={whisper_acceleration})")\n'
        + d + 'if RAM_MODE == "ram+":\n'
        + dd + "if target_whisper in WHISPER_CACHE:\n"
        + ddd + "w_model = None if enable_mlx_whisper else WHISPER_CACHE[target_whisper]\n"
        + ddd + "whisper_load_time = 0.0\n"
        + ddd + 'print(f"🎙️ Whisper \'{target_whisper}\' déjà en RAM (cache).")\n'
        + dd + "else:\n"
        + ddd + 'print(f"🎙️ Chargement Whisper \'{target_whisper}\' en RAM (ram+)...")\n'
        + ddd + "t0 = time.time()\n"
        + ddd + "w_model = _vce_load_whisper_model(target_whisper, enable_mlx_whisper, whisper_acceleration)\n"
        + ddd + "if not enable_mlx_whisper:\n"
        + ddd + "    WHISPER_CACHE[target_whisper] = w_model\n"
        + ddd + "whisper_load_time = time.time() - t0\n"
        + ddd + 'print(f"✅ Whisper \'{target_whisper}\' chargé en {whisper_load_time:.2f}s (ram+)")\n'
        + d + 'else:\n'
        + dd + 'print(f"🎙️ Chargement Whisper \'{target_whisper}\' (ram-)...")\n'
        + dd + "t0 = time.time()\n"
        + dd + "w_model = _vce_load_whisper_model(target_whisper, enable_mlx_whisper, whisper_acceleration)\n"
        + dd + "whisper_load_time = time.time() - t0\n"
        + dd + 'print(f"✅ Whisper \'{target_whisper}\' chargé en {whisper_load_time:.2f}s (ram-)")\n'
        + d + "if not enable_mlx_whisper:\n"
        + dd + "w_model = w_model.to(whisper_device)\n"
        + d + "_vce_whisper_model = w_model\n"
        + d + "_vce_whisper_task = asyncio.create_task(asyncio.to_thread(\n"
        + dd + "_vce_transcribe_audio_safe_timed,\n"
        + dd + "w_model,\n"
        + dd + "temp_path,\n"
        + dd + "model_name=target_whisper,\n"
        + dd + "use_mlx=enable_mlx_whisper,\n"
        + dd + "word_timestamps=word_timestamps,\n"
        + dd + "condition_on_previous_text=False,\n"
        + dd + "temperature=0.0,\n"
        + dd + "compression_ratio_threshold=1.8,\n"
        + d + "))\n"
        + d + 'print("🚀 Whisper инференс запущен в фоне; VLM начнёт анализ сцен, не ожидая полной транскрипции.")\n'
        + i + "else:\n"
        + d + "whisper_segments = []\n\n"
        + i + "if run_vlm:"
    )
    return source[:m.start()] + block + source[m.end():]


def _patch_summary_memory_fallback(source: str) -> str:
    route_start = source.find("async def analyze_video(")
    route_end = source.find("\n@app.", route_start + 10)
    if route_end < 0:
        route_end = len(source)
    route = source[route_start:route_end]
    pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)raw_summary = await asyncio\.to_thread\(\s*\n"
        r"(?P<j>[ \t]*)vlm_engine\.describe_scene,\s*\n"
        r"(?P=j)\[first_frame_img\],\s*\n"
        r"(?P=j)full_summary_prompt,\s*\n"
        r"(?P=j)max_tokens=vlm_max_tokens_summary,\s*\n"
        r"(?P=i)\)[ \t]*$"
    )
    matches = list(pattern.finditer(route))
    if len(matches) != 1:
        if "raw_summary = await asyncio.to_thread(" in route and "_vce_describe_scene_with_memory_fallback" in route[route.find("raw_summary = await asyncio.to_thread("):]:
            return source
        raise ValueError(f"Could not uniquely identify the global-summary VLM call; found {len(matches)}. No files changed.")
    m = matches[0]
    i, j = m.group("i"), m.group("j")
    new = (
        i + "raw_summary = await asyncio.to_thread(\n"
        + j + "_vce_describe_scene_with_memory_fallback,\n"
        + j + "vlm_engine,\n"
        + j + "[first_frame_img],\n"
        + j + "full_summary_prompt,\n"
        + j + "vlm_max_tokens_summary,\n"
        + i + ")"
    )
    patched_route = route[:m.start()] + new + route[m.end():]
    return source[:route_start] + patched_route + source[route_end:]

def _patch_scene_backfill(source: str) -> str:
    pattern = re.compile(r'(?m)^(?P<i>[ \t]*)set_job_progress\(job_id, "summary", None, "Формируем сводку по всем сценам"')
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError("Could not uniquely find the summary-stage boundary for transcript backfill. No files changed.")
    match = matches[0]
    indent = match.group("i")
    raw_backfill = r'''
# Join the concurrent Whisper result only after scene VLM/OCR work, then backfill
# audio fields. This preserves per-scene transcript alignment without blocking VLM.
if _vce_whisper_task is not None:
    audio_res, whisper_audio_error, whisper_infer_time = await _vce_whisper_task
    if whisper_audio_error:
        skip_audio = True
        audio_auto_skipped = True
        audio_track_available = False
        audio_probe_message = whisper_audio_error
        whisper_segments = []
        print("⚠️ [Audio] " + audio_probe_message)
    else:
        audio_res = audio_res or {}
        whisper_segments = audio_res.get("segments", []) or []
    set_job_progress(
        job_id, "whisper_done", 75,
        f"Whisper завершён: фрагментов речи {len(whisper_segments)}",
        stage_index=4, stage_count=6,
        transcript_segments=len(whisper_segments),
    )
    for _vce_scene_index, _vce_segment in enumerate(final_segments):
        _vce_scene = scenes_raw[_vce_scene_index]
        _vce_audio_text = (
            transcript_for_scene(_vce_scene, whisper_segments)
            if not skip_audio and whisper_segments else ""
        )
        _vce_segment["audio_transcript"] = _vce_audio_text
        if not skip_audio and whisper_segments and enable_audio_features:
            _vce_segment["audio_features"] = compute_audio_features_for_scene(
                _vce_scene, whisper_segments
            )
    if RAM_MODE == "ram-":
        try:
            del _vce_whisper_model
        except UnboundLocalError:
            pass
        try:
            del w_model
        except UnboundLocalError:
            pass
        release_accelerator_memory("after concurrent Whisper inference")
'''
    backfill = textwrap.indent(textwrap.dedent(raw_backfill), indent)
    return source[:match.start()] + backfill + source[match.start():]

def _patch_finally_wait_for_asr(source: str) -> str:
    """Wait for a worker still reading temp_path before analyze_video's cleanup runs."""
    try:
        tree = ast.parse(source, filename=ENGINE_NAME)
    except SyntaxError as exc:
        raise ValueError(f"Could not parse engine before cleanup patch: {exc}") from exc
    fn = next((n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "analyze_video"), None)
    if fn is None:
        raise ValueError("Could not find analyze_video AST for cleanup safety patch.")

    candidates = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        source_lines = source.splitlines()
        final_text = "\n".join(
            "\n".join(source_lines[max(0, stmt.lineno - 1):getattr(stmt, "end_lineno", stmt.lineno)])
            for stmt in node.finalbody
        )
        looks_like_temp_cleanup = (
            ("os.remove" in final_text or ".unlink(" in final_text or "safe_unlink" in final_text)
            and ("temp_path" in final_text or "owns_temp_path" in final_text)
        )
        if looks_like_temp_cleanup:
            candidates.append(node)
    if not candidates:
        raise ValueError(
            "Could not identify analyze_video's finally block that removes the temporary video; "
            "refusing to add a background ASR task without cleanup protection. No files changed."
        )
    # Pick the cleanup try with the widest source span (usually the endpoint-level finally).
    cleanup_try = max(candidates, key=lambda n: getattr(n, "end_lineno", n.lineno) - n.lineno)
    first_stmt = cleanup_try.finalbody[0]
    lines = source.splitlines(keepends=True)
    line_index = first_stmt.lineno - 1
    line = lines[line_index]
    indent = _indent(line)
    child = indent
    grandchild = child + "    "
    greatgrandchild = grandchild + "    "
    injection = (
        child + "# VCE parallel ASR cleanup: do not delete the source while Whisper's worker reads it.\n"
        + child + '_vce_pending_whisper_task = locals().get("_vce_whisper_task")\n'
        + child + "if _vce_pending_whisper_task is not None:\n"
        + grandchild + "try:\n"
        + greatgrandchild + "await _vce_pending_whisper_task\n"
        + grandchild + "except Exception as _vce_whisper_cleanup_error:\n"
        + greatgrandchild + 'print("⚠️ [Parallel Whisper] Worker завершился при очистке: " + str(_vce_whisper_cleanup_error)[:300])\n'
    )
    lines.insert(line_index, injection)
    return "".join(lines)


def patch_engine(source: str) -> str:
    ast.parse(source, filename=ENGINE_NAME)
    if MARKER in source:
        required = (
            "_vce_transcribe_audio_safe_timed",
            "_vce_whisper_task = asyncio.create_task",
            "VCE parallel ASR cleanup",
            "raw_summary = await asyncio.to_thread(",
        )
        missing = [x for x in required if x not in source]
        summary_index = source.find("raw_summary = await asyncio.to_thread(")
        if summary_index < 0 or "_vce_describe_scene_with_memory_fallback" not in source[summary_index:summary_index + 500]:
            missing.append("summary VLM memory fallback")
        if missing:
            raise ValueError("Partial parallel patch detected; missing: " + ", ".join(missing))
        return source
    if "VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1" not in source:
        raise ValueError("Install the Metal-memory/media-recovery patch first; no files changed.")
    if source.count(ENDPOINT) != 1:
        raise ValueError("Expected exactly one analyze endpoint declaration; no files changed.")
    source = replace_once(source, ENDPOINT, HELPER + ENDPOINT, "analyze endpoint insertion point")
    source = _patch_whisper_block(source)
    source = _patch_scene_backfill(source)
    source = _patch_summary_memory_fallback(source)
    source = _patch_finally_wait_for_asr(source)
    ast.parse(source, filename=ENGINE_NAME)
    return source


def prepare(project: Path):
    engine_path = project / ENGINE_NAME
    settings_path = project / SETTINGS_NAME
    if not engine_path.is_file() or not settings_path.is_file():
        missing = [str(p) for p in (engine_path, settings_path) if not p.is_file()]
        raise FileNotFoundError("Missing required file(s): " + ", ".join(missing))
    old_engine = engine_path.read_text(encoding="utf-8")
    old_settings = settings_path.read_text(encoding="utf-8")
    new_engine = patch_engine(old_engine)
    # Keep the previously benchmark-selected policy: CPU Whisper, MLX Whisper off,
    # stage timing on. Do not modify other user settings.
    settings = json.loads(old_settings)
    if not isinstance(settings, dict):
        raise ValueError("Settings root must be a JSON object.")
    settings["whisper_acceleration"] = False
    settings["enable_mlx_whisper"] = False
    settings["enable_stage_timing"] = True
    new_settings = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    ast.parse(new_engine, filename=str(engine_path))
    json.loads(new_settings)
    return engine_path, settings_path, old_engine, old_settings, new_engine, new_settings


def atomic_write(path: Path, content: str) -> None:
    temp = path.with_name(path.name + ".parallel_asr_vlm.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true", help="Show diff without writing")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    try:
        ep, sp, old_e, old_s, new_e, new_s = prepare(project)
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("No files changed.")
        return 2

    changed = [(p, old, new) for p, old, new in ((ep, old_e, new_e), (sp, old_s, new_s)) if old != new]
    if not changed:
        print("Parallel CPU-ASR/GPU-VLM patch is already installed.")
        return 0
    for path, old, new in changed:
        print(f"\n--- {path.name} ---")
        diff = difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=str(path), tofile=str(path) + " (patched)",
        )
        diff_lines = list(diff)
        print("".join(diff_lines[:260]))
        if len(diff_lines) > 260:
            print(f"... ({len(diff_lines) - 260} additional diff lines omitted)")
    if args.dry_run:
        print(f"\nDRY RUN: prepared changes in {len(changed)} file(s); nothing written.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {}
    written = []
    try:
        for path, old, new in changed:
            backup = path.with_name(path.name + f".before_parallel_asr_vlm_{stamp}.bak")
            shutil.copy2(path, backup)
            backups[path] = backup
        for path, old, new in changed:
            atomic_write(path, new)
            written.append(path)
        ast.parse(ep.read_text(encoding="utf-8"), filename=str(ep))
        json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        for path, old, new in changed:
            if path in written:
                path.write_text(old, encoding="utf-8")
        print("ERROR after writing: original files restored; backups retained.")
        raise

    print("\n✅ Parallel CPU-ASR/GPU-VLM patch installed.")
    for path, backup in backups.items():
        print(f"Backup {path.name}: {backup}")
    print("Whisper inference now runs in a background worker while VLM loads/analyzes scenes.")
    print("Per-scene Whisper text/audio features are merged before summary generation.")
    print("VLM scene prompts may not include Whisper context because ASR may still be running; transcript remains in scene results and summary context.")
    print("Restart VideoContext Engine before testing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())