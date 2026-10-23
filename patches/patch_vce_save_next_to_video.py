#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Save VideoContext TXT/JSON beside each source video via native macOS file picker.

Designed for the user's locally patched VideoContext Engine v3.19 project.
All edits are staged and validated before timestamped backups/writes are made.
"""
from __future__ import annotations
import ast
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
ENGINE = PROJECT / "VideoContextEngine_v3.19.py"
UI_FILE = PROJECT / "videocontext_web_ui.html"
SETTINGS_FILE = PROJECT / "videocontext_settings.json"

SELECT_ROUTE = r'''@app.post("/api/v1/select-local-videos", include_in_schema=False)
async def select_local_videos(request: Request):
    """Use macOS's native chooser so the engine knows the real source path."""
    if platform.system() != "Darwin":
        raise HTTPException(501, "Нативный выбор локальных файлов поддерживается в этой сборке для macOS.")
    if request.client and request.client.host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "Выбор локальных файлов разрешён только с этого Mac.")
    apple_script = (
        'set selectedFiles to choose file with prompt \"Выберите одно или несколько видео для VideoContext Engine\" with multiple selections allowed\n'
        'set oldDelimiters to AppleScript\'s text item delimiters\n'
        'set AppleScript\'s text item delimiters to linefeed\n'
        'set pathList to {}\n'
        'repeat with selectedFile in selectedFiles\n'
        '    set end of pathList to POSIX path of selectedFile\n'
        'end repeat\n'
        'set resultText to pathList as text\n'
        'set AppleScript\'s text item delimiters to oldDelimiters\n'
        'return resultText'
    )
    try:
        result = await asyncio.to_thread(
            subprocess.run, ["osascript", "-e", apple_script],
            capture_output=True, text=True, check=False
        )
    except Exception as exc:
        raise HTTPException(500, f"Не удалось открыть системный выбор файлов: {exc}") from exc
    if result.returncode != 0:
        # Choosing Cancel is a normal UI action, not an error.
        if "User canceled" in (result.stderr or "") or "(-128)" in (result.stderr or ""):
            return {"files": [], "cancelled": True}
        raise HTTPException(500, f"macOS не смог открыть окно выбора файлов: {(result.stderr or '').strip()}")

    chosen_paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    files = []
    rejected = []
    for raw_path in chosen_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            rejected.append(f"{path.name}: файл недоступен")
            continue
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            rejected.append(f"{path.name}: неподдерживаемый формат")
            continue
        token = secrets.token_urlsafe(24)
        LOCAL_VIDEO_SELECTIONS[token] = str(path)
        files.append({
            "token": token,
            "name": path.name,
            "size": path.stat().st_size,
            "type": "video/" + path.suffix.lower().lstrip("."),
        })
    if len(LOCAL_VIDEO_SELECTIONS) > 256:
        for old_key in list(LOCAL_VIDEO_SELECTIONS)[:len(LOCAL_VIDEO_SELECTIONS) - 192]:
            LOCAL_VIDEO_SELECTIONS.pop(old_key, None)
    return {"files": files, "rejected": rejected, "cancelled": not bool(files)}


'''

OUTPUT_ROUTE = r'''@app.get("/api/v1/output-file/{output_id}", include_in_schema=False)
async def download_registered_output(output_id: str):
    """Download a report from the source video's folder using an opaque in-memory ID."""
    record = OUTPUT_FILE_REGISTRY.get(output_id)
    if not record:
        raise HTTPException(404, "Файл отчёта не найден; возможно, сервер был перезапущен.")
    output_path, download_name = record
    if not output_path.is_file():
        raise HTTPException(404, "Файл отчёта больше не существует на диске.")
    return FileResponse(str(output_path), filename=download_name)


'''


def replace_once(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count != 1:
        raise ValueError(f"Ожидалось одно вхождение {label}, найдено: {count}")
    return src.replace(old, new, 1)


def insert_imports(src: str) -> str:
    # Imports are added only when absent.
    if "import secrets\n" not in src or "import subprocess\n" not in src:
        if "import re\n" not in src:
            raise ValueError("Не найден import re для добавления стандартных библиотек.")
        add = "import re\n"
        if "import secrets\n" not in src:
            add += "import secrets\n"
        if "import subprocess\n" not in src:
            add += "import subprocess\n"
        src = src.replace("import re\n", add, 1)
    if "from fastapi import " in src and "Request" not in src.split("from fastapi import ", 1)[1].split("\n", 1)[0]:
        line_match = re.search(r"(?m)^from fastapi import [^\n]+$", src)
        if not line_match:
            raise ValueError("Не найдён импорт FastAPI для добавления Request.")
        old_line = line_match.group(0)
        new_line = old_line + ", Request"
        src = src[:line_match.start()] + new_line + src[line_match.end():]
    return src


def patch_engine(src: str) -> str:
    src = insert_imports(src)
    # Runtime registries, separate from persisted settings.
    if "LOCAL_VIDEO_SELECTIONS: Dict[str, str] = {}" not in src:
        anchor = "CANCEL_EVENTS: Dict[str, threading.Event] = {}\n"
        if anchor not in src:
            raise ValueError("Не найден реестр заданий из патча остановки.")
        src = src.replace(anchor, anchor +
            "LOCAL_VIDEO_SELECTIONS: Dict[str, str] = {}\n"
            "OUTPUT_FILE_REGISTRY: Dict[str, tuple[Path, str]] = {}\n", 1)

    # The native file picker is placed adjacent to the existing UI route.
    if '@app.post("/api/v1/select-local-videos"' not in src:
        anchor = '@app.get("/ui", response_class=HTMLResponse'
        if src.count(anchor) != 1:
            raise ValueError("Не найден маршрут /ui для установки системного выбора видео.")
        src = src.replace(anchor, SELECT_ROUTE + anchor, 1)

    if '@app.get("/api/v1/output-file/{output_id}"' not in src:
        anchor = '@app.get("/api/v1/output/{filename}"'
        if src.count(anchor) != 1:
            raise ValueError("Не найден существующий маршрут скачивания отчётов.")
        src = src.replace(anchor, OUTPUT_ROUTE + anchor, 1)

    # Add opaque token for a file chosen through native macOS dialog.
    if "local_video_token: str = Form(" not in src:
        anchor = '    video_url: str = Form(\n'
        src = replace_once(src, anchor,
            '    local_video_token: str = Form("", description="Внутренний токен видео, выбранного в системном диалоге macOS"),\n'
            + anchor, "video_url Form parameter")

    src = src.replace(
        'if not video_file and not video_url.strip():',
        'if not video_file and not video_url.strip() and not local_video_token.strip():',
        1,
    )

    # Keep original input videos safe: only engine-created temporary files may be deleted.
    init_old = '    temp_path = None\n    cap = None\n    source_name = "Inconnu"\n'
    init_new = '    temp_path = None\n    cap = None\n    owns_temp_path = False\n    output_directory = Path.cwd()\n    source_name = "Inconnu"\n'
    if init_new not in src:
        src = replace_once(src, init_old, init_new, "temporary path initialisation")

    # Insert path-token handling before URL/upload handling. Path stays in its original folder.
    branch_anchor = '        if video_url.strip():\n'
    local_branch = '''        if local_video_token.strip():
            selected_path = LOCAL_VIDEO_SELECTIONS.pop(local_video_token.strip(), None)
            if not selected_path:
                raise HTTPException(400, "Токен локального видео недействителен или уже использован. Выберите видео повторно.")
            original_path = Path(selected_path).expanduser().resolve()
            if not original_path.is_file():
                raise HTTPException(404, f"Исходный видеофайл не найден: {original_path.name}")
            if original_path.suffix.lower() not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"Неподдерживаемое расширение: {original_path.suffix}")
            source_name = original_path.name
            temp_path = str(original_path)
            output_directory = original_path.parent
        elif video_url.strip():
            owns_temp_path = True
'''
    if 'if local_video_token.strip():\n            selected_path = LOCAL_VIDEO_SELECTIONS.pop' not in src:
        src = replace_once(src, branch_anchor, local_branch, "video URL branch")

    # Uploaded videos and downloaded URLs are temporary; do not remove selected originals.
    upload_anchor = '        elif video_file:\n            ext = Path(video_file.filename).suffix.lower()\n'
    if upload_anchor in src and '        elif video_file:\n            owns_temp_path = True\n            ext = Path(video_file.filename).suffix.lower()\n' not in src:
        src = replace_once(src, upload_anchor,
            '        elif video_file:\n            owns_temp_path = True\n            ext = Path(video_file.filename).suffix.lower()\n',
            "uploaded video branch")

    # Write TXT alongside the video for native-selected items. Upload/URL fallback remains in project dir.
    if 'txt_path = str((output_directory / txt_output_name).resolve())' not in src:
        src = replace_once(src, '            txt_path = os.path.abspath(txt_output_name)\n',
            '            txt_path = str((output_directory / txt_output_name).resolve())\n', "TXT output path")
    if 'txt_download_id = None' not in src:
        src = replace_once(src, '        txt_output_name = None\n',
            '        txt_output_name = None\n        txt_download_id = None\n', "TXT output name initialisation")
    txt_open_anchor = '            with open(txt_path, "w", encoding="utf-8") as f:\n                f.write(text_report_content)\n'
    if 'OUTPUT_FILE_REGISTRY[txt_download_id]' not in src:
        src = replace_once(src, txt_open_anchor,
            txt_open_anchor +
            '            txt_download_id = secrets.token_urlsafe(24)\n'
            '            OUTPUT_FILE_REGISTRY[txt_download_id] = (Path(txt_path), txt_output_name)\n',
            "TXT output write")

    # JSON payload is assembled before autosaving; add the download IDs before writing so JSON is self-describing.
    json_path_old = '            json_path = os.path.abspath(json_output_name)\n'
    json_path_new = '            json_path = str((output_directory / json_output_name).resolve())\n'
    if json_path_new not in src:
        src = replace_once(src, json_path_old, json_path_new, "JSON output path")
    json_anchor = '        json_payload["json_filename"] = None\n'
    if 'json_payload["output_directory"]' not in src:
        src = replace_once(src, json_anchor,
            json_anchor +
            '        json_payload["txt_download_id"] = txt_download_id\n'
            '        json_payload["json_download_id"] = None\n'
            '        json_payload["output_directory"] = str(output_directory.resolve())\n',
            "JSON payload filename metadata")
    json_write_anchor = '            json_payload["json_filename"] = json_output_name\n'
    if 'OUTPUT_FILE_REGISTRY[json_download_id]' not in src:
        src = replace_once(src, json_write_anchor,
            json_write_anchor +
            '            json_download_id = secrets.token_urlsafe(24)\n'
            '            json_payload["json_download_id"] = json_download_id\n',
            "JSON filename metadata")
        # Register only after a successful write. Match the final write line without assuming
        # how many escape characters the source uses for the newline literal.
        writer_pattern = re.compile(
            r'(?m)^(?P<i>[ \t]*)with open\(json_path, "w", encoding="utf-8"\) as json_file:\n'
            r'(?P=i)    json\.dump\(json_payload, json_file, ensure_ascii=False, indent=2\)\n'
            r'(?P=i)    json_file\.write\([^\n]+\)\n'
        )
        match = writer_pattern.search(src)
        if not match:
            raise ValueError("Не удалось найти блок записи JSON для регистрации ссылки на сохранённый файл.")
        writer_block = match.group(0)
        src = src[:match.end()] + '            OUTPUT_FILE_REGISTRY[json_download_id] = (Path(json_path), json_output_name)\n' + src[match.end():]

    # Ensure temporary cleanup can never unlink the original selected video.
    cleanup_old = '        if temp_path and os.path.exists(temp_path):\n            os.remove(temp_path)\n'
    cleanup_new = '        if owns_temp_path and temp_path and os.path.exists(temp_path):\n            os.remove(temp_path)\n'
    if cleanup_new not in src:
        src = replace_once(src, cleanup_old, cleanup_new, "temporary-file cleanup")

    return src


def patch_ui(ui: str) -> str:
    if 'async function pickLocalVideos()' not in ui:
        choose_anchor = "  choose.addEventListener('click', () => picker.click());\n  add.addEventListener('click', () => picker.click());\n  picker.addEventListener('change', e => addFiles(e.target.files));"
        choose_replacement = r'''  async function pickLocalVideos() {
    if (busy) return;
    setAlert('');
    choose.disabled = true; add.disabled = true;
    try {
      const response = await fetch('/api/v1/select-local-videos', {method:'POST'});
      let body = {};
      try { body = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      if (body.rejected && body.rejected.length) setAlert(body.rejected.join(' · '));
      for (const selected of (body.files || [])) {
        const entry = {
          id: String(nextId++),
          localToken: selected.token,
          file: {name:selected.name, size:selected.size, type:selected.type || 'video'},
          state:'queued', node:null
        };
        queue.push(entry); makeRow(entry); setState(entry, 'queued', 'В очереди · исходный файл будет обработан на месте');
      }
      refresh();
    } catch (error) {
      setAlert(`Не удалось выбрать локальные видео: ${error.message}. Откройте интерфейс с этого Mac и проверьте, что сервер запущен через приложение VideoContext Engine.`);
    } finally { refresh(); }
  }
  choose.addEventListener('click', pickLocalVideos);
  add.addEventListener('click', pickLocalVideos);
  picker.addEventListener('change', e => addFiles(e.target.files));'''
        if choose_anchor not in ui:
            raise ValueError("Не найден обработчик кнопок выбора видео в HTML-интерфейсе.")
        ui = ui.replace(choose_anchor, choose_replacement, 1)

    # Browser-upload fallback remains available for compatibility, but local native selection is primary.
    if "if (entry.localToken) form.append('local_video_token', entry.localToken);" not in ui:
        ui = replace_once(ui,
            "      form.append('video_file', entry.file, entry.file.name);\n",
            "      if (entry.localToken) form.append('local_video_token', entry.localToken);\n      else form.append('video_file', entry.file, entry.file.name);\n",
            "video form field")

    # Make progress display truthful for native paths: no video is uploaded over HTTP.
    if "if (!entry.localToken) {\n        setState(entry, 'uploading'" not in ui:
        ui = replace_once(ui,
            "      setState(entry, 'uploading', 'Загрузка файла…');\n      entry.caption.textContent = 'Загрузка: 0%';\n",
            "      if (!entry.localToken) {\n        setState(entry, 'uploading', 'Загрузка файла…');\n        entry.caption.textContent = 'Загрузка: 0%';\n      } else {\n        setState(entry, 'processing', 'Анализ локального видео…');\n        entry.caption.textContent = 'Файл читается с диска; отчёты сохранятся рядом с видео.';\n      }\n",
            "upload progress initial state")

    old_upload_progress = '''      xhr.upload.onprogress = e => {
        if (!e.lengthComputable) return;
        const pct = Math.max(0, Math.min(100, Math.round(100*e.loaded/e.total)));
        entry.fill.className = 'progress-fill'; entry.fill.style.width = `${pct}%`;
        entry.statusEl.textContent = `Загрузка · ${pct}%`;
        entry.caption.textContent = `Передано ${pct}% файла`;
        if (pct >= 100) setState(entry, 'processing', 'Анализ Whisper + Apple OCR + Qwen3-VL…');
      };'''
    new_upload_progress = '''      if (!entry.localToken) {
        xhr.upload.onprogress = e => {
          if (!e.lengthComputable) return;
          const pct = Math.max(0, Math.min(100, Math.round(100*e.loaded/e.total)));
          entry.fill.className = 'progress-fill'; entry.fill.style.width = `${pct}%`;
          entry.statusEl.textContent = `Загрузка · ${pct}%`;
          entry.caption.textContent = `Передано ${pct}% файла`;
          if (pct >= 100) setState(entry, 'processing', 'Анализ Whisper + Apple OCR + Qwen3-VL…');
        };
      }'''
    if new_upload_progress not in ui:
        ui = replace_once(ui, old_upload_progress, new_upload_progress, "XHR upload progress handler")
    ui = ui.replace(
        "      xhr.upload.onload = () => setState(entry, 'processing', 'Анализ Whisper + Apple OCR + Qwen3-VL…');\n",
        "      if (!entry.localToken) xhr.upload.onload = () => setState(entry, 'processing', 'Анализ Whisper + Apple OCR + Qwen3-VL…');\n",
        1,
    )

    ui = ui.replace(
        "        setState(entry, 'done', 'Готово · TXT и JSON сохранены');\n",
        "        setState(entry, 'done', data.output_directory ? `Готово · TXT/JSON сохранены в ${data.output_directory}` : 'Готово · TXT и JSON сохранены');\n",
        1,
    )
    old_links = '''        const links = [];
        if (data.txt_filename) links.push(['Скачать TXT', data.txt_filename]);
        if (data.json_filename) links.push(['Скачать JSON', data.json_filename]);
        for (const [label, filename] of links) {
          const a = document.createElement('a');
          a.textContent = label; a.href = '/api/v1/output/' + encodeURIComponent(filename); a.download = filename;
          entry.links.appendChild(a);
        }'''
    new_links = '''        const links = [];
        if (data.txt_filename) links.push(['Скачать TXT', data.txt_filename, data.txt_download_id ? '/api/v1/output-file/' + encodeURIComponent(data.txt_download_id) : '/api/v1/output/' + encodeURIComponent(data.txt_filename)]);
        if (data.json_filename) links.push(['Скачать JSON', data.json_filename, data.json_download_id ? '/api/v1/output-file/' + encodeURIComponent(data.json_download_id) : '/api/v1/output/' + encodeURIComponent(data.json_filename)]);
        for (const [label, filename, href] of links) {
          const a = document.createElement('a');
          a.textContent = label; a.href = href; a.download = filename;
          entry.links.appendChild(a);
        }'''
    if new_links not in ui:
        ui = replace_once(ui, old_links, new_links, "result download links")

    ui = ui.replace(
        "Для каждого успешного видео движок сохраняет <b>TXT и JSON</b> в рабочую папку VideoContext-Engine. Здесь будут доступны ссылки для скачивания готовых файлов.",
        "Для каждого выбранного через системное окно видео движок автоматически сохраняет <b>TXT и JSON</b> в ту же папку, где находится исходный ролик. Здесь доступны ссылки для скачивания отчётов.",
        1,
    )
    return ui


def main() -> int:
    for path in (ENGINE, UI_FILE, SETTINGS_FILE):
        if not path.is_file():
            print(f"ERROR: необходимый файл не найден: {path}")
            print("Распакуйте пакет в ~/Downloads/VideoContext-Engine.")
            return 2
    src_orig = ENGINE.read_text(encoding="utf-8")
    ui_orig = UI_FILE.read_text(encoding="utf-8")
    settings_orig = SETTINGS_FILE.read_text(encoding="utf-8")
    try:
        required = [
            "async def analyze_video(",
            "generate_txt: bool = Form(",
            "generate_json: bool = Form(",
            "CANCEL_EVENTS: Dict[str, threading.Event] = {}",
            '@app.get("/ui", response_class=HTMLResponse',
            '@app.get("/api/v1/output/{filename}"',
            "id=\"save-settings\"",
            "form.append('video_file', entry.file, entry.file.name);",
        ]
        missing = [needle for needle in required if needle not in src_orig + "\n" + ui_orig]
        if missing:
            raise ValueError("Текущая версия не совпадает с ожидаемой. Не найдены элементы: " + ", ".join(missing))
        src = patch_engine(src_orig)
        ui = patch_ui(ui_orig)
        settings = json.loads(settings_orig)
        if not isinstance(settings, dict):
            raise ValueError("videocontext_settings.json должен содержать JSON-объект.")
        ast.parse(src, filename=str(ENGINE))
        for needle in (
            '@app.post("/api/v1/select-local-videos"',
            "local_video_token: str = Form(",
            'output_directory = original_path.parent',
            'txt_path = str((output_directory / txt_output_name).resolve())',
            'json_path = str((output_directory / json_output_name).resolve())',
            'if owns_temp_path and temp_path and os.path.exists(temp_path):',
            '@app.get("/api/v1/output-file/{output_id}"',
            'OUTPUT_FILE_REGISTRY[json_download_id]',
        ):
            if needle not in src:
                raise ValueError("Проверка движка не пройдена: отсутствует " + needle)
        for needle in (
            "async function pickLocalVideos()",
            "form.append('local_video_token', entry.localToken)",
            "data.output_directory",
            "/api/v1/output-file/",
        ):
            if needle not in ui:
                raise ValueError("Проверка интерфейса не пройдена: отсутствует " + needle)
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("Файлы проекта не изменены.")
        return 3

    if src == src_orig and ui == ui_orig:
        print("Сохранение рядом с исходным видео уже установлено; изменений нет.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    engine_backup = ENGINE.with_name(f"{ENGINE.name}.before_save_beside_video_{stamp}.bak")
    ui_backup = UI_FILE.with_name(f"{UI_FILE.name}.before_save_beside_video_{stamp}.bak")
    shutil.copy2(ENGINE, engine_backup)
    shutil.copy2(UI_FILE, ui_backup)
    try:
        ENGINE.write_text(src, encoding="utf-8")
        UI_FILE.write_text(ui, encoding="utf-8")
        ast.parse(ENGINE.read_text(encoding="utf-8"), filename=str(ENGINE))
    except Exception as exc:
        shutil.copy2(engine_backup, ENGINE)
        shutil.copy2(ui_backup, UI_FILE)
        print(f"ERROR: запись/проверка не удалась; исходные файлы восстановлены: {exc}")
        return 4

    print("Patch installed successfully.")
    print(f"Engine backup: {engine_backup}")
    print(f"UI backup: {ui_backup}")
    print("Выбор видео теперь использует системное окно macOS и сохраняет исходный путь.")
    print("TXT и JSON сохраняются в ту же папку, где лежит соответствующий исходный ролик.")
    print("Остановка сервера не требуется до завершения установки; затем перезапустите сервер.")
    print("Откройте http://127.0.0.1:7555/ui и обновите страницу через Cmd+Shift+R.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
