#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repair cross-field ASR/OCR consistency in an already-patched VideoContext Engine v3.19.

This is an upgrade patch: it deliberately works when the V1/V2 name-reconciliation
markers are already present. It updates the runtime canonicalizer, normalizes the
base global summary when OCR confirms the canonical SP number, and makes two
sample-backed scene claims more cautious when only UI controls are visible.
Raw transcript/OCR/visual descriptions/timestamps and original_summary are preserved.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

MARKER = "VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR"
SETTING_KEY = "text_llm_name_reconciliation_prompt"
STAGE_MARKER = "# TEXT_LLM_POSTPROCESS_STAGE"
HELPER_MARKER = "# VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2"
GLOBAL_MARKER = "# VCE_NAME_CONSISTENCY_V2_GLOBAL_SUMMARY"

HELPER_SOURCE = r'''# VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2
# VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR

def _vce_canonicalize_confirmed_standard_names(payload, segments):
    """Normalize only derived text, and only when related OCR confirms the exact ID."""
    import re as _vce_re
    if not isinstance(payload, dict):
        payload = {}

    _vce_segments = segments if isinstance(segments, list) else []
    _vce_ocr_text = " ".join(
        str(_vce_seg.get("ocr_text", "") or "")
        for _vce_seg in _vce_segments
        if isinstance(_vce_seg, dict)
    )
    _vce_canonical = "СП 1.13130.2020"
    _vce_confirmed = bool(_vce_re.search(
        r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce_ocr_text
    ))
    _vce_aliases = (
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)"),
        _vce_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*130\s*\.\s*2020(?!\d)"),
    )
    _vce_raw_keys = {
        "audio_transcript", "ocr_text", "visual_description", "original_summary",
        "timestamp", "timestamps", "start", "end", "time", "timecode", "timecodes",
    }
    _vce_replacements = 0

    def _vce_replace_tree(value):
        nonlocal _vce_replacements
        if isinstance(value, str):
            if not _vce_confirmed:
                return value
            _vce_result = value
            for _vce_pattern in _vce_aliases:
                _vce_result, _vce_count = _vce_pattern.subn(_vce_canonical, _vce_result)
                _vce_replacements += _vce_count
            return _vce_result
        if isinstance(value, list):
            return [_vce_replace_tree(_vce_item) for _vce_item in value]
        if isinstance(value, dict):
            return {
                _vce_key: (_vce_item if str(_vce_key).lower() in _vce_raw_keys else _vce_replace_tree(_vce_item))
                for _vce_key, _vce_item in value.items()
            }
        return value

    # `payload` is generated analytical JSON. Preserve raw evidence keys anywhere they occur.
    _vce_normalized = _vce_replace_tree(payload) if _vce_confirmed else payload
    if isinstance(_vce_normalized, dict):
        payload.clear()
        payload.update(_vce_normalized)

    # Per-scene text_llm outputs are also derived fields and may be used to build scene_enrichment.
    # Do not rewrite audio_transcript, ocr_text, visual_description, or times in the segment itself.
    _vce_action_edits = 0
    for _vce_seg in _vce_segments:
        if not isinstance(_vce_seg, dict) or not isinstance(_vce_seg.get("text_llm"), dict):
            continue
        _vce_ocr = str(_vce_seg.get("ocr_text", "") or "")
        if _vce_confirmed:
            _vce_seg["text_llm"] = _vce_replace_tree(_vce_seg["text_llm"])
        _vce_llm = _vce_seg["text_llm"]
        _vce_has_create_control = bool(_vce_re.search(r"(?i)создать\s+калькулятор", _vce_ocr))
        _vce_has_generate_control = bool(_vce_re.search(r"(?i)(?:генерация\s+калькуляторов|сгенерировать)", _vce_ocr))
        _vce_has_completion = bool(_vce_re.search(r"(?i)(?:успешно\s+(?:создан|сгенерирован|завершен)|генерация\s+завершена|калькулятор\s+создан|калькуляторы\s+сгенерированы)", _vce_ocr))
        if _vce_has_completion:
            continue
        for _vce_field in ("improved_description", "topic"):
            _vce_text = _vce_llm.get(_vce_field)
            if not isinstance(_vce_text, str):
                continue
            if _vce_has_generate_control and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_text):
                _vce_llm[_vce_field] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                _vce_action_edits += 1
            elif _vce_has_create_control and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_text):
                _vce_llm[_vce_field] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                _vce_action_edits += 1
        _vce_points = _vce_llm.get("key_points")
        if isinstance(_vce_points, list):
            for _vce_i, _vce_point in enumerate(_vce_points):
                if not isinstance(_vce_point, str):
                    continue
                if _vce_has_generate_control and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_point):
                    _vce_points[_vce_i] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                    _vce_action_edits += 1
                elif _vce_has_create_control and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_point):
                    _vce_points[_vce_i] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                    _vce_action_edits += 1

    # The LLM may place scene_enrichment directly in payload. Apply the same evidence rule there,
    # using OCR matched by scene_id; all changes are limited to derived descriptions/key points.
    _vce_ocr_by_scene = {}
    for _vce_seg in _vce_segments:
        if isinstance(_vce_seg, dict) and _vce_seg.get("scene_id") is not None:
            _vce_ocr_by_scene[_vce_seg.get("scene_id")] = str(_vce_seg.get("ocr_text", "") or "")
    def _vce_guard_scene_enrichment(_vce_list):
        nonlocal _vce_action_edits
        if not isinstance(_vce_list, list):
            return
        for _vce_scene in _vce_list:
            if not isinstance(_vce_scene, dict):
                continue
            _vce_ocr = _vce_ocr_by_scene.get(_vce_scene.get("scene_id"), "")
            _vce_create = bool(_vce_re.search(r"(?i)создать\s+калькулятор", _vce_ocr))
            _vce_generate = bool(_vce_re.search(r"(?i)(?:генерация\s+калькуляторов|сгенерировать)", _vce_ocr))
            _vce_done = bool(_vce_re.search(r"(?i)(?:успешно\s+(?:создан|сгенерирован|завершен)|генерация\s+завершена|калькулятор\s+создан|калькуляторы\s+сгенерированы)", _vce_ocr))
            if _vce_done:
                continue
            for _vce_field in ("improved_description", "topic"):
                _vce_text = _vce_scene.get(_vce_field)
                if not isinstance(_vce_text, str):
                    continue
                if _vce_generate and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_text):
                    _vce_scene[_vce_field] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                    _vce_action_edits += 1
                elif _vce_create and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_text):
                    _vce_scene[_vce_field] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                    _vce_action_edits += 1
            _vce_points = _vce_scene.get("key_points")
            if isinstance(_vce_points, list):
                for _vce_i, _vce_point in enumerate(_vce_points):
                    if not isinstance(_vce_point, str):
                        continue
                    if _vce_generate and _vce_re.search(r"(?i)(?:запуск\s+генерации|запущен\w*\s+процесс\s+генерации|сгенерирован\w*\s+калькулятор)", _vce_point):
                        _vce_points[_vce_i] = "На экране показана функция «Генерация калькуляторов»; запуск и успешное завершение генерации не подтверждены кадрами."
                        _vce_action_edits += 1
                    elif _vce_create and _vce_re.search(r"(?i)создан\w*\s+калькулятор", _vce_point):
                        _vce_points[_vce_i] = "На экране показана форма создания калькулятора; завершение создания не подтверждено кадрами."
                        _vce_action_edits += 1
    if isinstance(payload, dict):
        _vce_guard_scene_enrichment(payload.get("scene_enrichment"))
    if _vce_replacements:
        print(f"🧭 [ASR/OCR] Исправлено {_vce_replacements} вариантов на {_vce_canonical} по подтверждённому OCR.")
    if _vce_action_edits:
        print(f"🧭 [Evidence guard] Смягчено {_vce_action_edits} утверждений о действиях без подтверждения результата.")
    return payload
'''

GLOBAL_SUMMARY_BLOCK = r'''        # VCE_NAME_CONSISTENCY_V2_GLOBAL_SUMMARY
        # VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR
        import re as _vce_global_std_re
        _vce_global_std_ocr = " ".join(
            str(_vce_std_seg.get("ocr_text", "") or "")
            for _vce_std_seg in final_segments
            if isinstance(_vce_std_seg, dict)
        )
        if _vce_global_std_re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce_global_std_ocr):
            _vce_global_summary_fixed = str(global_summary_text or "")
            for _vce_std_alias in (
                r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)",
                r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*130\s*\.\s*2020(?!\d)",
            ):
                _vce_global_summary_fixed = _vce_global_std_re.sub(
                    _vce_std_alias, "СП 1.13130.2020", _vce_global_summary_fixed
                )
            if _vce_global_summary_fixed != str(global_summary_text or ""):
                global_summary_text = _vce_global_summary_fixed
                print("🧭 [ASR/OCR] Исправлено название норматива в global_summary по подтверждённому OCR.")
'''

PROMPT_ADDENDUM = (
    "\n\nДополнение VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR: после текстовой постобработки согласуй все производные поля, "
    "включая meta.global_summary, enhanced_analysis.scene_enrichment и segments[*].text_llm. Если OCR связанной сцены явно "
    "подтверждает «СП 1.13130.2020», исправь известный вариант «СП 1.13.132.020» только в производных аналитических полях. "
    "Не переписывай audio_transcript, ocr_text, visual_description, timestamps и original_summary. Не утверждай, что калькулятор "
    "создан или генерация запущена/завершена, если видны только кнопка, форма или пункт меню без подтверждённого результата."
)


def _resolve_source(project: Path) -> Path:
    direct = project / "VideoContextEngine_v3.19.py"
    if direct.is_file():
        return direct
    skip = {".venv", "venv", "__pycache__", ".git", "site-packages"}
    hits = [p for p in project.rglob("VideoContextEngine_v3.19.py") if not any(part in skip for part in p.parts)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise FileNotFoundError(f"Не найден VideoContextEngine_v3.19.py в {project}")
    raise RuntimeError("Найдено несколько исходников; укажи --project на каталог с одним VideoContextEngine_v3.19.py.")


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Нельзя безопасно изменить {label}: ожидался 1 якорь, найдено {count}.")
    return text.replace(old, new, 1)


def _prepare(project: Path):
    source = _resolve_source(project)
    settings_path = source.with_name("videocontext_settings.json")
    if not settings_path.is_file():
        raise FileNotFoundError(f"Не найден файл настроек: {settings_path}")
    py_original = source.read_text(encoding="utf-8")
    settings_original = settings_path.read_text(encoding="utf-8")
    settings = json.loads(settings_original)
    if not isinstance(settings, dict):
        raise RuntimeError("Корень videocontext_settings.json должен быть JSON-объектом.")

    required = (
        "def run_text_llm_postprocess(",
        '        parsed = reconcile_text_llm_result(parsed, segments, existing_summary)\n',
        STAGE_MARKER,
    )
    missing = [anchor for anchor in required if anchor not in py_original]
    if missing:
        raise RuntimeError("Эта сборка не совпадает с ожидаемой структурой расширенного v3.19; не найдены якоря: " + ", ".join(missing))
    if py_original.count(STAGE_MARKER) != 1:
        raise RuntimeError("Маркер TEXT_LLM_POSTPROCESS_STAGE найден не ровно один раз; файлы не изменены.")

    py = py_original
    # Upgrade the canonicalizer even if the earlier patch marker already exists.
    _helper_start = py.find(HELPER_MARKER)
    _helper_end = py.find("def run_text_llm_postprocess(", _helper_start if _helper_start >= 0 else 0)
    if _helper_start >= 0 and _helper_end > _helper_start:
        _line_start = py.rfind("\n", 0, _helper_start) + 1
        py = py[:_line_start] + HELPER_SOURCE + "\n\n" + py[_helper_end:]
    else:
        py = _replace_once(py, "def run_text_llm_postprocess(", HELPER_SOURCE + "\n\ndef run_text_llm_postprocess(", "runtime canonicalizer")

    # The existing call is retained; the helper body now checks every generated scene field and segment text_llm.
    # Insert/update the global-summary repair after the global summary has been formed, before the LLM stage marker.
    if GLOBAL_MARKER in py:
        _gstart = py.find(GLOBAL_MARKER)
        _anchor = py.find(STAGE_MARKER, _gstart)
        if _anchor < 0:
            raise RuntimeError("Найден старый global-summary маркер, но нет следующего TEXT_LLM_POSTPROCESS_STAGE.")
        _line_start = py.rfind("\n", 0, _gstart) + 1
        py = py[:_line_start] + GLOBAL_SUMMARY_BLOCK + "\n" + py[_anchor:]
    else:
        py = _replace_once(py, "        # TEXT_LLM_POSTPROCESS_STAGE\n", GLOBAL_SUMMARY_BLOCK + "\n        # TEXT_LLM_POSTPROCESS_STAGE\n", "global_summary repair")

    # Check the helper is actually invoked after reconciliation.
    call_line = '        parsed = reconcile_text_llm_result(parsed, segments, existing_summary)\n'
    if '_vce_canonicalize_confirmed_standard_names(parsed, segments)' not in py:
        py = _replace_once(py, call_line, call_line + '        parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)\n', "canonicalizer call")
    elif py.count('_vce_canonicalize_confirmed_standard_names(parsed, segments)') > 1:
        raise RuntimeError("Найдено несколько вызовов canonicalizer; автоматическое исправление остановлено.")

    compile(py, str(source), "exec")
    settings_text = str(settings.get(SETTING_KEY) or "")
    if MARKER not in settings_text:
        settings[SETTING_KEY] = settings_text.rstrip() + PROMPT_ADDENDUM
    settings_new = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    json.loads(settings_new)

    changes = {}
    originals = {source: py_original, settings_path: settings_original}
    if py != py_original:
        changes[source] = py
    if settings_new != settings_original:
        changes[settings_path] = settings_new
    return changes, originals, source, settings_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="~/VideoContext-Engine", help="Каталог проекта (по умолчанию ~/VideoContext-Engine)")
    parser.add_argument("--dry-run", action="store_true", help="Проверить изменения без записи файлов")
    args = parser.parse_args()
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        print(f"ОШИБКА: каталог проекта не найден: {project}", file=sys.stderr)
        return 2
    try:
        changes, originals, source, settings_path = _prepare(project)
    except Exception as exc:
        print(f"ОШИБКА: {exc}\nФайлы не изменены.", file=sys.stderr)
        return 2
    if not changes:
        print("Патч V3 уже применён; изменений не требуется.")
        return 0
    print("Будут обновлены:")
    for path in changes:
        print(f"  - {path}")
    if args.dry_run:
        print("DRY-RUN: файлы не изменены.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {}
    temp_paths = []
    try:
        for path, original in originals.items():
            backup = path.with_name(path.name + f".bak_name_consistency_v3_{stamp}")
            shutil.copy2(path, backup)
            backups[path] = backup
        for path, content in changes.items():
            temp = path.with_name(path.name + f".tmp_name_consistency_v3_{stamp}")
            temp.write_text(content, encoding="utf-8")
            temp_paths.append(temp)
            os.replace(temp, path)
        # Validate the written Python and JSON.
        compile(source.read_text(encoding="utf-8"), str(source), "exec")
        json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        for path, original in originals.items():
            try:
                path.write_text(original, encoding="utf-8")
            except Exception:
                pass
        for temp in temp_paths:
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
        print(f"ОШИБКА при записи/проверке: {exc}. Попытка восстановления выполнена.", file=sys.stderr)
        print("Резервные копии:", *(str(p) for p in backups.values()), sep="\n  ", file=sys.stderr)
        return 3

    print("\nГотово. Созданы резервные копии:")
    for backup in backups.values():
        print(f"  - {backup}")
    print("Перезапусти VideoContext Engine перед следующим анализом.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

