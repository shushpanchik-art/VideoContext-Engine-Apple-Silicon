#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply memory-safe MLX inference and FFmpeg fallback for files OpenCV cannot decode.

This patch is intentionally conservative: it keeps the selected VLM and audio pipeline,
adds explicit release of PyTorch MPS / MLX cached buffers, retries VLM OOM with smaller
copies of frames, and creates a temporary H.264 proxy only when OpenCV cannot read a
selected file. Original video files and the user's TXT/JSON choices are preserved.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT = Path.home() / "VideoContext-Engine"
ENGINE_NAME = "VideoContextEngine_v3.19.py"
CONFIG_NAME = "videocontext_settings.json"

MEMORY_HELPERS = r'''def release_accelerator_memory(stage: str = "") -> None:
    """Release unused PyTorch MPS and MLX allocator caches between model stages."""
    gc.collect()
    try:
        import torch
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            try:
                torch.mps.synchronize()
            except Exception:
                pass
            torch.mps.empty_cache()
    except Exception as exc:
        print(f"⚠️ Не удалось очистить кэш PyTorch MPS ({stage}): {exc}")
    try:
        import mlx.core as mx
        mx.metal.clear_cache()
    except Exception as exc:
        # Keep this helper compatible with MLX versions without metal.clear_cache().
        print(f"⚠️ Не удалось очистить кэш MLX ({stage}): {exc}")
    gc.collect()


def is_gpu_memory_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(token in message for token in (
        "insufficient memory",
        "outofmemory",
        "out of memory",
        "kIOGPUCommandBufferCallbackErrorOutOfMemory".lower(),
        "metal command buffer",
    ))


def resize_images_for_oom_retry(images: List[Image.Image], max_side: int) -> List[Image.Image]:
    """Make smaller copies for a bounded retry; never mutate OCR source images."""
    resized: List[Image.Image] = []
    resampling = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    for image in images:
        candidate = image.copy()
        if max(candidate.size) > max_side:
            candidate.thumbnail((max_side, max_side), resampling)
        resized.append(candidate)
    return resized


def ensure_opencv_compatible_video(file_path: str) -> str:
    """Return file_path if OpenCV can decode it; otherwise make a temporary H.264 proxy."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(400, f"Видео не найдено или файл пуст: {path.name}")

    cap = cv2.VideoCapture(str(path))
    opened = cap.isOpened()
    ret, frame = cap.read() if opened else (False, None)
    cap.release()
    if opened and ret and frame is not None:
        return str(path)

    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe:
        raise HTTPException(
            400,
            f"OpenCV не смог прочитать видео «{path.name}», а ffprobe не найден. "
            "Установите FFmpeg (ffmpeg и ffprobe) и повторите выбор файла."
        )
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=45,
        )
    except Exception as exc:
        raise HTTPException(400, f"Не удалось проверить видео «{path.name}» через ffprobe: {exc}") from exc

    try:
        probe_data = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        probe_data = {}
    video_streams = [
        stream for stream in (probe_data.get("streams") or [])
        if stream.get("codec_type") == "video"
    ]
    if probe.returncode != 0 or not video_streams:
        details = (probe.stderr or "").strip()[-600:]
        raise HTTPException(
            400,
            f"Файл «{path.name}» не содержит читаемого видеопотока или повреждён. "
            f"ffprobe: {details or 'видеопоток не обнаружен'}"
        )

    if not ffmpeg:
        raise HTTPException(
            400,
            f"Видеопоток в «{path.name}» найден, но OpenCV не может его декодировать. "
            "FFmpeg не найден для создания совместимой временной копии."
        )

    codec = str(video_streams[0].get("codec_name") or "неизвестный")
    fd, proxy_path = tempfile.mkstemp(prefix="vce_compatible_", suffix=".mp4")
    import os
    os.close(fd)
    os.unlink(proxy_path)  # FFmpeg creates the output file itself.
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=1920:1920:force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", proxy_path,
    ]
    try:
        converted = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=7200,
        )
        if converted.returncode != 0 or not Path(proxy_path).is_file() or Path(proxy_path).stat().st_size == 0:
            details = (converted.stderr or "").strip()[-1000:]
            raise RuntimeError(details or f"ffmpeg завершился с кодом {converted.returncode}")
        test_cap = cv2.VideoCapture(proxy_path)
        proxy_opened = test_cap.isOpened()
        proxy_read, _ = test_cap.read() if proxy_opened else (False, None)
        test_cap.release()
        if not proxy_opened or not proxy_read:
            raise RuntimeError("OpenCV не смог открыть даже преобразованный H.264-файл.")
    except Exception as exc:
        try:
            Path(proxy_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            400,
            f"Видео «{path.name}» определяется как {codec}, но не декодируется OpenCV. "
            f"Не удалось создать совместимую копию: {exc}"
        ) from exc

    print(
        f"⚠️ OpenCV не открыл «{path.name}» (кодек {codec}); "
        "анализ продолжится на временной H.264-копии. Оригинал не изменён."
    )
    return proxy_path


'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Для {label} ожидалось ровно одно совпадение, найдено {count}.")
    return source.replace(old, new, 1)


def ensure_import(source: str, name: str, anchor: str = "import shutil\n") -> str:
    if re.search(rf"(?m)^import {re.escape(name)}(?:\s|$)", source):
        return source
    if anchor not in source:
        raise ValueError(f"Не найден импорт-якорь {anchor.strip()!r} для добавления {name}.")
    return source.replace(anchor, anchor + f"import {name}\n", 1)


def patch_mlx_oom_retry(source: str) -> str:
    if "VLM OOM: повторная попытка" in source:
        return source
    old = '''        output = self._generate_fn(
            self.model,
            self.processor,
            formatted_prompt,
            imgs,
            max_tokens=max_tokens,
            verbose=False,
            temp=0.0,
        )'''
    new = '''        try:
            output = self._generate_fn(
                self.model,
                self.processor,
                formatted_prompt,
                imgs,
                max_tokens=max_tokens,
                verbose=False,
                temp=0.0,
            )
        except Exception as original_error:
            if not is_gpu_memory_error(original_error):
                raise
            last_error = original_error
            retry_succeeded = False
            original_sizes = [image.size for image in images]
            for retry_side in (768, 512):
                retry_images = resize_images_for_oom_retry(images, retry_side)
                if [image.size for image in retry_images] == original_sizes:
                    continue
                retry_input = retry_images if len(retry_images) > 1 else retry_images[0]
                release_accelerator_memory(f"VLM OOM retry {retry_side}px")
                print(
                    f"⚠️ VLM OOM: повторная попытка с кадрами до {retry_side}px; "
                    "модель и аудиоанализ сохраняются."
                )
                try:
                    output = self._generate_fn(
                        self.model,
                        self.processor,
                        formatted_prompt,
                        retry_input,
                        max_tokens=max_tokens,
                        verbose=False,
                        temp=0.0,
                    )
                    print(f"✅ VLM восстановился на разрешении до {retry_side}px.")
                    retry_succeeded = True
                    break
                except Exception as retry_error:
                    if not is_gpu_memory_error(retry_error):
                        raise
                    last_error = retry_error
                    release_accelerator_memory(f"VLM OOM retry failed {retry_side}px")
            if not retry_succeeded:
                raise RuntimeError(
                    "Недостаточно памяти Metal для VLM даже после уменьшения кадра до 768/512px. "
                    "Закройте другие приложения, использующие GPU, и повторите анализ."
                ) from last_error'''
    return replace_once(source, old, new, "генерация MLX VLM с безопасным повтором")


def patch_engine(source: str) -> str:
    required = (
        "class VLMProvider:",
        "class MLXEngine(VLMProvider):",
        "def validate_video_file(file_path: str) -> float:",
        "video_duration = validate_video_file(temp_path)",
        "owns_temp_path = False",
        "del w_model\n                        gc.collect()",
        "if not skip_visual or generate_summary:\n                    t0 = time.time()",
        "def analyze_video(",
    )
    missing = [needle for needle in required if needle not in source]
    if missing:
        raise ValueError(
            "Версия движка не совпадает с ожидаемой или нужные патчи ещё не установлены. "
            "Отсутствуют маркеры: " + ", ".join(repr(x) for x in missing)
        )

    source = ensure_import(source, "tempfile")
    source = ensure_import(source, "subprocess")
    source = ensure_import(source, "shutil")

    if "def release_accelerator_memory(" not in source:
        source = replace_once(
            source, "class VLMProvider:", MEMORY_HELPERS + "class VLMProvider:",
            "вставка функций очистки памяти и совместимости видео",
        )

    source = patch_mlx_oom_retry(source)

    # PyTorch MPS can retain freed Whisper tensors in its allocator cache. Release it before MLX.
    old_whisper = "                        del w_model\n                        gc.collect()"
    new_whisper = "                        del w_model\n                        release_accelerator_memory('after Whisper inference')"
    if new_whisper not in source:
        source = replace_once(source, old_whisper, new_whisper, "очистка кэша после Whisper")

    old_load = "                if not skip_visual or generate_summary:\n                    t0 = time.time()"
    new_load = (
        "                if not skip_visual or generate_summary:\n"
        "                    release_accelerator_memory('before VLM load')\n"
        "                    t0 = time.time()"
    )
    if new_load not in source:
        source = replace_once(source, old_load, new_load, "очистка памяти перед загрузкой VLM")

    # Clean cached buffers after every model unload (including the RAM- post-processing path).
    unload_pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)vlm_engine\.unload_model\(\)\n(?![ \t]*release_accelerator_memory\()"
    )
    source = unload_pattern.sub(
        lambda match: (
            f"{match.group('indent')}vlm_engine.unload_model()\n"
            f"{match.group('indent')}release_accelerator_memory('after VLM unload')\n"
        ),
        source,
    )

    # Validate the local chooser/cleanup patch is present: converted proxies must be removed,
    # but the original path selected in Finder must never be unlinked.
    preflight = '''        original_input_path = temp_path
        prepared_path = await asyncio.to_thread(ensure_opencv_compatible_video, temp_path)
        if prepared_path != original_input_path:
            if owns_temp_path and os.path.exists(original_input_path):
                try:
                    os.remove(original_input_path)
                except OSError:
                    pass
            temp_path = prepared_path
            owns_temp_path = True
        video_duration = validate_video_file(temp_path)'''
    if preflight not in source:
        source = replace_once(
            source,
            "        video_duration = validate_video_file(temp_path)",
            preflight,
            "предварительная проверка и fallback для OpenCV",
        )

    if "await asyncio.to_thread(ensure_opencv_compatible_video, temp_path)" not in source:
        raise ValueError("Проверка не прошла: отсутствует предварительная проверка видео.")
    if "VLM OOM: повторная попытка" not in source:
        raise ValueError("Проверка не прошла: отсутствует повтор VLM при ошибке Metal OOM.")
    ast.parse(source, filename=ENGINE_NAME)
    return source


def safe_profile(current: dict[str, Any]) -> dict[str, Any]:
    """Apply only memory-critical settings; preserve model, prompts, OCR/audio, and TXT choice."""
    if not isinstance(current, dict):
        raise ValueError("Настройки должны быть JSON-объектом.")
    updated = dict(current)

    try:
        resolution = int(updated.get("vlm_resolution", 768))
    except (TypeError, ValueError):
        resolution = 768
    updated["vlm_resolution"] = min(max(128, resolution), 768)

    try:
        frames = int(updated.get("keyframes_per_scene", 1))
    except (TypeError, ValueError):
        frames = 1
    updated["keyframes_per_scene"] = min(max(1, frames), 2)

    updated["ram_mode"] = "ram-"
    # Never set generate_txt here: the user's current selection must be preserved.
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", type=Path, default=PROJECT,
        help=f"Папка проекта с движком (по умолчанию {PROJECT})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Проверить изменения без записи.")
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    engine_path = project / ENGINE_NAME
    config_path = project / CONFIG_NAME
    if not engine_path.is_file():
        print(f"ОШИБКА: движок не найден: {engine_path}", file=sys.stderr)
        return 2
    if not config_path.is_file():
        print(f"ОШИБКА: настройки не найдены: {config_path}", file=sys.stderr)
        return 2

    try:
        source_original = engine_path.read_text(encoding="utf-8")
        config_original = json.loads(config_path.read_text(encoding="utf-8-sig"))
        source_updated = patch_engine(source_original)
        config_updated = safe_profile(config_original)
        ast.parse(source_updated, filename=str(engine_path))
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        print("Изменения не записаны.", file=sys.stderr)
        return 3

    source_changed = source_updated != source_original
    config_changed = config_updated != config_original
    print("Проверка кода: OK")
    print(f"MLX модель сохранена: {config_updated.get('vlm_model', '(текущее значение)')}")
    print(f"VLM resolution: {config_original.get('vlm_resolution', '(default)')} -> {config_updated['vlm_resolution']}")
    print(f"Кадров на сцену: {config_original.get('keyframes_per_scene', '(default)')} -> {config_updated['keyframes_per_scene']}")
    print(f"RAM mode: {config_original.get('ram_mode', '(default)')} -> {config_updated['ram_mode']}")
    print(f"TXT setting preserved: generate_txt={config_updated.get('generate_txt', '(not present)')}")
    if args.dry_run:
        print("DRY RUN: файлы не изменены.")
        return 0
    if not source_changed and not config_changed:
        print("Патч уже установлен; изменений нет.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: list[Path] = []
    try:
        if source_changed:
            backup = engine_path.with_name(f"{engine_path.name}.before_memory_safety_{stamp}.bak")
            shutil.copy2(engine_path, backup)
            backups.append(backup)
        if config_changed:
            backup = config_path.with_name(f"{config_path.name}.before_memory_safety_{stamp}.bak")
            shutil.copy2(config_path, backup)
            backups.append(backup)

        if source_changed:
            engine_path.write_text(source_updated, encoding="utf-8")
            ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
        if config_changed:
            config_path.write_text(json.dumps(config_updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ОШИБКА записи: {exc}", file=sys.stderr)
        print("Резервные копии сохранены для ручного восстановления:", file=sys.stderr)
        for backup in backups:
            print(f"  {backup}", file=sys.stderr)
        return 4

    print("Патч установлен.")
    print("Он не меняет исходные ролики, имя выбранной модели, аудио, JSON и выбор TXT.")
    print("Резервные копии:")
    for backup in backups:
        print(f"  {backup}")
    print("Теперь остановите сервер после завершения текущих задач и запустите VideoContext Engine заново.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
