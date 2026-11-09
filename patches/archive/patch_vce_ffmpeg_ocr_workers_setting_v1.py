#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix persistence/validation of the FFmpeg OCR worker-count setting in VCE v3.19."""
from __future__ import annotations

import argparse
import ast
import difflib
import re
import shutil
from datetime import datetime
from pathlib import Path

TARGET = Path("VideoContextEngine_v3.19.py")
MARKER = "# VCE_FFMPEG_OCR_WORKERS_SETTING_V1"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Ожидалось одно вхождение {label}; найдено {count}. Файлы не изменены.")
    return source.replace(old, new, 1)


def patch_engine(source: str) -> str:
    if MARKER in source:
        ast.parse(source, filename=str(TARGET))
        return source

    if 'def setting(' not in source and 'setting = ' not in source:
        raise ValueError("Не найден интерфейс setting(...); патчер предназначен для сборки с JSON-настройками.")

    defaults_anchor = '"analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,'
    if '"ffmpeg_ocr_workers": 2' not in source:
        source = replace_once(
            source,
            defaults_anchor,
            defaults_anchor + '\n        "ffmpeg_ocr_workers": 2,',
            "значение по умолчанию ffmpeg_ocr_workers",
        )

    # The settings endpoint validates integer settings against this allowlist/range map.
    if '"ffmpeg_ocr_workers": (1, 8)' not in source:
        ranges = list(re.finditer(r'(?m)^(?P<i>[ \t]*)int_ranges\s*=\s*\{\s*$', source))
        if len(ranges) != 1:
            raise ValueError(f"Ожидался один int_ranges = {{...}}; найдено {len(ranges)}. Файлы не изменены.")
        m = ranges[0]
        child_indent = m.group("i") + "    "
        line_end = source.find("\n", m.start())
        if line_end < 0:
            raise ValueError("Не удалось прочитать блок int_ranges.")
        insertion = child_indent + '"ffmpeg_ocr_workers": (1, 8),\n'
        source = source[:line_end + 1] + insertion + source[line_end + 1:]

    # Persisted setting must also be used as the FastAPI Form default.
    form_pattern = re.compile(
        r'(?m)^(?P<i>[ \t]*ffmpeg_ocr_workers\s*:\s*int\s*=\s*Form\()\s*2\s*,(?P<rest>[^\n]*\bge=1\b[^\n]*\ble=8\b[^\n]*)$'
    )
    matches = list(form_pattern.finditer(source))
    if 'Form(int(setting("ffmpeg_ocr_workers", 2))' not in source:
        if len(matches) != 1:
            raise ValueError(f"Ожидалось одно объявление Form для ffmpeg_ocr_workers; найдено {len(matches)}. Файлы не изменены.")
        m = matches[0]
        source = source[:m.start()] + m.group("i") + 'int(setting("ffmpeg_ocr_workers", 2)),' + m.group("rest") + source[m.end():]

    # Marker is a comment inserted just before the defaults map.
    if MARKER not in source:
        line_match = re.search(r'(?m)^(?P<i>[ \t]*)defaults\s*=\s*\{[ \t]*$', source)
        if not line_match:
            raise ValueError("Не найден блок defaults настроек интерфейса. Файлы не изменены.")
        indent = line_match.group("i")
        source = source[:line_match.start()] + indent + MARKER + "\n" + source[line_match.start():]

    ast.parse(source, filename=str(TARGET))
    required = (
        '"ffmpeg_ocr_workers": 2',
        '"ffmpeg_ocr_workers": (1, 8)',
        'Form(int(setting("ffmpeg_ocr_workers", 2))',
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise ValueError("После исправления отсутствуют обязательные части: " + ", ".join(missing))
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    target = args.project.expanduser().resolve() / TARGET
    if not target.is_file():
        print(f"ОШИБКА: файл не найден: {target}")
        return 2
    original = target.read_text(encoding="utf-8")
    try:
        updated = patch_engine(original)
    except Exception as exc:
        print(f"ОШИБКА: {exc}")
        print("Файлы не изменены.")
        return 2
    if updated == original:
        print("Настройка ffmpeg_ocr_workers уже исправлена; изменений нет.")
        return 0

    diff = difflib.unified_diff(
        original.splitlines(keepends=True), updated.splitlines(keepends=True),
        fromfile=str(target), tofile=str(target) + " (new)",
    )
    print("".join(list(diff)[:200]))
    if args.dry_run:
        print("DRY RUN: подготовлены изменения; файлы не изменены.")
        return 0

    backup = target.with_name(
        target.name + ".before_ffmpeg_ocr_workers_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
    )
    shutil.copy2(target, backup)
    target.write_text(updated, encoding="utf-8")
    try:
        ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except Exception:
        target.write_text(original, encoding="utf-8")
        print(f"ОШИБКА проверки синтаксиса; исходник восстановлен. Резервная копия: {backup}")
        return 3
    print("Исправлено сохранение настройки FFmpeg OCR.")
    print(f"Резервная копия: {backup}")
    print("Перезапусти VideoContext Engine, чтобы загрузить исправление.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
