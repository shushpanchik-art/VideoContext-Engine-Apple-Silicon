#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch VCE v3.19 for resilient MLX-VLM memory fallback and damaged MP4 preflight.

Benchmark-backed policy:
- Keep PyTorch Whisper on MPS. User's full-clip benchmark: MPS inference 34.338 s;
  CPU configurations took 94.871-118.722 s on the same 108.23 s audio.
- Do not lower the user's default VLM resolution blindly: the benchmark did not
  measure visual accuracy at different resolutions. Instead retry at smaller sizes
  only after an actual Metal-memory failure, then continue with OCR/audio if needed.
- Corrupt fragmented MP4 is remuxed to a temporary copy only; the original is never
  overwritten. If remux fails, return a specific 400 error before model loading.

Usage: python3 patch_vce_metal_memory_and_media_recovery.py --project ~/VideoContext-Engine --dry-run
       python3 patch_vce_metal_memory_and_media_recovery.py --project ~/VideoContext-Engine
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ENGINE_NAME = "VideoContextEngine_v3.19.py"
SETTINGS_NAME = "videocontext_settings.json"
MARKER = "# VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1"

HELPERS = r'''# VCE_METAL_MEMORY_AND_MP4_RECOVERY_V1
class _VCEVlmMemoryFallback(Exception):
    """Internal marker for an exhausted VLM retry policy (normally not raised)."""


def _vce_is_metal_memory_error(exc):
    text = str(exc).casefold()
    oom_markers = (
        "out of memory", "oom", "memoryerror", "failed to allocate",
        "allocation failed", "insufficient memory", "not enough memory",
        "недостаточно памяти", "не хватает памяти", "не удалось выделить память",
        "mémoire insuffisante", "failed to allocate buffer", "bad_alloc",
    )
    if not any(marker in text for marker in oom_markers):
        return False
    return any(marker in text for marker in (
        "metal", "mlx", "mps", "memory", "mémoire", "памят", "allocate", "allocation", "oom", "bad_alloc"
    ))


def _vce_clear_metal_memory_caches():
    """Best-effort cleanup of unused MLX/PyTorch allocator cache after an OOM."""
    try:
        gc.collect()
    except Exception:
        pass
    try:
        import mlx.core as mx
        # Wait for outstanding work before releasing unused cached buffers.
        mx.synchronize()
        # MLX documents clear_cache() as the public cache-management API.
        mx.clear_cache()
    except Exception:
        pass
    try:
        import torch
        mps = getattr(torch, "mps", None)
        if mps is not None and getattr(torch.backends, "mps", None) is not None:
            if torch.backends.mps.is_available():
                mps.empty_cache()
    except Exception:
        pass
    try:
        gc.collect()
    except Exception:
        pass


def _vce_resize_image_max_edge(image, max_edge):
    """Return a resized PIL image preserving aspect ratio; leave unknown types untouched."""
    try:
        width, height = image.size
        width, height = int(width), int(height)
        longest = max(width, height)
        if longest <= max_edge or longest <= 0:
            return image
        scale = float(max_edge) / float(longest)
        new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        try:
            from PIL import Image as _PILImage
            resampling = _PILImage.Resampling.LANCZOS
        except Exception:
            resampling = 1
        return image.resize(new_size, resampling)
    except Exception:
        return image


def _vce_describe_scene_with_memory_fallback(vlm_engine, images, prompt, max_tokens):
    """Attempt normal inference first; shrink inputs only after a real Metal OOM.

    After all bounded retries fail, return a valid JSON sentinel. The scene loop
    recognizes it, disables VLM for the remainder of this job, and still saves
    transcript/OCR instead of failing the entire analysis.
    """
    original_images = list(images or [])
    requested_tokens = max(64, int(max_tokens or 512))
    try:
        return vlm_engine.describe_scene(original_images, prompt, max_tokens=requested_tokens)
    except Exception as exc:
        if not _vce_is_metal_memory_error(exc):
            raise
        print("⚠️ [VLM memory guard] Metal OOM; clearing unused caches and retrying at lower image resolution.")
        last_error_message = str(exc)[:350]

    _vce_clear_metal_memory_caches()
    if not original_images:
        return json.dumps({
            "description": "Визуальный анализ пропущен: кадры для VLM недоступны.",
            "tags": {}, "_vce_memory_fallback": True,
        }, ensure_ascii=False)

    attempts = [
        (384, original_images, requested_tokens, "384px, all keyframes"),
        (320, [original_images[len(original_images) // 2]], min(requested_tokens, 384), "320px, one representative keyframe"),
        (256, [original_images[len(original_images) // 2]], min(requested_tokens, 256), "256px, one representative keyframe"),
    ]
    seen = set()
    for max_edge, candidate_images, token_limit, label in attempts:
        resized = [_vce_resize_image_max_edge(image, max_edge) for image in candidate_images]
        try:
            signature = (tuple(tuple(getattr(image, "size", ())) for image in resized), len(resized), int(token_limit))
        except Exception:
            signature = (max_edge, len(resized), int(token_limit))
        if signature in seen:
            continue
        seen.add(signature)
        _vce_clear_metal_memory_caches()
        try:
            result = vlm_engine.describe_scene(resized, prompt, max_tokens=int(token_limit))
            print(f"✅ [VLM memory guard] Recovery succeeded at {label}.")
            return result
        except Exception as retry_exc:
            if not _vce_is_metal_memory_error(retry_exc):
                raise
            last_error_message = str(retry_exc)[:350]
            print(f"⚠️ [VLM memory guard] Still out of Metal memory at {label}.")

    # Free the model itself as well, so later report-generation stages have a
    # better chance to use unified memory. Never let cleanup mask the real failure.
    try:
        vlm_engine.unload_model()
    except Exception:
        pass
    _vce_clear_metal_memory_caches()
    message = (
        "Визуальное описание пропущено: Metal-памяти не хватило даже при повторе на 384/320/256 px. "
        "Для оставшихся сцен VLM отключена; транскрипция и OCR сохраняются. "
        "Последняя ошибка: " + last_error_message
    )
    print("🛟 [VLM memory guard] " + message)
    return json.dumps({
        "description": message,
        "tags": {},
        "_vce_memory_fallback": True,
    }, ensure_ascii=False)


def _vce_is_recoverable_container_error(exc):
    detail = getattr(exc, "detail", None)
    text = str(detail if detail is not None else exc).casefold()
    markers = (
        "trun track id unknown", "no tfhd was found", "invalid data found when processing input",
        "moov atom not found", "error reading header", "ffprobe:", "не содержит читаемого видеопотока",
        "не содержит читаемого видео", "поврежд", "fichier vidéo invalide", "invalid video file",
        "файл видео невалиден", "невозможно прочитать видео", "no video stream", "no readable video stream",
    )
    # Avoid remuxing for duration-limit or unrelated request-validation errors.
    return any(marker in text for marker in markers) and not any(marker in text for marker in (
        "vidéo trop longue", "видео слишком длин", "video too long", "слишком длинное видео"
    ))


def _vce_validate_video_with_remux(video_path):
    """Validate a video; on known MP4-container failures, try a non-destructive remux.

    Returns (duration_seconds, recovered_path_or_None, note_or_None). The source is
    never overwritten. Only a temp remux that passes VCE validation is accepted.
    """
    import os
    import shutil as _shutil
    import subprocess
    import tempfile

    try:
        return validate_video_file(video_path), None, None
    except Exception as exc:
        if not _vce_is_recoverable_container_error(exc):
            raise
        # Python clears the exception target after the except block, so retain
        # only its rendered message rather than the exception/traceback itself.
        detail = str(getattr(exc, "detail", None) or exc)

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(
            status_code=400,
            detail=("Контейнер видео повреждён, а FFmpeg недоступен для безопасного восстановления. "
                    "Исходный файл не изменён. Установите FFmpeg и повторите попытку либо заново скачайте/экспортируйте видео. "
                    + detail[:700]),
        )

    fd, candidate = tempfile.mkstemp(prefix="vce_mp4_recovery_", suffix=".mp4")
    os.close(fd)
    try:
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
            "-i", str(video_path), "-map", "0:v:0", "-map", "0:a?",
            "-c", "copy", "-movflags", "+faststart", candidate,
        ]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
        if proc.returncode == 0 and os.path.isfile(candidate) and os.path.getsize(candidate) > 1024:
            try:
                duration = validate_video_file(candidate)
                return duration, candidate, (
                    "FFmpeg восстановил MP4-контейнер во временной копии; исходный файл не менялся."
                )
            except Exception as verify_exc:
                remux_error = str(getattr(verify_exc, "detail", None) or verify_exc)
        else:
            remux_error = (proc.stderr or proc.stdout or f"FFmpeg exit code {proc.returncode}").strip()[-1200:]
    except subprocess.TimeoutExpired:
        remux_error = "Безопасный remux превысил лимит 240 секунд."
    except Exception as recovery_exc:
        remux_error = f"{type(recovery_exc).__name__}: {recovery_exc}"

    try:
        if os.path.exists(candidate):
            os.remove(candidate)
    except OSError:
        pass
    raise HTTPException(
        status_code=400,
        detail=(
            "Видео не прошло проверку контейнера. Выполнена попытка безопасного remux во временный файл, "
            "но FFmpeg не смог восстановить читаемый видеопоток. Исходный файл не изменён; код анализа остановлен "
            "до загрузки Whisper/VLM. Ошибка контейнера: " + detail[:500] + ". Ошибка восстановления: " + remux_error[:700]
            + ". Вероятнее всего, скачивание/экспорт оборвано или MP4-фрагменты повреждены; повторно скачайте файл или экспортируйте его в обычный MP4 (H.264/AAC)."
        ),
    )


'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Ожидалось ровно одно вхождение {label}; найдено {count}. Файлы не изменены.")
    return source.replace(old, new, 1)


def patch_engine(source: str) -> str:
    try:
        ast.parse(source, filename=ENGINE_NAME)
    except SyntaxError as exc:
        raise ValueError(f"Исходный движок не разбирается как Python: {exc}") from exc

    if MARKER in source:
        required = (
            "def _vce_describe_scene_with_memory_fallback(",
            "def _vce_validate_video_with_remux(",
            "_vce_describe_scene_with_memory_fallback,",
            "_vce_validate_video_with_remux(temp_path)",
        )
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("Найдена частичная установка recovery-патча: " + ", ".join(missing))
        return source

    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Не найдена функция async def analyze_video().")
    endpoint = '@app.post("/api/v1/analyze")'
    if source.count(endpoint) != 1:
        raise ValueError("Ожидался ровно один endpoint @app.post(\"/api/v1/analyze\").")
    source = replace_once(source, endpoint, HELPERS + endpoint, "recovery helper insertion point")
    route_start = source.find("async def analyze_video(")

    # Replace only the preflight video validation inside analyze_video().
    validation_pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)video_duration\s*=\s*validate_video_file\(temp_path\)[ \t]*$"
    )
    validations = list(validation_pattern.finditer(source, route_start))
    if len(validations) != 1:
        raise ValueError(
            "Не найдено единственное video_duration = validate_video_file(temp_path) внутри analyze_video(); "
            f"найдено {len(validations)}. Файлы не изменены."
        )
    m = validations[0]
    i = m.group("i")
    replacement = (
        i + "video_duration, _vce_recovered_video_path, _vce_recovery_note = _vce_validate_video_with_remux(temp_path)\n"
        + i + "if _vce_recovered_video_path:\n"
        + i + "    _vce_original_temp_path = temp_path\n"
        + i + "    if locals().get(\"owns_temp_path\", False) and os.path.exists(_vce_original_temp_path):\n"
        + i + "        try:\n"
        + i + "            os.remove(_vce_original_temp_path)\n"
        + i + "        except OSError:\n"
        + i + "            pass\n"
        + i + "    temp_path = _vce_recovered_video_path\n"
        + i + "    owns_temp_path = True\n"
        + i + "    print(\"⚠️ [MP4 recovery] \" + str(_vce_recovery_note or \"Используется восстановленная временная копия.\"))"
    )
    source = source[:m.start()] + replacement + source[m.end():]

    route_start = source.find("async def analyze_video(")
    # Route VLM calls through the memory fallback without changing the success path.
    call_pattern = re.compile(
        r"(?ms)^(?P<i>[ \t]*)raw\s*=\s*await\s+asyncio\.to_thread\(\s*\n"
        r"(?P<j>[ \t]*)vlm_engine\.describe_scene,\s*\n"
        r"(?P=j)images,\s*\n"
        r"(?P=j)full_visual_prompt,\s*\n"
        r"(?P=j)max_tokens\s*=\s*vlm_max_tokens_scene,\s*\n"
        r"(?P=i)\)"
    )
    calls = list(call_pattern.finditer(source, route_start))
    if len(calls) != 1:
        raise ValueError(
            "Не удалось однозначно заменить VLM-вызов asyncio.to_thread(vlm_engine.describe_scene, images, full_visual_prompt, max_tokens=vlm_max_tokens_scene). "
            f"Найдено {len(calls)}. Файлы не изменены."
        )
    m = calls[0]
    i, j = m.group("i"), m.group("j")
    replacement = (
        i + "raw = await asyncio.to_thread(\n"
        + j + "_vce_describe_scene_with_memory_fallback,\n"
        + j + "vlm_engine,\n"
        + j + "images,\n"
        + j + "full_visual_prompt,\n"
        + j + "vlm_max_tokens_scene,\n"
        + i + ")"
    )
    source = source[:m.start()] + replacement + source[m.end():]

    # Disable subsequent VLM inference for this job only when the helper returns
    # its explicit sentinel; leave normal parsed model output untouched.
    parse_pattern = re.compile(r"(?m)^(?P<i>[ \t]*)data\s*=\s*safe_json_parse\(raw\)[ \t]*$")
    parses = list(parse_pattern.finditer(source, route_start))
    if len(parses) != 1:
        raise ValueError(f"Oжидался один data = safe_json_parse(raw) в analyze_video(), найдено {len(parses)}.")
    m = parses[0]
    i = m.group("i")
    replacement = (
        m.group(0) + "\n"
        + i + "if isinstance(data, dict) and data.get(\"_vce_memory_fallback\"):\n"
        + i + "    run_vlm = False\n"
        + i + "    print(\"🛟 [VLM memory guard] VLM отключена до конца текущего видео; аудио и OCR продолжаются.\")"
    )
    source = source[:m.start()] + replacement + source[m.end():]

    try:
        ast.parse(source, filename=ENGINE_NAME)
    except SyntaxError as exc:
        raise ValueError(f"После патча Python не разбирается: {exc}") from exc
    return source


def patch_settings(settings_text: str) -> str:
    settings = json.loads(settings_text)
    if not isinstance(settings, dict):
        raise ValueError("Корень videocontext_settings.json должен быть JSON-объектом.")
    # Directly backed by user's full-video benchmark: MPS is about 2.76x faster
    # than the best tested CPU configuration (34.338 s vs 94.871 s inference).
    settings["whisper_acceleration"] = True
    settings["enable_mlx_whisper"] = False
    settings["enable_stage_timing"] = True
    # Intentionally do not change VLM resolution/scene thresholds: the benchmark
    # did not compare visual accuracy at different settings.
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".recovery_patch.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def prepare(project: Path):
    engine_path = project / ENGINE_NAME
    settings_path = project / SETTINGS_NAME
    missing = [str(p) for p in (engine_path, settings_path) if not p.is_file()]
    if missing:
        raise FileNotFoundError("Не найдены обязательные файлы: " + ", ".join(missing))
    original_engine = engine_path.read_text(encoding="utf-8")
    original_settings = settings_path.read_text(encoding="utf-8")
    changed_engine = patch_engine(original_engine)
    changed_settings = patch_settings(original_settings)
    json.loads(changed_settings)
    ast.parse(changed_engine, filename=str(engine_path))
    return engine_path, settings_path, original_engine, original_settings, changed_engine, changed_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true", help="Показать diff без записи")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    try:
        ep, sp, old_e, old_s, new_e, new_s = prepare(project)
    except Exception as exc:
        print(f"ОШИБКА: {exc}")
        print("Файлы не изменены.")
        return 2

    changes = [(ep, old_e, new_e), (sp, old_s, new_s)]
    changed = [(p, old, new) for p, old, new in changes if old != new]
    if not changed:
        print("Recovery-патч уже установлен; benchmark-backed настройки уже применены.")
        return 0
    for path, old, new in changed:
        print(f"\n--- {path.name} ---")
        diff = difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=str(path), tofile=str(path) + " (patched)",
        )
        print("".join(list(diff)[:180]))
    if args.dry_run:
        print(f"\nDRY RUN: подготовлены изменения в {len(changed)} файлах; запись не выполнялась.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {}
    written = []
    try:
        for path, old, new in changed:
            backup = path.with_name(path.name + f".before_metal_media_recovery_{stamp}.bak")
            shutil.copy2(path, backup)
            backups[path] = backup
        for path, old, new in changed:
            atomic_write(path, new)
            written.append(path)
        ast.parse(ep.read_text(encoding="utf-8"), filename=str(ep))
        json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        for path, old, new in changed:
            if path in written:
                path.write_text(old, encoding="utf-8")
        print("ОШИБКА после записи: исходные файлы восстановлены; резервные копии сохранены.")
        raise

    print("\n✅ Recovery-патч установлен.")
    for path, backup in backups.items():
        print(f"Backup {path.name}: {backup}")
    print("Настройки на базе бенчмарка: Whisper MPS включён; MLX Whisper выключен; тайминги включены.")
    print("Разрешение VLM и параметры сегментации не изменялись без проверки качества.")
    print("Перезапусти VideoContext Engine после завершения активных задач.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
