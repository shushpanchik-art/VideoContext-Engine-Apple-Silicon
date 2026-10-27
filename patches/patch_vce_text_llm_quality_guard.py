#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely add a video-agnostic, non-mutating Text LLM output-integrity validator.

The guard checks the VCE output schema and scene IDs. It deliberately does not rewrite
user-facing content or contain video-specific vocabulary. Run --dry-run first.
"""
from __future__ import annotations

import argparse
import inspect
import shutil
import sys
from datetime import datetime
from pathlib import Path

ENGINE_NAME = "VideoContextEngine_v3.19.py"
HELPER_NAME = "_vce_v6_validate_text_llm_payload"
CALL_MARKER = "parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)"
CALL_GUARD = f"parsed = {HELPER_NAME}(parsed, segments)"


def validate_text_llm_payload(payload, segments):
    """Validate generic VCE output invariants without altering content."""
    import re

    flags = []
    if not isinstance(payload, dict):
        print("⚠️ [Text LLM integrity guard] Output is not a JSON object.")
        return payload
    if not isinstance(segments, list) or not segments:
        return payload

    expected_ids = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            flags.append(f"source_scene[{index}]_not_object")
            continue
        try:
            expected_ids.append(int(segment.get("scene_id", index + 1)))
        except (TypeError, ValueError):
            flags.append(f"source_scene[{index}]_invalid_id")
    if len(expected_ids) != len(set(expected_ids)):
        flags.append("source_scene_ids_not_unique")
    expected = set(expected_ids)

    def check_records(field, require_all=False):
        records = payload.get(field)
        if not isinstance(records, list):
            flags.append(f"{field}_not_array")
            return
        seen = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                flags.append(f"{field}[{index}]_not_object")
                continue
            try:
                sid = int(record.get("scene_id"))
                seen.append(sid)
            except (TypeError, ValueError):
                flags.append(f"{field}[{index}]_invalid_scene_id")
        if len(seen) != len(set(seen)):
            flags.append(f"{field}_duplicate_scene_ids")
        unexpected = sorted(set(seen) - expected)
        if unexpected:
            flags.append(f"{field}_unexpected_scene_ids:{unexpected}")
        if require_all:
            missing = sorted(expected - set(seen))
            if missing:
                flags.append(f"{field}_missing_scene_ids:{missing}")

    check_records("key_points", require_all=False)
    check_records("scene_enrichment", require_all=True)

    summary = payload.get("executive_summary")
    if not isinstance(summary, str) or not summary.strip():
        flags.append("executive_summary_empty_or_missing")

    # Generic text-integrity checks only: no language, domain, brand, or title keywords.
    text_values = [summary] if isinstance(summary, str) else []
    for field in ("topics", "entities", "uncertainties"):
        value = payload.get(field)
        if isinstance(value, list):
            text_values.extend(
                item if isinstance(item, str) else str(item)
                for item in value
            )
    for field in ("key_points", "scene_enrichment"):
        value = payload.get(field)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                for key in ("text", "description", "topic", "improved_description"):
                    if isinstance(item.get(key), str):
                        text_values.append(item[key])
                child = item.get("key_points")
                if isinstance(child, list):
                    text_values.extend(str(x) for x in child)
    for index, text in enumerate(text_values):
        if "\ufffd" in text:
            flags.append(f"text[{index}]_unicode_replacement_character")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            flags.append(f"text[{index}]_control_character")

    if flags:
        print(f"⚠️ [Text LLM integrity guard] Найдено структурных/текстовых предупреждений: {len(flags)}; текст не переписывался.")
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

    helper_source = inspect.getsource(validate_text_llm_payload)
    helper_source = helper_source.replace(
        "def validate_text_llm_payload(",
        f"def {HELPER_NAME}(",
        1,
    )
    patched = source.replace(function_marker, "\n\n" + helper_source + "\n\n" + function_marker, 1)
    marker_pos = patched.find(CALL_MARKER)
    line_start = patched.rfind("\n", 0, marker_pos) + 1
    indent = patched[line_start:marker_pos]
    if indent.strip():
        raise ValueError("Не удалось определить отступ вызова Text LLM; изменений не записано.")
    insert_at = marker_pos + len(CALL_MARKER)
    patched = patched[:insert_at] + "\n" + indent + CALL_GUARD + patched[insert_at:]
    compile(patched, ENGINE_NAME, "exec")
    return patched, True


def main():
    parser = argparse.ArgumentParser(description="Add a generic non-mutating Text LLM integrity validator to local VCE.")
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
            print("DRY RUN OK: generic non-mutating output-integrity validator can be applied.")
            print("Only output-schema warnings are added; generated text, source scenes and settings remain untouched.")
            print("No files were written.")
            return 0

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = engine_path.with_name(engine_path.name + f".backup-quality-guard-{stamp}")
        shutil.copy2(engine_path, backup)
        temp = engine_path.with_name(engine_path.name + ".quality-guard.tmp")
        temp.write_text(patched, encoding="utf-8")
        compile(temp.read_text(encoding="utf-8"), str(temp), "exec")
        temp.replace(engine_path)
        print("Generic integrity validator applied.")
        print(f"Backup: {backup}")
        print("Restart VideoContext Engine before the next analysis.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
