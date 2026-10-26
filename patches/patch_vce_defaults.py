#!/usr/bin/env python3
"""Persist VideoContext Engine settings in videocontext_settings.json.

Run from the project root after the optimization and Apple Vision OCR patchers.
This patcher locates FastAPI Form() defaults structurally, including multi-line
arguments, rather than depending on the exact wording/line wrapping in v3.19.
A timestamped backup is created before the Python source is changed.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re
import shutil
import sys

TARGET = Path("VideoContextEngine_v3.19.py")
CONFIG = Path("videocontext_settings.json")

DEFAULT_SETTINGS = {
    "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    "whisper_model": "large",
    "response_format": "json",
    "vlm_resolution": 1280,
    "scene_threshold": 0.15,
    "min_scene_duration": 1.5,
    "max_scene_duration": 20,
    "keyframes_per_scene": 1,
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

SETTINGS_LOADER = '''SETTINGS_FILE = Path(__file__).with_name("videocontext_settings.json")

def load_user_settings() -> Dict[str, Any]:
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("settings root must be a JSON object")
        return loaded
    except FileNotFoundError:
        print(f"⚠️ Settings file not found: {SETTINGS_FILE}; using built-in defaults")
        return {}
    except Exception as exc:
        print(f"⚠️ Could not read {SETTINGS_FILE}: {exc}; using built-in defaults")
        return {}

USER_SETTINGS = load_user_settings()

def setting(name: str, fallback: Any) -> Any:
    return USER_SETTINGS.get(name, fallback)

'''

# Each key is a FastAPI argument name; the replacement is its first Form() argument.
FORM_DEFAULTS = {
    "visual_user_prompt": 'str(setting("visual_user_prompt", "Décris factuellement ce qui se passe à partir des images."))',
    "summary_user_prompt": 'str(setting("summary_user_prompt", "Résume la vidéo clairement."))',
    "vlm_model": 'str(setting("vlm_model", ACTIVE_DEFAULT_VLM))',
    "whisper_model": 'str(setting("whisper_model", DEFAULT_WHISPER_MODEL))',
    "response_format": 'str(setting("response_format", "json"))',
    "vlm_resolution": 'int(setting("vlm_resolution", 1280))',
    "scene_threshold": 'float(setting("scene_threshold", 0.15))',
    "min_scene_duration": 'float(setting("min_scene_duration", 1.5))',
    "max_scene_duration": 'float(setting("max_scene_duration", 20))',
    "keyframes_per_scene": 'int(setting("keyframes_per_scene", 1))',
    "vlm_max_tokens_scene": 'int(setting("vlm_max_tokens_scene", 1024))',
    "vlm_max_tokens_summary": 'int(setting("vlm_max_tokens_summary", 2048))',
    "skip_audio": 'bool(setting("skip_audio", False))',
    "skip_visual": 'bool(setting("skip_visual", False))',
    "generate_txt": 'bool(setting("generate_txt", True))',
    "enable_audio_features": 'bool(setting("enable_audio_features", True))',
    "enable_apple_ocr": 'bool(setting("enable_apple_ocr", True))',
    "generate_summary": 'bool(setting("generate_summary", True))',
}


def find_parameter_form_default(source: str, name: str):
    """Return (arg_start, arg_end, current_arg) for name's first Form() argument."""
    declaration = re.search(rf"(?m)^[ \t]*{re.escape(name)}[ \t]*:", source)
    if not declaration:
        raise ValueError(f"FastAPI parameter '{name}' was not found")

    # Bound the search to this parameter declaration, stopping before the next argument.
    next_decl = re.search(r"(?m)^[ \t]*[A-Za-z_]\w*[ \t]*:", source[declaration.end():])
    bound = declaration.end() + next_decl.start() if next_decl else min(len(source), declaration.end() + 2000)
    form_match = re.search(r"=\s*Form\s*\(", source[declaration.start():bound])
    if not form_match:
        raise ValueError(f"FastAPI parameter '{name}' does not use Form()")
    form_open = declaration.start() + form_match.end() - 1  # points at '(' of Form(

    i = form_open + 1
    while i < len(source) and source[i].isspace():
        i += 1
    start = i
    stack = []
    quote = None
    triple = False
    escaped = False
    pairs = {"(": ")", "[": "]", "{": "}"}

    while i < len(source):
        ch = source[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif triple and source.startswith(quote * 3, i):
                quote = None
                triple = False
                i += 2
            elif not triple and ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
                triple = source.startswith(ch * 3, i)
                if triple:
                    i += 2
            elif ch in pairs:
                stack.append(pairs[ch])
            elif ch in (")", "]", "}"):
                if not stack:
                    if ch == ")":
                        return start, i, source[start:i].strip()
                    raise ValueError(f"Unbalanced delimiters in Form() argument for '{name}'")
                expected = stack.pop()
                if ch != expected:
                    raise ValueError(f"Mismatched delimiters in Form() argument for '{name}'")
            elif ch == "," and not stack:
                return start, i, source[start:i].strip()
        i += 1
    raise ValueError(f"Could not find the end of Form() default for '{name}'")


def set_form_default(source: str, name: str, expression: str) -> str:
    start, end, current = find_parameter_form_default(source, name)
    if f'setting("{name}"' in current:
        return source  # idempotent; a prior run already connected this parameter to JSON
    return source[:start] + expression + source[end:]


def replace_line(source: str, pattern: str, replacement: str, label: str, required=True) -> str:
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count == 1:
        return updated
    # It's okay if a prior run has already applied the replacement.
    if label in source:
        return source
    if required:
        raise ValueError(f"Could not locate {label}")
    return source


def main() -> int:
    if not TARGET.is_file():
        print(f"ERROR: {TARGET.resolve()} not found. Run from ~/Downloads/VideoContext-Engine.")
        return 2

    source = TARGET.read_text(encoding="utf-8")
    original = source

    required = ["def resize_keep_aspect(", "def apple_vision_ocr(", "enable_apple_ocr: bool"]
    missing = [needle for needle in required if needle not in source]
    if missing:
        print("ERROR: required optimization / Apple OCR patch is missing:")
        for needle in missing:
            print(f"  - {needle}")
        print("Apply patch_vce_optimization.py and patch_vce_apple_ocr.py first. No files changed.")
        return 3

    # Install a JSON loader once. Imports in v3.19 already include json, Path, Dict and Any.
    if "def load_user_settings() -> Dict[str, Any]:" not in source:
        ram_line = re.search(r'(?m)^RAM_MODE\s*=\s*os\.getenv\("VIDEOCONTEXT_RAM_MODE"[^\n]*\n', source)
        if not ram_line:
            print('ERROR: could not find the RAM_MODE initialization line. No files changed.')
            return 3
        source = source[:ram_line.start()] + SETTINGS_LOADER + source[ram_line.start():]

    # RAM_MODE: an explicitly exported environment variable wins over the JSON setting.
    ram_pattern = r'(?m)^RAM_MODE\s*=\s*os\.getenv\("VIDEOCONTEXT_RAM_MODE",[^\n]*\)\.lower\(\)[^\n]*$'
    if 'setting("ram_mode"' not in source:
        source = replace_line(
            source, ram_pattern,
            'RAM_MODE = os.getenv("VIDEOCONTEXT_RAM_MODE", str(setting("ram_mode", "ram-"))).lower()  # "ram+" ou "ram-"',
            'RAM_MODE = os.getenv("VIDEOCONTEXT_RAM_MODE", str(setting("ram_mode", "ram-")))',
        )

    try:
        for name, expression in FORM_DEFAULTS.items():
            source = set_form_default(source, name, expression)

        # Apple Vision OCR tunables: only adjust default literals, preserving surrounding logic.
        substitutions = [
            (r'(?m)^([ \t]*recognition_level\s*=\s*)"accurate"\s*,', r'\1str(setting("apple_ocr_recognition_level", "accurate")),', 'setting("apple_ocr_recognition_level"'),
            (r'(?m)^([ \t]*language_preference\s*=\s*)\["ru-RU",\s*"en-US"\]\s*,', r'\1setting("apple_ocr_languages", ["ru-RU", "en-US"]),', 'setting("apple_ocr_languages"'),
            (r'(?m)^([ \t]*confidence_threshold\s*=\s*)0\.30\s*,', r'\1float(setting("apple_ocr_confidence", 0.30)),', 'setting("apple_ocr_confidence"'),
            (r'(?m)^([ \t]*if not text or confidence < )0\.30(:)', r'\1float(setting("apple_ocr_confidence", 0.30))\2', 'confidence < float(setting("apple_ocr_confidence"'),
        ]
        for pattern, replacement, idempotency_marker in substitutions:
            if idempotency_marker not in source:
                source, count = re.subn(pattern, replacement, source, count=1)
                if count != 1:
                    raise ValueError(f"Could not locate Apple OCR setting to connect: {idempotency_marker}")

        # MLX device override (environment variable still takes precedence over JSON).
        device_pattern = r'(?m)^([ \t]*device_override\s*=\s*os\.environ\.get\("VIDEOCONTEXT_MLX_DEVICE",\s*)"auto"(\)\.strip\(\)\.lower\(\))'
        if 'setting("mlx_device"' not in source:
            source, count = re.subn(
                device_pattern,
                r'\1str(setting("mlx_device", "auto"))\2',
                source,
                count=1,
            )
            if count != 1:
                raise ValueError("Could not locate VIDEOCONTEXT_MLX_DEVICE override inserted by optimization patch")

        # In ram+ mode preload the same models chosen in videocontext_settings.json.
        if 'vlm_engine.load_model(str(setting("vlm_model"' not in source:
            old = 'vlm_engine.load_model(ACTIVE_DEFAULT_VLM)'
            if old not in source:
                raise ValueError("Could not locate RAM+ VLM preload call")
            source = source.replace(old, 'vlm_engine.load_model(str(setting("vlm_model", ACTIVE_DEFAULT_VLM)))', 1)
        if 'setting("whisper_model", DEFAULT_WHISPER_MODEL)' not in source:
            old = 'WHISPER_CACHE[DEFAULT_WHISPER_MODEL] = whisper.load_model(DEFAULT_WHISPER_MODEL)'
            if old not in source:
                raise ValueError("Could not locate RAM+ Whisper preload call")
            new = ('configured_whisper = str(setting("whisper_model", DEFAULT_WHISPER_MODEL))\n'
                   '            WHISPER_CACHE[configured_whisper] = whisper.load_model(configured_whisper)')
            source = source.replace(old, new, 1)

    except ValueError as exc:
        print(f"ERROR: {exc}. No files changed.")
        return 4

    # Validate all defaults are now attached to the JSON config before writing.
    for name in FORM_DEFAULTS:
        try:
            _, _, expression = find_parameter_form_default(source, name)
        except ValueError as exc:
            print(f"ERROR: validation failed for '{name}': {exc}. No files changed.")
            return 5
        if f'setting("{name}"' not in expression:
            print(f'ERROR: parameter "{name}" is not connected to settings JSON. No files changed.')
            return 5

    # Keep existing user edits. Create the JSON only if it isn't present.
    if CONFIG.exists():
        try:
            existing = json.loads(CONFIG.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                raise ValueError("root must be a JSON object")
        except Exception as exc:
            print(f"ERROR: existing {CONFIG} is not valid JSON: {exc}. No files changed.")
            return 6
    else:
        CONFIG.write_text(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Created settings file: {CONFIG.resolve()}")

    if source == original:
        print("Settings integration is already applied; no source changes were needed.")
        print(f"Settings: {CONFIG.resolve()}")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = TARGET.with_name(f"{TARGET.name}.before_settings_{timestamp}.bak")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(source, encoding="utf-8")

    print("Persistent settings applied successfully.")
    print(f"Backup: {backup.resolve()}")
    print(f"Settings: {CONFIG.resolve()}")
    print("Restart the server for JSON defaults to appear in Swagger.")
    print("Explicit VIDEOCONTEXT_RAM_MODE / VIDEOCONTEXT_MLX_DEVICE environment variables override JSON values.")
    print("skip_audio remains false by default to preserve lecture transcription; enable it per request for silent/music-only clips.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
