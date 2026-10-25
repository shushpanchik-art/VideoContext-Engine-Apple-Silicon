#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch VCE optimization checkboxes, optional MLX Whisper, and a local stop-server control.

The five experimental switches default to False. The installer applies only the
explicitly requested balanced benchmark settings, preserves user prompts and
generate_txt, makes backups, and refuses to write on integration mismatches.
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

TARGET = Path("VideoContextEngine_v3.19.py")
CONFIG = Path("videocontext_settings.json")
UI_FILE = Path("videocontext_web_ui.html")
MARKER = "# VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1"

HELPERS = r'''# VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1
_VCE_MLX_WHISPER_REPOS = {
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
_VCE_OCR_CACHE = {}
_VCE_OCR_CACHE_MAX = 256


def _vce_validate_mlx_whisper_model(model_name):
    """Validate opt-in MLX Whisper availability and resolve its model repository."""
    try:
        import mlx_whisper  # noqa: F401
    except ImportError as exc:
        raise HTTPException(
            422,
            "MLX Whisper выбран в настройках, но пакет не установлен в окружении VCE. "
            "В активном venv установите его командой: python -m pip install mlx-whisper",
        ) from exc
    normalized = str(model_name or "").strip().lower()
    repo = _VCE_MLX_WHISPER_REPOS.get(normalized)
    if not repo:
        override = os.environ.get("VCE_MLX_WHISPER_REPO", "").strip()
        if not override:
            raise HTTPException(
                422,
                "Для MLX Whisper поддерживаются модели large, large-v3 и large-v3-turbo. "
                "Для другой совместимой MLX-модели задайте VCE_MLX_WHISPER_REPO.",
            )
        repo = override
    return repo


def _vce_load_whisper_model(model_name, use_mlx, acceleration_enabled=None):
    """Avoid PyTorch when MLX is selected; otherwise preserve VCE's configured loader."""
    if use_mlx:
        _vce_validate_mlx_whisper_model(model_name)
        return None
    # Preserve the existing loader and its optional MPS/CUDA selection flag.
    loader = globals().get("load_whisper_model")
    if callable(loader):
        if acceleration_enabled is None:
            return loader(model_name)
        return loader(model_name, acceleration_enabled=acceleration_enabled)
    return whisper.load_model(model_name)


def _vce_transcribe_audio(audio_path, model_name, model, use_mlx, **kwargs):
    """Use the selected ASR backend while keeping Whisper-compatible output fields."""
    if not use_mlx:
        return model.transcribe(audio_path, **kwargs)
    import mlx_whisper
    repo = _vce_validate_mlx_whisper_model(model_name)
    return mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=repo,
        **kwargs,
    )


def _vce_adaptive_keyframe_limit(scene, configured_max):
    """Treat keyframes_per_scene as a ceiling and lower it for short scenes."""
    try:
        duration = max(0.0, float(scene.get("end", 0.0)) - float(scene.get("start", 0.0)))
        limit = max(1, min(5, int(configured_max)))
    except (TypeError, ValueError, AttributeError):
        return max(1, min(5, int(configured_max or 1)))
    recommended = 1 if duration <= 6.0 else (2 if duration <= 14.0 else limit)
    return max(1, min(limit, recommended))


def _vce_cached_apple_vision_ocr(image):
    """Cache OCR text for byte-identical images, with a bounded per-process cache."""
    digest = hashlib.sha256()
    digest.update(str(getattr(image, "mode", "")).encode("utf-8"))
    digest.update(str(getattr(image, "size", "")).encode("utf-8"))
    digest.update(image.tobytes())
    key = digest.hexdigest()
    if key in _VCE_OCR_CACHE:
        return _VCE_OCR_CACHE[key]
    result = apple_vision_ocr(image)
    if len(_VCE_OCR_CACHE) >= _VCE_OCR_CACHE_MAX:
        _VCE_OCR_CACHE.pop(next(iter(_VCE_OCR_CACHE)))
    _VCE_OCR_CACHE[key] = result
    return result


def _vce_detect_scenes_fast(video_path, threshold, min_duration, max_duration):
    """Experimental scene scan: seek to one frame per second and histogram a small frame."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Erreur lecture vidéo")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
    if duration <= 0:
        cap.release()
        return detect_scenes(video_path, threshold, min_duration, max_duration)

    scenes = []
    last_hist = None
    start_sec = 0.0
    prev_sec = 0.0
    sample_sec = 0.0
    sample_interval = 1.0
    try:
        while sample_sec < duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, sample_sec * 1000.0)
            ret, frame = cap.read()
            if not ret:
                break
            height, width = frame.shape[:2]
            longest = max(height, width)
            if longest > 480:
                scale = 480.0 / float(longest)
                frame = cv2.resize(
                    frame,
                    (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            hist = compute_hsv_histogram(frame)
            is_visual_change = False
            if last_hist is not None:
                score = cv2.compareHist(last_hist, hist, cv2.HISTCMP_CORREL)
                is_visual_change = (1.0 - score) > threshold
            duration_ok = (sample_sec - start_sec) >= min_duration
            force_cut = (sample_sec - start_sec) >= max_duration
            if (is_visual_change and duration_ok) or force_cut:
                scenes.append({"start": start_sec, "end": prev_sec})
                start_sec = sample_sec
            last_hist = hist
            prev_sec = sample_sec
            sample_sec += sample_interval
    finally:
        cap.release()

    if prev_sec > start_sec:
        scenes.append({"start": start_sec, "end": min(prev_sec, duration)})
    if not scenes and duration > 0:
        scenes.append({"start": 0.0, "end": duration})
    return scenes


'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Ожидалось ровно одно вхождение {label}; найдено {count}. Файлы не изменены.")
    return source.replace(old, new, 1)


def add_form_fields(source: str) -> str:
    fields = (
        '    enable_stage_timing: bool = Form(False, description="Добавлять подробные тайминги этапов в JSON-ответ"),\n'
        '    enable_adaptive_keyframes: bool = Form(False, description="Адаптивно уменьшать число кадров на коротких сценах"),\n'
        '    enable_ocr_cache: bool = Form(False, description="Кэшировать OCR для повторяющихся кадров"),\n'
        '    enable_mlx_whisper: bool = Form(False, description="Использовать опциональный MLX Whisper вместо PyTorch Whisper"),\n'
        '    enable_fast_scene_detection: bool = Form(False, description="Экспериментальная ускоренная детекция сцен с выборкой 1 кадр/сек"),\n'
    )
    names = ("enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
             "enable_mlx_whisper", "enable_fast_scene_detection")
    existing = [name for name in names if re.search(rf"\b{name}\s*:\s*bool\s*=\s*Form\(", source)]
    if len(existing) == len(names):
        return source
    if existing:
        raise ValueError("Найден только набор части новых API-параметров. Проверьте исходник вручную.")
    start = source.find("async def analyze_video(")
    if start < 0:
        raise ValueError("Не найдена сигнатура async def analyze_video().")
    close = re.search(r"(?m)^(?P<i>[ \t]*)\):[ \t]*$", source[start:])
    if not close:
        raise ValueError("Не найден конец сигнатуры analyze_video().")
    pos = start + close.start()
    return source[:pos] + fields + source[pos:]


def patch_scene_detection(source: str) -> str:
    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Не найдена сигнатура async def analyze_video().")
    if "_vce_detect_scenes_fast(" in source[route_start:]:
        return source

    # The engine has appeared in two forms:
    #   scenes = detect_scenes(...)
    #   scenes = await asyncio.to_thread(detect_scenes, ...)
    # Inspect the assignment AST and preserve the threading wrapper when present.
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Исходный Python не разбирается до патча: {exc}") from exc
    analyze_node = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
         and node.name == "analyze_video"),
        None,
    )
    if analyze_node is None:
        raise ValueError("AST: не найдена функция analyze_video().")

    candidates = []
    observed = []
    for statement in ast.walk(analyze_node):
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue

        value = statement.value
        awaited = isinstance(value, ast.Await)
        call = value.value if awaited else value
        if not isinstance(call, ast.Call):
            continue

        func = call.func
        func_name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )

        if func_name == "detect_scenes":
            observed.append(f"detect_scenes direct (строка {call.lineno})")
            candidates.append((statement, targets[0].id, "direct", call))
        elif (
            func_name == "to_thread"
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "detect_scenes"
        ):
            observed.append(f"asyncio.to_thread(detect_scenes, ...) (строка {call.lineno})")
            candidates.append((statement, targets[0].id, "to_thread", call))

    if len(candidates) != 1:
        details = "; ".join(observed[:12]) or "присваивание результата detect_scenes не найдено"
        raise ValueError(
            "Не удалось однозначно найти присваивание результата detect_scenes(...) внутри analyze_video(). "
            f"Найдено подходящих вызовов: {len(candidates)}. Обнаруженные кандидаты: {details}. "
            "Локальная версия движка отличается от ожидаемых вариантов. Файлы не изменены."
        )

    statement, target_name, mode, call_node = candidates[0]
    if mode == "to_thread":
        call_kwargs = call_node.keywords
        args = list(call_node.args[1:])
        call_func = ast.unparse(call_node.func)
    else:
        call_kwargs = call_node.keywords
        args = list(call_node.args)
        call_func = ""

    if not any(keyword.arg == "max_duration" for keyword in call_kwargs):
        raise ValueError(
            "Вызов detect_scenes(...) не содержит ожидаемого аргумента max_duration; файлы не изменены."
        )

    call_args = [ast.unparse(arg) for arg in args]
    for keyword in call_kwargs:
        if keyword.arg is None:
            call_args.append("**" + ast.unparse(keyword.value))
        else:
            call_args.append(keyword.arg + "=" + ast.unparse(keyword.value))

    indent = " " * statement.col_offset

    def assignment_for(function_name: str) -> str:
        branch_indent = indent + "    "
        call_indent = branch_indent + "    "
        if mode == "to_thread":
            header = f"{branch_indent}{target_name} = await {call_func}(\n{call_indent}{function_name},\n"
            body = "".join(f"{call_indent}{arg},\n" for arg in call_args)
            return header + body + f"{branch_indent})\n"
        header = f"{branch_indent}{target_name} = {function_name}(\n"
        body = "".join(f"{call_indent}{arg},\n" for arg in call_args)
        return header + body + f"{branch_indent})\n"

    replacement = (
        indent + "scene_detection_started = time.perf_counter()\n"
        + indent + "apple_ocr_seconds = 0.0\n"
        + indent + "if enable_fast_scene_detection:\n"
        + assignment_for("_vce_detect_scenes_fast")
        + indent + "else:\n"
        + assignment_for("detect_scenes")
        + indent + "scene_detection_seconds = time.perf_counter() - scene_detection_started\n"
    )
    lines = source.splitlines(keepends=True)
    lines[statement.lineno - 1:statement.end_lineno] = [replacement]
    return "".join(lines)

def patch_adaptive_keyframes(source: str) -> str:
    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Не найдена сигнатура analyze_video().")
    pattern = re.compile(
        r"(?ms)^(?P<i>[ \t]*)keyframes\s*=\s*sample_keyframes_for_scene\(\n"
        r"(?P<args>.*?)^(?P=i)\)"
    )
    matches = list(pattern.finditer(source, route_start))
    if len(matches) != 1:
        raise ValueError(f"Ожидался один вызов sample_keyframes_for_scene(...), найдено {len(matches)}.")
    m = matches[0]
    if "effective_keyframes" in m.group(0):
        return source
    args = m.group("args")
    if "keyframes_per_scene" not in args:
        raise ValueError("Не найден keyframes_per_scene в вызове sample_keyframes_for_scene().")
    args = args.replace("keyframes_per_scene", "effective_keyframes", 1)
    i = m.group("i")
    replacement = (
        i + "effective_keyframes = _vce_adaptive_keyframe_limit(scene, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene\n"
        + i + "keyframes = sample_keyframes_for_scene(\n"
        + args + i + ")"
    )
    return source[:m.start()] + replacement + source[m.end():]


def patch_ocr_cache(source: str) -> str:
    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Не найдена сигнатура async def analyze_video().")
    route = source[route_start:]

    # Idempotence guards cover both the older single-frame integration and the
    # current multi-keyframe asyncio.to_thread OCR loop.
    if (
        "_vce_cached_apple_vision_ocr(keyframes[0]" in route
        or "asyncio.to_thread((_vce_cached_apple_vision_ocr if enable_ocr_cache else apple_vision_ocr)" in route
    ):
        return source

    # Older engine variant: one synchronous OCR call on the first frame.
    pattern = re.compile(
        r'(?m)^(?P<i>[ \t]*)scene_ocr_text\s*=\s*apple_vision_ocr\(keyframes\[0\]\["image"\]\)[ \t]*$'
    )
    matches = list(pattern.finditer(source, route_start))
    if len(matches) == 1:
        m = matches[0]
        i = m.group("i")
        replacement = (
            i + "_vce_ocr_started = time.perf_counter()\n"
            + i + "scene_ocr_text = (\n"
            + i + "    _vce_cached_apple_vision_ocr(keyframes[0][\"image\"]) if enable_ocr_cache\n"
            + i + "    else apple_vision_ocr(keyframes[0][\"image\"])\n"
            + i + ")\n"
            + i + "apple_ocr_seconds += time.perf_counter() - _vce_ocr_started"
        )
        return source[:m.start()] + replacement + source[m.end():]

    # Current VCE variant OCRs every keyframe asynchronously and prefers the
    # original-resolution image. Switch only the callable, preserving image choice,
    # await/thread behavior, cancellation handling, and OCR aggregation.
    async_call = re.compile(
        r'(?m)^(?P<i>[ \t]*)extracted\s*=\s*await\s+asyncio\.to_thread\('
        r'\s*apple_vision_ocr\s*,\s*kf\.get\("original_image",\s*kf\["image"\]\)\s*\)[ \t]*$'
    )
    calls = list(async_call.finditer(source, route_start))
    if len(calls) != 1:
        raise ValueError(
            "Не удалось однозначно найти асинхронный вызов "
            "asyncio.to_thread(apple_vision_ocr, kf.get('original_image', kf['image'])); "
            f"найдено {len(calls)}. Файлы не изменены."
        )

    t_start_pattern = re.compile(r"(?m)^(?P<i>[ \t]*)t_ocr_start\s*=\s*time\.time\(\)[ \t]*$")
    ocr_elapsed_pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)ocr_infer_time\s*\+=\s*time\.time\(\)\s*-\s*t_ocr_start[ \t]*$"
    )
    start_matches = list(t_start_pattern.finditer(source, route_start))
    elapsed_matches = list(ocr_elapsed_pattern.finditer(source, route_start))
    if len(start_matches) != 1 or len(elapsed_matches) != 1:
        raise ValueError(
            "Найдены не все ожидаемые таймеры OCR (t_ocr_start / ocr_infer_time); "
            "файлы не изменены."
        )

    m = calls[0]
    updated_call, n_subs = re.subn(
        r"asyncio\.to_thread\(\s*apple_vision_ocr\s*,",
        "asyncio.to_thread((_vce_cached_apple_vision_ocr if enable_ocr_cache else apple_vision_ocr),",
        m.group(0),
        count=1,
    )
    if n_subs != 1:
        raise ValueError("Не удалось заменить callable в асинхронном OCR; файлы не изменены.")
    source = source[:m.start()] + updated_call + source[m.end():]

    # Measure the complete Apple OCR block separately for optional JSON diagnostics.
    # Re-find anchors after the call replacement because source offsets have changed.
    start_matches = list(t_start_pattern.finditer(source, route_start))
    elapsed_matches = list(ocr_elapsed_pattern.finditer(source, route_start))
    if len(start_matches) != 1 or len(elapsed_matches) != 1:
        raise ValueError("Не удалось повторно найти таймеры OCR после замены вызова; файлы не изменены.")

    start_match = start_matches[0]
    indent = start_match.group("i")
    after_start = (
        source[start_match.start():start_match.end()]
        + "\n" + indent + "_vce_ocr_wall_start = time.perf_counter()"
    )
    source = source[:start_match.start()] + after_start + source[start_match.end():]

    elapsed_matches = list(ocr_elapsed_pattern.finditer(source, route_start))
    if len(elapsed_matches) != 1:
        raise ValueError("Не удалось повторно найти ocr_infer_time; файлы не изменены.")
    elapsed_match = elapsed_matches[0]
    indent = elapsed_match.group("i")
    before_elapsed = (
        indent + "apple_ocr_seconds += time.perf_counter() - _vce_ocr_wall_start\n"
        + source[elapsed_match.start():elapsed_match.end()]
    )
    source = source[:elapsed_match.start()] + before_elapsed + source[elapsed_match.end():]
    return source

def patch_mlx_whisper(source: str) -> str:
    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Не найдена сигнатура analyze_video().")
    route = source[route_start:]

    # Support VCE variants where the result variable, model variable, or path
    # expression differs, as long as the transcribe call is direct or threaded.
    if "_vce_transcribe_audio" not in route:
        async_patterns = [
            re.compile(
                r"(?m)^(?P<i>[ \t]*)(?P<result>[A-Za-z_]\w*)\s*=\s*await\s+asyncio\.to_thread\(\s*\n"
                r"(?P<j>[ \t]*)(?P<model>[A-Za-z_]\w*)\.transcribe,\s*\n"
                r"(?P<k>[ \t]*)(?P<path>[^,\n]+),[ \t]*$"
            ),
            re.compile(
                r"(?m)^(?P<i>[ \t]*)(?P<result>[A-Za-z_]\w*)\s*=\s*await\s+asyncio\.to_thread\("
                r"(?P<model>[A-Za-z_]\w*)\.transcribe,\s*(?P<path>[^,\n]+),"
            ),
        ]
        async_matches = [(pattern, list(pattern.finditer(route))) for pattern in async_patterns]
        matches = [(pattern, match) for pattern, found in async_matches for match in found]
        if len(matches) > 1:
            raise ValueError(f"Найдено несколько кандидатов транскрибации Whisper через asyncio.to_thread: {len(matches)}.")
        if len(matches) == 1:
            pattern, match = matches[0]
            path_expr = match.group("path").strip()
            replacement = (
                match.group("i") + match.group("result") + " = await asyncio.to_thread(\n"
                + match.group("j") + "_vce_transcribe_audio,\n"
                + match.group("k") + path_expr + ", target_whisper, "
                + match.group("model") + ", enable_mlx_whisper,"
            )
            route = route[:match.start()] + replacement + route[match.end():]
        else:
            direct_patterns = [
                re.compile(
                    r"(?m)^(?P<i>[ \t]*)(?P<result>[A-Za-z_]\w*)\s*=\s*(?P<model>[A-Za-z_]\w*)\.transcribe\(\s*\n"
                    r"(?P<j>[ \t]*)(?P<path>[^,\n]+),[ \t]*$"
                ),
                re.compile(
                    r"(?m)^(?P<i>[ \t]*)(?P<result>[A-Za-z_]\w*)\s*=\s*(?P<model>[A-Za-z_]\w*)\.transcribe\("
                    r"(?P<path>[^,\n]+),"
                ),
            ]
            direct_matches = [(pattern, match) for pattern in direct_patterns for match in pattern.finditer(route)]
            if len(direct_matches) != 1:
                # Emit discovered calls in the error so a future source variant can be
                # adapted from concrete evidence rather than guessed at.
                try:
                    tree = ast.parse(source)
                    analyze_node = next(
                        node for node in ast.walk(tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == "analyze_video"
                    )
                    diagnostics = []
                    for node in ast.walk(analyze_node):
                        if isinstance(node, ast.Call):
                            callee = ast.unparse(node.func)
                            if "transcrib" in callee.lower() or "whisper" in callee.lower():
                                diagnostics.append(f"{callee} (строка {node.lineno})")
                    observed = "; ".join(diagnostics[:12]) or "вызовы transcribe/whisper не найдены"
                except Exception:
                    observed = "не удалось разобрать AST"
                raise ValueError(
                    "Не удалось найти однозначный вызов Whisper: ожидается метод *.transcribe(...) "
                    "или await asyncio.to_thread(*.transcribe, ...). "
                    f"Подходящих прямых вызовов найдено: {len(direct_matches)}. "
                    f"Обнаружено в analyze_video(): {observed}. Файлы не изменены."
                )
            pattern, match = direct_matches[0]
            path_expr = match.group("path").strip()
            replacement = (
                match.group("i") + match.group("result") + " = _vce_transcribe_audio(\n"
                + match.group("j") + path_expr + ", target_whisper, "
                + match.group("model") + ", enable_mlx_whisper,"
            )
            route = route[:match.start()] + replacement + route[match.end():]

    # Do not route an MLX-selected model through the existing PyTorch cache.
    route = route.replace(
        "w_model = WHISPER_CACHE[target_whisper]",
        "w_model = None if enable_mlx_whisper else WHISPER_CACHE[target_whisper]",
    )
    route = route.replace(
        "whisper.load_model(target_whisper)",
        "_vce_load_whisper_model(target_whisper, enable_mlx_whisper)",
    )
    if re.search(
        r"(?<![\w.])load_whisper_model\(target_whisper\s*,\s*acceleration_enabled\s*=\s*whisper_acceleration\)",
        route,
    ):
        route = re.sub(
            r"(?<![\w.])load_whisper_model\(target_whisper\s*,\s*acceleration_enabled\s*=\s*whisper_acceleration\)",
            "_vce_load_whisper_model(target_whisper, enable_mlx_whisper, whisper_acceleration)",
            route,
        )
    else:
        route = re.sub(
            r"(?<![\w.])load_whisper_model\(target_whisper\)",
            "_vce_load_whisper_model(target_whisper, enable_mlx_whisper)",
            route,
        )
    route = re.sub(
        r"(?m)^(?P<i>[ \t]*)WHISPER_CACHE\[target_whisper\]\s*=\s*w_model\s*$",
        lambda m: m.group("i") + "if not enable_mlx_whisper:\n"
        + m.group("i") + "    WHISPER_CACHE[target_whisper] = w_model",
        route,
    )

    # Fail before long analysis if the optional backend is selected but unavailable.
    validation_anchor = "video_duration = validate_video_file(temp_path)"
    validation_pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)video_duration = validate_video_file\(temp_path\)[ \t]*$"
    )
    validation_matches = list(validation_pattern.finditer(route))
    if not any("_vce_validate_mlx_whisper_model(target_whisper)" in line for line in route.splitlines()):
        if len(validation_matches) != 1:
            raise ValueError("Не найден единственный video_duration = validate_video_file(temp_path) для MLX-проверки.")
        match = validation_matches[0]
        indent = match.group("i")
        replacement = (
            indent + "if enable_mlx_whisper:\n"
            + indent + "    _vce_validate_mlx_whisper_model(target_whisper)\n"
            + indent + validation_anchor
        )
        route = route[:match.start()] + replacement + route[match.end():]

    if "_vce_transcribe_audio" not in route:
        raise ValueError("MLX Whisper: вызов транскрибации не был изменён.")
    # Ensure at least one path to the existing loader was redirected so MLX does
    # not load PyTorch eagerly when the option is enabled.
    if "whisper.load_model(target_whisper)" not in route and not re.search(
        r"_vce_load_whisper_model\(target_whisper\s*,\s*enable_mlx_whisper",
        route,
    ):
        if "load_whisper_model(target_whisper" in route:
            raise ValueError("MLX Whisper: существующий helper загрузки не удалось перенаправить.")
    return source[:route_start] + route



SERVER_SHUTDOWN_MARKER = "# VCE_LOCAL_SERVER_SHUTDOWN_ENDPOINT_V1"
SERVER_SHUTDOWN_UI_MARKER = "VCE_LOCAL_SERVER_SHUTDOWN_BUTTON_V1"


def patch_mlx_device_transfers(source: str) -> str:
    """Skip torch Module.to(device) when MLX Whisper intentionally has no torch model."""
    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Не найдена сигнатура async def analyze_video() для проверки MLX device transfer.")
    route = source[route_start:]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Исходный Python не разбирается перед MLX device-transfer fix: {exc}") from exc
    analyze = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
         and node.name == "analyze_video"),
        None,
    )
    if analyze is None:
        raise ValueError("AST: не найдена функция analyze_video() для MLX device-transfer fix.")

    model_vars = set()
    for node in ast.walk(analyze):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        calls_loader = any(
            isinstance(child, ast.Call)
            and (
                (isinstance(child.func, ast.Name) and child.func.id == "_vce_load_whisper_model")
                or (isinstance(child.func, ast.Attribute) and child.func.attr == "load_model")
            )
            for child in ast.walk(value)
        )
        if calls_loader:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            model_vars.update(t.id for t in targets if isinstance(t, ast.Name))

    # Existing installations patched by the earlier version use w_model. Discover
    # any model variable passed to the new transcription adapter as a fallback.
    adapter_pattern = re.compile(
        r"_vce_transcribe_audio\(\s*[^,\n]+,\s*target_whisper,\s*(?P<model>[A-Za-z_]\w*)\s*,\s*enable_mlx_whisper",
        re.S,
    )
    model_vars.update(adapter_pattern.findall(route))

    assign_pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)(?P<var>[A-Za-z_]\w*)\s*=\s*(?P=var)\.to\((?P<args>[^()\n]*)\)[ \t]*$"
    )
    lines = route.splitlines(keepends=True)
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    replacements = []
    saw_model_to = False
    for match in assign_pattern.finditer(route):
        var = match.group("var")
        if var not in model_vars:
            continue
        saw_model_to = True
        line_start = route.rfind("\n", 0, match.start()) + 1
        previous = route[:line_start].rstrip("\n").splitlines()
        if previous and previous[-1].strip() == "if not enable_mlx_whisper:":
            continue
        indent = match.group("i")
        original = match.group(0).lstrip(" \t")
        replacement = indent + "if not enable_mlx_whisper:\n" + indent + "    " + original
        replacements.append((match.start(), match.end(), replacement))

    for start, end, replacement in reversed(replacements):
        route = route[:start] + replacement + route[end:]

    if not replacements and saw_model_to:
        return source
    if replacements:
        return source[:route_start] + route

    # If the loaded torch model has a .to(...) call but the source shape is not
    # the simple self-assignment handled above, fail closed rather than leaving
    # the reported NoneType error in place without warning.
    model_to_calls = [
        child for child in ast.walk(analyze)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "to"
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id in model_vars
    ]
    already_guarded = re.search(
        r"(?m)^[ \t]*if not enable_mlx_whisper:[ \t]*\n(?:^[ \t]+.*\n){1,4}^[ \t]*[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\.to\(",
        route,
    )
    if model_to_calls and not already_guarded:
        raise ValueError(
            "Обнаружен вызов model.to(...) у PyTorch Whisper, но его форма не распознана безопасным патчером. "
            "Файлы не изменены; требуется адаптация по конкретному фрагменту analyze_video()."
        )
    return source


def patch_shutdown_server_endpoint(source: str) -> str:
    """Add a loopback-only endpoint that gracefully stops the local VCE server."""
    if SERVER_SHUTDOWN_MARKER in source:
        return source
    anchor = '@app.post("/api/v1/analyze")'
    if source.count(anchor) != 1:
        raise ValueError("Для кнопки остановки сервера ожидался ровно один endpoint /api/v1/analyze.")

    if not re.search(r"(?m)^from fastapi import [^\n]*\bRequest\b", source):
        one_line_import = re.search(r"(?m)^from fastapi import (?!\()[^\n]+$", source)
        if one_line_import:
            line = one_line_import.group(0)
            source = source[:one_line_import.start()] + line + ", Request" + source[one_line_import.end():]
        elif "import os\n" in source:
            source = source.replace("import os\n", "import os\nfrom fastapi import Request\n", 1)
        else:
            raise ValueError("Не удалось безопасно добавить импорт Request для локальной кнопки остановки.")

    endpoint = r'''
# VCE_LOCAL_SERVER_SHUTDOWN_ENDPOINT_V1
@app.post("/api/v1/server/shutdown")
async def _vce_shutdown_server(request: Request):
    # This control is intended only for the local desktop UI, not remote callers.
    client_host = request.client.host if request.client else ""
    import ipaddress
    try:
        is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = client_host == "testclient"
    if not is_loopback:
        raise HTTPException(status_code=403, detail="Остановка разрешена только с локального компьютера")

    origin = request.headers.get("origin", "")
    if origin and not (
        origin.startswith("http://127.0.0.1:7555")
        or origin.startswith("http://localhost:7555")
        or origin.startswith("http://[::1]:7555")
    ):
        raise HTTPException(status_code=403, detail="Недопустимый Origin для остановки локального сервера")

    async def _stop_after_response():
        await asyncio.sleep(0.75)
        import signal
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_stop_after_response())
    return {
        "status": "stopping",
        "message": "Команда остановки принята. Для следующего запуска откройте VideoContext Engine.app заново.",
    }


'''
    source = replace_once(source, anchor, endpoint + anchor, "local server shutdown endpoint")
    return source


def patch_server_shutdown_ui(ui: str) -> str:
    """Add a confirmable stop-server button and its same-origin POST handler."""
    if f'id="vce-stop-server"' not in ui:
        anchor = "<!-- VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1 -->"
        if anchor not in ui:
            raise ValueError("Не найден маркер блока настроек для кнопки остановки сервера.")
        controls = '''
  <div id="vce-server-controls" style="grid-column:1 / -1; display:flex; gap:8px; align-items:center; flex-wrap:wrap">
    <button type="button" id="vce-stop-server">Остановить сервер VCE</button>
    <span id="vce-stop-server-status" role="status" aria-live="polite">Для запуска после остановки откройте приложение заново.</span>
  </div>
  <!-- VCE_LOCAL_SERVER_SHUTDOWN_BUTTON_V1 -->
'''
        ui = ui.replace(anchor, anchor + controls, 1)

    if "VCE_LOCAL_SERVER_SHUTDOWN_BUTTON_HANDLER_V1" not in ui:
        script_anchor = "const readSettings = () => ({"
        script_start = ui.find(script_anchor)
        if script_start < 0:
            raise ValueError("Не найден JS-блок readSettings() для кнопки остановки сервера.")
        script_end = ui.find("</script>", script_start)
        if script_end < 0:
            raise ValueError("Не найден конец script-блока веб-интерфейса.")
        handler = r'''
  // VCE_LOCAL_SERVER_SHUTDOWN_BUTTON_HANDLER_V1
  const stopServerButton = document.getElementById('vce-stop-server');
  const stopServerStatus = document.getElementById('vce-stop-server-status');
  if (stopServerButton) {
    stopServerButton.addEventListener('click', async () => {
      const confirmed = window.confirm(
        'Остановить сервер VideoContext Engine? Текущие запросы прервутся. Для следующего запуска откройте приложение заново.'
      );
      if (!confirmed) return;
      stopServerButton.disabled = true;
      if (stopServerStatus) stopServerStatus.textContent = 'Отправляется команда остановки сервера...';
      try {
        const response = await fetch('/api/v1/server/shutdown', {
          method: 'POST',
          headers: { 'Accept': 'application/json' }
        });
        if (!response.ok) {
          const details = await response.text();
          throw new Error('HTTP ' + response.status + (details ? ': ' + details : ''));
        }
        if (stopServerStatus) {
          stopServerStatus.textContent = 'Команда принята. Сервер остановится; для нового запуска откройте VideoContext Engine.app.';
        }
      } catch (error) {
        if (stopServerStatus) stopServerStatus.textContent = 'Не удалось подтвердить остановку: ' + error.message;
        stopServerButton.disabled = false;
      }
    });
  }
'''
        ui = ui[:script_end] + handler + "\n" + ui[script_end:]
    return ui


def patch_engine(source: str) -> str:
    if MARKER in source:
        # A complete install contains all five API fields and the execution hooks.
        required = [
            "enable_stage_timing: bool = Form(",
            "enable_adaptive_keyframes: bool = Form(",
            "enable_ocr_cache: bool = Form(",
            "enable_mlx_whisper: bool = Form(",
            "enable_fast_scene_detection: bool = Form(",
            "timings[\"stage_diagnostics\"]",
        ]
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("Обнаружена неполная установка патча; отсутствуют: " + ", ".join(missing))
        source = patch_mlx_device_transfers(source)
        source = patch_shutdown_server_endpoint(source)
        ast.parse(source, filename=str(TARGET))
        return source

    if "def apple_vision_ocr(" not in source:
        raise ValueError("Сначала примените патч Apple Vision OCR: функция apple_vision_ocr() не найдена.")
    if "def compute_hsv_histogram(" not in source:
        raise ValueError("Не найдена compute_hsv_histogram(); этот вариант VCE не поддерживается патчем.")
    if "@app.post(\"/api/v1/analyze\")" not in source:
        raise ValueError("Не найден endpoint /api/v1/analyze.")
    if "import hashlib" not in source:
        source = replace_once(source, "import re\n", "import re\nimport hashlib\n", "import re anchor")
    route_start = source.find("async def analyze_video(")
    route = source[route_start:]

    # MLX-only model switches must not leave None inside the PyTorch Whisper cache.
    # Insert the runtime helpers before the API route so all referenced functions are defined.
    api_anchor = '@app.post("/api/v1/analyze")'
    if source.count(api_anchor) != 1:
        raise ValueError("Ожидался ровно один endpoint /api/v1/analyze для вставки helper-функций.")
    source = replace_once(source, api_anchor, HELPERS + api_anchor, "priority helper insertion point")

    source = add_form_fields(source)
    source = patch_scene_detection(source)
    source = patch_adaptive_keyframes(source)
    source = patch_ocr_cache(source)
    source = patch_mlx_whisper(source)
    source = patch_mlx_device_transfers(source)
    source = patch_shutdown_server_endpoint(source)

    # Add diagnostic values to the already-existing timings dictionary.
    tree = ast.parse(source)
    analyze_node = next(
        (node for node in ast.walk(tree) if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
         and node.name == "analyze_video"),
        None,
    )
    if analyze_node is None:
        raise ValueError("AST: analyze_video() не найден после изменений.")
    timing_assignments = []
    for node in ast.walk(analyze_node):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "timings" for target in node.targets
        ):
            timing_assignments.append(node)
    if len(timing_assignments) != 1:
        raise ValueError(f"Ожидалось одно присваивание timings = {{...}}, найдено {len(timing_assignments)}.")
    timing_node = timing_assignments[0]
    lines = source.splitlines(keepends=True)
    offset = sum(len(line) for line in lines[:timing_node.end_lineno])
    indent = " " * timing_node.col_offset
    diagnostics = (
        indent + 'if enable_stage_timing:\n'
        + indent + '    timings["stage_diagnostics"] = {\n'
        + indent + '        "scene_detection_seconds": round(float(locals().get("scene_detection_seconds", 0.0) or 0.0), 3),\n'
        + indent + '        "whisper_load_seconds": round(float(locals().get("whisper_load_time", 0.0) or 0.0), 3),\n'
        + indent + '        "whisper_inference_seconds": round(float(locals().get("whisper_infer_time", locals().get("whisper_inference_time", 0.0)) or 0.0), 3),\n'
        + indent + '        "vlm_load_seconds": round(float(locals().get("vlm_load_time", 0.0) or 0.0), 3),\n'
        + indent + '        "vlm_inference_seconds": round(float(locals().get("vlm_infer_time", 0.0) or 0.0), 3),\n'
        + indent + '        "apple_ocr_seconds": round(float(locals().get("apple_ocr_seconds", 0.0) or 0.0), 3),\n'
        + indent + '        "ffmpeg_ocr_seconds": round(float(locals().get("ffmpeg_ocr_infer_time", 0.0) or 0.0), 3),\n'
        + indent + '    }\n'
    )
    source = source[:offset] + diagnostics + source[offset:]

    # Register defaults and allow the five booleans in the existing settings API.
    source = patch_settings_api(source)
    ast.parse(source, filename=str(TARGET))
    return source


def patch_settings_api(source: str) -> str:
    get_anchor = '@app.get("/api/v1/ui-settings"'
    post_anchor = '@app.post("/api/v1/ui-settings"'
    get_start = source.find(get_anchor)
    post_start = source.find(post_anchor)
    if get_start < 0 or post_start < 0 or post_start <= get_start:
        raise ValueError("Не найдены маршруты GET/POST /api/v1/ui-settings.")

    defaults = (
        '        "enable_stage_timing": False,\n'
        '        "enable_adaptive_keyframes": False,\n'
        '        "enable_ocr_cache": False,\n'
        '        "enable_mlx_whisper": False,\n'
        '        "enable_fast_scene_detection": False,\n'
    )
    get_route = source[get_start:post_start]
    names = ("enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
             "enable_mlx_whisper", "enable_fast_scene_detection")
    have = [name for name in names if f'"{name}"' in get_route]
    if len(have) != len(names):
        if have:
            raise ValueError("В settings GET route уже есть только часть новых значений по умолчанию.")
        match = re.search(r'(?m)^(?P<i>[ \t]*)"vlm_model"\s*:', get_route)
        if not match:
            raise ValueError("Не найден vlm_model в defaults GET /ui-settings.")
        indent = match.group("i")
        defaults = "".join(indent + line.lstrip() for line in defaults.splitlines(keepends=True))
        get_route = get_route[:match.start()] + defaults + get_route[match.start():]
        source = source[:get_start] + get_route + source[post_start:]
        post_start = source.find(post_anchor)

    post_end = source.find("\n@app.", post_start + len(post_anchor))
    if post_end < 0:
        post_end = len(source)
    post_route = source[post_start:post_end]
    if not all(f'"{name}"' in post_route for name in names):
        lines = post_route.splitlines(keepends=True)
        bool_line_idx = next(
            (idx for idx, line in enumerate(lines)
             if '"enable_apple_ocr"' in line and '"enable_audio_features"' in line),
            None,
        )
        if bool_line_idx is None:
            raise ValueError("Не найден allow-list булевых настроек Apple OCR/Audio features.")
        additions = (
            '        "enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",\n'
            '        "enable_mlx_whisper", "enable_fast_scene_detection",\n'
        )
        lines.insert(bool_line_idx + 1, additions)
        post_route_new = "".join(lines)
        source = source[:post_start] + post_route_new + source[post_end:]
    return source


def patch_ui(ui: str) -> str:
    if "VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1" in ui:
        required = (
            'id="cfg-stage-timing"', 'id="cfg-adaptive-keyframes"', 'id="cfg-ocr-cache"',
            'id="cfg-mlx-whisper"', 'id="cfg-fast-scene-detection"',
        )
        missing = [item for item in required if item not in ui]
        if missing:
            raise ValueError("Неполная UI-установка; отсутствуют: " + ", ".join(missing))
        return patch_server_shutdown_ui(ui)

    controls = '''  <!-- VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1 -->
  <div class="setting-checks" id="vce-optimization-checkboxes">
    <strong style="grid-column:1 / -1">Экспериментальные оптимизации (все выключены по умолчанию)</strong>
    <label><input id="cfg-stage-timing" type="checkbox"> Подробные тайминги этапов в JSON</label>
    <label><input id="cfg-adaptive-keyframes" type="checkbox"> Адаптивное число кадров на сцену (настройка количества кадров — верхний предел)</label>
    <label><input id="cfg-ocr-cache" type="checkbox"> Кэш OCR Apple Vision для повторяющихся кадров</label>
    <label><input id="cfg-mlx-whisper" type="checkbox"> MLX Whisper вместо PyTorch Whisper (нужен пакет mlx-whisper и совместимая MLX-модель)</label>
    <label><input id="cfg-fast-scene-detection" type="checkbox"> Быстрая детекция сцен — выборка 1 кадр/сек, возможно пропускать короткие переходы</label>
  </div>
'''

    def apply_indent(block: str, indent: str) -> str:
        return "".join(indent + line if line.strip() else line
                       for line in block.splitlines(keepends=True))

    # Prefer the canonical location used by the settings-button UI, when present.
    save_match = re.search(r'(?m)^(?P<i>[ \t]*)<button[^>]*id="save-settings"', ui)
    if save_match:
        controls = apply_indent(controls, save_match.group("i"))
        ui = ui[:save_match.start()] + controls + ui[save_match.start():]
    else:
        # Some customized builds have readSettings()/fillSettings() but their
        # save button has a different ID or no separate save button. Anchor
        # beside the existing Apple OCR checkbox, which is a member of the
        # settings control group, instead of failing on an irrelevant ID.
        apple_pos = ui.find('id="cfg-apple-ocr"')
        if apple_pos < 0:
            visible_ids = re.findall(r'<input\b[^>]*\bid="([^"]+)"', ui)
            buttons = re.findall(r'<button\b[^>]*\bid="([^"]+)"', ui)
            raise ValueError(
                "Не найдена кнопка save-settings и не найден существующий чекбокс cfg-apple-ocr. "
                "Автоматически определить контейнер настроек небезопасно. "
                f"Обнаруженные input id: {visible_ids[:20]}; button id: {buttons[:20]}. Файлы не изменены."
            )
        label_start = ui.rfind("<label", 0, apple_pos)
        label_end = ui.find("</label>", apple_pos)
        if label_start < 0 or label_end < 0:
            raise ValueError("Найден cfg-apple-ocr, но не удалось определить его label; файлы не изменены.")

        # If the Apple OCR control is inside the existing .setting-checks grid,
        # add the new labels to that same grid (no nested grid). Otherwise add
        # a self-contained grid immediately before the Apple OCR label.
        grid_start = ui.rfind('<div class="setting-checks"', 0, label_start)
        section_start = ui.rfind("<section", 0, label_start)
        grid_close = ui.find("</div>", grid_start) if grid_start >= 0 else -1
        if (
            grid_start >= 0
            and grid_start > section_start
            and grid_close > label_start
        ):
            inner = (
                '    <strong id="vce-optimization-title" style="grid-column:1 / -1">'
                'Экспериментальные оптимизации (все выключены по умолчанию)</strong>\n'
                '    <label><input id="cfg-stage-timing" type="checkbox"> Подробные тайминги этапов в JSON</label>\n'
                '    <label><input id="cfg-adaptive-keyframes" type="checkbox"> Адаптивное число кадров на сцену (настройка количества кадров — верхний предел)</label>\n'
                '    <label><input id="cfg-ocr-cache" type="checkbox"> Кэш OCR Apple Vision для повторяющихся кадров</label>\n'
                '    <label><input id="cfg-mlx-whisper" type="checkbox"> MLX Whisper вместо PyTorch Whisper (нужен пакет mlx-whisper и совместимая MLX-модель)</label>\n'
                '    <label><input id="cfg-fast-scene-detection" type="checkbox"> Быстрая детекция сцен — выборка 1 кадр/сек, возможно пропускать короткие переходы</label>\n'
                '    <!-- VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1 -->\n'
            )
            indent_match = re.match(r"(?m)^([ \t]*)", ui[grid_start:label_start])
            indent = indent_match.group(1) if indent_match else "  "
            inner = "".join(indent + line if line.strip() else line
                            for line in inner.splitlines(keepends=True))
            ui = ui[:label_start] + inner + ui[label_start:]
        else:
            # Keep a complete accessible block near the existing checkbox even
            # when this build has no identifiable settings grid.
            line_start = ui.rfind("\n", 0, label_start) + 1
            indent_match = re.match(r"[ \t]*", ui[line_start:label_start])
            indent = indent_match.group(0) if indent_match else "  "
            controls = apply_indent(controls, indent)
            ui = ui[:line_start] + controls + ui[line_start:]

    # Persist the values with the existing settings object; analysis submits the same object as FormData.
    read_anchor = "  const readSettings = () => ({\n"
    if read_anchor not in ui:
        raise ValueError("Не найден readSettings() в веб-интерфейсе.")
    fields = (
        "    enable_stage_timing: document.getElementById('cfg-stage-timing').checked,\n"
        "    enable_adaptive_keyframes: document.getElementById('cfg-adaptive-keyframes').checked,\n"
        "    enable_ocr_cache: document.getElementById('cfg-ocr-cache').checked,\n"
        "    enable_mlx_whisper: document.getElementById('cfg-mlx-whisper').checked,\n"
        "    enable_fast_scene_detection: document.getElementById('cfg-fast-scene-detection').checked,\n"
    )
    ui = replace_once(ui, read_anchor, read_anchor + fields, "read priority checkbox values")

    checks_anchor = "    const checks = {"
    if checks_anchor not in ui:
        raise ValueError("Не найден checks map в fillSettings().")
    checks_entries = (
        "\n      'cfg-stage-timing':'enable_stage_timing',\n"
        "      'cfg-adaptive-keyframes':'enable_adaptive_keyframes',\n"
        "      'cfg-ocr-cache':'enable_ocr_cache',\n"
        "      'cfg-mlx-whisper':'enable_mlx_whisper',\n"
        "      'cfg-fast-scene-detection':'enable_fast_scene_detection',"
    )
    ui = replace_once(ui, checks_anchor, checks_anchor + checks_entries, "fill priority checkbox values")
    return patch_server_shutdown_ui(ui)

def patch_settings(settings_text: str) -> str:
    settings = json.loads(settings_text)
    if not isinstance(settings, dict):
        raise ValueError("Корень videocontext_settings.json должен быть JSON-объектом.")

    # Benchmark-backed balanced profile, from results/vce-benchmark/20261025_120626/analysis.md.
    # The report recommends isolating the resolution reduction first and keeping
    # the baseline scene boundaries/keyframe count/OCR coverage for that check.
    # Override these values explicitly because the user asked to install this profile.
    benchmark_profile = {
        "vlm_resolution": 384,
        "scene_threshold": 0.15,
        "min_scene_duration": 1.5,
        "max_scene_duration": 20,
        "keyframes_per_scene": 1,
        "enable_apple_ocr": True,
        "enable_ffmpeg_ocr": True,
        "ffmpeg_ocr_interval": 5,
        "ffmpeg_ocr_workers": 3,
    }
    settings.update(benchmark_profile)

    # The five additional switches have no direct benchmark evidence yet.
    # Leave each opt-in and preserve any deliberate existing boolean choice.
    for key in (
        "enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
        "enable_mlx_whisper", "enable_fast_scene_detection",
    ):
        settings.setdefault(key, False)
        if type(settings[key]) is not bool:
            settings[key] = False

    # Preserve generate_txt and all user prompts without modification.
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"

def prepare(project: Path):
    paths = (project / TARGET, project / CONFIG, project / UI_FILE)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Не найдены обязательные файлы: " + ", ".join(missing))
    originals = {path: path.read_text(encoding="utf-8") for path in paths}
    source = patch_engine(originals[paths[0]])
    ui = patch_ui(originals[paths[2]])
    settings = patch_settings(originals[paths[1]])
    ast.parse(source, filename=str(paths[0]))
    json.loads(settings)
    changes = {}
    if source != originals[paths[0]]:
        changes[paths[0]] = source
    if settings != originals[paths[1]]:
        changes[paths[1]] = settings
    if ui != originals[paths[2]]:
        changes[paths[2]] = ui
    return changes, originals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true", help="Показать diff без записи файлов")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    try:
        changes, originals = prepare(project)
    except Exception as exc:
        print(f"ОШИБКА: {exc}")
        print("Файлы не изменены.")
        return 2
    if not changes:
        print("Пять оптимизационных чекбоксов уже установлены; изменений нет.")
        return 0

    for path, updated in changes.items():
        diff = difflib.unified_diff(
            originals[path].splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path) + " (new)",
        )
        print(f"\n--- {path.name} ---")
        print("".join(list(diff)[:240]))
    if args.dry_run:
        print(f"\nDRY RUN: подготовлены изменения в {len(changes)} файлах; запись не выполнялась.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {}
    written = []
    try:
        for path in changes:
            backup = path.with_name(path.name + f".before_priority_checkboxes_{stamp}.bak")
            shutil.copy2(path, backup)
            backups[path] = backup
        for path, content in changes.items():
            tmp = path.with_name(path.name + ".priority_checkboxes.tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
            written.append(path)
        ast.parse((project / TARGET).read_text(encoding="utf-8"), filename=str(project / TARGET))
        json.loads((project / CONFIG).read_text(encoding="utf-8"))
    except Exception:
        for path in written:
            path.write_text(originals[path], encoding="utf-8")
        print("ОШИБКА после записи. Исходные файлы восстановлены; резервные копии сохранены.")
        raise

    print("\nПатч установлен. Все пять новых флагов по умолчанию выключены.")
    for path, backup in backups.items():
        print(f"Backup {path.name}: {backup}")
    print("Перезапустите VCE после применения. Запускать во время активного анализа не следует.")
    print("MLX Whisper требует отдельной установки mlx-whisper; никакие пакеты автоматически не устанавливаются.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
