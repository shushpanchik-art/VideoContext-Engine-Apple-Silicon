#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add an editable ASR/OCR name-reconciliation instruction to an extended VCE v3.19 build.

This is an optional patch for builds that already contain Qwen3.5 text post-processing.
It is not applicable to vanilla upstream v3.19 alone.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

PROMPT_DEFAULT = '''
### Согласование названий и собственных имён между источниками

Цель: единообразно нормализовать названия и обозначения во всех аналитических полях, используя только подтверждения из этого видео.

1. Не принимай фонетическую запись ASR за официальное написание. Whisper может искажать бренды, названия программ, имена, аббревиатуры и номера нормативов.
2. Приоритет точного написания — читаемая надпись OCR, если по смыслу и времени она относится к тому же объекту или фразе. Учитывай соседние связанные сцены, но не считай любое слово на экране подтверждением.
3. Для каждой подтверждённой сущности используй один канонический вариант во всех производных полях: executive_summary, topics, key_points, entities, scene_enrichment и improved_description. Не создавай дубль под фонетическим вариантом ASR.
4. Для стандартов, нормативов, версий и числовых кодов проверяй каждый символ и разделитель. Если OCR в связанной сцене чётко показывает «СП 1.13130.2020», а ASR искажает номер как «СП 1.13.132.020», в производных полях используй «СП 1.13130.2020». Применяй пример только при явном подтверждении именно этого обозначения в OCR.
5. После выбора канонического варианта выполни финальный поиск по всем производным полям. Исправь оставшиеся противоречивые варианты в topics, сводках, тезисах, сущностях и описаниях сцен, не меняя смысл фактов.
6. Не изменяй исходные audio_transcript, ocr_text, visual_description, временные метки и базовую original_summary. Это контрольные исходные данные; коррекция применяется только к производным аналитическим полям.
7. Не придумывай написание по общим знаниям и не делай вывод, опираясь только на фонетическое сходство. Если OCR нечитаем, противоречив или относится к другому объекту, сохрани неопределённость.
8. Не добавляй предупреждение о неопределённости только потому, что ASR произнёс название иначе, если связанный OCR чётко его подтверждает. Если доказательств недостаточно, различай наблюдаемое написание, вероятную идентификацию и гипотезу.
9. Перед выдачей JSON сверь все значения повторяющихся сущностей между полями. Не оставляй неправильную форму в одном массиве, если остальные поля уже подтверждают каноническую форму.
'''

SETTING_KEY = "text_llm_name_reconciliation_prompt"
MARKER = "VCE_NAME_RECONCILIATION_PROMPT_V1"


def js_injection(default_prompt: str) -> str:
    default_js = json.dumps(default_prompt, ensure_ascii=False)
    template = r'''<!-- VCE_NAME_RECONCILIATION_PROMPT_V1 -->
<script>
(() => {
  "use strict";
  const KEY = "text_llm_name_reconciliation_prompt";
  const DEFAULT_PROMPT = __DEFAULT_PROMPT_JSON__;
  const FIELD_ID = "vceNameReconciliationPrompt";
  if (window.__VCE_NAME_RECONCILIATION_PROMPT_V1) return;
  window.__VCE_NAME_RECONCILIATION_PROMPT_V1 = true;

  const getText = (el) => String(el?.innerText || el?.value || el?.textContent || "").replace(/\s+/g, " ").trim();
  const isSettingsUrl = (url) => String(url || "").includes("/api/v1/ui-settings");
  let editor = null;

  function findButton(regex) {
    return [...document.querySelectorAll("button, input[type='button'], input[type='submit']")]
      .find((el) => regex.test(getText(el)));
  }

  function installEditor(attempt = 0) {
    if (document.getElementById(FIELD_ID)) {
      editor = document.getElementById(FIELD_ID);
      return true;
    }
    const saveButton = findButton(/сохранить\s+настройки/i);
    if (!saveButton) {
      if (attempt < 40) setTimeout(() => installEditor(attempt + 1), 250);
      return false;
    }

    let settingsPanel = saveButton.parentElement;
    for (let node = saveButton.parentElement, n = 0; node && n < 10; node = node.parentElement, n++) {
      if (/параметры\s+анализа/i.test(getText(node)) && node.contains(saveButton)) {
        settingsPanel = node;
        break;
      }
    }

    let actionRow = saveButton.parentElement;
    for (let n = 0; n < 6 && actionRow.parentElement && actionRow.parentElement !== settingsPanel; n++) {
      const count = actionRow.querySelectorAll("button, input[type='button'], input[type='submit']").length;
      if (count >= 2) break;
      actionRow = actionRow.parentElement;
    }
    const insertionParent = actionRow.parentElement || settingsPanel;

    const wrapper = document.createElement("section");
    wrapper.id = "vceNameReconciliationPromptBlock";
    wrapper.style.cssText = "box-sizing:border-box;width:100%;flex:0 0 100%;grid-column:1 / -1;margin:16px 0;padding:12px;border:1px solid #b8b8b8;border-radius:8px;";

    const label = document.createElement("label");
    label.htmlFor = FIELD_ID;
    label.textContent = "Промпт: согласование названий и собственных имён (ASR ↔ OCR)";
    label.style.cssText = "display:block;font-weight:600;margin-bottom:8px;";

    const help = document.createElement("p");
    help.textContent = "Эта инструкция добавляется к запросу Qwen3.5 при текстовой постобработке. Сохраните настройки, чтобы применить изменения к следующим анализам. Схема JSON и защита исходного транскрипта/OCR остаются фиксированными правилами движка.";
    help.style.cssText = "margin:0 0 8px;font-size:0.92em;opacity:0.85;";

    const textarea = document.createElement("textarea");
    textarea.id = FIELD_ID;
    textarea.rows = 12;
    textarea.spellcheck = true;
    textarea.autocapitalize = "sentences";
    textarea.value = DEFAULT_PROMPT;
    textarea.setAttribute("aria-label", label.textContent);
    textarea.style.cssText = "display:block;box-sizing:border-box;width:100%;min-height:240px;max-height:55vh;resize:vertical;padding:10px;border:1px solid #888;border-radius:6px;font:inherit;line-height:1.45;white-space:pre-wrap;";

    wrapper.append(label, help, textarea);
    if (insertionParent && actionRow.parentElement === insertionParent) {
      insertionParent.insertBefore(wrapper, actionRow);
    } else if (insertionParent) {
      insertionParent.appendChild(wrapper);
    } else {
      settingsPanel.appendChild(wrapper);
    }
    editor = textarea;
    return true;
  }

  function populateFromSettings(data) {
    if (!editor) installEditor();
    if (!editor) return;
    const value = data?.settings?.[KEY] ?? data?.[KEY];
    if (typeof value === "string") editor.value = value;
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function(input, init) {
    const requestUrl = typeof input === "string" ? input : (input && input.url) || "";
    const requestMethod = String(init?.method || (input && input.method) || "GET").toUpperCase();
    let nextInit = init;
    if (isSettingsUrl(requestUrl) && requestMethod === "POST" && init && typeof init.body === "string") {
      try {
        const payload = JSON.parse(init.body);
        if (editor) payload[KEY] = editor.value;
        nextInit = { ...init, body: JSON.stringify(payload) };
      } catch (_) {
        // Keep the original request untouched if the UI uses another body format.
      }
    }
    const response = await nativeFetch(input, nextInit);
    if (isSettingsUrl(requestUrl) && response.ok) {
      response.clone().json().then(populateFromSettings).catch(() => {});
    }
    return response;
  };

  function start() {
    installEditor();
    nativeFetch("/api/v1/ui-settings", { cache: "no-store" })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => { if (data) populateFromSettings(data); })
      .catch(() => {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
</script>
'''
    return template.replace("__DEFAULT_PROMPT_JSON__", default_js)


def require_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Не удалось изменить {label}: ожидался один якорь, найдено {count}.")
    return text.replace(old, new, 1)


def resolve_source(project: Path) -> Path:
    direct = project / "VideoContextEngine_v3.19.py"
    if direct.is_file():
        return direct
    skip_parts = {".venv", "venv", "__pycache__", ".git", "site-packages"}
    hits = [
        p for p in project.rglob("VideoContextEngine_v3.19.py")
        if not any(part in skip_parts for part in p.parts)
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise FileNotFoundError(f"Не найден VideoContextEngine_v3.19.py в {project}")
    raise RuntimeError("Найдено несколько VideoContextEngine_v3.19.py; укажи --project на папку с одним исходником.")


def prepare_changes(project: Path) -> Tuple[Dict[Path, str], Dict[Path, str]]:
    source = resolve_source(project)
    settings_path = source.with_name("videocontext_settings.json")
    html_path = source.with_name("videocontext_web_ui.html")
    for path in (settings_path, html_path):
        if not path.is_file():
            raise FileNotFoundError(f"Не найден обязательный файл: {path}")

    py_original = source.read_text(encoding="utf-8")
    settings_original = settings_path.read_text(encoding="utf-8")
    html_original = html_path.read_text(encoding="utf-8")

    required_anchors = {
        "объявление модели Text LLM": 'TEXT_LLM_DEFAULT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"\n',
        "GET API настроек": '    defaults.setdefault("text_llm_max_tokens", TEXT_LLM_DEFAULT_MAX_TOKENS)\n',
        "allow-list POST API настроек": '        elif key == "text_llm_model":\n            if value != TEXT_LLM_DEFAULT_MODEL:\n',
        "инструкция текстовой постобработки": '            + coverage_note\n            + "\\n\\nДанные анализа (JSON):\\n"\n',
    }
    missing = [label for label, anchor in required_anchors.items() if anchor not in py_original]
    if missing:
        raise RuntimeError(
            "Патчер предназначен для сборки, где уже есть расширение текстовой постобработки Qwen3.5. "
            "В стандартном upstream v3.19 этих точек подключения нет. Не найдено: "
            + "; ".join(missing) + ". Файлы не изменены."
        )
    if "/api/v1/ui-settings" not in py_original or "сохранить настройки" not in html_original.lower():
        raise RuntimeError("Не найдены действующие API/кнопка сохранения настроек. Файлы не изменены.")

    if MARKER in py_original and MARKER in html_original and SETTING_KEY in settings_original:
        return ({}, {})
    if MARKER in py_original or MARKER in html_original:
        raise RuntimeError("Найден частично применённый патч. Восстанови резервную копию; файлы не изменены.")

    py = py_original
    model_marker = 'TEXT_LLM_DEFAULT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"\n'
    const_block = (
        f"# {MARKER}\n"
        "TEXT_LLM_NAME_RECONCILIATION_PROMPT_DEFAULT = r'''\n"
        + PROMPT_DEFAULT
        + "\n'''.strip()\n\n"
        + model_marker
    )
    py = require_once(py, model_marker, const_block, "default prompt")

    get_anchor = '    defaults.setdefault("text_llm_max_tokens", TEXT_LLM_DEFAULT_MAX_TOKENS)\n'
    py = require_once(
        py, get_anchor,
        get_anchor + f'    defaults.setdefault("{SETTING_KEY}", TEXT_LLM_NAME_RECONCILIATION_PROMPT_DEFAULT)\n',
        "GET settings API",
    )

    save_anchor = '        elif key == "text_llm_model":\n            if value != TEXT_LLM_DEFAULT_MODEL:\n'
    save_replacement = (
        f'        elif key == "{SETTING_KEY}":\n'
        '            if not isinstance(value, str):\n'
        '                raise HTTPException(422, "Промпт согласования названий должен быть строкой")\n'
        '            if len(value) > 8000:\n'
        '                raise HTTPException(422, "Промпт согласования названий не должен превышать 8000 символов")\n'
        + save_anchor
    )
    py = require_once(py, save_anchor, save_replacement, "POST settings API allow-list")

    context_anchor = '            + coverage_note\n            + "\\n\\nДанные анализа (JSON):\\n"\n'
    context_replacement = (
        '            + "\\n\\nПромпт согласования названий и собственных имён между ASR и OCR:\\n"\n'
        f'            + str(setting("{SETTING_KEY}", TEXT_LLM_NAME_RECONCILIATION_PROMPT_DEFAULT)).strip()\n'
        '            + "\\n\\n"\n'
        + context_anchor
    )
    py = require_once(py, context_anchor, context_replacement, "Text LLM instruction hook")
    compile(py, str(source), "exec")

    try:
        settings = json.loads(settings_original)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Текущий videocontext_settings.json невалиден: {exc}") from exc
    if not isinstance(settings, dict):
        raise RuntimeError("Корень videocontext_settings.json должен быть JSON-объектом.")
    if SETTING_KEY not in settings:
        settings[SETTING_KEY] = PROMPT_DEFAULT
    settings_new = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    json.loads(settings_new)

    if "</body>" not in html_original.lower():
        raise RuntimeError("Не найден закрывающий тег </body> в Web UI.")
    html = re.sub(
        r"</body>",
        lambda _match: js_injection(PROMPT_DEFAULT) + "\n</body>",
        html_original, count=1, flags=re.IGNORECASE,
    )
    if html == html_original or MARKER not in html:
        raise RuntimeError("Не удалось добавить редактор промпта в HTML.")

    return (
        {source: py, settings_path: settings_new, html_path: html},
        {source: py_original, settings_path: settings_original, html_path: html_original},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="~/VideoContext-Engine", help="Папка проекта (по умолчанию ~/VideoContext-Engine)")
    parser.add_argument("--dry-run", action="store_true", help="Проверить применимость патча без записи файлов")
    args = parser.parse_args()
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        print(f"ОШИБКА: каталог проекта не найден: {project}", file=sys.stderr)
        return 2

    try:
        changes, originals = prepare_changes(project)
    except Exception as exc:
        print(f"ОШИБКА: {exc}\nФайлы не изменены.", file=sys.stderr)
        return 2

    if not changes:
        print("Патч уже применён; изменений не требуется.")
        return 0

    print("Проверки пройдены. Будут изменены:")
    for path in changes:
        print(f"  - {path}")
    if args.dry_run:
        print("Режим dry-run: файлы не изменены.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: Dict[Path, Path] = {}
    try:
        for path, original in originals.items():
            backup = path.with_name(path.name + f".bak_name_prompt_{stamp}")
            shutil.copy2(path, backup)
            backups[path] = backup
        for path, content in changes.items():
            temp = path.with_name(path.name + f".tmp_name_prompt_{stamp}")
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, path)
    except Exception as exc:
        for path, original in originals.items():
            try:
                path.write_text(original, encoding="utf-8")
            except Exception:
                pass
        print(f"ОШИБКА при записи: {exc}. Исходные тексты восстановлены там, где это удалось.", file=sys.stderr)
        print("Резервные копии:", *(str(p) for p in backups.values()), sep="\n  ", file=sys.stderr)
        return 3

    print("\nГотово. Созданы резервные копии:")
    for backup in backups.values():
        print(f"  - {backup}")
    print("\nПерезапусти VideoContext Engine. Промпт доступен в «Параметры анализа»; затем нажми «Сохранить настройки».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
