#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install configurable VCE analysis profiles and optional FFmpeg/Tesseract OCR.

Designed for an already-patched VideoContext Engine v3.19 checkout that includes
Apple Vision OCR, the persistent settings panel/API, and (optionally) the Qwen3.5
text post-processing extension. It deliberately does not alter or disable the
Qwen3.5 text post-processing stage.

Profiles:
  full        Whisper + Apple OCR (if checked) + Qwen3-VL + existing Qwen3.5 text stage
  whisper_ocr Whisper + Apple OCR, no Qwen3-VL; Qwen3.5 text stage remains active
  ocr_only    Apple OCR, no Whisper or Qwen3-VL; Qwen3.5 text stage remains active

FFmpeg OCR is strictly optional. If the current ffmpeg executable lacks the
Tesseract-backed `ocr` filter, the run continues with other enabled OCR and
provides a diagnostic rather than failing. This script does not install packages
or replace system ffmpeg automatically.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET = Path("VideoContextEngine_v3.19.py")
CONFIG = Path("videocontext_settings.json")
UI_FILE = Path("videocontext_web_ui.html")
ENGINE_MARKER = "# VCE_ANALYSIS_PROFILES_FFMPEG_OCR_V1"
UI_MARKER = "VCE_ANALYSIS_PROFILES_FFMPEG_OCR_V1"

HELPERS = r'''# VCE_ANALYSIS_PROFILES_FFMPEG_OCR_V1

def _vce_ffmpeg_ocr_capability() -> tuple[bool, str]:
    """Return whether the current ffmpeg binary exposes the optional Tesseract OCR filter."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "Команда ffmpeg не найдена в PATH. Apple Vision OCR продолжит работать."
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception as exc:
        return False, f"Не удалось проверить фильтры ffmpeg: {exc}"
    listed = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if re.search(r"(?im)^\s*\S+\s+ocr\s+V->V\s+Optical Character Recognition", listed):
        return True, "Фильтр OCR доступен (Tesseract)."
    return False, (
        "В текущей сборке FFmpeg отсутствует фильтр ocr/libtesseract. "
        "Apple Vision OCR продолжит работать. Для теста нужна сборка FFmpeg с --enable-libtesseract."
    )


def _vce_run_ffmpeg_ocr(video_path: str, interval_seconds: int) -> tuple[list[dict], str]:
    """Sample frames at a bounded interval and parse FFmpeg lavfi.ocr metadata."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return [], "Команда ffmpeg не найдена в PATH."
    available, reason = _vce_ffmpeg_ocr_capability()
    if not available:
        return [], reason
    interval = max(1, min(60, int(interval_seconds)))
    metadata_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="vce_ffmpeg_ocr_", suffix=".txt", delete=False) as tmp:
            metadata_path = tmp.name
        # The metadata file path is generated in /tmp, avoiding shell interpretation.
        vf = f"fps=1/{interval},ocr=language=rus+eng,metadata=mode=print:file={metadata_path}"
        proc = subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video_path),
             "-vf", vf, "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800, check=False,
        )
        if proc.returncode != 0:
            details = ((proc.stderr or "").strip().splitlines()[-4:])
            return [], "FFmpeg OCR завершился с ошибкой: " + (" | ".join(details) or f"код {proc.returncode}")
        raw = Path(metadata_path).read_text(encoding="utf-8", errors="replace") if Path(metadata_path).exists() else ""
        blocks = re.split(r"(?m)(?=^frame:\d+\s)", raw)
        records: list[dict] = []
        seen: set[tuple[float, str]] = set()
        for block in blocks:
            time_match = re.search(r"pts_time:([-+]?\d+(?:\.\d+)?)", block)
            text_match = re.search(r"(?m)^lavfi\.ocr\.text=(.*)$", block)
            confidence_match = re.search(r"(?m)^lavfi\.ocr\.confidence=([-+]?\d+(?:\.\d+)?)", block)
            if not time_match or not text_match:
                continue
            try:
                frame_time = float(time_match.group(1))
                confidence = float(confidence_match.group(1)) if confidence_match else None
            except (TypeError, ValueError):
                continue
            text = text_match.group(1).replace(r"\n", " ").replace(r"\,", ",").strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                continue
            key = (round(frame_time, 2), text.casefold())
            if key in seen:
                continue
            seen.add(key)
            records.append({"time": round(frame_time, 3), "text": text, "confidence": confidence})
        return records, "" if records else "Фильтр OCR выполнился, но не вернул распознанного текста."
    except Exception as exc:
        return [], f"Не удалось выполнить FFmpeg OCR: {exc}"
    finally:
        if metadata_path:
            try:
                Path(metadata_path).unlink(missing_ok=True)
            except Exception:
                pass




def _vce_ffmpeg_ocr_scene_text(records: list[dict], scene_start: float, scene_end: float) -> str:
    """Assign sampled OCR text to one scene and remove repeated slide text."""
    output = []
    seen = set()
    for record in sorted(records or [], key=lambda item: float(item.get("time", 0) or 0)):
        try:
            stamp = float(record.get("time", -1))
        except (TypeError, ValueError):
            continue
        text = re.sub(r"\s+", " ", str(record.get("text") or "")).strip()
        if not (scene_start <= stamp < scene_end) or not text:
            continue
        key = re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()
        if key and key not in seen:
            seen.add(key)
            output.append(text)
    return "\n".join(output)


def _vce_merge_ocr_sources(apple_text: str, ffmpeg_text: str) -> str:
    """Keep Apple Vision as primary OCR and append only non-duplicate Tesseract lines."""
    apple = str(apple_text or "").strip()
    candidates = [line.strip() for line in str(ffmpeg_text or "").splitlines() if line.strip()]
    existing = {
        re.sub(r"[^\w]+", " ", line.casefold(), flags=re.UNICODE).strip()
        for line in apple.splitlines() if line.strip()
    }
    extra = []
    for line in candidates:
        key = re.sub(r"[^\w]+", " ", line.casefold(), flags=re.UNICODE).strip()
        if key and key not in existing:
            existing.add(key)
            extra.append(line)
    if not extra:
        return apple
    ffmpeg_part = "[FFmpeg/Tesseract OCR]\n" + "\n".join(extra)
    return (apple + "\n\n" + ffmpeg_part).strip() if apple else ffmpeg_part

'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Ожидалось ровно одно вхождение {label}; найдено {count}.")
    return source.replace(old, new, 1)


def add_import(source: str, module: str) -> str:
    if re.search(rf"(?m)^import {re.escape(module)}(?:\s|$)", source) or re.search(
        rf"(?m)^from {re.escape(module)} import ", source
    ):
        return source
    anchor = "import re\n"
    if anchor in source:
        return replace_once(source, anchor, anchor + f"import {module}\n", f"import {module} insertion point")
    raise ValueError(f"Не найден безопасный якорь import re для подключения {module}.")


def _wrap_vlm_block(source: str) -> str:
    """Guard the per-scene VLM call while leaving the OCR block before it untouched."""
    if "# VCE_ANALYSIS_PROFILES_VLM_GUARD" in source:
        return source
    outer = "if run_vlm or enable_apple_ocr or (enable_ffmpeg_ocr and ffmpeg_ocr_available):"
    outer_pos = source.find(outer)
    if outer_pos < 0:
        raise ValueError("Не найден новый условный блок OCR/VLM; невозможно безопасно отключать VLM отдельно от OCR.")
    t_match = re.search(r"(?m)^(?P<i>[ \t]*)t_vlm_start = time\.time\(\)\s*$", source[outer_pos:])
    if not t_match:
        raise ValueError("Не найден t_vlm_start после блока OCR.")
    start = outer_pos + t_match.start()
    line_indent = t_match.group("i")
    end = source.find("\n" + line_indent[:-4] + "final_segments.append(", start) if len(line_indent) >= 4 else -1
    if end < 0:
        # The final_segments.append line is expected to be one nesting level outside the keyframe block.
        match = re.search(r"(?m)^(?P<i>[ \t]*)final_segments\.append\(", source[start:])
        if not match:
            raise ValueError("Не найден final_segments.append после VLM-блока.")
        end = start + match.start()
    block = source[start:end]
    if not block.strip() or "describe_scene" not in block:
        raise ValueError("VLM-блок между t_vlm_start и final_segments.append имеет неожиданную структуру.")
    indent = line_indent
    wrapped = indent + "if run_vlm:  # VCE_ANALYSIS_PROFILES_VLM_GUARD\n"
    for line in block.splitlines(keepends=True):
        wrapped += ("    " + line if line.strip() else line)
    return source[:start] + wrapped + source[end:]


def patch_engine(source: str) -> str:
    if ENGINE_MARKER not in source:
        source = add_import(source, "subprocess")
        source = add_import(source, "tempfile")
        route_anchor = '@app.post("/api/v1/analyze")'
        if source.count(route_anchor) == 1:
            source = replace_once(source, route_anchor, HELPERS + route_anchor, "вставка FFmpeg OCR helpers before analyze route")
        else:
            anchor = "async def analyze_video("
            if source.count(anchor) != 1:
                raise ValueError("Не найден единственный async def analyze_video(): текущая версия движка не поддерживается этим патчем.")
            line_start = source.rfind("\n", 0, source.find(anchor)) + 1
            source = source[:line_start] + HELPERS + source[line_start:]

    # Form fields: mode is independent of the Qwen3.5 text post-processing option.
    if "analysis_mode: str = Form(" not in source:
        source = replace_once(
            source,
            "    skip_visual: bool = Form(False),\n",
            "    skip_visual: bool = Form(False),\n"
            '    analysis_mode: str = Form("full", description="full | whisper_ocr | ocr_only"),\n'
            '    enable_ffmpeg_ocr: bool = Form(False, description="Дополнительный OCR через FFmpeg/Tesseract"),\n'
            '    ffmpeg_ocr_interval: int = Form(5, ge=1, le=60, description="Интервал выборки FFmpeg OCR, секунд"),\n',
            "analysis mode form parameters",
        )
    elif "enable_ffmpeg_ocr: bool = Form(" not in source or "ffmpeg_ocr_interval: int = Form(" not in source:
        raise ValueError("Обнаружена частичная установка параметров FFmpeg OCR; ручная проверка нужна до повторного запуска.")

    mode_block = '''    analysis_mode = (analysis_mode or "full").strip().lower()
    if analysis_mode not in {"full", "whisper_ocr", "ocr_only"}:
        raise HTTPException(422, "Недопустимый сценарий анализа")
    if analysis_mode in {"whisper_ocr", "ocr_only"}:
        skip_visual = True
    if analysis_mode == "ocr_only":
        skip_audio = True
    # Important: only this flag controls Qwen3-VL. The separate Qwen3.5 text post-processing
    # stage is intentionally untouched and can still build a coherent report from ASR + OCR.
    run_vlm = (analysis_mode == "full") and (not skip_visual)
'''
    if "# Important: only this flag controls Qwen3-VL." not in source:
        source = replace_once(source, "    if isinstance(video_file, str):\n", mode_block + "\n    if isinstance(video_file, str):\n", "analysis mode normalization")

    # Run FFmpeg OCR at bounded frame intervals, only if explicitly requested.
    ffmpeg_setup = '''        ffmpeg_ocr_available, ffmpeg_ocr_reason = _vce_ffmpeg_ocr_capability()
        ffmpeg_ocr_records = []
        ffmpeg_ocr_infer_time = 0.0
        if enable_ffmpeg_ocr:
            if ffmpeg_ocr_available:
                _vce_ffmpeg_ocr_start = time.time()
                ffmpeg_ocr_records, ffmpeg_ocr_reason = await asyncio.to_thread(
                    _vce_run_ffmpeg_ocr, temp_path, ffmpeg_ocr_interval
                )
                ffmpeg_ocr_infer_time = time.time() - _vce_ffmpeg_ocr_start
                if ffmpeg_ocr_reason:
                    print("⚠️ [FFmpeg OCR] " + ffmpeg_ocr_reason)
                else:
                    print(f"🔎 [FFmpeg OCR] Получено образцов: {len(ffmpeg_ocr_records)}")
            else:
                print("⚠️ [FFmpeg OCR] " + ffmpeg_ocr_reason)

'''
    if "ffmpeg_ocr_records = []" not in source:
        fast_match = re.search(r"(?m)^(?P<i>[ \t]*)if skip_audio and skip_visual:\s*$", source)
        if not fast_match:
            raise ValueError("Не найден fast-path if skip_audio and skip_visual перед циклом сцен.")
        base_indent = fast_match.group("i")
        prepared = []
        for line in ffmpeg_setup.splitlines(keepends=True):
            if line.strip():
                prepared.append(base_indent + (line[8:] if line.startswith("        ") else line))
            else:
                prepared.append(line)
        fast_replacement = "".join(prepared) + base_indent + "if skip_audio and skip_visual and not (enable_apple_ocr or (enable_ffmpeg_ocr and ffmpeg_ocr_available)):\n"
        source = source[:fast_match.start()] + fast_replacement + source[fast_match.end():]
    elif "ffmpeg_ocr_available, ffmpeg_ocr_reason = _vce_ffmpeg_ocr_capability()" not in source:
        raise ValueError("Обнаружена частичная установка FFmpeg OCR перед циклом сцен.")

    # Avoid loading Qwen3-VL just to fabricate a global summary when VLM is disabled.
    load_match = re.search(r"(?m)^(?P<i>[ \t]*)if not skip_visual or generate_summary:[ \t]*$", source)
    if load_match:
        source = source[:load_match.start()] + load_match.group("i") + "if run_vlm:" + source[load_match.end():]
    elif not re.search(r"(?m)^[ \t]*if run_vlm:[ \t]*$", source):
        raise ValueError("Не найден блок загрузки VLM с ожидаемой структурой.")

    # OCR must still sample frames when visual inference is disabled.
    scene_gate = re.search(r"(?m)^(?P<i>[ \t]*)if not skip_visual:\n(?P=i)    keyframes = sample_keyframes_for_scene\(\n", source)
    if scene_gate:
        i = scene_gate.group("i")
        new_gate = i + "if run_vlm or enable_apple_ocr or (enable_ffmpeg_ocr and ffmpeg_ocr_available):\n" + i + "    keyframes = sample_keyframes_for_scene(\n"
        source = source[:scene_gate.start()] + new_gate + source[scene_gate.end():]
    elif not re.search(r"(?m)^[ \t]*if run_vlm or enable_apple_ocr or \(enable_ffmpeg_ocr and ffmpeg_ocr_available\):[ \t]*$", source):
        raise ValueError("Не найден блок выбора кадров, который можно расширить для OCR без VLM.")

    # The per-scene OCR block has to remain outside the VLM guard.
    if "ffmpeg_scene_ocr_text =" not in source:
        # Add a separate field for the Tesseract OCR result; keep Apple Vision source text intact.
        init_match = re.search(r'(?m)^(?P<i>[ \t]*)scene_ocr_text = ""\s*$', source)
        if not init_match:
            raise ValueError("Не найден scene_ocr_text = \"\" для инициализации FFmpeg OCR.")
        insert_line = init_match.group("i") + 'ffmpeg_scene_ocr_text = ""'
        source = source[:init_match.end()] + "\n" + insert_line + source[init_match.end():]
        scene_t_match = re.search(r"(?m)^(?P<i>[ \t]*)t_vlm_start = time\.time\(\)\s*$", source)
        if not scene_t_match:
            raise ValueError("Не найден вызов VLM после Apple Vision OCR для объединения результатов.")
        i = scene_t_match.group("i")
        ffmpeg_scene_block = (
            f'{i}if enable_ffmpeg_ocr and ffmpeg_ocr_records:\n'
            f'{i}    ffmpeg_scene_ocr_text = _vce_ffmpeg_ocr_scene_text(ffmpeg_ocr_records, start_s, end_s)\n'
            f'{i}    if ffmpeg_scene_ocr_text:\n'
            f'{i}        scene_ocr_text = _vce_merge_ocr_sources(scene_ocr_text, ffmpeg_scene_ocr_text)\n'
            f'{i}        if run_vlm:\n'
            f'{i}            full_visual_prompt += "\\n\\nДополнительный OCR из FFmpeg/Tesseract (может содержать ошибки):\\n" + ffmpeg_scene_ocr_text\n'
        )
        source = source[:scene_t_match.start()] + ffmpeg_scene_block + source[scene_t_match.start():]

    # Store FFmpeg/Tesseract output separately as provenance, while keeping merged OCR in ocr_text.
    if '"ffmpeg_ocr_text": ffmpeg_scene_ocr_text' not in source:
        ocr_field = re.search(r'(?m)^(?P<i>[ \t]*)"ocr_text": scene_ocr_text,[ \t]*$', source)
        if not ocr_field:
            raise ValueError("Не найдено поле ocr_text в структуре сегмента для записи отдельного FFmpeg OCR.")
        i = ocr_field.group("i")
        insert = "\n" + i + '"ffmpeg_ocr_text": ffmpeg_scene_ocr_text,'
        source = source[:ocr_field.end()] + insert + source[ocr_field.end():]

    # Wrap only the Qwen3-VL inference; OCR remains outside this guard. Text LLM stage is not edited.
    source = _wrap_vlm_block(source)

    summary_match = re.search(r"(?m)^(?P<i>[ \t]*)if not skip_visual and first_frame_img is not None:\s*$", source)
    if summary_match:
        source = source[:summary_match.start()] + summary_match.group("i") + "if run_vlm and first_frame_img is not None:" + source[summary_match.end():]
    elif not re.search(r"(?m)^[ \t]*if run_vlm and first_frame_img is not None:[ \t]*$", source):
        raise ValueError("Не найден глобальный summary-участок VLM.")

    # Keep mode and optional OCR metadata visible in API results and the generated report.
    if '"ffmpeg_ocr": {' not in source:
        timings_obj_lines = [
            '"ffmpeg_ocr": {',
            '    "requested": enable_ffmpeg_ocr,',
            '    "available": ffmpeg_ocr_available,',
            '    "interval_seconds": ffmpeg_ocr_interval,',
            '    "sample_count": len(ffmpeg_ocr_records),',
            '    "inference_time": round(ffmpeg_ocr_infer_time, 3),',
            '    "message": ffmpeg_ocr_reason,',
            '},',
        ]
        timing_match = re.search(r"(?m)^(?P<i>[ \t]*)timings = \{[ \t]*$", source)
        if not timing_match:
            raise ValueError("Не найден объект timings для добавления состояния FFmpeg OCR.")
        timing_pos = timing_match.start()
        ram_match = re.search(r'(?m)^(?P<i>[ \t]*)"ram_mode": RAM_MODE,[ \t]*$', source[timing_pos:])
        if not ram_match:
            raise ValueError("Не найден конец timings object для добавления состояния FFmpeg OCR.")
        indent = ram_match.group("i")
        inserted = "".join(indent + line + "\n" for line in timings_obj_lines)
        anchor_pos = timing_pos + ram_match.start()
        source = source[:anchor_pos] + inserted + source[anchor_pos:]

    if '"analysis_mode": analysis_mode' not in source:
        return_match = re.search(r"(?m)^[ \t]*else:[ \t]*\n(?P<i>[ \t]*)return \{[ \t]*$", source)
        if not return_match:
            raise ValueError("Не найден return-словарь JSON-ответа.")
        meta_pos = source.find('"params": {', return_match.start())
        if meta_pos < 0:
            raise ValueError("Не найден meta.params в JSON-ответе.")
        params_match = re.search(r'(?m)^(?P<i>[ \t]*)"keyframes_per_scene": keyframes_per_scene,[ \t]*$', source[meta_pos:])
        if not params_match:
            raise ValueError("Не найден keyframes_per_scene в meta.params.")
        indent = params_match.group("i")
        replacement = "".join(indent + line + "\n" for line in (
            '"analysis_mode": analysis_mode,',
            '"run_vlm": run_vlm,',
            '"enable_apple_ocr": enable_apple_ocr,',
            '"enable_ffmpeg_ocr": enable_ffmpeg_ocr,',
            '"keyframes_per_scene": keyframes_per_scene,',
        ))
        anchor_pos = meta_pos + params_match.start()
        source = source[:anchor_pos] + replacement + source[anchor_pos + params_match.end():]

    # Preserve the underlying Qwen3.5 call if present. This patch intentionally makes no edits there.
    if "run_text_llm_postprocess(" in source and "# Important: only this flag controls Qwen3-VL." not in source:
        raise ValueError("Unexpected post-processing state; refusing to change any file.")

    ast.parse(source, filename=str(TARGET))
    return source


def patch_settings_api(source: str) -> str:
    if '@app.get("/api/v1/ui-settings"' not in source or '@app.post("/api/v1/ui-settings"' not in source:
        raise ValueError("Не найдены существующие GET/POST маршруты панели настроек.")
    # Defaults are read by the GET handler; new keys are persisted in the same user settings file.
    defaults_anchor = '        "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",\n'
    defaults_new = (
        '        "analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n'
        + defaults_anchor
    )
    if '"analysis_mode": "full"' not in source:
        source = replace_once(source, defaults_anchor, defaults_new, "analysis mode settings defaults")

    bool_anchor = '        "enable_apple_ocr", "whisper_acceleration", "enable_audio_features",\n'
    bool_new = '        "enable_apple_ocr", "enable_ffmpeg_ocr", "whisper_acceleration", "enable_audio_features",\n'
    if '"enable_ffmpeg_ocr", "whisper_acceleration"' not in source:
        source = replace_once(source, bool_anchor, bool_new, "FFmpeg OCR boolean allow-list")

    mode_anchor = '        if key == "vlm_model":\n'
    if 'if key == "analysis_mode":' not in source:
        source = replace_once(
            source, mode_anchor,
            '        if key == "analysis_mode":\n'
            '            if value not in {"full", "whisper_ocr", "ocr_only"}: raise HTTPException(422, "Недопустимый сценарий анализа")\n'
            '        elif key == "vlm_model":\n',
            "analysis mode settings validation",
        )

    if '"ffmpeg_ocr_interval": (1, 60)' not in source:
        int_anchor = '        "vlm_resolution": (128, 2048), "keyframes_per_scene": (1, 5),\n'
        source = replace_once(
            source, int_anchor,
            '        "ffmpeg_ocr_interval": (1, 60),\n' + int_anchor,
            "FFmpeg OCR interval range",
        )

    # GET reports live filter availability without persisting it as a user preference.
    old_return = '    return {key: persisted.get(key, defaults[key]) for key in defaults}\n'
    if 'result["ffmpeg_ocr_available"]' not in source:
        source = replace_once(
            source, old_return,
            '    result = {key: persisted.get(key, defaults[key]) for key in defaults}\n'
            '    result["ffmpeg_ocr_available"], result["ffmpeg_ocr_reason"] = _vce_ffmpeg_ocr_capability()\n'
            '    result["qwen35_text_stage_note"] = "Режим анализа не отключает Qwen3.5 текстовую постобработку."\n'
            '    return result\n',
            "settings capability response",
        )
    return source


def patch_ui(ui: str) -> str:
    if UI_MARKER in ui:
        return ui
    # Place the mode selector first, so the user picks a workflow before model tuning.
    anchor = '      <label class="setting">VLM-модель\n'
    mode_markup = '''      <!-- VCE_ANALYSIS_PROFILES_FFMPEG_OCR_V1 -->
      <label class="setting">Сценарий анализа
        <select id="cfg-analysis-mode">
          <option value="full">Полный анализ — Whisper + OCR + Qwen3-VL</option>
          <option value="whisper_ocr">Whisper + Apple OCR — без Qwen3-VL</option>
          <option value="ocr_only">Только Apple OCR — без Whisper и Qwen3-VL</option>
        </select>
        <span class="hint">Qwen3.5 текстовая постобработка не отключается сценариями: она собирает итог из доступных транскрипта и OCR.</span>
      </label>
'''
    ui = replace_once(ui, anchor, mode_markup + anchor, "analysis mode control")

    # Experimental FFmpeg OCR lives beside other checkboxes, clearly separate from Apple Vision.
    apple_anchor = '      <label><input id="cfg-apple-ocr" type="checkbox" checked> Apple Vision OCR</label>\n'
    ffmpeg_markup = '''      <label><input id="cfg-ffmpeg-ocr" type="checkbox"> Дополнительный OCR через FFmpeg/Tesseract (экспериментально)</label>
      <label class="setting">Интервал FFmpeg OCR (сек)
        <input id="cfg-ffmpeg-ocr-interval" type="number" min="1" max="60" step="1" value="5">
        <span class="hint">Распознаётся один кадр за интервал. Чем меньше значение, тем дольше обработка.</span>
      </label>
      <div id="ffmpeg-ocr-status" class="hint" role="status">Проверка поддержки OCR в FFmpeg…</div>
'''
    ui = replace_once(ui, apple_anchor, apple_anchor + ffmpeg_markup, "optional FFmpeg OCR UI controls")

    # Add settings fields to JavaScript read/fill paths.
    read_anchor = '  const readSettings = () => ({\n'
    ui = replace_once(ui, read_anchor, read_anchor + "    analysis_mode: document.getElementById('cfg-analysis-mode').value,\n", "read analysis mode setting")
    whisper_anchor = "    vlm_model: document.getElementById('cfg-vlm-model').value,\n"
    ui = replace_once(ui, whisper_anchor, whisper_anchor, "VLM model setting (verification)")

    # Insert FFmpeg fields before the existing Apple OCR checkbox in readSettings.
    read_ocr_anchor = "    enable_apple_ocr: document.getElementById('cfg-apple-ocr').checked,\n"
    ui = replace_once(
        ui, read_ocr_anchor,
        "    enable_ffmpeg_ocr: document.getElementById('cfg-ffmpeg-ocr').checked,\n"
        "    ffmpeg_ocr_interval: Number(document.getElementById('cfg-ffmpeg-ocr-interval').value),\n"
        + read_ocr_anchor,
        "read FFmpeg OCR settings",
    )

    field_map_anchor = "      'cfg-vlm-model':'vlm_model','cfg-whisper-model':'whisper_model','cfg-vlm-resolution':'vlm_resolution',\n"
    ui = replace_once(
        ui, field_map_anchor,
        "      'cfg-analysis-mode':'analysis_mode',\n" + field_map_anchor,
        "fill analysis mode setting",
    )
    checks_anchor = "    const checks = {'cfg-apple-ocr':'enable_apple_ocr','cfg-whisper-acceleration':'whisper_acceleration',\n"
    ui = replace_once(
        ui, checks_anchor,
        "    const checks = {'cfg-ffmpeg-ocr':'enable_ffmpeg_ocr','cfg-apple-ocr':'enable_apple_ocr','cfg-whisper-acceleration':'whisper_acceleration',\n",
        "fill FFmpeg OCR checkbox",
    )
    # Add the numeric FFmpeg interval into the existing fields map.
    resolution_anchor = "      'cfg-analysis-mode':'analysis_mode',\n"
    ui = replace_once(
        ui, resolution_anchor,
        resolution_anchor + "      'cfg-ffmpeg-ocr-interval':'ffmpeg_ocr_interval',\n",
        "fill FFmpeg OCR interval",
    )

    # Show actual capability status and explicitly keep the Qwen3.5 text postprocessor enabled.
    status_pattern = re.compile(r"(?m)^(?P<i>[ \t]*)fillSettings\(body\); settingsPill\.textContent = 'Сохранённые настройки';\s*$")
    status_match = status_pattern.search(ui)
    if not status_match:
        raise ValueError("Не найден код заполнения сохранённых настроек для вывода статуса FFmpeg OCR.")
    i = status_match.group("i")
    status_new = (
        i + "fillSettings(body); settingsPill.textContent = 'Сохранённые настройки';\n"
        + i + "const ffmpegStatus = document.getElementById('ffmpeg-ocr-status');\n"
        + i + "if (ffmpegStatus) {\n"
        + i + "  ffmpegStatus.textContent = body.ffmpeg_ocr_available\n"
        + i + "    ? 'FFmpeg OCR доступен. Результат будет добавлен как дополнительный источник; Apple Vision остаётся основным OCR.'\n"
        + i + "    : `FFmpeg OCR недоступен: ${body.ffmpeg_ocr_reason || 'текущая сборка не содержит фильтр ocr'}`;\n"
        + i + "}"
    )
    ui = ui[:status_match.start()] + status_new + ui[status_match.end():]

    # Form submissions include mode-derived flags; Qwen3.5 is deliberately not controlled by these flags.
    form_anchor = "      for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));\n"
    form_new = form_anchor + "      form.append('skip_audio', String(cfg.analysis_mode === 'ocr_only'));\n      form.append('skip_visual', String(cfg.analysis_mode !== 'full'));\n"
    ui = replace_once(ui, form_anchor, form_new, "mode-specific analysis form flags")

    return ui


def patch_settings(settings_text: str) -> str:
    settings = json.loads(settings_text)
    if not isinstance(settings, dict):
        raise ValueError("Корень videocontext_settings.json должен быть JSON-объектом.")
    settings.setdefault("analysis_mode", "full")
    settings.setdefault("enable_ffmpeg_ocr", False)
    settings.setdefault("ffmpeg_ocr_interval", 5)
    if settings["analysis_mode"] not in {"full", "whisper_ocr", "ocr_only"}:
        settings["analysis_mode"] = "full"
    if type(settings["enable_ffmpeg_ocr"]) is not bool:
        settings["enable_ffmpeg_ocr"] = False
    try:
        interval = int(settings["ffmpeg_ocr_interval"])
    except (TypeError, ValueError):
        interval = 5
    settings["ffmpeg_ocr_interval"] = max(1, min(60, interval))
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"


def prepare(project: Path) -> tuple[dict[Path, str], dict[Path, str]]:
    source_path = project / TARGET
    config_path = project / CONFIG
    ui_path = project / UI_FILE
    for path in (source_path, config_path, ui_path):
        if not path.is_file():
            raise FileNotFoundError(f"Не найден обязательный файл: {path}")
    originals = {
        source_path: source_path.read_text(encoding="utf-8"),
        config_path: config_path.read_text(encoding="utf-8"),
        ui_path: ui_path.read_text(encoding="utf-8"),
    }
    source_new = patch_engine(originals[source_path])
    source_new = patch_settings_api(source_new)
    ui_new = patch_ui(originals[ui_path])
    config_new = patch_settings(originals[config_path])
    ast.parse(source_new, filename=str(source_path))
    json.loads(config_new)
    changes = {}
    if source_new != originals[source_path]: changes[source_path] = source_new
    if config_new != originals[config_path]: changes[config_path] = config_new
    if ui_new != originals[ui_path]: changes[ui_path] = ui_new
    return changes, originals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine", help="Путь к локальному проекту VCE")
    parser.add_argument("--dry-run", action="store_true", help="Проверить изменения без записи")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    try:
        changes, originals = prepare(project)
    except Exception as exc:
        print(f"ОШИБКА: {exc}")
        print("Файлы не изменены.")
        return 2
    if not changes:
        print("Профили анализа и необязательный FFmpeg OCR уже настроены; изменений нет.")
        return 0
    for path, updated in changes.items():
        old = originals[path].splitlines(keepends=True)
        new = updated.splitlines(keepends=True)
        diff = list(difflib.unified_diff(old, new, fromfile=str(path), tofile=str(path) + " (new)"))
        print(f"\n--- Изменения: {path.name} ---")
        print("".join(diff[:500]))
        if len(diff) > 500: print("... diff truncated ...")
    if args.dry_run:
        print(f"\nDRY RUN: подготовлено изменений в файлах: {len(changes)}. Записи не выполнялись.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {}
    for path in changes:
        backup = path.with_name(path.name + f".before_analysis_profiles_{stamp}.bak")
        shutil.copy2(path, backup)
        backups[path] = backup
    written = []
    try:
        for path, content in changes.items():
            temp = path.with_name(path.name + ".analysis_profiles.tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(path)
            written.append(path)
        ast.parse((project / TARGET).read_text(encoding="utf-8"), filename=str(project / TARGET))
        json.loads((project / CONFIG).read_text(encoding="utf-8"))
    except Exception:
        for path in written:
            path.write_text(originals[path], encoding="utf-8")
        print("ОШИБКА после записи; исходные тексты восстановлены из памяти, резервные копии сохранены.")
        raise
    print("\nУстановлены сценарии анализа и опциональный FFmpeg OCR.")
    for path, backup in backups.items(): print(f"Backup: {backup}")
    print("Перезапусти VCE и открой http://127.0.0.1:7555/ui.")
    print("Сценарий Whisper + Apple OCR отключает только Qwen3-VL; Qwen3.5 текстовая постобработка остаётся независимой и включённой.")
    print("FFmpeg OCR по умолчанию выключен. Если фильтр ocr отсутствует, анализ продолжится с Apple Vision OCR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

