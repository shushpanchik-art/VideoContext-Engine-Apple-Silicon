#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Improve technical-video prompts and preserve visual detail in the global summary.

Run --dry-run first. The patch updates only the visual/summary prompt constants in
VideoContextEngine_v3.19.py and the two prompt fields in videocontext_settings.json.
It also fixes summary-context assembly so visual descriptions are not discarded
whenever visual tags exist. Both files receive timestamped backups before writes.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ENGINE_NAME = "VideoContextEngine_v3.19.py"
CONFIG_NAME = "videocontext_settings.json"

VISUAL_USER_PROMPT = (
    "Анализируй техническую сцену на русском языке и в первую очередь передавай полезное "
    "содержимое экрана, а не общие слова об обстановке. Для слайдов, терминала, кода, "
    "приложений, таблиц и схем сохраняй читаемые заголовки, ключевые пункты, команды, "
    "флаги, имена файлов и путей, числа, параметры, ошибки и результаты дословно; "
    "не исправляй видимые опечатки. Для таблиц связывай значения с правильными строками, "
    "для схем описывай подписи, стрелки и связи. Не угадывай нечитаемый текст, причины "
    "или невидимые действия: укажи [неразборчиво], если важная часть не читается. "
    "Отделяй факт от интерпретации. Между кадрами одной сцены отмечай только существенные "
    "изменения и не повторяй неизменившиеся детали. Если технического содержимого нет, "
    "кратко опиши видимое действие. Соблюдай заданную JSON-схему; при нехватке оснований "
    "выбирай нейтральное значение."
)

SUMMARY_USER_PROMPT = (
    "Составь содержательный технический конспект видео на русском языке по временным "
    "отметкам, распознанной речи и визуальным описаниям. Сохраняй последовательность "
    "объяснения: тема, понятия, шаги работы, команды и код, результаты, ошибки и "
    "способы их исправления, ограничения и практические выводы. Ключевые команды, "
    "аргументы, пути, имена файлов, параметры, числовые значения и сообщения об "
    "ошибках цитируй дословно, когда они присутствуют во входных данных. Убирай "
    "повторы, но не удаляй уникальные шаги или важные детали. Речь описывает сказанное, "
    "визуальные данные — то, что показано на экране: если они расходятся, явно укажи "
    "это, не выдумывая согласование. Не добавляй отсутствующие команды, результаты, "
    "причины или факты. Если информация неразборчива или неоднозначна, укажи это. "
    "Используй конкретные временные отметки, когда они доступны, и заверши коротким "
    "списком главных выводов и практических действий."
)

BASE_VISUAL_PROMPT = """
Ты анализируешь один или несколько кадров одной сцены видео. Верни только корректный JSON
строго с той же структурой и именами полей:
{
  "description": "фактическое и достаточно подробное описание сцены",
  "tags": {
    "people_count": 0,
    "place_type": "studio | tv_set | classroom | office | home | outdoor | nature | stage | other",
    "main_action": "краткое основное действие",
    "emotional_tone": "calm | neutral | tense | conflictual | joyful | sad | enthusiastic | serious | other",
    "movement_level": "low | medium | high"
  }
}
Правила:
- Не добавляй текст, Markdown или комментарии за пределами JSON; не меняй схему.
- Описывай только то, что подтверждается предоставленными кадрами. Не угадывай намерения,
  невидимые действия, личности людей или содержание нечитаемого текста.
- Если видны слайды, терминал, код, браузер, интерфейс программы, таблицы, диаграммы,
  графики или сообщения об ошибках, ставь их информативное содержимое в приоритет:
  заголовки, ключевые пункты, имена элементов, команды, пути, параметры, числа и результаты.
  Важные читаемые фрагменты текста цитируй точно, сохраняя регистр, знаки и опечатки;
  не пытайся переписать мелкий текст, который нельзя уверенно прочитать.
- Для диаграмм указывай связи между блоками и направление стрелок. Для таблиц связывай
  значение с правильной строкой/столбцом. Для терминала различай введённую команду и вывод.
- Если изображения показывают разные моменты одной сцены, упомяни существенные изменения,
  не перечисляя повторно неизменившиеся детали.
- Если технического содержимого нет, опиши наблюдаемое действие, контекст и заметные изменения.
- Текстовые значения description и tags пиши на языке пользовательских инструкций;
  если язык не задан, используй французский. При недостатке оснований указывай neutral/other,
  а не придумывай конкретную эмоцию или тип места.
""".strip()

BASE_SUMMARY_PROMPT = """
Составь точное и связное резюме видео по предоставленным заметкам сцен, распознанной речи
и визуальному контексту. Верни только корректный JSON строго вида {"summary": "..."}.
Правила:
- Используй язык пользовательских инструкций; по умолчанию — французский.
- Сохраняй хронологию и логику объяснения. Выделяй тему, определения, последовательность действий,
  примеры, команды, фрагменты кода, ошибки, результаты, ограничения и практические выводы.
- Не теряй важные команды, названия файлов, пути, имена API, параметры и числовые значения;
  копируй их без изменений, если они есть во входных данных.
- Объединяй повторения, но не своди разные действия или результаты в один общий тезис.
- Различай то, что произнесено в аудио, и то, что фактически видно на экране. Не заполняй пробелы
  догадками. Если данные противоречат друг другу или неразборчивы, прямо отметь неопределённость.
- Не добавляй текст за пределами JSON.
""".strip()

def _default_project() -> Path:
    candidates = (
        Path.home() / "VideoContext-Engine",
        Path.home() / "Downloads" / "VideoContext-Engine",
    )
    for candidate in candidates:
        if (candidate / ENGINE_NAME).is_file():
            return candidate
    return candidates[0]

def replace_prompt_constant(source: str, name: str, prompt: str) -> str:
    """Replace one triple-quoted prompt constant without touching surrounding code."""
    start_match = re.search(rf"(?m)^{re.escape(name)}\s*=\s*\"\"\"", source)
    if not start_match:
        raise ValueError(f"Cannot find {name}")
    end_marker = '""".strip()'
    end = source.find(end_marker, start_match.end())
    if end < 0:
        raise ValueError(f"Cannot find end of {name}")
    current = source[start_match.end():end]
    if prompt.strip() == current.strip():
        return source
    replacement = f'{name} = """\n{prompt}\n""".strip()'
    return source[:start_match.start()] + replacement + source[end + len(end_marker):]

def transform_engine_source(source: str) -> str:
    updated = source
    updated = replace_prompt_constant(updated, "BASE_VISUAL_PROMPT", BASE_VISUAL_PROMPT)
    updated = replace_prompt_constant(updated, "BASE_SUMMARY_PROMPT", BASE_SUMMARY_PROMPT)

    marker = 'parts.append(f"Visual details: {visual_description}")'
    if marker not in updated:
        pattern = re.compile(
            r'(?ms)^ {24}vt = s\.get\("visual_tags"\) or \{\}\n'
            r'.*?^ {24}elif s\.get\("visual_description"\):\n'
            r'^ {28}parts\.append\(f"Visuel: \{s\[[\'"]visual_description[\'"]\]\}"\)\n'
        )
        replacement = '''                        visual_description = (s.get("visual_description") or "").strip()
                        if visual_description:
                            parts.append(f"Visual details: {visual_description}")

                        vt = s.get("visual_tags") or {}
                        if vt:
                            parts.append(
                                "Visual tags: "
                                f"people={vt.get('people_count', '?')}, "
                                f"place={vt.get('place_type', '?')}, "
                                f"action={vt.get('main_action', '?')}, "
                                f"tone={vt.get('emotional_tone', '?')}, "
                                f"movement={vt.get('movement_level', '?')}"
                            )
'''
        updated, count = pattern.subn(replacement, updated, count=1)
        if count != 1:
            raise ValueError(
                "Cannot locate the expected global-summary context block. "
                "Source may differ from the supported v3.19 layout; no files changed."
            )
    return updated

def optimize_settings(current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(current, dict):
        raise ValueError("Settings JSON must contain an object")
    updated = dict(current)
    updated["visual_user_prompt"] = VISUAL_USER_PROMPT
    updated["summary_user_prompt"] = SUMMARY_USER_PROMPT
    return updated

def atomic_write(path: Path, content: str) -> None:
    original_mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent),
        prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as temp:
        temp.write(content)
        temp_path = Path(temp.name)
    temp_path.chmod(original_mode)
    temp_path.replace(path)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=_default_project())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    engine_path = project / ENGINE_NAME
    config_path = project / CONFIG_NAME
    if not engine_path.is_file() or not config_path.is_file():
        print(f"ERROR: expected both {engine_path} and {config_path}", file=sys.stderr)
        return 2

    try:
        original_engine = engine_path.read_text(encoding="utf-8")
        original_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        updated_engine = transform_engine_source(original_engine)
        updated_config = optimize_settings(original_config)
        # Validate in memory before making backups or writing anything.
        compile(updated_engine, str(engine_path), "exec")
        json.dumps(updated_config, ensure_ascii=False, indent=2)
    except (OSError, json.JSONDecodeError, ValueError, SyntaxError) as exc:
        print(f"ERROR: {exc}. No files changed.", file=sys.stderr)
        return 3

    engine_changed = updated_engine != original_engine
    config_changed = updated_config != original_config
    print(f"Project: {project}")
    print(f"Engine prompt/context update needed: {'yes' if engine_changed else 'no'}")
    print(f"Settings prompts update needed: {'yes' if config_changed else 'no'}")
    print("Changes: stronger technical visual prompt; more faithful global summary; "
          "summary now receives both visual descriptions and tags.")
    print("All other configuration keys and model settings are preserved.")
    if args.dry_run:
        print("DRY RUN: no files changed.")
        return 0
    if not engine_changed and not config_changed:
        print("Already up to date; nothing to change.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = []
    try:
        if engine_changed:
            backup = engine_path.with_name(f"{engine_path.name}.before_quality_prompt_{stamp}.bak")
            shutil.copy2(engine_path, backup)
            backups.append(backup)
        if config_changed:
            backup = config_path.with_name(f"{config_path.name}.before_quality_prompt_{stamp}.bak")
            shutil.copy2(config_path, backup)
            backups.append(backup)

        if config_changed:
            atomic_write(config_path, json.dumps(updated_config, ensure_ascii=False, indent=2) + "\n")
        if engine_changed:
            atomic_write(engine_path, updated_engine)

        # Validate the written outputs. Backups remain available for rollback.
        json.loads(config_path.read_text(encoding="utf-8"))
        compile(engine_path.read_text(encoding="utf-8"), str(engine_path), "exec")
    except Exception as exc:
        print(f"ERROR: validation/write failed: {exc}", file=sys.stderr)
        for backup in backups:
            print(f"Backup available: {backup}", file=sys.stderr)
        return 4

    print("Quality prompt patch applied.")
    for backup in backups:
        print(f"Backup: {backup}")
    print("Restart VideoContext Engine before the next analysis.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
