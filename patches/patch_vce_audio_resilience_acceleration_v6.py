#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install resilient audio handling and overlapping CPU OCR for patched VCE v3.19.

The patcher expects the analysis-profiles + CPU/Tesseract OCR patch set already
installed. It never installs software or changes the selected analysis profile.
It creates a timestamped backup and validates Python syntax before writing.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

TARGET = Path("VideoContextEngine_v3.19.py")
MARKER = "# VCE_AUDIO_RESILIENCE_ACCELERATION_V6"
OCR_MARKER = "VCE_ANALYSIS_PROFILES_FFMPEG_OCR_V2_CPU_TESSERACT"

HELPERS = r'''# VCE_AUDIO_RESILIENCE_ACCELERATION_V6

def _vce_which_binary(name: str) -> str | None:
    """Find tools even when the GUI launcher receives a minimal macOS PATH."""
    found = shutil.which(name)
    if found:
        return found
    candidates = (
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path("/opt/local/bin") / name,
        Path.home() / ".local/bin" / name,
        Path("/usr/bin") / name,
        Path("/bin") / name,
    )
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _vce_probe_audio_stream(media_path: str) -> tuple[bool | None, str]:
    """Return (has_audio, diagnostic); None means probing was inconclusive."""
    ffprobe = _vce_which_binary("ffprobe")
    last_error = ""
    if ffprobe:
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=index,codec_name,codec_type",
                 "-of", "json", str(media_path)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            if proc.returncode == 0:
                payload = json.loads(proc.stdout or "{}")
                streams = payload.get("streams") or []
                if streams:
                    codec = str(streams[0].get("codec_name") or "unknown")
                    return True, f"Аудиопоток обнаружен (кодек: {codec})."
                return False, "Аудиопоток не обнаружен: ffprobe вернул пустой список audio streams."
            last_error = (proc.stderr or "").strip()[-500:]
        except Exception as exc:
            last_error = str(exc)[:500]

    # Fallback for installations where ffprobe is missing or cannot parse the file.
    ffmpeg = _vce_which_binary("ffmpeg")
    if ffmpeg:
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(media_path)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            diagnostic = ((proc.stderr or "") + "\n" + (proc.stdout or ""))
            if re.search(r"(?im)^\s*Stream #\d+:\d+.*?\bAudio:", diagnostic):
                match = re.search(r"(?im)^\s*Stream #\d+:\d+.*?\bAudio:\s*([^,\r\n]+)", diagnostic)
                codec = match.group(1).strip() if match else "unknown"
                return True, f"Аудиопоток обнаружен через FFmpeg (кодек: {codec})."
            if re.search(r"(?im)^\s*Stream #\d+:\d+.*?\bVideo:", diagnostic):
                return False, "Видеопоток найден, аудиопоток отсутствует (проверка FFmpeg)."
            last_error = (diagnostic.strip()[-500:] or last_error)
        except Exception as exc:
            last_error = str(exc)[:500]

    detail = f" Детали проверки: {last_error}" if last_error else ""
    return None, "Не удалось надёжно определить наличие аудио; VCE попробует Whisper и применит резервный режим при ошибке." + detail


def _vce_is_audio_decode_failure(exc: BaseException) -> bool:
    """Recognize common Whisper/FFmpeg audio-load failures without hiding unrelated bugs."""
    message = str(exc).casefold()
    markers = (
        "failed to load audio",
        "output file does not contain any stream",
        "does not contain any stream",
        "no audio stream",
        "could not find codec parameters",
        "error opening input file",
        "invalid data found when processing input",
        "matches no streams",
    )
    return any(marker in message for marker in markers)


def _vce_transcribe_audio_safe(model, media_path: str) -> tuple[dict, str]:
    """Transcribe on a worker thread; downgrade only known audio-decode failures to video-only."""
    try:
        result = model.transcribe(
            str(media_path),
            condition_on_previous_text=False,
            temperature=0.0,
            compression_ratio_threshold=1.8,
        )
        if not isinstance(result, dict):
            raise TypeError("Whisper returned a non-dictionary transcription result.")
        return result, ""
    except Exception as exc:
        if not _vce_is_audio_decode_failure(exc):
            raise
        detail = re.sub(r"\s+", " ", str(exc)).strip()[-700:]
        return {"segments": []}, (
            "Whisper автоматически пропущен: FFmpeg не смог прочитать аудиодорожку. "
            "Видеоанализ продолжается без транскрипции. " + detail
        )


def _vce_run_ffmpeg_ocr_timed(video_path: str, interval_seconds: int, workers: int = 2) -> tuple[list[dict], str, float]:
    """Return OCR records, status and actual OCR wall time; never fail the video job."""
    started = time.perf_counter()
    try:
        records, message = _vce_run_ffmpeg_ocr(video_path, interval_seconds, workers)
    except Exception as exc:
        records, message = [], f"FFmpeg/Tesseract OCR пропущен без остановки анализа: {exc}"
    return records, message, round(time.perf_counter() - started, 3)

'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Ожидалось ровно одно вхождение {label}; найдено {count}.")
    return source.replace(old, new, 1)


def add_import(source: str, module: str) -> str:
    if re.search(rf"(?m)^import {re.escape(module)}(?:\s|$)", source):
        return source
    anchor = "import re\n"
    if source.count(anchor) == 1:
        return source.replace(anchor, anchor + f"import {module}\n", 1)
    raise ValueError(f"Не найден безопасный якорь import re для подключения {module}.")


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", source):
        offsets.append(match.end())
    return offsets


def _patch_whisper_call(source: str) -> str:
    if "audio_res, whisper_audio_error = await asyncio.to_thread(_vce_transcribe_audio_safe" in source:
        return source
    tree = ast.parse(source, filename=str(TARGET))
    candidates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if not (isinstance(target, ast.Name) and target.id == "audio_res" and isinstance(value, ast.Call)):
            continue
        func = value.func
        if (isinstance(func, ast.Attribute) and func.attr == "transcribe"
                and isinstance(func.value, ast.Name) and func.value.id == "w_model"):
            candidates.append(node)
    if len(candidates) != 1:
        raise ValueError(
            f"Ожидался один прямой вызов audio_res = w_model.transcribe(...); найдено {len(candidates)}. "
            "Локальная версия отличается от проверенной; файлы не изменены."
        )
    node = candidates[0]
    offsets = _line_offsets(source)
    start = offsets[node.lineno - 1]
    end = offsets[node.end_lineno] if node.end_lineno < len(offsets) else len(source)
    line = source[start:offsets[node.lineno]]
    indent_match = re.match(r"[ \t]*", line)
    indent = indent_match.group(0) if indent_match else ""
    replacement = (
        indent + "audio_res, whisper_audio_error = await asyncio.to_thread(\n"
        + indent + "    _vce_transcribe_audio_safe, w_model, temp_path\n"
        + indent + ")\n"
    )
    return source[:start] + replacement + source[end:]


def _patch_whisper_result(source: str) -> str:
    if "if whisper_audio_error:" in source and "audio_auto_skipped = True" in source:
        return source
    pattern = re.compile(
        r'(?m)^(?P<i>[ \t]*)whisper_segments\s*=\s*audio_res\.get\(["\']segments["\'],\s*\[\]\)\s*or\s*\[\][ \t]*$'
    )
    matches = list(pattern.finditer(source))
    safe_call = source.find("audio_res, whisper_audio_error = await asyncio.to_thread(_vce_transcribe_audio_safe")
    match = next((m for m in matches if m.start() > safe_call), None)
    if match is None or safe_call < 0:
        raise ValueError("Не найден результат Whisper после безопасного transcribe; файлы не изменены.")
    i = match.group("i")
    replacement = "\n".join((
        i + "if whisper_audio_error:",
        i + "    skip_audio = True",
        i + "    audio_auto_skipped = True",
        i + "    audio_track_available = False",
        i + "    audio_probe_message = whisper_audio_error",
        i + "    whisper_segments = []",
        i + "    print(\"⚠️ [Audio] \" + audio_probe_message)",
        i + "else:",
        i + "    whisper_segments = audio_res.get(\"segments\", []) or []",
    ))
    return source[:match.start()] + replacement + source[match.end():]


def _patch_async_ocr_setup(source: str) -> str:
    if "ffmpeg_ocr_task = asyncio.create_task(" in source:
        return source
    start = source.find("        if enable_ffmpeg_ocr:\n")
    if start < 0:
        raise ValueError("Не найден блок включения FFmpeg OCR; текущая версия не поддерживается.")
    end = source.find("        if skip_audio and skip_visual", start)
    if end < 0:
        raise ValueError("Не найден следующий этап analyze_video после настройки FFmpeg OCR.")
    block = source[start:end]
    if "_vce_run_ffmpeg_ocr" not in block or "ffmpeg_ocr_infer_time" not in block:
        raise ValueError("Блок FFmpeg OCR имеет неожиданную структуру; файлы не изменены.")
    workers = "ffmpeg_ocr_workers" if "ffmpeg_ocr_workers" in source else "2"
    replacement = (
        "        ffmpeg_ocr_task = None\n"
        "        if enable_ffmpeg_ocr:\n"
        "            if ffmpeg_ocr_available:\n"
        "                _vce_ffmpeg_ocr_start = time.time()\n"
        "                ffmpeg_ocr_task = asyncio.create_task(asyncio.to_thread(\n"
        f"                    _vce_run_ffmpeg_ocr_timed, temp_path, ffmpeg_ocr_interval, {workers}\n"
        "                ))\n"
        "            else:\n"
        "                print(\"⚠️ [FFmpeg OCR] \" + ffmpeg_ocr_reason)\n\n"
    )
    return source[:start] + replacement + source[end:]


def _insert_ocr_wait(source: str) -> str:
    if "ffmpeg_ocr_records, ffmpeg_ocr_reason, ffmpeg_ocr_infer_time = await ffmpeg_ocr_task" in source:
        return source
    frame_start = source.find("first_frame_img = None")
    if frame_start < 0:
        raise ValueError("Не найден first_frame_img; невозможно синхронизировать OCR со сценами.")
    loop_start = source.find("for idx, scene in enumerate(scenes_raw):", frame_start)
    if loop_start < 0:
        raise ValueError("Не найден цикл сцен после подготовки первого кадра.")
    line_start = source.rfind("\n", 0, loop_start) + 1
    line_indent = re.match(r"[ \t]*", source[line_start:]).group(0)
    body_indent = line_indent + "    "
    wait_block = "\n".join((
        line_indent + "if ffmpeg_ocr_task is not None:",
        body_indent + "ffmpeg_ocr_records, ffmpeg_ocr_reason, ffmpeg_ocr_infer_time = await ffmpeg_ocr_task",
        body_indent + "if ffmpeg_ocr_reason:",
        body_indent + "    print(\"⚠️ [FFmpeg OCR] \" + ffmpeg_ocr_reason)",
        body_indent + "else:",
        body_indent + "    print(f\"🔎 [FFmpeg OCR] Получено образцов: {len(ffmpeg_ocr_records)}\")",
        "",
    ))
    return source[:line_start] + wait_block + source[line_start:]


def _patch_report_metadata(source: str) -> str:
    fields = (
        '            "audio_track_available": audio_track_available,\n'
        '            "audio_auto_skipped": audio_auto_skipped,\n'
        '            "audio_status": audio_probe_message,\n'
    )
    # The separate params object is consumed by TXT generation.
    local_anchor = '            "ram_mode": RAM_MODE,\n        }\n\n        text_report_content = generate_text_report'
    if '"audio_track_available": audio_track_available' not in source:
        if local_anchor in source:
            source = replace_once(
                source, local_anchor,
                '            "ram_mode": RAM_MODE,\n' + fields + '        }\n\n        text_report_content = generate_text_report',
                "text report audio diagnostics",
            )

    # Report JSON has a second, literal meta.params object. Insert into this object,
    # not into the text-LLM prompt or any of the protected raw transcript fields.
    if '"audio_status": audio_probe_message' not in source:
        matches = list(re.finditer(r'(?m)^(?P<i>[ \t]*)"params": \{[ \t]*$', source))
        if len(matches) != 1:
            raise ValueError(f"Ожидался один объект meta.params для диагностики аудио; найдено {len(matches)}.")
        match = matches[0]
        child_indent = match.group("i") + "    "
        insertion = (
            child_indent + '"audio_track_available": audio_track_available,\n'
            + child_indent + '"audio_auto_skipped": audio_auto_skipped,\n'
            + child_indent + '"audio_status": audio_probe_message,\n'
        )
        end = source.find("\n", match.start()) + 1
        source = source[:end] + insertion + source[end:]
    return source


def patch_engine(source: str) -> str:
    if MARKER in source:
        ast.parse(source, filename=str(TARGET))
        return source
    if OCR_MARKER not in source:
        raise ValueError(
            "Не найден маркер CPU/Tesseract OCR v5. Сначала установи исправленный патч v5; "
            "этот патч намеренно не переписывает неизвестные блоки обработки."
        )

    for module in ("asyncio", "json", "os", "re", "shutil", "subprocess", "time"):
        source = add_import(source, module)

    route_anchor = '@app.post("/api/v1/analyze")'
    if source.count(route_anchor) != 1:
        raise ValueError("Не найден единственный маршрут /api/v1/analyze.")
    route_pos = source.index(route_anchor)

    # The GUI launcher may not inherit Homebrew's PATH. Patch only the existing
    # OCR helper region so _vce_which_binary itself cannot become recursive.
    ocr_start = source.find(OCR_MARKER)
    if ocr_start < 0 or ocr_start > route_pos:
        raise ValueError("Не найден корректно расположенный OCR helper.")
    helper_region = source[ocr_start:route_pos]
    helper_region = helper_region.replace('shutil.which("ffmpeg")', '_vce_which_binary("ffmpeg")')
    helper_region = helper_region.replace('shutil.which("tesseract")', '_vce_which_binary("tesseract")')
    source = source[:ocr_start] + helper_region + source[route_pos:]

    route_pos = source.index(route_anchor)
    source = source[:route_pos] + HELPERS + source[route_pos:]

    preflight_anchor = "        video_duration = validate_video_file(temp_path)\n"
    preflight = (
        "        audio_track_available, audio_probe_message = _vce_probe_audio_stream(temp_path)\n"
        "        audio_auto_skipped = False\n"
        "        if audio_track_available is False and not skip_audio:\n"
        "            skip_audio = True\n"
        "            audio_auto_skipped = True\n"
        "            print(\"⚠️ [Audio] Аудиопоток отсутствует; продолжим анализ видео без Whisper.\")\n"
        "        elif audio_track_available is None:\n"
        "            print(\"ℹ️ [Audio] \" + audio_probe_message)\n\n"
    )
    if "audio_track_available, audio_probe_message = _vce_probe_audio_stream(temp_path)" not in source:
        source = replace_once(source, preflight_anchor, preflight + preflight_anchor, "audio preflight before validation")

    source = _patch_whisper_call(source)
    source = _patch_whisper_result(source)
    source = _patch_async_ocr_setup(source)
    source = _insert_ocr_wait(source)
    source = _patch_report_metadata(source)

    ast.parse(source, filename=str(TARGET))
    required = (
        "_vce_probe_audio_stream(temp_path)",
        "_vce_transcribe_audio_safe, w_model, temp_path",
        "audio_auto_skipped = True",
        "ffmpeg_ocr_task = asyncio.create_task(",
        "await ffmpeg_ocr_task",
        '"audio_status": audio_probe_message',
    )
    missing = [token for token in required if token not in source]
    if missing:
        raise ValueError("После патча отсутствуют обязательные фрагменты: " + ", ".join(missing))
    return source


def prepare(project: Path) -> tuple[dict[Path, str], dict[Path, str]]:
    target = project / TARGET
    if not target.is_file():
        raise FileNotFoundError(f"Не найден файл движка: {target}")
    original = target.read_text(encoding="utf-8")
    updated = patch_engine(original)
    changes = {target: updated} if updated != original else {}
    return changes, {target: original}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine",
                        help="Путь к локальному проекту VCE")
    parser.add_argument("--dry-run", action="store_true", help="Проверить патч без записи")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    try:
        changes, originals = prepare(project)
    except Exception as exc:
        print(f"ОШИБКА: {exc}")
        print("Файлы не изменены.")
        return 2
    if not changes:
        print("Патч audio resilience + CPU/OCR overlap уже установлен; изменений нет.")
        return 0

    for path, updated in changes.items():
        diff = difflib.unified_diff(
            originals[path].splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path) + " (new)",
        )
        print(f"\n--- Изменения: {path.name} ---")
        print("".join(list(diff)[:500]))
    if args.dry_run:
        print(f"\nDRY RUN: подготовлено изменений: {len(changes)}. Файлы не изменены.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: dict[Path, Path] = {}
    for path in changes:
        backup = path.with_name(path.name + f".before_audio_resilience_v6_{stamp}.bak")
        shutil.copy2(path, backup)
        backups[path] = backup

    written: list[Path] = []
    try:
        for path, updated in changes.items():
            temp = path.with_name(path.name + ".audio_resilience_v6.tmp")
            temp.write_text(updated, encoding="utf-8")
            temp.replace(path)
            written.append(path)
        ast.parse((project / TARGET).read_text(encoding="utf-8"), filename=str(project / TARGET))
    except Exception:
        for path in written:
            path.write_text(originals[path], encoding="utf-8")
        print("ОШИБКА после записи; исходник восстановлен, резервная копия сохранена.")
        raise

    print("\nУстановлен VCE audio resilience + CPU/OCR overlap.")
    for path, backup in backups.items():
        print(f"Backup: {backup}")
    print("Что изменено:")
    print("- отсутствие аудиопотока определяется через ffprobe, с резервной проверкой FFmpeg")
    print("- Whisper пропускается автоматически при отсутствии или известной ошибке декодирования аудио")
    print("- OCR на CPU запускается параллельно с загрузкой/работой Whisper и ожидается перед анализом сцен")
    print("- ffmpeg/tesseract ищутся также в стандартных каталогах Homebrew при запуске из GUI")
    print("- исходные OCR, транскрипты и этап Qwen3.5 не переписываются этим патчем")
    print("Перезапусти VCE и повтори анализ видео.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
