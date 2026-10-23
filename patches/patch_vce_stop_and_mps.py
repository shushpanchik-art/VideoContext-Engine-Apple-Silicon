#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply Whisper/MPS fix and add cooperative stop control to the VideoContext UI, including settings-panel variants.

Run from the project's venv. Creates timestamped backups, validates before writes,
and preserves the user's videocontext_settings.json and prompt settings.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
ENGINE = PROJECT / "VideoContextEngine_v3.19.py"
UI_FILE = PROJECT / "videocontext_web_ui.html"

MPS_OLD = "return dtw_cpu(x.double().cpu().numpy())"
MPS_NEW = "return dtw_cpu(x.cpu().double().numpy())"

CANCEL_ROUTE = '''@app.post("/api/v1/cancel/{job_id}", include_in_schema=False)
async def cancel_video_analysis(job_id: str):
    """Request cooperative cancellation of a running local analysis job."""
    event = CANCEL_EVENTS.setdefault(job_id, threading.Event())
    event.set()
    # Bound stale events created if a user stops during a multipart upload.
    if len(CANCEL_EVENTS) > 128:
        for old_id, old_event in list(CANCEL_EVENTS.items()):
            if old_id != job_id and old_event.is_set():
                CANCEL_EVENTS.pop(old_id, None)
                if len(CANCEL_EVENTS) <= 96:
                    break
    return {"ok": True, "message": "Остановка запрошена; движок завершит текущую вычислительную операцию и остановится на ближайшей безопасной точке."}


'''

STOP_BUTTON = '<button id="stop" type="button" hidden>Остановить распознавание</button>'


def backup(path: Path, stamp: str) -> Path:
    target = path.with_name(f"{path.name}.before_stop_mps_{stamp}.bak")
    shutil.copy2(path, target)
    return target


def patch_whisper_timing() -> tuple[Path, str | None]:
    try:
        spec = importlib.util.find_spec("whisper.timing")
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        raise RuntimeError(f"Не удалось найти whisper.timing в Python {sys.executable}: {exc}") from exc
    if not spec or not spec.origin:
        raise RuntimeError(f"whisper.timing не найден в Python {sys.executable}. Активируйте проектный .venv.")
    target = Path(spec.origin).resolve()
    original = target.read_text(encoding="utf-8")
    if MPS_NEW in original and MPS_OLD not in original:
        return target, None
    if original.count(MPS_OLD) != 1:
        raise RuntimeError(
            f"Не найден единственный ожидаемый вызов DTW в {target}. "
            f"Старых строк: {original.count(MPS_OLD)}, новых: {original.count(MPS_NEW)}. Ничего не изменено."
        )
    updated = original.replace(MPS_OLD, MPS_NEW, 1)
    ast.parse(updated, filename=str(target))
    return target, updated


def patch_engine(source: str) -> str:
    if "CANCEL_EVENTS: Dict[str, threading.Event]" not in source:
        if "import threading\n" not in source:
            if "import re\n" not in source:
                raise ValueError("Не найден import re для добавления threading.")
            source = source.replace("import re\n", "import re\nimport threading\n", 1)
        if "gpu_lock = asyncio.Lock()\n" not in source:
            raise ValueError("Не найден gpu_lock = asyncio.Lock(). Неожиданная версия движка.")
        source = source.replace(
            "gpu_lock = asyncio.Lock()\n",
            "gpu_lock = asyncio.Lock()\nCANCEL_EVENTS: Dict[str, threading.Event] = {}\n",
            1,
        )

    if "job_id: str = Form(\"\"" not in source:
        match = re.search(r"(?m)^(?P<i>[ \t]*)word_timestamps: bool = Form\(", source)
        if not match:
            raise ValueError("Не найден параметр word_timestamps. Сначала установите пакет пакетной обработки.")
        indent = match.group("i")
        source = source[:match.start()] + f'{indent}job_id: str = Form("", description="Внутренний ID задания для кнопки остановки"),\n' + source[match.start():]

    if '@app.post("/api/v1/cancel/{job_id}"' not in source:
        anchor = '@app.post("/api/v1/analyze")'
        if source.count(anchor) != 1:
            raise ValueError('Не найден единственный маршрут @app.post("/api/v1/analyze").')
        source = source.replace(anchor, CANCEL_ROUTE + anchor, 1)

    # Register the cancellation event after validating the request arguments.
    init_anchor = "    request_start_time = time.time()\n"
    if "cancel_event = CANCEL_EVENTS.setdefault(job_id, threading.Event())" not in source:
        if source.count(init_anchor) != 1:
            raise ValueError("Не удалось найти request_start_time в обработчике API.")
        init_block = (
            '    job_id = (job_id or "").strip() or f"api_{time.time_ns()}"\n'
            '    cancel_event = CANCEL_EVENTS.setdefault(job_id, threading.Event())\n'
            + init_anchor
        )
        source = source.replace(init_anchor, init_block, 1)

    # `finally` also releases the capture on cancellation between scenes.
    if "    cap = None\n" not in source:
        source = source.replace("    temp_path = None\n    source_name = \"Inconnu\"\n", "    temp_path = None\n    cap = None\n    source_name = \"Inconnu\"\n", 1)
    if "if cancel_event.is_set():\n            raise HTTPException(status_code=409" not in source:
        source = source.replace(
            "    try:\n        if video_url.strip():\n",
            '    try:\n        if cancel_event.is_set():\n            raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")\n        if video_url.strip():\n',
            1,
        )

    # Run scene detection in a worker thread so the cancellation route remains responsive.
    old_detect = '''        scenes_raw = detect_scenes(
            temp_path,
            threshold=scene_threshold,
            min_duration=min_scene_duration,
            max_duration=max_scene_duration,
        )'''
    new_detect = '''        scenes_raw = await asyncio.to_thread(
            detect_scenes,
            temp_path,
            threshold=scene_threshold,
            min_duration=min_scene_duration,
            max_duration=max_scene_duration,
        )
        if cancel_event.is_set():
            raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")'''
    if new_detect not in source:
        if old_detect not in source:
            raise ValueError("Не найден вызов detect_scenes с ожидаемыми аргументами.")
        source = source.replace(old_detect, new_detect, 1)

    # The Whisper call is offloaded to a worker so /cancel can be served while ASR runs.
    if "audio_res = await asyncio.to_thread(\n" not in source:
        pattern = re.compile(r"(?m)^(?P<i>[ \t]*)audio_res = w_model\.transcribe\(\n(?P<a>[ \t]*)temp_path,\n")
        match = pattern.search(source)
        if not match:
            raise ValueError("Не найден вызов w_model.transcribe().")
        i, a = match.group("i"), match.group("a")
        replacement = f"{i}audio_res = await asyncio.to_thread(\n{a}w_model.transcribe,\n{a}temp_path,\n"
        source = source[:match.start()] + replacement + source[match.end():]

    # Preserve MPS acceleration; only offload the blocking call, not the model/device.
    if "await asyncio.to_thread(vlm_engine.load_model, target_vlm)" not in source:
        source = source.replace("                vlm_engine.load_model(target_vlm)\n", "                await asyncio.to_thread(vlm_engine.load_model, target_vlm)\n", 1)

    # Offload OCR and VLM inference so the stop endpoint can set the cancellation event.
    ocr_old = 'extracted = apple_vision_ocr(kf.get("original_image", kf["image"]))'
    ocr_new = 'extracted = await asyncio.to_thread(apple_vision_ocr, kf.get("original_image", kf["image"]))'
    if ocr_new not in source:
        if ocr_old not in source:
            raise ValueError("Не найден вызов Apple Vision OCR для кадра исходного разрешения.")
        source = source.replace(ocr_old, ocr_new, 1)

    for func in ("raw", "raw_summary"):
        line_pattern = re.compile(rf"(?m)^(?P<i>[ \t]*){func} = vlm_engine\.describe_scene\(\n")
        if line_pattern.search(source):
            source, count = line_pattern.subn(
                lambda m, f=func: f"{m.group('i')}{f} = await asyncio.to_thread(\n{m.group('i')}    vlm_engine.describe_scene,\n",
                source,
                count=1,
            )
            if count != 1:
                raise ValueError(f"Не удалось вынести вызов VLM {func} в worker thread.")
        elif f"{func} = await asyncio.to_thread(\n" not in source:
            raise ValueError(f"Не найден вызов VLM {func} = vlm_engine.describe_scene().")

    # Add a cooperative stop check at scene boundaries and immediately after model stages.
    scene_pattern = re.compile(r"(?m)^(?P<i>[ \t]*)for idx, scene in enumerate\(scenes_raw\):\n(?![ \t]*if cancel_event\.is_set\(\):)")
    source = scene_pattern.sub(
        lambda m: m.group(0) + m.group("i") + '    if cancel_event.is_set():\n' + m.group("i") + '        raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")\n',
        source,
    )

    infer_pattern = re.compile(r"(?m)^(?P<i>[ \t]*)vlm_infer_time \+= t_vlm_end - t_vlm_start\n(?![ \t]*if cancel_event\.is_set\(\):)")
    source = infer_pattern.sub(
        lambda m: m.group(0) + m.group("i") + 'if cancel_event.is_set():\n' + m.group("i") + '    raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")\n',
        source,
    )

    # If Stop was pressed during Whisper, do not load Qwen after Whisper returns.
    marker = "                if not skip_visual or generate_summary:\n"
    if "if cancel_event.is_set():\n                    raise HTTPException(status_code=409" not in source:
        if marker not in source:
            raise ValueError("Не найден блок загрузки VLM после Whisper.")
        source = source.replace(
            marker,
            '                if cancel_event.is_set():\n                    raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")\n'
            + marker,
            1,
        )

    # Stop as soon as a per-frame OCR worker returns; use its actual indentation.
    ocr_check_pattern = re.compile(
        r'(?m)^(?P<i>[ \t]*)extracted = await asyncio\.to_thread\(apple_vision_ocr, kf\.get\("original_image", kf\["image"\]\)\)\n(?![ \t]*if cancel_event\.is_set\(\):)'
    )
    source = ocr_check_pattern.sub(
        lambda m: m.group(0) + m.group("i") + 'if cancel_event.is_set():\n'
        + m.group("i") + '    raise HTTPException(status_code=409, detail="Распознавание остановлено пользователем")\n',
        source,
        count=1,
    )

    # Ensure `job_id` and the event registry are cleaned up after success, failure, or stop.
    finally_old = '''    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)'''
    finally_new = '''    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        CANCEL_EVENTS.pop(job_id, None)
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)'''
    if finally_new not in source:
        if finally_old not in source:
            raise ValueError("Не найден блок finally для очистки временного видео.")
        source = source.replace(finally_old, finally_new, 1)

    return source


def patch_ui(ui: str) -> str:
    if 'id="stop"' not in ui:
        anchor = '      <button id="start" disabled>Запустить обработку</button>\n'
        if ui.count(anchor) != 1:
            raise ValueError("Не найдена кнопка запуска в videocontext_web_ui.html.")
        ui = ui.replace(anchor, anchor + "      " + STOP_BUTTON + "\n", 1)

    # Store current request and the control references.
    if "const stop = document.getElementById('stop');" not in ui:
        ui = ui.replace("  const start = document.getElementById('start');\n", "  const start = document.getElementById('start');\n  const stop = document.getElementById('stop');\n", 1)
    if "let stopRequested = false;" not in ui:
        ui = ui.replace("  let busy = false;\n", "  let busy = false;\n  let stopRequested = false;\n  let currentEntry = null;\n", 1)

    # The Stop button is only available while a queue is running.
    if "stop.hidden = !busy;" not in ui:
        ui = ui.replace(
            "    start.disabled = busy || !queue.some(x => x.state === 'queued');\n    choose.disabled = busy; add.disabled = busy;\n",
            "    start.disabled = busy || !queue.some(x => x.state === 'queued');\n    choose.disabled = busy; add.disabled = busy;\n    stop.hidden = !busy; stop.disabled = !busy || stopRequested;\n",
            1,
        )

    # Attach the cancellation ID after all FormData fields have been appended.
    # The settings-panel UI appends fields in a loop, while older UI builds append
    # fixed fields one at a time. Inserting immediately before xhr.open supports both.
    if "form.append('job_id', jobId);" not in ui:
        open_anchor = "      xhr.open('POST', '/api/v1/analyze', true);\n"
        if ui.count(open_anchor) != 1:
            raise ValueError("Не найден уникальный вызов xhr.open для добавления job_id.")
        ui = ui.replace(
            open_anchor,
            "      form.append('job_id', jobId);\n      entry.jobId = jobId;\n" + open_anchor,
            1,
        )
    if "entry.xhr = xhr;" not in ui:
        xhr_anchor = "      const xhr = new XMLHttpRequest();\n"
        if ui.count(xhr_anchor) != 1:
            raise ValueError("Не найден уникальный XMLHttpRequest для поддержки остановки.")
        ui = ui.replace(xhr_anchor, xhr_anchor + "      entry.xhr = xhr;\n", 1)

    # Interpret 409 as a requested stop rather than as a processing failure.
    old_error = """        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = `HTTP ${xhr.status}`;
          try { const err = JSON.parse(xhr.responseText); detail = err.detail || detail; } catch (_) {}
          setState(entry, 'error', `Ошибка: ${detail}`); resolve(); return;
        }"""
    new_error = """        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = `HTTP ${xhr.status}`;
          try { const err = JSON.parse(xhr.responseText); detail = err.detail || detail; } catch (_) {}
          if (stopRequested && (xhr.status === 409 || xhr.status === 0)) setState(entry, 'stopped', 'Остановлено пользователем');
          else setState(entry, 'error', `Ошибка: ${detail}`);
          resolve(); return;
        }"""
    if new_error not in ui:
        if old_error not in ui:
            raise ValueError("Не найден обработчик ошибок XMLHttpRequest в интерфейсе.")
        ui = ui.replace(old_error, new_error, 1)

    ui = ui.replace(
        "      xhr.onerror = () => { setState(entry, 'error', 'Ошибка сети: проверьте, что движок работает.'); resolve(); };\n      xhr.onabort = () => { setState(entry, 'error', 'Запрос прерван.'); resolve(); };",
        "      xhr.onerror = () => { if (stopRequested) setState(entry, 'stopped', 'Остановлено пользователем'); else setState(entry, 'error', 'Ошибка сети: проверьте, что движок работает.'); resolve(); };\n      xhr.onabort = () => { if (stopRequested) setState(entry, 'stopped', 'Загрузка/запрос остановлены'); else setState(entry, 'error', 'Запрос прерван.'); resolve(); };",
        1,
    )

    # Replace the queue loop so pending videos are marked stopped and not started.
    old_loop = """  start.addEventListener('click', async () => {
    if (busy) return;
    const pending = queue.filter(x => x.state === 'queued');
    if (!pending.length) return;
    busy = true; setAlert(''); refresh();
    for (let i = 0; i < pending.length; i++) {
      const item = pending[i];
      setState(item, 'processing', `Обработка ${i+1} из ${pending.length}…`);
      await processOne(item);
    }
    busy = false; refresh();
    const errors = queue.filter(x => x.state === 'error').length;
    if (errors) setAlert(`Очередь завершена. С ошибкой: ${errors}. Эти файлы можно удалить и выбрать повторно.`);
  });"""
    new_loop = """  stop.addEventListener('click', async () => {
    if (!busy || stopRequested) return;
    stopRequested = true;
    stop.disabled = true;
    setAlert('Остановка запрошена. Текущая вычислительная операция завершится, затем очередь остановится.');
    const item = currentEntry;
    if (item && item.jobId) {
      // Do not wait before aborting a file that is still uploading.
      const cancelPromise = fetch('/api/v1/cancel/' + encodeURIComponent(item.jobId), {method:'POST'}).catch(() => null);
      if (item.state === 'uploading' && item.xhr) item.xhr.abort();
      await cancelPromise;
    }
  });

  start.addEventListener('click', async () => {
    if (busy) return;
    const pending = queue.filter(x => x.state === 'queued');
    if (!pending.length) return;
    busy = true; stopRequested = false; currentEntry = null; setAlert(''); refresh();
    for (let i = 0; i < pending.length; i++) {
      const item = pending[i];
      if (stopRequested) { setState(item, 'stopped', 'Остановлено до запуска'); continue; }
      currentEntry = item;
      setState(item, 'processing', `Обработка ${i+1} из ${pending.length}…`);
      await processOne(item);
      currentEntry = null;
      if (stopRequested) {
        for (const rest of pending.slice(i + 1)) {
          if (rest.state === 'queued') setState(rest, 'stopped', 'Остановлено до запуска');
        }
        break;
      }
    }
    const wasStopped = stopRequested;
    busy = false; currentEntry = null; stopRequested = false; refresh();
    const errors = queue.filter(x => x.state === 'error').length;
    if (wasStopped) setAlert('Распознавание остановлено. Оставшиеся видео не запускались. Уже сохранённые результаты не удалены.');
    else if (errors) setAlert(`Очередь завершена. С ошибкой: ${errors}. Эти файлы можно удалить и выбрать повторно.`);
  });"""
    if new_loop not in ui:
        if old_loop not in ui:
            raise ValueError("Не найден основной обработчик очереди для добавления кнопки остановки.")
        ui = ui.replace(old_loop, new_loop, 1)

    return ui


def main() -> int:
    if not ENGINE.is_file():
        print(f"ERROR: движок не найден: {ENGINE}")
        print("Распакуйте пакет в ~/Downloads/VideoContext-Engine.")
        return 2
    if not UI_FILE.is_file():
        print(f"ERROR: интерфейс не найден: {UI_FILE}")
        print("Сначала установите VideoContext_UI_Batch_Progress_Patch.zip и Settings Button Patch.")
        return 2

    source_orig = ENGINE.read_text(encoding="utf-8")
    ui_orig = UI_FILE.read_text(encoding="utf-8")
    try:
        missing = [x for x in (
            "def load_user_settings()", "def apple_vision_ocr(",
            "word_timestamps: bool = Form(", "generate_json: bool = Form(",
            '@app.get("/api/v1/ui-settings"', '@app.get("/ui", response_class=HTMLResponse',
        ) if x not in source_orig]
        if missing:
            raise ValueError("Текущая версия движка не совпадает с ожидаемой; отсутствуют: " + ", ".join(missing))
        source_new = patch_engine(source_orig)
        ui_new = patch_ui(ui_orig)
        ast.parse(source_new, filename=str(ENGINE))
        # Validate the Python source changes and the visible UI hooks.
        for needle in (
            'CANCEL_EVENTS: Dict[str, threading.Event] = {}',
            '@app.post("/api/v1/cancel/{job_id}"',
            'await asyncio.to_thread(\n',
            'job_id: str = Form(""',
            'CANCEL_EVENTS.pop(job_id, None)',
        ):
            if needle not in source_new:
                raise ValueError("Проверка движка не прошла: отсутствует " + needle)
        for needle in ('id="stop"', "form.append('job_id', jobId);", "stop.addEventListener('click'", "/api/v1/cancel/"):
            if needle not in ui_new:
                raise ValueError("Проверка интерфейса не прошла: отсутствует " + needle)
        # Stage Whisper's MPS fix in memory as well; do not write anything until all validation passes.
        timing_path, timing_updated = patch_whisper_timing()
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("Файлы проекта не изменены.")
        return 3

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = []
    try:
        if source_new != source_orig:
            backups.append(backup(ENGINE, stamp))
        if ui_new != ui_orig:
            backups.append(backup(UI_FILE, stamp))
        if timing_updated is not None:
            backups.append(backup(timing_path, stamp))
        if source_new != source_orig:
            ENGINE.write_text(source_new, encoding="utf-8")
        if ui_new != ui_orig:
            UI_FILE.write_text(ui_new, encoding="utf-8")
        if timing_updated is not None:
            timing_path.write_text(timing_updated, encoding="utf-8")
        if timing_updated is not None and MPS_NEW not in timing_path.read_text(encoding="utf-8"):
            raise RuntimeError("Проверка записи timing.py не прошла.")
        ast.parse(ENGINE.read_text(encoding="utf-8"), filename=str(ENGINE))
    except Exception as exc:
        # Backups were already made; restore affected files if a write/validation failed.
        for path in backups:
            name = path.name
            if ".before_stop_mps_" not in name:
                continue
            original_path = path.with_name(name.split(".before_stop_mps_")[0])
            try:
                shutil.copy2(path, original_path)
            except Exception:
                pass
        print(f"ERROR during write/validation: {exc}")
        print("Резервные копии сохранены; проверьте их перед повторным запуском.")
        return 4

    print("Patch installed successfully.")
    print(f"Engine: {ENGINE}")
    print(f"UI: {UI_FILE}")
    print(f"Whisper timing.py: {timing_path}")
    if timing_updated is None:
        print("Whisper MPS word-timestamp fix: уже установлен.")
    else:
        print("Whisper MPS word-timestamp fix: исправлен x.cpu().double().numpy().")
    print("Добавлена кнопка остановки с кооперативной отменой на ближайшей безопасной точке.")
    print("Распознавание Whisper и инференс VLM/OCR вынесены в worker threads, чтобы API отмены оставался отзывчивым.")
    print("Backups:")
    for p in backups:
        print(f"  {p}")
    print("Перезапустите сервер. Откройте http://127.0.0.1:7555/ui и обновите страницу Cmd+Shift+R.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
