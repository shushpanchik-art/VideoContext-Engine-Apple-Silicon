#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch VideoContext Engine v3.19 for Whisper acceleration, full-size Apple OCR,
and the best tested quality/speed defaults.

Run from ~/Downloads/VideoContext-Engine after prior OCR/optimization patches.
It validates all edits before writing and creates timestamped backups.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ast
import json
import re
import shutil
import sys

TARGET = Path("VideoContextEngine_v3.19.py")
CONFIG = Path("videocontext_settings.json")

BEST_DEFAULTS = {
    "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    "whisper_model": "large",
    "response_format": "json",
    "vlm_resolution": 1280,
    "scene_threshold": 0.15,
    "min_scene_duration": 1.5,
    "max_scene_duration": 10,
    "keyframes_per_scene": 2,
    "vlm_max_tokens_scene": 1024,
    "vlm_max_tokens_summary": 2048,
    "skip_audio": False,
    "skip_visual": False,
    "generate_txt": True,
    "enable_audio_features": True,
    "enable_apple_ocr": True,
    "generate_summary": True,
    "ram_mode": "ram-",
    "mlx_device": "auto",
    "whisper_acceleration": True,
    "apple_ocr_recognition_level": "accurate",
    "apple_ocr_languages": ["ru-RU", "en-US"],
    "apple_ocr_confidence": 0.30,
    "visual_user_prompt": (
        "Анализируй техническое видео на русском языке. Сосредоточься на информации, "
        "видимой на экране: заголовках и пунктах слайдов, новых понятиях, коде, командах "
        "терминала, именах файлов, таблицах, схемах, ошибках и результатах. Переписывай "
        "ключевой текст и команды дословно, когда они читаемы. Разделяй факты, видимые на "
        "кадре, и предположения. Не трать место на курсор, анимации и декоративные изменения. "
        "Если текст неразборчив, прямо укажи это. При анализе схемы описывай связи между "
        "блоками и сохраняй читаемые подписи и значения."
    ),
    "summary_user_prompt": (
        "Составь подробный технический конспект на русском языке. Организуй содержание по "
        "временным отметкам. Выдели основные темы, новые понятия, команды терминала, примеры "
        "кода, названия инструментов, ошибки и решения. Сохраняй важные команды и имена файлов "
        "дословно, если они доступны в расшифровке или описаниях сцен. Не выдумывай отсутствующую "
        "информацию. Заверши списком ключевых выводов и практических действий."
    ),
}


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one occurrence of {label}; found {count}.")
    return source.replace(old, new, 1)


def ensure_literal(source: str, old: str, new: str, label: str) -> str:
    if old in source:
        return source.replace(old, new, 1)
    if new in source:
        return source  # idempotent
    raise ValueError(f"Could not find expected source setting: {label}")


def patch_builtin_defaults(source: str) -> str:
    replacements = [
        ('DEFAULT_VLM_MODEL_MLX = "mlx-community/Qwen3-VL-2B-Instruct-4bit"', 'DEFAULT_VLM_MODEL_MLX = "mlx-community/Qwen3-VL-8B-Instruct-4bit"', "MLX VLM model"),
        ('DEFAULT_WHISPER_MODEL = "small"', 'DEFAULT_WHISPER_MODEL = "large"', "Whisper model"),
        ('DEFAULT_SCENE_THRESHOLD = 0.35', 'DEFAULT_SCENE_THRESHOLD = 0.15', "scene threshold"),
        ('DEFAULT_MIN_DURATION = 2.0', 'DEFAULT_MIN_DURATION = 1.5', "minimum scene duration"),
        ('DEFAULT_MAX_DURATION = 60.0', 'DEFAULT_MAX_DURATION = 10.0', "maximum scene duration"),
        ('DEFAULT_RESOLUTION = 768', 'DEFAULT_RESOLUTION = 1280', "VLM resolution"),
        ('DEFAULT_KEYFRAMES_PER_SCENE = 1', 'DEFAULT_KEYFRAMES_PER_SCENE = 2', "keyframes per scene"),
        ('DEFAULT_VLM_MAX_TOKENS_SCENE = 220', 'DEFAULT_VLM_MAX_TOKENS_SCENE = 1024', "scene token budget"),
        ('DEFAULT_VLM_MAX_TOKENS_SUMMARY = 260', 'DEFAULT_VLM_MAX_TOKENS_SUMMARY = 2048', "summary token budget"),
        ('int(setting("vlm_resolution", 1440))', 'int(setting("vlm_resolution", 1280))', "API VLM resolution fallback"),
        ('float(setting("max_scene_duration", 20))', 'float(setting("max_scene_duration", 10))', "API maximum scene duration fallback"),
    ]
    for old, new, label in replacements:
        source = ensure_literal(source, old, new, label)
    # Keep the header documentation aligned when these comments are present.
    source = source.replace(
        'Nombre de keyframes par scène paramétrable (1 à 5), 1 par défaut.',
        'Nombre de keyframes par scène paramétrable (1 à 5), 2 par défaut.',
    )
    source = source.replace(
        'max_tokens VLM réglables avec valeurs par défaut (220 / 260).',
        'max_tokens VLM réglables avec valeurs par défaut (1024 / 2048).',
    )
    return source


def patch_whisper_loader(source: str) -> str:
    if (
        'def load_whisper_model(model_name: str, acceleration_enabled: bool = True)' in source
        and 'def resolve_whisper_device(acceleration_enabled: bool = True)' in source
    ):
        return source

    pattern = re.compile(
        r"(?ms)^def load_whisper_model\(model_name: str\):\n.*?(?=^# Optionnel : supprimer le warning HF tokenizers)")
    match = pattern.search(source)
    if not match:
        raise ValueError("Could not find the existing load_whisper_model() helper from the supplied v3.19 code.")

    replacement = '''def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Choose MPS/CUDA when acceleration is enabled, otherwise force CPU."""
    if not acceleration_enabled:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return "mps"
    except Exception as exc:
        print(f"⚠️ Whisper accelerator detection failed; using CPU: {exc}")
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    device = resolve_whisper_device(acceleration_enabled)
    print(f"🧮 Whisper device: {device} (acceleration={acceleration_enabled})")
    return whisper.load_model(model_name, device=device)


'''
    return source[:match.start()] + replacement + source[match.end():]


def patch_full_resolution_ocr(source: str) -> str:
    if '"original_image": original_pil_img' not in source:
        pattern = re.compile(
            r'(?m)^(?P<i>[ \t]*)frame = resize_keep_aspect\(frame, base_res\)\n'
            r'(?P=i)pil_img = Image\.fromarray\(cv2\.cvtColor\(frame, cv2\.COLOR_BGR2RGB\)\)\n'
            r'(?P=i)frames\.append\(\{"time": round\(t, 2\), "image": pil_img\}\)'
        )
        replacement = (
            r'\g<i>original_pil_img = Image.fromarray(cv2.cvtColor(frame.copy(), cv2.COLOR_BGR2RGB))\n'
            r'\g<i>frame = resize_keep_aspect(frame, base_res)\n'
            r'\g<i>pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))\n'
            r'\g<i>frames.append({"time": round(t, 2), "image": pil_img, "original_image": original_pil_img})'
        )
        source, count = pattern.subn(replacement, source, count=1)
        if count != 1:
            raise ValueError(
                "Could not locate the keyframe resize block in sample_keyframes_for_scene(). "
                "Expected the current optimized code with resize_keep_aspect(frame, base_res)."
            )

    if 'apple_vision_ocr(kf.get("original_image", kf["image"]))' not in source:
        if 'apple_vision_ocr(kf["image"])' not in source:
            raise ValueError("Could not locate Apple OCR frame call in analyze_video().")
        source = replace_once(
            source,
            'apple_vision_ocr(kf["image"])',
            'apple_vision_ocr(kf.get("original_image", kf["image"]))',
            "Apple OCR frame call",
        )
    return source


def patch_api_whisper_toggle(source: str) -> str:
    # The supplied source calls load_whisper_model(...), rather than loading directly
    # via whisper.load_model() in the request handler. Wire the UI toggle to that helper.
    if "whisper_acceleration: bool" not in source:
        response_decl = re.search(r"(?m)^([ \t]*)response_format\s*:", source)
        if not response_decl:
            raise ValueError("Could not find response_format in the API Form parameters.")
        indent = response_decl.group(1)
        parameter = (
            f'{indent}whisper_acceleration: bool = Form(\n'
            f'{indent}    bool(setting("whisper_acceleration", True)),\n'
            f'{indent}    description="Ускорение Whisper: MPS на Apple Silicon / CUDA при наличии; выключите для CPU",\n'
            f'{indent}),\n\n'
        )
        source = source[:response_decl.start()] + parameter + source[response_decl.start():]

    analyze_start = source.find("async def analyze_video(")
    if analyze_start < 0:
        raise ValueError("Could not find analyze_video() endpoint.")
    prefix, endpoint = source[:analyze_start], source[analyze_start:]

    if "whisper_device = resolve_whisper_device(whisper_acceleration)" not in endpoint:
        target_line = re.search(r"(?m)^(?P<i>[ \t]*)target_whisper\s*=\s*whisper_model\s*$", endpoint)
        if not target_line:
            raise ValueError("Could not find target_whisper = whisper_model in API endpoint.")
        indent = target_line.group("i")
        endpoint = endpoint[:target_line.end()] + (
            "\n" + indent + "whisper_device = resolve_whisper_device(whisper_acceleration)"
        ) + endpoint[target_line.end():]

    # Change both request-time load paths (RAM+ cache miss and ram-); startup preloading
    # remains separate and respects the persistent default setting.
    endpoint = endpoint.replace(
        "load_whisper_model(target_whisper)",
        "load_whisper_model(target_whisper, acceleration_enabled=whisper_acceleration)",
    )

    if "w_model = w_model.to(whisper_device)" not in endpoint:
        anchor = re.search(r"(?m)^(?P<i>[ \t]*)t_infer_start\s*=\s*time\.time\(\)\s*$", endpoint)
        if not anchor:
            raise ValueError("Could not locate Whisper inference timer for device selection.")
        indent = anchor.group("i")
        endpoint = endpoint[:anchor.start()] + (
            indent + "w_model = w_model.to(whisper_device)\n" + endpoint[anchor.start():]
        )

    if 'print(f"🧮 Whisper device: {whisper_device} (acceleration={whisper_acceleration})")' not in endpoint:
        audio_if = re.search(r"(?m)^(?P<i>[ \t]*)if not skip_audio:\s*$", endpoint)
        if not audio_if:
            raise ValueError("Could not find the request's if not skip_audio block.")
        indent = audio_if.group("i") + "    "
        endpoint = endpoint[:audio_if.end()] + (
            "\n" + indent + 'print(f"🧮 Whisper device: {whisper_device} (acceleration={whisper_acceleration})")'
        ) + endpoint[audio_if.end():]

    # Include the effective selection in the returned metadata for reproducible tests.
    if '"whisper_acceleration": whisper_acceleration' not in endpoint:
        params_anchor = '"enable_apple_ocr": enable_apple_ocr,'
        if params_anchor not in endpoint:
            raise ValueError("Could not find response params block to expose whisper_acceleration.")
        endpoint = replace_once(
            endpoint,
            params_anchor,
            params_anchor + '\n                        "whisper_acceleration": whisper_acceleration,\n                        "whisper_device": whisper_device,',
            "response params Apple OCR entry",
        )

    if '"device": whisper_device' not in endpoint:
        whisper_timing_anchor = '"model": target_whisper,\n                "load_time": round(whisper_load_time, 3),'
        if whisper_timing_anchor not in endpoint:
            raise ValueError("Could not find Whisper timing metadata block.")
        endpoint = replace_once(
            endpoint,
            whisper_timing_anchor,
            '"model": target_whisper,\n                "device": whisper_device,\n                "acceleration": whisper_acceleration,\n                "load_time": round(whisper_load_time, 3),',
            "Whisper timing metadata",
        )

    return prefix + endpoint


def patch_startup_preload(source: str) -> str:
    # Keep ram+ preload aligned with the configured default model and accelerator.
    old = 'WHISPER_CACHE[DEFAULT_WHISPER_MODEL] = load_whisper_model(DEFAULT_WHISPER_MODEL)'
    new = (
        'startup_whisper_model = str(setting("whisper_model", DEFAULT_WHISPER_MODEL))\n'
        '            WHISPER_CACHE[startup_whisper_model] = load_whisper_model(\n'
        '                startup_whisper_model,\n'
        '                acceleration_enabled=bool(setting("whisper_acceleration", True)),\n'
        '            )'
    )
    if old in source:
        source = replace_once(source, old, new, "ram+ startup Whisper preload")
    elif 'startup_whisper_model = str(setting("whisper_model", DEFAULT_WHISPER_MODEL))' not in source:
        raise ValueError("Could not locate the ram+ Whisper startup preload.")
    return source


def main() -> int:
    if not TARGET.is_file():
        print(f"ERROR: {TARGET.resolve()} not found. Run from ~/Downloads/VideoContext-Engine.")
        return 2
    if not CONFIG.is_file():
        print(f"ERROR: {CONFIG.resolve()} not found; no files changed.")
        return 2

    original_source = TARGET.read_text(encoding="utf-8")
    original_config_text = CONFIG.read_text(encoding="utf-8")
    source = original_source
    try:
        required = [
            "def sample_keyframes_for_scene(",
            "def resize_keep_aspect(",
            "def apple_vision_ocr(",
            "enable_apple_ocr: bool",
            'setting("vlm_resolution"',
            "def load_whisper_model(model_name: str):",
            "async def analyze_video(",
        ]
        # Permit rerunning after this patch has already rewritten the loader signature.
        if "def load_whisper_model(model_name: str, acceleration_enabled: bool = True)" in source:
            required[5] = "def load_whisper_model(model_name: str, acceleration_enabled: bool = True)"
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("The file does not match the supplied/optimized v3.19 code. Missing: " + ", ".join(missing))

        source = patch_builtin_defaults(source)
        source = patch_whisper_loader(source)
        source = patch_full_resolution_ocr(source)
        source = patch_api_whisper_toggle(source)
        source = patch_startup_preload(source)
        ast.parse(source, filename=str(TARGET))

        config_data = json.loads(original_config_text)
        if not isinstance(config_data, dict):
            raise ValueError("Settings JSON root must be an object.")
        config_data.update(BEST_DEFAULTS)

        checks = {
            "Swagger acceleration toggle": "whisper_acceleration: bool" in source,
            "MPS/CUDA/CPU device resolver": "def resolve_whisper_device(acceleration_enabled: bool = True)" in source,
            "Whisper device argument": "return whisper.load_model(model_name, device=device)" in source,
            "request-time device selection": "whisper_device = resolve_whisper_device(whisper_acceleration)" in source,
            "cached-model device movement": "w_model = w_model.to(whisper_device)" in source,
            "full-size frame saved for OCR": '"original_image": original_pil_img' in source,
            "full-size image used for OCR": 'apple_vision_ocr(kf.get("original_image", kf["image"]))' in source,
            "accelerator reported in output": '"whisper_acceleration": whisper_acceleration' in source,
            "large Whisper configured": config_data.get("whisper_model") == "large",
            "Qwen3-VL-8B configured": config_data.get("vlm_model") == "mlx-community/Qwen3-VL-8B-Instruct-4bit",
            "1280px VLM resolution configured": config_data.get("vlm_resolution") == 1280,
            "Apple OCR enabled by default": config_data.get("enable_apple_ocr") is True,
            "Whisper acceleration enabled by default": config_data.get("whisper_acceleration") is True,
        }
        failed = [name for name, valid in checks.items() if not valid]
        if failed:
            raise ValueError("Validation failed: " + ", ".join(failed))

    except Exception as exc:
        print(f"ERROR: {exc}")
        print("No files have been changed.")
        return 3

    serialized_config = json.dumps(config_data, ensure_ascii=False, indent=2) + "\n"
    if source == original_source and serialized_config == original_config_text:
        print("Patch already applied and defaults are current; no files changed.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    engine_backup = TARGET.with_name(f"{TARGET.name}.before_whisper_ocr_{stamp}.bak")
    config_backup = CONFIG.with_name(f"{CONFIG.name}.before_whisper_ocr_{stamp}.bak")
    shutil.copy2(TARGET, engine_backup)
    shutil.copy2(CONFIG, config_backup)
    TARGET.write_text(source, encoding="utf-8")
    CONFIG.write_text(serialized_config, encoding="utf-8")

    print("Patch applied successfully.")
    print(f"Engine backup:   {engine_backup.resolve()}")
    print(f"Settings backup: {config_backup.resolve()}")
    print("Swagger: whisper_acceleration=true selects MPS/CUDA when available; false forces CPU.")
    print("Apple OCR gets original-resolution frames; VLM gets aspect-preserving 1280px frames.")
    print("Defaults: Qwen3-VL-8B 4-bit, Whisper large, 1280px VLM, 2 frames, max scene 10s, OCR enabled, ram-.")
    print("Restart the server and reload /docs to display the new Swagger field.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
