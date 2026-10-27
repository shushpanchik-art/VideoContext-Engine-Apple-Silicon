#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely add an evidence-gated final-title text guard to an installed VCE engine.

The guard only edits user-facing final-scene text when source OCR/visual description
already confirms THE END or КОНЕЦ. It never edits source segments, summaries, or
non-final scenes. Run --dry-run first.
"""
from __future__ import annotations

import argparse
import inspect
import shutil
import sys
from datetime import datetime
from pathlib import Path

ENGINE_NAME = "VideoContextEngine_v3.19.py"
HELPER_NAME = "_vce_v5_repair_final_scene_artifacts"
CALL_MARKER = "parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)"
CALL_GUARD = f"parsed = {HELPER_NAME}(parsed, segments)"


def repair_final_scene_artifacts(payload, segments):
    """Repair garbled final-title fields only with source OCR/visual confirmation."""
    import re

    if not isinstance(payload, dict) or not isinstance(segments, list) or not segments:
        return payload

    valid = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        try:
            sid = int(segment.get("scene_id", index + 1))
            start = float(segment.get("start", 0) or 0)
            end = float(segment.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        valid.append((end, start, sid, segment))
    if not valid:
        return payload
    _end, _start, last_id, last_scene = max(valid, key=lambda item: (item[0], item[1], item[2]))

    ocr = str(last_scene.get("ocr_text") or "")
    visual = str(last_scene.get("visual_description") or "")
    visible_source = ocr + "\n" + visual
    marker = re.search(r"(?i)\bTHE\s+END\b|\bКОНЕЦ\b", visible_source)
    if marker is None:
        return payload

    title = "THE END" if re.search(r"(?i)\bTHE\s+END\b", marker.group(0)) else "КОНЕЦ"
    clean_text = f"Финальная заставка с текстом «{title}»."
    corrupt_re = re.compile(r"(?i)\bAND\s+JU\b|\bNC\s+END\b|(?<!\w)D_(?!\w)")
    marker_channels = []
    if re.search(r"(?i)\bTHE\s+END\b|\bКОНЕЦ\b", ocr):
        marker_channels.append("ocr")
    if re.search(r"(?i)\bTHE\s+END\b|\bКОНЕЦ\b", visual):
        marker_channels.append("visual")
    if not marker_channels:
        return payload

    repairs = 0
    key_points = payload.get("key_points")
    if isinstance(key_points, list):
        for item in key_points:
            if not isinstance(item, dict):
                continue
            try:
                sid = int(item.get("scene_id"))
            except (TypeError, ValueError):
                continue
            text = str(item.get("text") or item.get("description") or "")
            if sid == last_id and corrupt_re.search(text):
                item["text"] = clean_text
                item.pop("description", None)
                item["evidence"] = list(marker_channels)
                repairs += 1

    enrichment = payload.get("scene_enrichment")
    if isinstance(enrichment, list):
        for item in enrichment:
            if not isinstance(item, dict):
                continue
            try:
                sid = int(item.get("scene_id"))
            except (TypeError, ValueError):
                continue
            if sid != last_id:
                continue
            fields = [
                str(item.get("topic") or ""),
                str(item.get("improved_description") or ""),
            ]
            points = item.get("key_points")
            if isinstance(points, list):
                fields.extend(str(point or "") for point in points)
            if not any(corrupt_re.search(field) for field in fields):
                continue
            item["topic"] = "Завершение видео"
            item["improved_description"] = clean_text
            item["key_points"] = [f"Видео завершается финальной заставкой «{title}»."]
            repairs += 1

    if repairs:
        print(f"🛡️ [Text LLM quality guard] Исправлены подтверждённые артефакты последней заставки: {repairs}.")
    return payload


def patch_source(source: str) -> tuple[str, bool]:
    """Return patched source and whether a new patch was applied; no file writes."""
    helper_marker = f"def {HELPER_NAME}("
    has_helper = helper_marker in source
    has_call = CALL_GUARD in source
    if has_helper and has_call:
        return source, False
    if has_helper != has_call:
        raise ValueError("Частично установлен quality guard; восстановите backup перед продолжением.")
    if source.count(CALL_MARKER) != 1:
        raise ValueError(
            "Не найден единственный совместимый маркер "
            + repr(CALL_MARKER)
            + "; версия движка отличается. Изменения не записаны."
        )
    function_marker = "\ndef run_text_llm_postprocess("
    if source.count(function_marker) != 1:
        raise ValueError("Не найден единственный run_text_llm_postprocess; изменения не записаны.")

    helper_source = inspect.getsource(repair_final_scene_artifacts)
    helper_source = helper_source.replace(
        "def repair_final_scene_artifacts(",
        f"def {HELPER_NAME}(",
        1,
    )
    patched = source.replace(function_marker, "\n\n" + helper_source + "\n\n" + function_marker, 1)
    patched = patched.replace(CALL_MARKER, CALL_MARKER + "\n        " + CALL_GUARD, 1)
    compile(patched, ENGINE_NAME, "exec")
    return patched, True


def main():
    parser = argparse.ArgumentParser(description="Add an evidence-gated final-scene quality guard to local VCE.")
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true", help="Validate patch applicability without writing files.")
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    engine_path = project / ENGINE_NAME
    if not engine_path.is_file():
        print(f"ERROR: engine not found: {engine_path}", file=sys.stderr)
        return 2
    try:
        source = engine_path.read_text(encoding="utf-8")
        patched, changed = patch_source(source)
        if not changed:
            print("Quality guard is already installed; no changes needed.")
            return 0
        if args.dry_run:
            print("DRY RUN OK: evidence-gated final-title quality guard can be applied.")
            print("Only the Text LLM postprocess function is changed; source scenes/settings remain untouched.")
            print("No files were written.")
            return 0

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = engine_path.with_name(engine_path.name + f".backup-quality-guard-{stamp}")
        shutil.copy2(engine_path, backup)
        temp = engine_path.with_name(engine_path.name + ".quality-guard.tmp")
        temp.write_text(patched, encoding="utf-8")
        compile(temp.read_text(encoding="utf-8"), str(temp), "exec")
        temp.replace(engine_path)
        print("Quality guard applied.")
        print(f"Backup: {backup}")
        print("Restart VideoContext Engine before the next analysis.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
