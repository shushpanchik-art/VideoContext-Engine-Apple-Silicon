#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add five independent, opt-in optimization checkboxes to a patched VCE v3.19.

The patch is intentionally conservative: all five new options default to False,
existing user settings/prompts are preserved, files are backed up, and the
installer refuses to write if its expected integration points do not match.
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


def _vce_load_whisper_model(model_name, use_mlx):
    """Avoid loading PyTorch Whisper when the optional MLX backend is selected."""
    if use_mlx:
        _vce_validate_mlx_whisper_model(model_name)
        return None
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
    if "_vce_cached_apple_vision_ocr(keyframes[0][\"image\"])" in source:
        return source
    pattern = re.compile(
        r'(?m)^(?P<i>[ \t]*)scene_ocr_text\s*=\s*apple_vision_ocr\(keyframes\[0\]\["image"\]\)[ \t]*$'
    )
    matches = list(pattern.finditer(source, source.find("async def analyze_video(")))
    if len(matches) != 1:
        raise ValueError(
            "Не найден единственный scene_ocr_text = apple_vision_ocr(keyframes[0]['image']). "
            "Этот патч требует уже установленного Apple Vision OCR."
        )
    m = matches[0]
    i = m.group("i")
    replacement = (
        i + "_vce_ocr_started = time.perf_counter()\n"
        + i + "scene_ocr_text = (\n"
        + i + "    _vce_cached_apple_vision_ocr(keyframes[0][\"image\"])\n"
        + i + "    if enable_ocr_cache\n"
        + i + "    else apple_vision_ocr(keyframes[0][\"image\"])\n"
        + i + ")\n"
        + i + "apple_ocr_seconds += time.perf_counter() - _vce_ocr_started"
    )
    return source[:m.start()] + replacement + source[m.end():]


def patch_mlx_whisper(source: str) -> str:
    route_start = source.find("async def analyze_video(")
    if route_start < 0:
        raise ValueError("Не найдена сигнатура analyze_video().")
    route = source[route_start:]

    if "_vce_transcribe_audio(" not in route:
        pattern = re.compile(
            r"(?m)^(?P<i>[ \t]*)audio_res\s*=\s*w_model\.transcribe\(\n"
            r"(?P<j>[ \t]*)temp_path,[ \t]*$"
        )
        if len(list(pattern.finditer(route))) != 1:
            raise ValueError(
                "Не найден единственный вызов w_model.transcribe(temp_path, ...); "
                "не удалось безопасно подключить MLX Whisper."
            )
        route = pattern.sub(
            lambda m: (
                m.group("i") + "audio_res = _vce_transcribe_audio(\n"
                + m.group("j") + "temp_path, target_whisper, w_model, enable_mlx_whisper,"
            ),
            route,
            count=1,
        )
    if "w_model = None if enable_mlx_whisper else WHISPER_CACHE[target_whisper]" not in route:
        route = route.replace(
            "w_model = WHISPER_CACHE[target_whisper]",
            "w_model = None if enable_mlx_whisper else WHISPER_CACHE[target_whisper]",
            1,
        )
    route = route.replace(
        "whisper.load_model(target_whisper)",
        "_vce_load_whisper_model(target_whisper, enable_mlx_whisper)",
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
    if "audio_res = _vce_transcribe_audio(" not in route:
        raise ValueError("MLX Whisper: вызов транскрибации не был изменён.")
    return source[:route_start] + route


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
        return ui

    controls = '''  <!-- VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1 -->
  <div class="setting-checks" id="vce-optimization-checkboxes">
    <strong>Экспериментальные оптимизации (все выключены по умолчанию)</strong>
    <label><input id="cfg-stage-timing" type="checkbox"> Подробные тайминги этапов в JSON</label>
    <label><input id="cfg-adaptive-keyframes" type="checkbox"> Адаптивное число кадров на сцену (настройка количества кадров — верхний предел)</label>
    <label><input id="cfg-ocr-cache" type="checkbox"> Кэш OCR Apple Vision для повторяющихся кадров</label>
    <label><input id="cfg-mlx-whisper" type="checkbox"> MLX Whisper вместо PyTorch Whisper (нужен пакет mlx-whisper и совместимая MLX-модель)</label>
    <label><input id="cfg-fast-scene-detection" type="checkbox"> Быстрая детекция сцен — выборка 1 кадр/сек, возможно пропускать короткие переходы</label>
  </div>
'''
    # Put the controls inside the settings panel, before its Save button.
    save_match = re.search(r'(?m)^(?P<i>[ \t]*)<button[^>]*id="save-settings"', ui)
    if not save_match:
        raise ValueError("Не найдена кнопка save-settings в веб-интерфейсе.")
    controls = "".join(save_match.group("i") + line if line.strip() else line
                       for line in controls.splitlines(keepends=True))
    ui = ui[:save_match.start()] + controls + ui[save_match.start():]

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
    return ui


def patch_settings(settings_text: str) -> str:
    settings = json.loads(settings_text)
    if not isinstance(settings, dict):
        raise ValueError("Корень videocontext_settings.json должен быть JSON-объектом.")
    for key in (
        "enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
        "enable_mlx_whisper", "enable_fast_scene_detection",
    ):
        settings.setdefault(key, False)
        if type(settings[key]) is not bool:
            settings[key] = False
    # Do not change generate_txt or any existing prompts/settings.
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
