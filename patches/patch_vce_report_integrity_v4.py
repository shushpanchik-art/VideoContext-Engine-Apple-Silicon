#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V4 report-integrity upgrade for patched VideoContext Engine v3.19.

Adds:
  1. Evidence-aware harmonization between the base/global summary and the LLM executive summary.
  2. Post-LLM validation of derived JSON shape, aliases, and scene references.
  3. OCR-grounded entity-to-scene link expansion (including standards visible in multiple scenes).
  4. A read-only --validate-json mode for a saved report JSON file.

This upgrades an existing V3-patched checkout. It preserves raw transcript/OCR/visual evidence,
timestamps, and original_summary. It does not modify an already-saved report unless you explicitly
edit it yourself; --validate-json is read-only.
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
from typing import Any, Dict, List

V3_MARKER = "VCE_NAME_CONSISTENCY_V3_CROSS_FIELD_REPAIR"
V4_MARKER = "VCE_REPORT_INTEGRITY_V4"
SETTING_KEY = "text_llm_name_reconciliation_prompt"
STAGE_MARKER = "# TEXT_LLM_POSTPROCESS_STAGE"
SUMMARY_MARKER = "# VCE_REPORT_INTEGRITY_V4_SUMMARY_HARMONIZER"
HELPER_MARKER = "# VCE_REPORT_INTEGRITY_V4_HELPER"

HELPER_SOURCE = r'''# VCE_REPORT_INTEGRITY_V4_HELPER

def _vce_v4_evidence_summary(text, segments):
    """Downgrade model-verified language when the nonconformity is only spoken, not shown."""
    import re as _vce4_re
    if not isinstance(text, str) or not isinstance(segments, list):
        return text
    _vce4_audio = " ".join(
        str(_vce4_seg.get("audio_transcript", "") or "")
        for _vce4_seg in segments if isinstance(_vce4_seg, dict)
    )
    _vce4_ocr = " ".join(
        str(_vce4_seg.get("ocr_text", "") or "")
        for _vce4_seg in segments if isinstance(_vce4_seg, dict)
    )
    _vce4_speech_noncompliance = bool(_vce4_re.search(
        r"(?i)(?:не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*)", _vce4_audio
    ))
    _vce4_collision_ui = bool(_vce4_re.search(r"(?i)проверка\s+коллизий", _vce4_ocr))
    _vce4_ocr_shows_result = bool(_vce4_re.search(
        r"(?i)(?:не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*\s+(?:выявлено|обнаружено)|"
        r"результат\s+проверки\s*[:=]\s*(?:ошибка|не\s+соответствует)|"
        r"итог\s+проверки\s*[:=]\s*(?:ошибка|не\s+соответствует))", _vce4_ocr
    ))
    if not (_vce4_speech_noncompliance and _vce4_collision_ui and not _vce4_ocr_shows_result):
        return text

    _vce4_replacement = (
        "По словам диктора, проверка выявила несоответствие нормативу; "
        "фактическое значение уклона и итог проверки визуально не подтверждены."
    )
    _vce4_result = text
    _vce4_sentences = _vce4_re.split(r"(?<=[.!?])\s+", _vce4_result)
    _vce4_new_sentences = []
    _vce4_changed = False
    for _vce4_sentence in _vce4_sentences:
        _vce4_has_noncompliance = bool(_vce4_re.search(
            r"(?i)(?:уклон\s+пандус\w*|пандус\w*[^.!?]{0,70}уклон)[^.!?]{0,140}не\s+соответствует\s+нормативу", _vce4_sentence
        ))
        _vce4_is_visual_assertion = bool(_vce4_re.search(
            r"(?i)(?:в\s+(?:3d[- ]?)?модел\w*|модель\s+показала|визуально\s+выявлено|"
            r"проверка\s+выявила|выявлено,?\s+что|обнаружено,?\s+что|установлено,?\s+что)", _vce4_sentence
        ))
        _vce4_already_attributed = bool(_vce4_re.search(
            r"(?i)(?:по\s+словам\s+диктора|в\s+речи\s+сообщается|аудиодорожк\w*)", _vce4_sentence
        ))
        if _vce4_has_noncompliance and _vce4_is_visual_assertion and not _vce4_already_attributed:
            _vce4_new_sentences.append(_vce4_replacement)
            _vce4_changed = True
        else:
            _vce4_new_sentences.append(_vce4_sentence)
    if _vce4_changed:
        return " ".join(_vce4_new_sentences)

    # Fallback for a brief global summary whose overconfident model-result sentence does not name the model.
    _vce4_overclaim = _vce4_re.search(
        r"(?i)(?:выявлено|обнаружено|установлено)[^.!?]{0,120}уклон\s+пандус\w*[^.!?]{0,80}не\s+соответствует\s+нормативу", _vce4_result
    )
    if _vce4_overclaim and not _vce4_re.search(r"(?i)визуально\s+не\s+подтвержден", _vce4_result):
        _vce4_start = _vce4_result.rfind(".", 0, _vce4_overclaim.start()) + 1
        _vce4_end = _vce4_result.find(".", _vce4_overclaim.end())
        if _vce4_end < 0:
            _vce4_end = len(_vce4_result)
        _vce4_result = _vce4_result[:_vce4_start] + " " + _vce4_replacement + _vce4_result[_vce4_end + (1 if _vce4_end < len(_vce4_result) else 0):]
    return _vce4_result


def _vce_v4_name_pattern(name):
    """Build a case-insensitive OCR literal matcher while allowing line-wrap whitespace."""
    import re as _vce4_re
    if not isinstance(name, str) or not name.strip():
        return None
    _vce4_tokens = name.strip().split()
    if not _vce4_tokens:
        return None
    return _vce4_re.compile(r"(?i)(?<![\w])" + r"\s+".join(_vce4_re.escape(_vce4_t) for _vce4_t in _vce4_tokens) + r"(?![\w])")


def _vce_v4_update_entity_scene_ids(payload, segments):
    """Add scene IDs only when an entity's literal name is visible in that scene's OCR."""
    import re as _vce4_re
    if not isinstance(payload, dict) or not isinstance(payload.get("entities"), list) or not isinstance(segments, list):
        return 0
    _vce4_valid_ids = set()
    for _vce4_seg in segments:
        if isinstance(_vce4_seg, dict):
            _vce4_id = _vce4_seg.get("scene_id")
            if isinstance(_vce4_id, int) and not isinstance(_vce4_id, bool):
                _vce4_valid_ids.add(_vce4_id)
    _vce4_changes = 0
    for _vce4_entity in payload.get("entities", []):
        if not isinstance(_vce4_entity, dict):
            continue
        _vce4_name = _vce4_entity.get("name")
        _vce4_pattern = _vce_v4_name_pattern(_vce4_name)
        if _vce4_pattern is None:
            continue
        _vce4_old = _vce4_entity.get("scene_ids")
        _vce4_current = []
        if isinstance(_vce4_old, list):
            for _vce4_id in _vce4_old:
                if isinstance(_vce4_id, int) and not isinstance(_vce4_id, bool) and _vce4_id in _vce4_valid_ids:
                    if _vce4_id not in _vce4_current:
                        _vce4_current.append(_vce4_id)
        # Avoid broad, noisy associations for very short acronyms such as "КСИ".
        if len(str(_vce4_name).strip()) >= 4:
            for _vce4_seg in segments:
                if not isinstance(_vce4_seg, dict):
                    continue
                _vce4_id = _vce4_seg.get("scene_id")
                _vce4_ocr = str(_vce4_seg.get("ocr_text", "") or "")
                if isinstance(_vce4_id, int) and not isinstance(_vce4_id, bool) and _vce4_id in _vce4_valid_ids and _vce4_pattern.search(_vce4_ocr):
                    if _vce4_id not in _vce4_current:
                        _vce4_current.append(_vce4_id)
        _vce4_current.sort()
        if _vce4_old != _vce4_current:
            _vce4_entity["scene_ids"] = _vce4_current
            _vce4_changes += 1
    return _vce4_changes


def _vce_v4_validate_analysis(payload, segments, global_summary=""):
    """Validate the derived analysis JSON contract; warn rather than abort a video run."""
    import re as _vce4_re
    _vce4_issues = []
    if not isinstance(payload, dict):
        print("⚠️ [JSON integrity V4] enhanced_analysis — ожидался JSON-объект.")
        return payload
    if not isinstance(segments, list):
        _vce4_issues.append("segments не является массивом")
        segments = []
    _vce4_valid_ids = {
        _vce4_seg.get("scene_id") for _vce4_seg in segments
        if isinstance(_vce4_seg, dict) and isinstance(_vce4_seg.get("scene_id"), int) and not isinstance(_vce4_seg.get("scene_id"), bool)
    }
    _vce4_contract = {
        "executive_summary": str,
        "topics": list,
        "key_points": list,
        "entities": list,
        "scene_enrichment": list,
    }
    for _vce4_key, _vce4_type in _vce4_contract.items():
        if _vce4_key not in payload:
            _vce4_issues.append(f"отсутствует поле { _vce4_key }")
        elif not isinstance(payload[_vce4_key], _vce4_type):
            _vce4_issues.append(f"{_vce4_key} имеет тип {type(payload[_vce4_key]).__name__}, ожидался {_vce4_type.__name__}")
    if isinstance(payload.get("topics"), list) and any(not isinstance(_vce4_item, str) for _vce4_item in payload["topics"]):
        _vce4_issues.append("topics содержит элементы не-строки")
    for _vce4_key in ("key_points", "scene_enrichment"):
        _vce4_value = payload.get(_vce4_key)
        if isinstance(_vce4_value, list):
            for _vce4_idx, _vce4_item in enumerate(_vce4_value):
                if not isinstance(_vce4_item, dict):
                    _vce4_issues.append(f"{_vce4_key}[{_vce4_idx}] не является объектом")
                    continue
                _vce4_id = _vce4_item.get("scene_id")
                if _vce4_id is not None and _vce4_id not in _vce4_valid_ids:
                    _vce4_issues.append(f"{_vce4_key}[{_vce4_idx}] ссылается на неизвестную сцену {_vce4_id!r}")
    _vce4_ocr = " ".join(str(_vce4_seg.get("ocr_text", "") or "") for _vce4_seg in segments if isinstance(_vce4_seg, dict))
    _vce4_has_canonical_sp = bool(_vce4_re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", _vce4_ocr))
    if _vce4_has_canonical_sp:
        _vce4_alias = _vce4_re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)")
        if _vce4_alias.search(str(global_summary or "")):
            _vce4_issues.append("неисправленный вариант номера СП остался в global_summary")
    _vce4_known_keys = {"audio_transcript", "ocr_text", "visual_description", "original_summary", "start", "end", "time", "timestamp", "timestamps", "timecode", "timecodes"}
    def _vce4_scan_alias(value, path="enhanced_analysis"):
        if isinstance(value, dict):
            for _vce4_key, _vce4_child in value.items():
                if str(_vce4_key).lower() in _vce4_known_keys:
                    continue
                _vce4_scan_alias(_vce4_child, f"{path}.{_vce4_key}")
        elif isinstance(value, list):
            for _vce4_i, _vce4_child in enumerate(value):
                _vce4_scan_alias(_vce4_child, f"{path}[{_vce4_i}]")
        elif isinstance(value, str) and _vce4_has_canonical_sp and _vce4_re.search(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)", value):
            _vce4_issues.append(f"неисправленный вариант номера СП в {path}")
    _vce4_scan_alias(payload)
    _vce4_entity_changes = _vce_v4_update_entity_scene_ids(payload, segments)
    if _vce4_entity_changes:
        print(f"🧭 [JSON integrity V4] Обновлены связи сущностей со сценами: {_vce4_entity_changes} сущностей.")
    if _vce4_issues:
        print("⚠️ [JSON integrity V4] Проверка аналитического JSON нашла замечания:")
        for _vce4_issue in _vce4_issues:
            print("   - " + _vce4_issue)
    else:
        print(f"✅ [JSON integrity V4] Структура аналитики проверена; доступно {len(_vce4_valid_ids)} scene_id, ссылки сущностей сверены с OCR.")
    return payload


def _vce_v4_finalize_analysis_payload(payload, segments, global_summary=""):
    """Apply evidence-grounded consistency repairs, then validate the derived analysis payload."""
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("executive_summary"), str):
        _vce4_new = _vce_v4_evidence_summary(payload["executive_summary"], segments)
        if _vce4_new != payload["executive_summary"]:
            payload["executive_summary"] = _vce4_new
            print("🧭 [Summary integrity V4] Executive summary reconciled with audio/OCR evidence.")
    _vce_v4_update_entity_scene_ids(payload, segments)
    return _vce_v4_validate_analysis(payload, segments, global_summary)
'''

SUMMARY_HARMONIZER_BLOCK = r'''        # VCE_REPORT_INTEGRITY_V4_SUMMARY_HARMONIZER
        global_summary_text = _vce_v4_evidence_summary(global_summary_text, final_segments)
'''

PROMPT_ADDENDUM = (
    "\n\nДополнение VCE_REPORT_INTEGRITY_V4: согласуй meta.global_summary и enhanced_analysis.executive_summary по уровню уверенности. "
    "Если вывод о несоответствии сообщён диктором, а экран не показывает конкретный результат проверки, явно атрибутируй вывод речи "
    "и укажи, что визуальное подтверждение отсутствует. Перед возвратом результата проверь типы полей JSON, ссылки scene_id и "
    "связи entities.scene_ids с OCR; для стандартов добавь все сцены, где их обозначение явно читается в OCR. Не меняй сырой transcript, "
    "ocr_text, visual_description, временные метки или original_summary."
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
    raise RuntimeError("Найдено несколько исходников; укажи --project на каталог с одним исходником.")


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
    if V3_MARKER not in py_original:
        raise RuntimeError("Не найден маркер V3. Этот патч предназначен для проекта с установленным patch_vce_name_consistency_v3.py. Файлы не изменены.")
    required = (
        "def run_text_llm_postprocess(",
        '        parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)\n',
        STAGE_MARKER + "\n",
    )
    missing = [anchor for anchor in required if anchor not in py_original]
    if missing:
        raise RuntimeError("Сборка не совпадает с ожидаемой структурой V3; отсутствуют якоря: " + ", ".join(missing))
    if py_original.count(STAGE_MARKER) != 1:
        raise RuntimeError("Маркер TEXT_LLM_POSTPROCESS_STAGE найден не ровно один раз; файлы не изменены.")

    py = py_original
    if V4_MARKER not in py:
        py = _replace_once(py, "def run_text_llm_postprocess(", HELPER_SOURCE + "\n\ndef run_text_llm_postprocess(", "V4 helpers")
        call_anchor = '        parsed = _vce_canonicalize_confirmed_standard_names(parsed, segments)\n'
        py = _replace_once(py, call_anchor, call_anchor + '        parsed = _vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)\n', "post-LLM integrity validator call")
        stage_anchor = "        " + STAGE_MARKER + "\n"
        py = _replace_once(py, stage_anchor, SUMMARY_HARMONIZER_BLOCK + "\n" + stage_anchor, "global-summary evidence harmonizer")
    else:
        for marker in (HELPER_MARKER, SUMMARY_MARKER, "_vce_v4_finalize_analysis_payload(parsed, segments, existing_summary)"):
            if marker not in py:
                raise RuntimeError(f"Частично применённый V4: отсутствует {marker!r}; файлы не изменены.")

    compile(py, str(source), "exec")
    prompt = str(settings.get(SETTING_KEY) or "")
    if V4_MARKER not in prompt:
        settings[SETTING_KEY] = prompt.rstrip() + PROMPT_ADDENDUM
    settings_new = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    json.loads(settings_new)

    changes: Dict[Path, str] = {}
    originals: Dict[Path, str] = {source: py_original, settings_path: settings_original}
    if py != py_original:
        changes[source] = py
    if settings_new != settings_original:
        changes[settings_path] = settings_new
    return changes, originals, source, settings_path


def _report_issues(report: Any) -> List[str]:
    """Read-only validator for a complete saved report JSON."""
    issues: List[str] = []
    if not isinstance(report, dict):
        return ["корень отчёта не является объектом"]
    meta = report.get("meta")
    analysis = report.get("enhanced_analysis")
    segments = report.get("segments")
    if not isinstance(meta, dict):
        issues.append("нет объекта meta")
        meta = {}
    if not isinstance(analysis, dict):
        issues.append("нет объекта enhanced_analysis")
        analysis = {}
    if not isinstance(segments, list):
        issues.append("нет массива segments")
        segments = []
    valid_ids = {s.get("scene_id") for s in segments if isinstance(s, dict) and isinstance(s.get("scene_id"), int) and not isinstance(s.get("scene_id"), bool)}
    if isinstance(meta.get("scene_count"), int) and valid_ids and meta["scene_count"] != len(valid_ids):
        issues.append(f"meta.scene_count={meta['scene_count']} не совпадает с числом уникальных scene_id={len(valid_ids)}")
    if "global_summary" in meta and not isinstance(meta["global_summary"], str):
        issues.append("meta.global_summary имеет неверный тип")
    for key, typ in (("executive_summary", str), ("topics", list), ("key_points", list), ("entities", list), ("scene_enrichment", list)):
        if key not in analysis:
            issues.append(f"enhanced_analysis.{key} отсутствует")
        elif not isinstance(analysis[key], typ):
            issues.append(f"enhanced_analysis.{key} имеет неверный тип")
    for key in ("key_points", "scene_enrichment"):
        vals = analysis.get(key)
        if isinstance(vals, list):
            for i, item in enumerate(vals):
                if isinstance(item, dict) and item.get("scene_id") is not None and item.get("scene_id") not in valid_ids:
                    issues.append(f"enhanced_analysis.{key}[{i}].scene_id={item.get('scene_id')!r} отсутствует в segments")
    ocr_all = " ".join(str(s.get("ocr_text", "") or "") for s in segments if isinstance(s, dict))
    canonical_sp = bool(re.search(r"(?i)(?<![\w])СП\s*1\.13130\.2020(?!\d)", ocr_all))
    alias = re.compile(r"(?i)(?<![\w])СП\s*1\s*\.\s*13\s*\.\s*132\s*\.\s*0?20(?!\d)")
    if canonical_sp:
        if alias.search(str(meta.get("global_summary", ""))):
            issues.append("неправильный вариант СП остался в meta.global_summary")
        raw_keys = {"original_summary", "audio_transcript", "ocr_text", "visual_description", "start", "end", "time", "timestamp", "timestamps", "timecode", "timecodes"}
        derived_analysis = {k: v for k, v in analysis.items() if str(k).lower() not in raw_keys}
        if alias.search(json.dumps(derived_analysis, ensure_ascii=False, default=str)):
            issues.append("неправильный вариант СП остался в производных полях enhanced_analysis")
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            # Raw audio_transcript is intentionally allowed to retain ASR output.
            if alias.search(json.dumps(seg.get("text_llm", {}), ensure_ascii=False, default=str)):
                issues.append(f"неправильный вариант СП остался в segments[{i}].text_llm")
    entities = analysis.get("entities")
    if isinstance(entities, list):
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                issues.append(f"enhanced_analysis.entities[{i}] не является объектом")
                continue
            ids = entity.get("scene_ids")
            if not isinstance(ids, list):
                issues.append(f"entities[{i}].scene_ids не является массивом")
                continue
            invalid = [sid for sid in ids if sid not in valid_ids]
            if invalid:
                issues.append(f"entities[{i}].scene_ids содержит неизвестные сцены {invalid!r}")
            name = entity.get("name")
            if isinstance(name, str) and len(name.strip()) >= 4:
                pattern = re.compile(r"(?i)(?<![\w])" + r"\s+".join(re.escape(t) for t in name.strip().split()) + r"(?![\w])")
                ocr_scene_ids = [s.get("scene_id") for s in segments if isinstance(s, dict) and isinstance(s.get("scene_id"), int) and pattern.search(str(s.get("ocr_text", "") or ""))]
                missing_links = sorted(set(ocr_scene_ids) - set(ids))
                if missing_links:
                    issues.append(f"entities[{i}] ({name}) не привязана к OCR-подтверждённым сценам {missing_links}")
    audio_text = " ".join(str(s.get("audio_transcript", "") or "") for s in segments if isinstance(s, dict))
    if re.search(r"(?i)не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*", audio_text) and re.search(r"(?i)проверка\s+коллизий", ocr_all):
        ocr_has_result = bool(re.search(r"(?i)(?:не\s+соответствует\s+(?:нормативу|требованиям)|несоответств\w*\s+(?:выявлено|обнаружено)|результат\s+проверки\s*[:=]\s*(?:ошибка|не\s+соответствует))", ocr_all))
        if not ocr_has_result:
            global_summary = str(meta.get("global_summary", ""))
            executive = str(analysis.get("executive_summary", ""))
            if re.search(r"(?i)(?:в\s+ходе\s+проверки\s+)?(?:в\s+(?:3d[- ]?)?модел\w*\s+)?(?:выявлено|обнаружено|установлено)[^.!?]{0,120}не\s+соответствует\s+нормативу", global_summary):
                issues.append("global_summary утверждает визуально подтверждённый результат, хотя OCR его не показывает")
            if not re.search(r"(?i)(?:по\s+словам\s+диктора|в\s+речи\s+сообщается|в\s+финальной\s+речи|аудиодорожк\w*)", executive):
                issues.append("executive_summary не атрибутирует вывод аудиодорожке")
            if not re.search(r"(?i)(?:визуально\s+не\s+подтвержден|связь\s+[^.]{0,60}не\s+подтвержд|на\s+экране\s+не\s+показан)", executive):
                issues.append("executive_summary не отмечает отсутствие визуального подтверждения")
    return issues


def _validate_json_file(path: Path) -> int:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ОШИБКА: не удалось прочитать JSON {path}: {exc}", file=sys.stderr)
        return 2
    issues = _report_issues(report)
    if issues:
        print(f"JSON integrity V4: {len(issues)} замечаний в {path}:")
        for issue in issues:
            print("  - " + issue)
        return 1
    print(f"✅ JSON integrity V4: проверка пройдена — {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="~/VideoContext-Engine", help="Каталог проекта (по умолчанию ~/VideoContext-Engine)")
    parser.add_argument("--dry-run", action="store_true", help="Проверить патч без записи файлов")
    parser.add_argument("--validate-json", metavar="FILE", help="Только проверить готовый JSON-отчёт, без изменения файлов")
    args = parser.parse_args()
    if args.validate_json:
        return _validate_json_file(Path(args.validate_json).expanduser().resolve())

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
        print("Патч V4 уже применён; изменений не требуется.")
        return 0
    print("Будут обновлены:")
    for path in changes:
        print(f"  - {path}")
    if args.dry_run:
        print("DRY-RUN: файлы не изменены.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: Dict[Path, Path] = {}
    temporary: List[Path] = []
    try:
        for path, original in originals.items():
            backup = path.with_name(path.name + f".bak_report_integrity_v4_{stamp}")
            shutil.copy2(path, backup)
            backups[path] = backup
        for path, content in changes.items():
            temp = path.with_name(path.name + f".tmp_report_integrity_v4_{stamp}")
            temp.write_text(content, encoding="utf-8")
            temporary.append(temp)
            os.replace(temp, path)
        compile(source.read_text(encoding="utf-8"), str(source), "exec")
        json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        for path, original in originals.items():
            try:
                path.write_text(original, encoding="utf-8")
            except Exception:
                pass
        for temp in temporary:
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
        print(f"ОШИБКА при записи/проверке: {exc}. Попытка восстановления выполнена.", file=sys.stderr)
        print("Резервные копии:", *(str(p) for p in backups.values()), sep="\n  ", file=sys.stderr)
        return 3
    print("\nГотово. Созданы резервные копии:")
    for backup in backups.values():
        print("  - " + str(backup))
    print("Перезапусти VideoContext Engine перед следующим анализом.")
    print("Для проверки сохранённого отчёта: python3 patch_vce_report_integrity_v4.py --validate-json /путь/к/отчёту.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
