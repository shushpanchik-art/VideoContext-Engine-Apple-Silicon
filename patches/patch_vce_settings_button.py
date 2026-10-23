#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add a persistent settings panel/button to an installed VideoContext v3.19 UI patch."""
from __future__ import annotations
import ast
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

TARGET = Path('VideoContextEngine_v3.19.py')
CONFIG = Path('videocontext_settings.json')
UI_FILE = Path('videocontext_web_ui.html')

SETTINGS_MARKUP = r'''  <section class="panel" id="settings-panel">
    <div class="topline"><div><strong>1. Настройки анализа</strong><div class="hint">Значения сохраняются в videocontext_settings.json и применяются к следующим запускам из этого интерфейса.</div></div><span class="pill" id="settings-pill">Загрузка…</span></div>
    <div class="settings-grid">
      <label class="setting">VLM-модель
        <select id="cfg-vlm-model"><option value="mlx-community/Qwen3-VL-8B-Instruct-4bit">Qwen3-VL-8B · качество</option><option value="mlx-community/Qwen3-VL-4B-Instruct-4bit">Qwen3-VL-4B · быстрее</option></select>
      </label>
      <label class="setting">Whisper-модель
        <select id="cfg-whisper-model"><option>large</option><option>medium</option><option>small</option><option>base</option><option>tiny</option></select>
      </label>
      <label class="setting">Разрешение Qwen (px)<input id="cfg-vlm-resolution" type="number" min="128" max="2048" step="64" value="1280"></label>
      <label class="setting">Порог смены сцены<input id="cfg-scene-threshold" type="number" min="0.01" max="1" step="0.01" value="0.15"></label>
      <label class="setting">Мин. длительность сцены (с)<input id="cfg-min-scene-duration" type="number" min="0.5" max="60" step="0.5" value="1.5"></label>
      <label class="setting">Макс. длительность сцены (с)<input id="cfg-max-scene-duration" type="number" min="5" max="300" step="1" value="20"></label>
      <label class="setting">Кадров на сцену<input id="cfg-keyframes" type="number" min="1" max="5" step="1" value="1"></label>
      <label class="setting">Токенов на сцену<input id="cfg-scene-tokens" type="number" min="16" max="1024" step="16" value="1024"></label>
      <label class="setting">Токенов на резюме<input id="cfg-summary-tokens" type="number" min="16" max="2048" step="16" value="2048"></label>
    </div>
    <div class="setting-checks">
      <label><input id="cfg-apple-ocr" type="checkbox" checked> Apple Vision OCR</label>
      <label><input id="cfg-whisper-acceleration" type="checkbox" checked> Ускорение Whisper (MPS)</label>
      <label><input id="cfg-audio-features" type="checkbox" checked> Аудиопризнаки по сценам</label>
      <label><input id="cfg-summary" type="checkbox" checked> Глобальный конспект</label>
      <label><input id="cfg-txt" type="checkbox" checked> Автосохранение TXT</label>
      <label><input id="cfg-json" type="checkbox" checked> Автосохранение JSON</label>
      <label><input id="cfg-word-timestamps" type="checkbox" checked> Метки времени отдельных слов</label>
    </div>
    <div class="actions"><button id="save-settings" class="primary" type="button">Сохранить настройки</button><button id="reload-settings" type="button">Загрузить сохранённые</button><span id="settings-status" class="hint" role="status"></span></div>
  </section>
'''

ROUTES = r'''@app.get("/api/v1/ui-settings", include_in_schema=False)
async def get_ui_settings():
    """Return the persisted settings shown by the local Web UI."""
    config_path = Path(__file__).with_name("videocontext_settings.json")
    try:
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(persisted, dict):
            raise ValueError("settings root must be an object")
    except Exception as exc:
        raise HTTPException(500, f"Не удалось прочитать настройки: {exc}")
    defaults = {
        "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        "whisper_model": "large", "vlm_resolution": 1280,
        "scene_threshold": 0.15, "min_scene_duration": 1.5,
        "max_scene_duration": 20, "keyframes_per_scene": 1,
        "vlm_max_tokens_scene": 1024, "vlm_max_tokens_summary": 2048,
        "enable_apple_ocr": True, "whisper_acceleration": True,
        "enable_audio_features": True, "generate_summary": True,
        "generate_txt": True, "generate_json": True, "word_timestamps": True,
    }
    return {key: persisted.get(key, defaults[key]) for key in defaults}


@app.post("/api/v1/ui-settings", include_in_schema=False)
async def save_ui_settings(payload: Dict[str, Any]):
    """Persist allow-listed settings without touching prompts or unrelated preferences."""
    config_path = Path(__file__).with_name("videocontext_settings.json")
    try:
        current = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("settings root must be an object")
    except Exception as exc:
        raise HTTPException(500, f"Не удалось прочитать настройки: {exc}")

    int_ranges = {
        "vlm_resolution": (128, 2048), "keyframes_per_scene": (1, 5),
        "vlm_max_tokens_scene": (16, 1024), "vlm_max_tokens_summary": (16, 2048),
    }
    float_ranges = {
        "scene_threshold": (0.01, 1.0), "min_scene_duration": (0.5, 60.0),
        "max_scene_duration": (5.0, 300.0),
    }
    bool_keys = {
        "enable_apple_ocr", "whisper_acceleration", "enable_audio_features",
        "generate_summary", "generate_txt", "generate_json", "word_timestamps",
    }
    allowed_vlm = {"mlx-community/Qwen3-VL-8B-Instruct-4bit", "mlx-community/Qwen3-VL-4B-Instruct-4bit"}
    allowed_whisper = {"tiny", "base", "small", "medium", "large"}
    merged = dict(current)
    for key, value in payload.items():
        if key == "vlm_model":
            if value not in allowed_vlm: raise HTTPException(422, "Недопустимая VLM-модель")
        elif key == "whisper_model":
            if value not in allowed_whisper: raise HTTPException(422, "Недопустимая Whisper-модель")
        elif key in int_ranges:
            if type(value) is not int or not int_ranges[key][0] <= value <= int_ranges[key][1]:
                raise HTTPException(422, f"Недопустимое значение настройки {key}")
        elif key in float_ranges:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not float_ranges[key][0] <= float(value) <= float_ranges[key][1]:
                raise HTTPException(422, f"Недопустимое значение настройки {key}")
            value = float(value)
        elif key in bool_keys:
            if type(value) is not bool: raise HTTPException(422, f"Настройка {key} должна быть логической")
        else:
            raise HTTPException(422, f"Неизвестная настройка: {key}")
        merged[key] = value

    temp_path = config_path.with_name(config_path.name + ".tmp")
    try:
        temp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(config_path)
        # Keep the module settings dict in sync where it is a mutable dict.
        if isinstance(globals().get("USER_SETTINGS"), dict):
            USER_SETTINGS.clear()
            USER_SETTINGS.update(merged)
    except Exception as exc:
        try: temp_path.unlink(missing_ok=True)
        except Exception: pass
        raise HTTPException(500, f"Не удалось сохранить настройки: {exc}")
    return {"ok": True, "message": "Настройки сохранены", "settings": {key: merged[key] for key in payload}}


'''

STYLE = r'''
.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-top:16px}.setting{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:13px}.setting input:not([type=checkbox]),.setting select{width:100%;padding:9px 10px;border-radius:9px;border:1px solid var(--line);background:#111720;color:var(--text);font:inherit}.setting-checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:16px}.setting-checks label{display:flex;align-items:center;gap:8px;color:var(--text);font-size:14px}.setting-checks input{accent-color:var(--accent)}#settings-status{min-width:180px}#settings-pill.saved{color:var(--good);border-color:var(--good)}#settings-pill.bad{color:var(--bad);border-color:var(--bad)}
'''

JS = r'''
  const settingsPill = document.getElementById('settings-pill');
  const settingsStatus = document.getElementById('settings-status');
  const saveSettingsButton = document.getElementById('save-settings');
  const readSettings = () => ({
    vlm_model: document.getElementById('cfg-vlm-model').value,
    whisper_model: document.getElementById('cfg-whisper-model').value,
    vlm_resolution: Number(document.getElementById('cfg-vlm-resolution').value),
    scene_threshold: Number(document.getElementById('cfg-scene-threshold').value),
    min_scene_duration: Number(document.getElementById('cfg-min-scene-duration').value),
    max_scene_duration: Number(document.getElementById('cfg-max-scene-duration').value),
    keyframes_per_scene: Number(document.getElementById('cfg-keyframes').value),
    vlm_max_tokens_scene: Number(document.getElementById('cfg-scene-tokens').value),
    vlm_max_tokens_summary: Number(document.getElementById('cfg-summary-tokens').value),
    enable_apple_ocr: document.getElementById('cfg-apple-ocr').checked,
    whisper_acceleration: document.getElementById('cfg-whisper-acceleration').checked,
    enable_audio_features: document.getElementById('cfg-audio-features').checked,
    generate_summary: document.getElementById('cfg-summary').checked,
    generate_txt: document.getElementById('cfg-txt').checked,
    generate_json: document.getElementById('cfg-json').checked,
    word_timestamps: document.getElementById('cfg-word-timestamps').checked
  });
  function fillSettings(s) {
    const fields = {
      'cfg-vlm-model':'vlm_model','cfg-whisper-model':'whisper_model','cfg-vlm-resolution':'vlm_resolution',
      'cfg-scene-threshold':'scene_threshold','cfg-min-scene-duration':'min_scene_duration',
      'cfg-max-scene-duration':'max_scene_duration','cfg-keyframes':'keyframes_per_scene',
      'cfg-scene-tokens':'vlm_max_tokens_scene','cfg-summary-tokens':'vlm_max_tokens_summary'
    };
    for (const [id,key] of Object.entries(fields)) if (s[key] !== undefined) document.getElementById(id).value = s[key];
    const checks = {'cfg-apple-ocr':'enable_apple_ocr','cfg-whisper-acceleration':'whisper_acceleration',
      'cfg-audio-features':'enable_audio_features','cfg-summary':'generate_summary','cfg-txt':'generate_txt',
      'cfg-json':'generate_json','cfg-word-timestamps':'word_timestamps'};
    for (const [id,key] of Object.entries(checks)) if (s[key] !== undefined) document.getElementById(id).checked = Boolean(s[key]);
  }
  async function loadSettings() {
    settingsPill.textContent = 'Загрузка…'; settingsPill.className = 'pill'; settingsStatus.textContent = '';
    try {
      const res = await fetch('/api/v1/ui-settings', {cache:'no-store'});
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      fillSettings(body); settingsPill.textContent = 'Сохранённые настройки';
    } catch (err) { settingsPill.textContent = 'Ошибка'; settingsPill.className = 'pill bad'; settingsStatus.textContent = `Не удалось загрузить: ${err.message}`; }
  }
  saveSettingsButton.addEventListener('click', async () => {
    saveSettingsButton.disabled = true; settingsStatus.textContent = 'Сохранение…';
    try {
      const res = await fetch('/api/v1/ui-settings', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readSettings())});
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      settingsPill.textContent = 'Сохранено'; settingsPill.className = 'pill saved'; settingsStatus.textContent = 'Настройки записаны в videocontext_settings.json.';
    } catch (err) { settingsPill.textContent = 'Ошибка'; settingsPill.className = 'pill bad'; settingsStatus.textContent = `Не удалось сохранить: ${err.message}`; }
    finally { saveSettingsButton.disabled = false; }
  });
  document.getElementById('reload-settings').addEventListener('click', loadSettings);
  loadSettings();
'''

FORM_PARAMS_JS = r'''      // Per-request settings make saved values effective immediately in this Web UI.
      const cfg = readSettings();
      for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));
'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {label}; found {count}.")
    return source.replace(old, new, 1)


def patch_engine(source: str) -> str:
    if '@app.get("/api/v1/ui-settings"' not in source:
        anchor = '@app.post("/api/v1/analyze")'
        if source.count(anchor) != 1:
            raise ValueError('Could not find exactly one @app.post("/api/v1/analyze") route.')
        source = source.replace(anchor, ROUTES + anchor, 1)
    return source


def patch_ui(ui: str) -> str:
    if 'id="save-settings"' not in ui:
        anchor = '  <section class="panel">\n    <div class="topline"><div><strong>1. Видео для анализа</strong>'
        ui = replace_once(ui, anchor, SETTINGS_MARKUP + anchor.replace('1. Видео для анализа', '2. Видео для анализа'), 'video panel')
        ui = ui.replace('<strong>2. Автоматическое сохранение</strong>', '<strong>3. Автоматическое сохранение</strong>')
    if '.settings-grid{' not in ui:
        ui = replace_once(ui, '</style>', STYLE + '\n</style>', 'closing style tag')
    if 'const settingsPill = document.getElementById' not in ui:
        anchor = '  const alertEl = document.getElementById(\'alert\');\n'
        ui = replace_once(ui, anchor, anchor + JS, 'JavaScript initialization anchor')
    # Do not append duplicate fields: the UI controls below now own these request values.
    fixed_fields = (
        "      form.append('response_format', 'json');\n"
        "      form.append('generate_txt', 'true');\n"
        "      form.append('generate_json', 'true');\n"
        "      form.append('word_timestamps', 'true');\n"
    )
    if fixed_fields in ui:
        ui = ui.replace(fixed_fields, "      form.append('response_format', 'json');\n", 1)
    if 'Per-request settings make saved values effective immediately' not in ui:
        anchor = "      form.append('response_format', 'json');\n"
        ui = replace_once(ui, anchor, anchor + FORM_PARAMS_JS, 'analysis form settings anchor')
    return ui


def main() -> int:
    for path in (TARGET, CONFIG, UI_FILE):
        if not path.is_file():
            print(f"ERROR: required file missing: {path.resolve()}")
            print('Stop the server and copy this patch into ~/Downloads/VideoContext-Engine, then run it there.')
            return 2
    original_source = TARGET.read_text(encoding='utf-8')
    original_settings = CONFIG.read_text(encoding='utf-8')
    original_ui = UI_FILE.read_text(encoding='utf-8')
    try:
        source = patch_engine(original_source)
        ui = patch_ui(original_ui)
        settings_obj = json.loads(original_settings)
        if not isinstance(settings_obj, dict): raise ValueError('Settings JSON root is not an object.')
        settings_obj.setdefault('generate_json', True)
        settings_text = json.dumps(settings_obj, ensure_ascii=False, indent=2) + '\n'
        ast.parse(source, filename=str(TARGET))
        checks = {
            'GET settings route': '@app.get("/api/v1/ui-settings"' in source,
            'POST save settings route': '@app.post("/api/v1/ui-settings"' in source,
            'save button': 'id="save-settings"' in ui,
            'settings loaded from API': "fetch('/api/v1/ui-settings'" in ui,
            'settings sent to API': "fetch('/api/v1/ui-settings', {method:'POST'" in ui,
            'saved values submitted with analysis': 'Per-request settings make saved values effective immediately' in ui,
        }
        failed = [k for k,v in checks.items() if not v]
        if failed: raise ValueError('Validation failed: ' + ', '.join(failed))
    except Exception as exc:
        print(f'ERROR: {exc}')
        print('No files have been changed.')
        return 3
    if source == original_source and ui == original_ui and settings_text == original_settings:
        print('Settings UI already installed; no changes needed.')
        return 0
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    engine_backup = TARGET.with_name(f'{TARGET.name}.before_settings_button_{stamp}.bak')
    ui_backup = UI_FILE.with_name(f'{UI_FILE.name}.before_settings_button_{stamp}.bak')
    config_backup = CONFIG.with_name(f'{CONFIG.name}.before_settings_button_{stamp}.bak')
    shutil.copy2(TARGET, engine_backup); shutil.copy2(UI_FILE, ui_backup); shutil.copy2(CONFIG, config_backup)
    TARGET.write_text(source, encoding='utf-8'); UI_FILE.write_text(ui, encoding='utf-8'); CONFIG.write_text(settings_text, encoding='utf-8')
    print('Settings button installed successfully.')
    print(f'Engine backup: {engine_backup.resolve()}')
    print(f'UI backup:     {ui_backup.resolve()}')
    print(f'Settings copy: {config_backup.resolve()}')
    print('Restart the server and open http://127.0.0.1:7555/ui')
    print('The Save settings button writes to videocontext_settings.json; the Web UI sends saved values with each analysis request.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
