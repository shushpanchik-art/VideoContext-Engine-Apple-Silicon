#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR-grounded consistency repair for VideoContext Engine v3.19 extended builds.

Repairs only known variants when the canonical identifier is visibly confirmed by OCR.
Raw transcript/OCR/visual descriptions and original_summary are not modified.
Creates timestamped backups and is safe to re-run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

MARKER = "VCE_NAME_CONSISTENCY_V2"
SETTING_KEY = "text_llm_name_reconciliation_prompt"

CANONICALIZER_SOURCE = r'''# VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2
def _vce_canonicalize_confirmed_standard_names(payload: Dict[str, Any], segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Only normalize a known alias after OCR confirms the canonical identifier.
    import re as _vce_re
    if not isinstance(payload, dict):
        return payload
    ocr_text = " ".join(str(seg.get("ocr_text", "") or "") for seg in segments if isinstance(seg, dict))
    canonical = "СП 1.13130.2020"
    if not _vce_re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", ocr_text):
        return payload
    aliases = (
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)"),
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*130\s*\.\s*2020(?!\d)"),
    )
    replacement_count = 0
    def _replace(value: Any) -> Any:
        nonlocal replacement_count
        if isinstance(value, str):
            result = value
            for pattern in aliases:
                result, count = pattern.subn(canonical, result)
                replacement_count += count
            return result
        if isinstance(value, list):
            return [_replace(item) for item in value]
        if isinstance(value, dict):
            return {key: _replace(item) for key, item in value.items()}
        return value
    # Generated analytics only. original_summary and raw evidence are deliberately excluded.
    for field in ("executive_summary", "topics", "key_points", "entities", "scene_enrichment"):
        if field in payload:
            payload[field] = _replace(payload[field])
    if replacement_count:
        print(f"🧭 [ASR/OCR] Исправлено {replacement_count} вариантов обозначения на {canonical} по подтверждённому OCR.")
    return payload
'''

INSTRUCTION_LINE = (
    '            "Согласование названий и нормативов: если читаемый OCR связанной сцены подтверждает точное написание, '
    'используй его единообразно во всех производных полях executive_summary, topics, key_points, entities и scene_enrichment. '
    'Перед возвратом JSON сверь эти поля между собой. Например, OCR «СП 1.13130.2020» подтверждает каноническую форму; '
    'вариант ASR «СП 1.13.132.020» исправляй только в производных полях. Не изменяй audio_transcript, ocr_text, '
    'visual_description, таймкоды или original_summary. "\n'
)

GLOBAL_SUMMARY_BLOCK = r'''        # VCE_NAME_CONSISTENCY_V2_GLOBAL_SUMMARY
        import re as _vce_global_std_re
        _vce_global_std_ocr = " ".join(
            str(_vce_std_seg.get("ocr_text", "") or "")
            for _vce_std_seg in final_segments
            if isinstance(_vce_std_seg, dict)
        )
        if _vce_global_std_re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce_global_std_ocr):
            for _vce_std_alias in (
                r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)",
                r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*130\s*\.\s*2020(?!\d)",
            ):
                global_summary_text = _vce_global_std_re.sub(_vce_std_alias, "СП 1.13130.2020", str(global_summary_text or ""))
'''

def require_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Не удалось изменить {label}: ожидался один якорь, найдено {count}.")
    return text.replace(old, new, 1)

def resolve_source(project: Path) -> Path:
    direct = project / "VideoContextEngine_v3.19.py"
    if direct.is_file():
        return direct
    skip = {".venv", "venv", "__pycache__", ".git", "site-packages"}
    hits = [p for p in project.rglob("VideoContextEngine_v3.19.py") if not any(part in skip for part in p.parts)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise FileNotFoundError(f"Не найден VideoContextEngine_v3.19.py в {project}")
    raise RuntimeError("Найдено несколько исходников; укажи --project на каталог с одним исходником.")

def prepare_changes(project: Path) -> Tuple[Dict[Path, str], Dict[Path, str]]:
    source = resolve_source(project)
    py_original = source.read_text(encoding="utf-8")
    settings_path = source.with_name("videocontext_settings.json")
    settings_original = settings_path.read_text(encoding="utf-8") if settings_path.is_file() else None

    installed = (
        MARKER in py_original
        and "VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2" in py_original
        and "_vce_canonicalize_confirmed_standard_names(parsed, segments)" in py_original
    )
    py = py_original
    if not installed:
        anchors = {
            "JSON post-processing call": '        parsed = reconcile_text_llm_result(parsed, segments, existing_summary)\n',
            "Text LLM instruction hook": '            + coverage_note\n',
            "global summary insertion point": '        # TEXT_LLM_POSTPROCESS_STAGE\n',
        }
        missing = [name for name, anchor in anchors.items() if anchor not in py]
        if missing:
            raise RuntimeError("Не найдены точки подключения: " + "; ".join(missing) + ". Файлы не изменены.")
        if "def run_text_llm_postprocess(" not in py:
            raise RuntimeError("Не найдена функция run_text_llm_postprocess. Файлы не изменены.")

        if "VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2" not in py:
            py = require_once(py, "def run_text_llm_postprocess(", CANONICALIZER_SOURCE + "\n\ndef run_text_llm_postprocess(", "canonicalizer helper")
        call_anchor = '        parsed = reconcile_text_llm_result(parsed, segments, existing_summary)\n'
        if "_vce_canonicalize_confirmed_standard_names(parsed, segments)" not in py:
            py = require_once(py, call_anchor, call_anchor + '        parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)\n', "canonicalizer call")
        if "Согласование названий и нормативов: если читаемый OCR" not in py:
            py = require_once(py, '            + coverage_note\n', INSTRUCTION_LINE + '            + coverage_note\n', "cross-field consistency instruction")
        if "VCE_NAME_CONSISTENCY_V2_GLOBAL_SUMMARY" not in py:
            py = require_once(py, '        # TEXT_LLM_POSTPROCESS_STAGE\n', GLOBAL_SUMMARY_BLOCK + '\n        # TEXT_LLM_POSTPROCESS_STAGE\n', "global summary normalization")

        compile(py, str(source), "exec")

    changes: Dict[Path, str] = {}
    originals: Dict[Path, str] = {source: py_original}
    if py != py_original:
        changes[source] = py

    if settings_original is not None:
        try:
            settings = json.loads(settings_original)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Текущий videocontext_settings.json невалиден: {exc}") from exc
        if isinstance(settings, dict) and isinstance(settings.get(SETTING_KEY), str):
            current = settings[SETTING_KEY]
            if "VCE_NAME_CONSISTENCY_V2" not in current:
                addendum = (
                    "Дополнение VCE_NAME_CONSISTENCY_V2: при чётком OCR используй одно и то же каноническое написание "
                    "во всех производных полях, включая topics и entities. Например, подтверждённое OCR «СП 1.13130.2020» "
                    "должно заменить «СП 1.13.132.020» только в производных полях; исходные transcript/OCR/visual_description, "
                    "таймкоды и original_summary не изменяй."
                )
                settings[SETTING_KEY] = current.rstrip() + "\n\n" + addendum
                settings_new = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
                json.loads(settings_new)
                changes[settings_path] = settings_new
                originals[settings_path] = settings_original

    return changes, originals

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="~/VideoContext-Engine", help="Папка проекта (по умолчанию ~/VideoContext-Engine)")
    parser.add_argument("--dry-run", action="store_true", help="Проверить применимость патча без записи файлов")
    args = parser.parse_args()
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        print(f"ОШИБКА: каталог проекта не найден: {project}", file=sys.stderr)
        return 2
    try:
        changes, originals = prepare_changes(project)
    except Exception as exc:
        print(f"ОШИБКА: {exc}\nФайлы не изменены.", file=sys.stderr)
        return 2
    if not changes:
        print("Патч уже применён; изменений не требуется.")
        return 0
    print("Проверки пройдены. Будут изменены:")
    for path in changes:
        print(f"  - {path}")
    if args.dry_run:
        print("Режим dry-run: файлы не изменены.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: Dict[Path, Path] = {}
    try:
        for path, original in originals.items():
            backup = path.with_name(path.name + f".bak_name_consistency_v2_{stamp}")
            shutil.copy2(path, backup)
            backups[path] = backup
        for path, content in changes.items():
            temp = path.with_name(path.name + f".tmp_name_consistency_v2_{stamp}")
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, path)
    except Exception as exc:
        for path, original in originals.items():
            try:
                path.write_text(original, encoding="utf-8")
            except Exception:
                pass
        print(f"ОШИБКА при записи: {exc}. Оригиналы восстановлены там, где это удалось.", file=sys.stderr)
        print("Резервные копии:", *(str(p) for p in backups.values()), sep="\n  ", file=sys.stderr)
        return 3

    print("\nГотово. Перезапусти VideoContext Engine. Созданы резервные копии:")
    for backup in backups.values():
        print(f"  - {backup}")
    print("Исправления выполняются только при подтверждённом OCR; исходные данные не переписываются.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
