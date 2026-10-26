#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upgrade VCE MP4 recovery before OpenCV preflight, with safe remux/transcode retries."""
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
RECOVERY_MARKER = "# VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1"
PATCH_MARKER = "# VCE_MP4_PREFLIGHT_RECOVERY_RETRY_V2"

UPDATED_RECOVERY = r'''def _vce_validate_video_with_remux(video_path, force_remux=False, prior_error=None):
    """Validate a video, or safely try remux and then H.264/AAC transcode.

    The input is never overwritten. Returns (duration, recovered_path_or_None, note).
    force_remux is used when OpenCV compatibility preflight fails even if ffprobe
    can inspect the original file.
    """
    import os
    import shutil as _shutil
    import subprocess
    import tempfile

    detail = str(prior_error or "")
    if not force_remux:
        try:
            return validate_video_file(video_path), None, None
        except Exception as exc:
            if not _vce_is_recoverable_container_error(exc):
                raise
            detail = str(getattr(exc, "detail", None) or exc)

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(
            status_code=400,
            detail=(
                "Обнаружена ошибка контейнера MP4, но FFmpeg недоступен для восстановления. "
                "Исходный файл не изменён. Установите FFmpeg или заново скачайте/экспортируйте видео. "
                + detail[:700]
            ),
        )

    # When OpenCV compatibility has already failed, prefer a real transcode first.
    # Otherwise remux is cheaper and transcode is the fallback.
    strategies = [
        ("transcode-h264-aac", [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
        ]),
        ("stream-copy-remux", ["-c", "copy", "-movflags", "+faststart"]),
    ] if force_remux else [
        ("stream-copy-remux", ["-c", "copy", "-movflags", "+faststart"]),
        ("transcode-h264-aac", [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
        ]),
    ]

    failures = []
    for label, codec_args in strategies:
        fd, candidate = tempfile.mkstemp(prefix="vce_mp4_recovery_", suffix=".mp4")
        os.close(fd)
        candidate_valid = False
        try:
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-fflags", "+genpts+discardcorrupt",
                "-err_detect", "ignore_err",
                "-analyzeduration", "100M", "-probesize", "100M",
                "-i", str(video_path),
                "-map", "0:v:0", "-map", "0:a?",
                *codec_args,
            ]
            if label == "transcode-h264-aac":
                command += ["-max_muxing_queue_size", "2048"]
            command.append(candidate)
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=180, check=False
            )
            if proc.returncode == 0 and os.path.isfile(candidate) and os.path.getsize(candidate) > 1024:
                try:
                    duration = validate_video_file(candidate)
                    note = (
                        f"FFmpeg восстановил видео во временной копии методом {label}; "
                        "оригинальный файл не менялся."
                    )
                    print("✅ [MP4 recovery] " + note)
                    candidate_valid = True
                    return duration, candidate, note
                except Exception as verify_exc:
                    err = str(getattr(verify_exc, "detail", None) or verify_exc)
            else:
                err = (proc.stderr or proc.stdout or f"FFmpeg exit code {proc.returncode}").strip()[-1200:]
            failures.append(f"{label}: {err}")
        except subprocess.TimeoutExpired:
            failures.append(f"{label}: превышен лимит времени 180 секунд")
        except Exception as recovery_exc:
            failures.append(f"{label}: {type(recovery_exc).__name__}: {recovery_exc}")
        finally:
            # Preserve the successful file for analyze_video; remove only failed attempts.
            if not candidate_valid and os.path.exists(candidate):
                try:
                    os.remove(candidate)
                except OSError:
                    pass

    detail_text = " | ".join(failures)[-1800:]
    raise HTTPException(
        status_code=400,
        detail=(
            "VideoContext Engine обнаружил повреждённый/неполный MP4-контейнер и попробовал "
            "безопасный remux и перекодирование H.264/AAC во временную копию, но FFmpeg не смог "
            "получить валидный видеопоток. Исходный файл не изменён. "
            "Ошибка контейнера: " + detail[:500] +
            ". Ошибки восстановления: " + detail_text +
            ". Сообщение «trun track id unknown / no tfhd was found» обычно указывает на "
            "повреждённый или неполный fragmented MP4 (например, отсутствующий init-сегмент/оборванная загрузка). "
            "Повторно скачайте видео целиком либо экспортируйте его как обычный MP4 H.264/AAC."
        ),
    )


def _vce_ensure_opencv_compatible_with_recovery(video_path):
    """Catch container errors at the first compatibility preflight, before VCE aborts."""
    try:
        return ensure_opencv_compatible_video(video_path)
    except Exception as exc:
        if not _vce_is_recoverable_container_error(exc):
            raise
        detail = str(getattr(exc, "detail", None) or exc)
        _, recovered_path, note = _vce_validate_video_with_remux(
            video_path, force_remux=True, prior_error=detail
        )
        if not recovered_path:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Проверка OpenCV не смогла открыть исходное видео, а recovery не создал "
                    "исправленную копию. Исходный файл не изменён. " + detail[:700]
                ),
            )
        try:
            compatible_path = ensure_opencv_compatible_video(recovered_path)
        except Exception as retry_exc:
            try:
                os.remove(recovered_path)
            except OSError:
                pass
            raise HTTPException(
                status_code=400,
                detail=(
                    "FFmpeg создал временную копию, но повторная проверка совместимости тоже не прошла. "
                    "Исходный файл не изменён. Причина: " + str(retry_exc)[:900]
                ),
            ) from retry_exc
        if str(Path(compatible_path).resolve()) != str(Path(recovered_path).resolve()):
            try:
                os.remove(recovered_path)
            except OSError:
                pass
        print("⚠️ [MP4 recovery preflight] " + str(note or "Используется восстановленная временная копия."))
        return compatible_path
'''


def abort(message: str):
    raise ValueError(message + " Файлы не изменены.")


def replace_function(source: str, name: str, replacement: str) -> str:
    tree = ast.parse(source, filename=ENGINE_NAME)
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(nodes) != 1:
        abort(f"Ожидалась одна функция {name} на уровне модуля; найдено {len(nodes)}.")
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    new_block = textwrap.dedent(replacement).rstrip() + "\n\n"
    return "".join(lines[:start]) + new_block + "".join(lines[end:])


def replace_preflight_call(source: str) -> str:
    if "_vce_ensure_opencv_compatible_with_recovery" in source and "prepared_path = await asyncio.to_thread(" in source:
        # Verify it really is the callsite, not merely the helper definition.
        if re.search(r"(?m)^\s*prepared_path\s*=\s*await asyncio\.to_thread\(\s*\n\s*_vce_ensure_opencv_compatible_with_recovery,", source):
            return source

    pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)prepared_path\s*=\s*await asyncio\.to_thread\("
        r"ensure_opencv_compatible_video,\s*original_input_path\s*\)[ \t]*$"
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        abort(
            "Не удалось однозначно найти вызов ensure_opencv_compatible_video(original_input_path) "
            "в начале анализа; найдено " + str(len(matches)) + "."
        )
    match = matches[0]
    i = match.group("i")
    replacement = (
        i + "prepared_path = await asyncio.to_thread(\n"
        + i + "    _vce_ensure_opencv_compatible_with_recovery,\n"
        + i + "    original_input_path,\n"
        + i + ")"
    )
    return source[:match.start()] + replacement + source[match.end():]


def patch_engine(source: str) -> str:
    ast.parse(source, filename=ENGINE_NAME)
    if RECOVERY_MARKER not in source:
        abort("Сначала необходим базовый recovery-патч VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1.")
    if "def _vce_is_recoverable_container_error(" not in source:
        abort("Не найдена функция распознавания повреждений MP4 из recovery-патча.")
    if PATCH_MARKER in source:
        if "def _vce_ensure_opencv_compatible_with_recovery(" in source and "transcode-h264-aac" in source:
            # Still ensure the initial callsite uses the recovery wrapper.
            updated = replace_preflight_call(source)
            ast.parse(updated, filename=ENGINE_NAME)
            return updated
        abort("Обнаружена неполная предыдущая установка MP4 retry patch.")
    source = replace_function(source, "_vce_validate_video_with_remux", UPDATED_RECOVERY)
    # Insert version marker and wrapper next to the recovered function, before the next helper.
    needle = "def _vce_ensure_opencv_compatible_with_recovery(video_path):"
    if needle not in source:
        abort("Внутренняя ошибка патчера: recovery wrapper не сформирован.")
    source = source.replace(needle, PATCH_MARKER + "\n" + needle, 1)
    source = replace_preflight_call(source)
    ast.parse(source, filename=ENGINE_NAME)
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    engine_path = root / ENGINE_NAME
    if not engine_path.is_file():
        print(f"ОШИБКА: не найден {engine_path}. Файлы не изменены.")
        return 2
    old_source = engine_path.read_text(encoding="utf-8")
    try:
        new_source = patch_engine(old_source)
        ast.parse(new_source, filename=str(engine_path))
    except Exception as exc:
        print(f"ОШИБКА: {exc}\nФайлы не изменены.")
        return 2
    if new_source == old_source:
        print("MP4 preflight recovery retry patch уже установлен.")
        return 0
    diff = list(difflib.unified_diff(
        old_source.splitlines(keepends=True), new_source.splitlines(keepends=True),
        fromfile=str(engine_path), tofile=str(engine_path) + " (MP4 recovery retry)",
    ))
    print("".join(diff[:300]))
    if len(diff) > 300:
        print(f"... ещё {len(diff)-300} строк diff")
    if args.dry_run:
        print("\nDRY RUN: подготовлены изменения в одном файле; запись не выполнялась.")
        return 0

    backup = engine_path.with_name(
        engine_path.name + ".before_mp4_recovery_retry_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
    )
    try:
        shutil.copy2(engine_path, backup)
        temp = engine_path.with_name(engine_path.name + ".mp4_recovery_retry.tmp")
        temp.write_text(new_source, encoding="utf-8")
        temp.replace(engine_path)
        ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
    except Exception as exc:
        engine_path.write_text(old_source, encoding="utf-8")
        print(f"ОШИБКА записи; исходный файл восстановлен, backup сохранён: {exc}")
        return 3
    print("\n✅ MP4 recovery retry patch установлен.")
    print("Backup:", backup)
    print("Теперь ошибка из ensure_opencv_compatible_video перехватывается до остановки анализа.")
    print("Recovery пробует stream-copy remux и при неудаче H.264/AAC transcode во временный файл.")
    print("Если FFmpeg не может прочитать повреждённые фрагменты, приложение вернёт диагностическую ошибку и не изменит оригинал.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
