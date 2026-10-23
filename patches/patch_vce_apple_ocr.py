#!/usr/bin/env python3
"""Add optional Apple Vision OCR support to VideoContext Engine v3.19.

This patcher is designed to be run in the VideoContext-Engine project folder
AFTER patch_vce_optimization.py. It adds a Swagger checkbox enable_apple_ocr,
extracts text from the same aspect-correct keyframes with macOS Vision through
ocrmac, passes the text as grounding context to the VLM, exposes it in JSON/TXT,
and reports OCR timing. A timestamped backup is created first.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import sys

TARGET = Path("VideoContextEngine_v3.19.py")

OCR_HELPER = r'''def apple_vision_ocr(image: Image.Image) -> str:
    """Extract visible text using macOS Vision via ocrmac; preserve reading order."""
    try:
        from ocrmac import ocrmac
    except Exception as exc:
        raise RuntimeError(
            "Apple OCR is enabled, but ocrmac is unavailable. Activate the project venv "
            "and run: python -m pip install ocrmac"
        ) from exc

    try:
        rows = ocrmac.OCR(
            image,
            recognition_level="accurate",
            language_preference=["ru-RU", "en-US"],
            confidence_threshold=0.30,
            detail=True,
        ).recognize()
    except ValueError:
        # Some macOS/Vision combinations may not support one of the requested languages.
        rows = ocrmac.OCR(
            image,
            recognition_level="accurate",
            language_preference=None,
            confidence_threshold=0.30,
            detail=True,
        ).recognize()

    lines = []
    seen = set()
    for row in rows or []:
        if isinstance(row, (tuple, list)) and row:
            text = str(row[0])
            confidence = float(row[1]) if len(row) > 1 else 1.0
        else:
            text = str(row)
            confidence = 1.0
        text = re.sub(r"\s+", " ", text).strip()
        if not text or confidence < 0.30:
            continue
        key = re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()
        if key and key not in seen:
            seen.add(key)
            lines.append(text)
    return "\n".join(lines)


'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one occurrence of {label}; found {count}.")
    return source.replace(old, new, 1)


def main() -> int:
    if not TARGET.is_file():
        print(f"ERROR: {TARGET.resolve()} not found. Run this script in ~/Downloads/VideoContext-Engine.")
        return 2

    source = TARGET.read_text(encoding="utf-8")
    original = source

    # Do not apply twice; an explicit stop is safer than duplicate form fields.
    if "def apple_vision_ocr(" in source or "enable_apple_ocr: bool" in source:
        print("Apple OCR hooks already appear to be present. No changes made.")
        return 0

    try:
        # Helper is inserted below sample_keyframes_for_scene and above audio helpers.
        source = replace_once(
            source,
            "# --- AUDIO FEATURES ---\n",
            OCR_HELPER + "# --- AUDIO FEATURES ---\n",
            "audio-features insertion point",
        )

        source = replace_once(
            source,
            '    enable_audio_features: bool = Form(\n        True, description="Calculer les audio_features par scène ? (True par défaut)"\n    ),\n',
            '    enable_audio_features: bool = Form(\n        True, description="Calculer les audio_features par scène ? (True par défaut)"\n    ),\n'
            '    enable_apple_ocr: bool = Form(\n'
            '        False, description="Extraire le texte des keyframes avec Apple Vision OCR et le fournir au VLM"\n'
            '    ),\n',
            "enable_apple_ocr form field",
        )

        source = replace_once(
            source,
            "    whisper_infer_time = 0.0\n    vlm_infer_time = 0.0\n    whisper_load_time = 0.0\n",
            "    whisper_infer_time = 0.0\n    vlm_infer_time = 0.0\n    ocr_infer_time = 0.0\n    whisper_load_time = 0.0\n",
            "timing initialization",
        )

        # Add an empty OCR field in the skip-both branch to keep the response schema consistent.
        source = replace_once(
            source,
            '                        "visual_description": "",\n                        "visual_tags": {},\n',
            '                        "visual_description": "",\n                        "ocr_text": "",\n                        "visual_tags": {},\n',
            "empty scene OCR field",
        )

        source = replace_once(
            source,
            '                    visual_description = ""\n                    visual_tags: Dict[str, Any] = {}\n',
            '                    visual_description = ""\n                    scene_ocr_text = ""\n                    visual_tags: Dict[str, Any] = {}\n',
            "scene OCR initialization",
        )

        source = replace_once(
            source,
            '                            if visual_user_prompt.strip():\n'
            '                                full_visual_prompt += (\n'
            '                                    "\\n\\nAdditional user instructions (follow them if possible):\\n"\n'
            '                                    + visual_user_prompt.strip()\n'
            '                                )\n\n'
            '                            t_vlm_start = time.time()\n',
            '                            if visual_user_prompt.strip():\n'
            '                                full_visual_prompt += (\n'
            '                                    "\\n\\nAdditional user instructions (follow them if possible):\\n"\n'
            '                                    + visual_user_prompt.strip()\n'
            '                                )\n\n'
            '                            if enable_apple_ocr:\n'
            '                                t_ocr_start = time.time()\n'
            '                                ocr_parts = []\n'
            '                                for kf in keyframes:\n'
            '                                    extracted = apple_vision_ocr(kf["image"])\n'
            '                                    if extracted:\n'
            '                                        ocr_parts.append(f"[Кадр {kf[\'time\']:.2f} с]\\n{extracted}")\n'
            '                                scene_ocr_text = "\\n\\n".join(ocr_parts)\n'
            '                                ocr_infer_time += time.time() - t_ocr_start\n'
            '                                if scene_ocr_text:\n'
            '                                    full_visual_prompt += (\n'
            '                                        "\\n\\nТЕКСТ, РАСПОЗНАННЫЙ APPLE VISION OCR (возможны ошибки):\\n"\n'
            '                                        + scene_ocr_text\n'
            '                                        + "\\n\\nИспользуй OCR как подсказку для точного чтения подписей. Сверяй его с изображениями, не выдумывай отсутствующие надписи и отдельно описывай связи между блоками схемы."\n'
            '                                    )\n\n'
            '                            t_vlm_start = time.time()\n',
            "Apple OCR prompt hook",
        )

        source = replace_once(
            source,
            '                            "visual_description": visual_description,\n                            "visual_tags": visual_tags,\n',
            '                            "visual_description": visual_description,\n'
            '                            "ocr_text": scene_ocr_text,\n'
            '                            "visual_tags": visual_tags,\n',
            "scene output OCR field",
        )

        source = replace_once(
            source,
            '                        elif s.get("visual_description"):\n'
            '                            parts.append(f"Visuel: {s[\'visual_description\']}")\n\n'
            '                        if parts:\n',
            '                        elif s.get("visual_description"):\n'
            '                            parts.append(f"Visuel: {s[\'visual_description\']}")\n'
            '                        if s.get("ocr_text"):\n'
            '                            ocr_for_summary = s["ocr_text"].replace("\\n", " | ")[:500]\n'
            '                            parts.append(f"Texte à l’écran (Apple OCR): {ocr_for_summary}")\n\n'
            '                        if parts:\n',
            "summary OCR context hook",
        )

        source = replace_once(
            source,
            '        print(f"⏱️ VLM    : load={vlm_load_time:.2f}s, infer={vlm_infer_time:.2f}s")\n'
            '        print(f"RAM_MODE = {RAM_MODE}")\n',
            '        print(f"⏱️ VLM    : load={vlm_load_time:.2f}s, infer={vlm_infer_time:.2f}s")\n'
            '        print(f"⏱️ Apple OCR: enabled={enable_apple_ocr}, infer={ocr_infer_time:.2f}s")\n'
            '        print(f"RAM_MODE = {RAM_MODE}")\n',
            "OCR timing log",
        )

        source = replace_once(
            source,
            '            "vlm": {\n'
            '                "model": target_vlm,\n'
            '                "load_time": round(vlm_load_time, 3),\n'
            '                "inference_time": round(vlm_infer_time, 3),\n'
            '            },\n'
            '            "ram_mode": RAM_MODE,\n',
            '            "vlm": {\n'
            '                "model": target_vlm,\n'
            '                "load_time": round(vlm_load_time, 3),\n'
            '                "inference_time": round(vlm_infer_time, 3),\n'
            '            },\n'
            '            "apple_ocr": {\n'
            '                "enabled": enable_apple_ocr,\n'
            '                "inference_time": round(ocr_infer_time, 3),\n'
            '            },\n'
            '            "ram_mode": RAM_MODE,\n',
            "OCR timing JSON field",
        )

        source = replace_once(
            source,
            '                    "params": {\n'
            '                        "keyframes_per_scene": keyframes_per_scene,\n'
            '                        "enable_audio_features": enable_audio_features,\n',
            '                    "params": {\n'
            '                        "resolution": vlm_resolution,\n'
            '                        "scene_threshold": scene_threshold,\n'
            '                        "min_scene_duration": min_scene_duration,\n'
            '                        "max_scene_duration": max_scene_duration,\n'
            '                        "keyframes_per_scene": keyframes_per_scene,\n'
            '                        "enable_apple_ocr": enable_apple_ocr,\n'
            '                        "enable_audio_features": enable_audio_features,\n',
            "visible experiment params",
        )

        source = replace_once(
            source,
            '        if seg.get("visual_description"):\n'
            '            lines.append(f"   👀 VISUEL : {seg[\'visual_description\']}")\n\n'
            '        vt = seg.get("visual_tags") or {}\n',
            '        if seg.get("visual_description"):\n'
            '            lines.append(f"   👀 VISUEL : {seg[\'visual_description\']}")\n'
            '        if seg.get("ocr_text"):\n'
            '            lines.append("   🔎 APPLE OCR : " + seg["ocr_text"].replace("\\n", " | "))\n\n'
            '        vt = seg.get("visual_tags") or {}\n',
            "TXT OCR report field",
        )

    except ValueError as exc:
        print(f"ERROR: {exc}")
        print("No changes have been written.")
        return 3

    # Validate all new hooks before touching the source file.
    required = [
        "def apple_vision_ocr(",
        "enable_apple_ocr: bool",
        '"ocr_text": scene_ocr_text',
        '"apple_ocr": {',
        '"enable_apple_ocr": enable_apple_ocr',
    ]
    missing = [needle for needle in required if needle not in source]
    if missing or source == original:
        print(f"ERROR: patch validation failed. Missing: {missing}; changed={source != original}")
        print("No changes have been written.")
        return 4

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = TARGET.with_name(f"{TARGET.name}.before_apple_ocr_{timestamp}.bak")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(source, encoding="utf-8")
    print("Apple Vision OCR integration applied successfully.")
    print(f"Backup: {backup.resolve()}")
    print("- Enable/disable Apple OCR via enable_apple_ocr in /docs.")
    print("- OCR text is included in each scene's ocr_text field and sent to Qwen as grounding context.")
    print("- OCR inference time and effective resolution/settings are recorded in the JSON response.")
    print("Next trial: rerun the same video and settings as test 1, changing only enable_apple_ocr=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
