#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe MP4 recovery plus audio-only fallback for VCE v3.19."""
from __future__ import annotations
import argparse, ast, difflib, os, re, shutil, textwrap
from datetime import datetime
from pathlib import Path

ENGINE_NAME = "VideoContextEngine_v3.19.py"
RECOVERY_MARKER = "# VCE_METAL_MEMORY_AND_MEDIA_RECOVERY_V1"
PATCH_MARKER = "# VCE_MP4_PREFLIGHT_RECOVERY_RETRY_V2"

CLASSIFIER = r'''
def _vce_is_recoverable_container_error(exc):
    detail = getattr(exc, "detail", None)
    text = str(detail if detail is not None else exc).casefold()
    markers = (
        "trun track id unknown", "no tfhd was found", "invalid data found when processing input",
        "moov atom not found", "error reading header", "ffprobe:",
        "не содержит читаемого видеопотока", "не содержит читаемого видео", "поврежд",
        "fichier vidéo invalide", "invalid video file", "файл видео невалиден",
        "невозможно прочитать видео", "no video stream", "no readable video stream",
        "unsupported codec", "unsupported video codec", "codec not supported",
        "decoder not found", "could not find decoder", "could not open codec",
        "failed to open codec", "could not find codec parameters", "invalid codec parameters",
        "unsupported pixel format", "could not open video", "cannot open video",
        "failed to read video", "videocapture not opened", "video capture not opened",
    )
    return any(marker in text for marker in markers) and not any(marker in text for marker in (
        "vidéo trop longue", "видео слишком длин", "video too long", "слишком длинное видео"
    ))
'''

VALIDATE = r'''
def _vce_validate_video_with_remux(video_path, force_remux=False, prior_error=None):
    """Validate video; on container/codec failure make a temporary remux or H.264 copy."""
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
        raise HTTPException(status_code=400, detail=(
            "Ошибка контейнера/кодека, но FFmpeg недоступен. Исходник не изменён. " + detail[:700]
        ))
    strategies = [
        ("transcode-h264-aac", [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-max_muxing_queue_size", "2048", "-movflags", "+faststart",
        ]),
        ("stream-copy-remux", ["-c", "copy", "-movflags", "+faststart"]),
    ] if force_remux else [
        ("stream-copy-remux", ["-c", "copy", "-movflags", "+faststart"]),
        ("transcode-h264-aac", [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-max_muxing_queue_size", "2048", "-movflags", "+faststart",
        ]),
    ]
    failures = []
    for label, codec_args in strategies:
        fd, candidate = tempfile.mkstemp(prefix="vce_mp4_recovery_", suffix=".mp4")
        os.close(fd)
        valid = False
        try:
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
                "-analyzeduration", "100M", "-probesize", "100M",
                "-i", str(video_path), "-map", "0:v:0", "-map", "0:a?",
                *codec_args, candidate,
            ]
            proc = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
            if proc.returncode == 0 and os.path.isfile(candidate) and os.path.getsize(candidate) > 1024:
                try:
                    duration = validate_video_file(candidate)
                    note = f"FFmpeg восстановил видео во временной копии методом {label}; исходник не изменён."
                    valid = True
                    print("✅ [MP4 recovery] " + note)
                    return duration, candidate, note
                except Exception as verify_exc:
                    err = str(getattr(verify_exc, "detail", None) or verify_exc)
            else:
                err = (proc.stderr or proc.stdout or f"FFmpeg exit code {proc.returncode}").strip()[-1200:]
            failures.append(f"{label}: {err}")
        except subprocess.TimeoutExpired:
            failures.append(f"{label}: превышен лимит 240 секунд")
        except Exception as recovery_exc:
            failures.append(f"{label}: {type(recovery_exc).__name__}: {recovery_exc}")
        finally:
            if not valid and os.path.exists(candidate):
                try: os.remove(candidate)
                except OSError: pass
    raise HTTPException(status_code=400, detail=(
        "FFmpeg не смог восстановить читаемый видеопоток через transcode/remux. Исходник не изменён. "
        "Ошибка источника: " + detail[:450] + ". Попытки: " + " | ".join(failures)[-1500:] +
        ". Если MP4 fragmented и обязательные фрагменты отсутствуют, перекачайте или экспортируйте файл заново."
    ))
'''

AUDIO_HELPER = r'''
def _vce_build_audio_only_video(video_path, prior_error=""):
    """Extract readable audio and wrap it in a temporary black video so ASR can still run."""
    import os
    import shutil as _shutil
    import subprocess
    import tempfile
    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg недоступен для извлечения аудио.")
    afd, audio_path = tempfile.mkstemp(prefix="vce_audio_fallback_", suffix=".m4a")
    os.close(afd)
    vfd, synthetic_path = tempfile.mkstemp(prefix="vce_audio_only_", suffix=".mp4")
    os.close(vfd)
    success = False
    try:
        extract = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
            "-analyzeduration", "100M", "-probesize", "100M", "-i", str(video_path),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "aac", "-b:a", "96k", audio_path,
        ]
        proc = subprocess.run(extract, capture_output=True, text=True, timeout=240, check=False)
        if proc.returncode or not os.path.isfile(audio_path) or os.path.getsize(audio_path) <= 1024:
            raise RuntimeError("Не удалось извлечь аудиодорожку: " + (proc.stderr or proc.stdout or str(proc.returncode))[-800:])
        synthesize = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:r=1",
            "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-pix_fmt", "yuv420p", "-r", "1", "-c:a", "copy",
            "-shortest", "-movflags", "+faststart", synthetic_path,
        ]
        proc = subprocess.run(synthesize, capture_output=True, text=True, timeout=240, check=False)
        if proc.returncode or not os.path.isfile(synthetic_path) or os.path.getsize(synthetic_path) <= 1024:
            raise RuntimeError("Не удалось создать служебное audio-only видео: " + (proc.stderr or proc.stdout or str(proc.returncode))[-800:])
        validate_video_file(synthetic_path)
        cap = cv2.VideoCapture(synthetic_path)
        try:
            if not cap.isOpened():
                raise RuntimeError("OpenCV не смог открыть служебное видео.")
            ok, _frame = cap.read()
            if not ok:
                raise RuntimeError("OpenCV не смог прочитать первый кадр служебного видео.")
        finally:
            cap.release()
        note = (
            "Видеопоток не удалось восстановить; продолжение в audio-only режиме. "
            "Whisper и сводка работают по аудио; VLM, OCR и анализ визуальных сцен отключены. "
            "Чёрная дорожка — техническая временная копия для синхронизации. "
            "Исходник не изменён. " + str(prior_error)[:250]
        )
        success = True
        print("⚠️ [Audio-only fallback] " + note)
        return synthetic_path, note
    finally:
        try: os.remove(audio_path)
        except OSError: pass
        if not success:
            try: os.remove(synthetic_path)
            except OSError: pass
'''

WRAPPER = r'''
def _vce_ensure_opencv_compatible_with_recovery(video_path):
    """Return (usable_path, audio_only_fallback, note), degrading gracefully if possible."""
    import os
    from pathlib import Path
    prepared = None
    initial_error = ""
    try:
        prepared = ensure_opencv_compatible_video(video_path)
        validate_video_file(prepared)
        return prepared, False, None
    except Exception as exc:
        if not _vce_is_recoverable_container_error(exc):
            if prepared and str(Path(prepared).resolve()) != str(Path(video_path).resolve()):
                try: os.remove(prepared)
                except OSError: pass
            raise
        initial_error = str(getattr(exc, "detail", None) or exc)
    if prepared and str(Path(prepared).resolve()) != str(Path(video_path).resolve()):
        try: os.remove(prepared)
        except OSError: pass

    recovery_error = ""
    recovered = None
    try:
        _duration, recovered, note = _vce_validate_video_with_remux(
            video_path, force_remux=True, prior_error=initial_error
        )
        if recovered:
            compatible = ensure_opencv_compatible_video(recovered)
            validate_video_file(compatible)
            if str(Path(compatible).resolve()) != str(Path(recovered).resolve()):
                try: os.remove(recovered)
                except OSError: pass
            return compatible, False, note
    except Exception as exc:
        recovery_error = str(getattr(exc, "detail", None) or exc)
        if recovered and os.path.exists(recovered):
            try: os.remove(recovered)
            except OSError: pass

    synthetic = None
    compatible = None
    try:
        synthetic, audio_note = _vce_build_audio_only_video(
            video_path, prior_error=(initial_error + " " + recovery_error)
        )
        compatible = ensure_opencv_compatible_video(synthetic)
        validate_video_file(compatible)
        if str(Path(compatible).resolve()) != str(Path(synthetic).resolve()):
            try: os.remove(synthetic)
            except OSError: pass
        return compatible, True, audio_note
    except Exception as exc:
        for candidate in (compatible, synthetic):
            if candidate and os.path.exists(candidate) and str(Path(candidate).resolve()) != str(Path(video_path).resolve()):
                try: os.remove(candidate)
                except OSError: pass
        audio_error = str(getattr(exc, "detail", None) or exc)
        raise HTTPException(status_code=400, detail=(
            "Не удалось прочитать видеопоток. VCE попробовал remux/перекодирование, затем audio-only fallback, "
            "но FFmpeg не смог продолжить. Исходный файл не изменён. Ошибка preflight: " +
            initial_error[:400] + ". Ошибка recovery: " + recovery_error[:600] +
            ". Ошибка извлечения аудио: " + audio_error[:600] +
            ". Если скачивание/экспорт оборваны, скачайте или экспортируйте видео заново."
        ))
'''

def fail(message: str):
    raise ValueError(message + " Файлы не изменены.")

def replace_function(source: str, name: str, replacement: str) -> str:
    tree = ast.parse(source, filename=ENGINE_NAME)
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        fail(f"Ожидалась ровно одна функция {name}; найдено {len(nodes)}.")
    n = nodes[0]
    lines = source.splitlines(keepends=True)
    return "".join(lines[:n.lineno - 1]) + textwrap.dedent(replacement).strip() + "\n\n" + "".join(lines[n.end_lineno:])

def replace_preflight_call(source: str) -> str:
    if re.search(r"(?m)^\s*prepared_path,\s*_vce_audio_only_fallback,\s*_vce_media_recovery_note\s*=\s*await asyncio\.to_thread\(", source):
        return source
    pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)prepared_path\s*=\s*await asyncio\.to_thread\(\s*"
        r"(?:ensure_opencv_compatible_video|_vce_ensure_opencv_compatible_with_recovery),\s*"
        r"original_input_path\s*,?\s*\)[ \t]*$"
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        fail("Не найден единственный preflight ensure_opencv_compatible_video(original_input_path); найдено " + str(len(matches)) + ".")
    m = matches[0]
    i = m.group("i")
    replacement = (
        i + "prepared_path, _vce_audio_only_fallback, _vce_media_recovery_note = await asyncio.to_thread(\n"
        + i + "    _vce_ensure_opencv_compatible_with_recovery,\n"
        + i + "    original_input_path,\n" + i + ")"
    )
    return source[:m.start()] + replacement + source[m.end():]

def add_audio_only_policy(source: str) -> str:
    if "VCE_AUDIO_ONLY_FALLBACK_POLICY_V1" in source:
        return source
    route = source.find("async def analyze_video(")
    if route < 0:
        fail("Не найдена async def analyze_video().")
    marker = "        if enable_mlx_whisper:\n"
    pos = source.find(marker, route)
    if pos < 0:
        fail("Не найдена точка после подготовки видео перед проверкой MLX Whisper.")
    policy = (
        "        # VCE_AUDIO_ONLY_FALLBACK_POLICY_V1\n"
        "        if _vce_audio_only_fallback:\n"
        "            _vce_media_recovery_note = _vce_media_recovery_note or \"Продолжение в audio-only режиме.\"\n"
        "            skip_visual = True\n"
        "            run_vlm = False\n"
        "            enable_apple_ocr = False\n"
        "            enable_ffmpeg_ocr = False\n"
        "            skip_audio = False\n"
        "            analysis_mode = \"audio_only_fallback\"\n"
        "            print(\"⚠️ [Media fallback] \" + _vce_media_recovery_note)\n"
    )
    source = source[:pos] + policy + source[pos:]
    anchor = (
        "                    final_segments.append(\n"
        "                        {\n"
        "                            \"scene_id\": scene_id,\n"
    )
    if source.count(anchor) != 1:
        fail("Не найден единственный scene result append; некуда безопасно добавить предупреждение.")
    source = source.replace(
        anchor,
        "                    if _vce_audio_only_fallback and idx == 0:\n"
        "                        visual_description = _vce_media_recovery_note\n" + anchor,
        1,
    )
    return source

def patch_engine(source: str) -> str:
    ast.parse(source, filename=ENGINE_NAME)
    if RECOVERY_MARKER not in source:
        fail("Сначала нужен базовый recovery-патч VCE_METAL_MEMORY_AND_MEDIA_RECOVERY_V1.")
    if PATCH_MARKER in source:
        required = (
            "def _vce_build_audio_only_video(", "def _vce_ensure_opencv_compatible_with_recovery(",
            "prepared_path, _vce_audio_only_fallback, _vce_media_recovery_note",
            "VCE_AUDIO_ONLY_FALLBACK_POLICY_V1",
        )
        missing = [x for x in required if x not in source]
        if missing:
            fail("Обнаружена частичная установка MP4/audio-only: " + ", ".join(missing))
        return source

    source = replace_function(source, "_vce_is_recoverable_container_error", CLASSIFIER)
    source = replace_function(source, "_vce_validate_video_with_remux", VALIDATE)

    tree = ast.parse(source)
    wrappers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_vce_ensure_opencv_compatible_with_recovery"]
    if len(wrappers) == 1:
        source = replace_function(source, "_vce_ensure_opencv_compatible_with_recovery", WRAPPER)
    elif len(wrappers) == 0:
        endpoint = '@app.post("/api/v1/analyze")'
        if source.count(endpoint) != 1:
            fail("Не найдена единственная точка вставки перед /api/v1/analyze.")
        source = source.replace(endpoint, textwrap.dedent(WRAPPER).strip() + "\n\n" + endpoint, 1)
    else:
        fail("Найдено несколько recovery-wrapper; автоматическая установка небезопасна.")

    if "def _vce_build_audio_only_video(" not in source:
        pos = source.find("def _vce_ensure_opencv_compatible_with_recovery(")
        if pos < 0:
            fail("Не удалось разместить audio-only helper.")
        source = source[:pos] + textwrap.dedent(AUDIO_HELPER).strip() + "\n\n" + PATCH_MARKER + "\n" + source[pos:]

    source = replace_preflight_call(source)
    source = add_audio_only_policy(source)
    ast.parse(source, filename=ENGINE_NAME)
    return source

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    engine = project / ENGINE_NAME
    if not engine.is_file():
        print(f"ОШИБКА: не найден {engine}. Файлы не изменены.")
        return 2
    old = engine.read_text(encoding="utf-8")
    try:
        new = patch_engine(old)
        ast.parse(new, filename=str(engine))
    except Exception as exc:
        print(f"ОШИБКА: {exc}\nФайлы не изменены.")
        return 2
    if old == new:
        print("MP4 recovery + audio-only fallback уже установлен.")
        return 0
    diff = list(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=str(engine), tofile=str(engine) + " (MP4/audio-only recovery)"))
    print("".join(diff[:360]))
    if len(diff) > 360:
        print(f"... ещё {len(diff)-360} строк diff")
    if args.dry_run:
        print("\nDRY RUN: один файл подготовлен; запись не выполнялась.")
        return 0
    backup = engine.with_name(engine.name + ".before_mp4_audio_fallback_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak")
    try:
        shutil.copy2(engine, backup)
        temp = engine.with_name(engine.name + ".mp4_audio_fallback.tmp")
        temp.write_text(new, encoding="utf-8")
        temp.replace(engine)
        ast.parse(engine.read_text(encoding="utf-8"), filename=str(engine))
    except Exception as exc:
        engine.write_text(old, encoding="utf-8")
        print(f"ОШИБКА записи; исходный движок восстановлен: {exc}")
        return 3
    print("\n✅ MP4 recovery + audio-only fallback установлен.")
    print("Backup:", backup)
    print("Если video stream не восстановить, но аудио читается, VCE продолжит в audio-only режиме.")
    print("Если не читается ни видео, ни аудио, будет диагностическая ошибка; оригинал не меняется.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
