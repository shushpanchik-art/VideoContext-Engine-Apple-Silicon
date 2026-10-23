#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install VideoContext Engine UI with per-file status bars, aligned Whisper audio,
and automatic JSON + TXT output. Intended for the locally patched v3.19 project.

Run from ~/Downloads/VideoContext-Engine after extracting the ZIP there.
A timestamped backup is made before changing the engine and settings files.
The UI is served at http://127.0.0.1:7555/ui; Swagger remains at /docs.
"""
from __future__ import annotations
import ast
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

TARGET = Path("VideoContextEngine_v3.19.py")
CONFIG = Path("videocontext_settings.json")
UI_FILE = Path("videocontext_web_ui.html")

AUDIO_SECTION = r'''# --- AUDIO FEATURES ---


def _word_records(whisper_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten Whisper's word-level timestamps into stable records."""
    records: List[Dict[str, Any]] = []
    for segment in whisper_segments or []:
        words = segment.get("words") or []
        if not isinstance(words, list):
            continue
        for word in words:
            if not isinstance(word, dict):
                continue
            try:
                word_start = float(word.get("start"))
                word_end = float(word.get("end"))
            except (TypeError, ValueError):
                continue
            text = str(word.get("word") or word.get("text") or "")
            if word_end <= word_start or not text.strip():
                continue
            records.append({"start": word_start, "end": word_end, "text": text})
    records.sort(key=lambda item: (item["start"], item["end"]))
    return records


def _inside_scene(midpoint: float, start: float, end: float) -> bool:
    # Assign a word/segment to one and only one scene, even at scene boundaries.
    return start <= midpoint < end or (midpoint == end and start == end)


def _join_timed_text(items: List[tuple]) -> str:
    """Join Whisper's leading-space word tokens without adding spaces before punctuation."""
    output = ""
    for _time, fragment, is_word in sorted(items, key=lambda item: item[0]):
        fragment = str(fragment or "")
        if not fragment:
            continue
        if not is_word:
            fragment = fragment.strip()
            if not fragment:
                continue
            if output and not output[-1].isspace() and fragment[0] not in ",.!?;:%)]}»”’":
                output += " "
        elif output and not fragment[0].isspace() and not output[-1].isspace() and fragment[0] not in ",.!?;:%)]}»”’":
            output += " "
        output += fragment
    return output.strip()


def transcript_for_scene(
    scene: Dict[str, float],
    whisper_segments: List[Dict[str, Any]],
) -> str:
    """Build a scene transcript from word timestamps; use segment midpoints as fallback."""
    start = float(scene["start"])
    end = float(scene["end"])
    parts: List[tuple] = []
    for segment in whisper_segments or []:
        words = segment.get("words") or []
        if isinstance(words, list) and words:
            for word in words:
                if not isinstance(word, dict):
                    continue
                try:
                    word_start = float(word.get("start"))
                    word_end = float(word.get("end"))
                except (TypeError, ValueError):
                    continue
                text = str(word.get("word") or word.get("text") or "")
                if word_end <= word_start or not text.strip():
                    continue
                midpoint = (word_start + word_end) / 2.0
                if _inside_scene(midpoint, start, end):
                    parts.append((midpoint, text, True))
        else:
            # Older/fallback ASR segments have no word timestamps. Assign the entire
            # segment to the scene containing its midpoint so text is never duplicated.
            try:
                seg_start = float(segment["start"])
                seg_end = float(segment["end"])
            except (KeyError, TypeError, ValueError):
                continue
            midpoint = (seg_start + seg_end) / 2.0
            text = str(segment.get("text") or "").strip()
            if text and _inside_scene(midpoint, start, end):
                parts.append((midpoint, text, False))
    return _join_timed_text(parts)


def _union_duration(intervals: List[tuple]) -> float:
    """Duration of the union of time intervals; overlapping words are not double-counted."""
    if not intervals:
        return 0.0
    intervals = sorted((float(a), float(b)) for a, b in intervals if b > a)
    if not intervals:
        return 0.0
    total = 0.0
    current_start, current_end = intervals[0]
    for interval_start, interval_end in intervals[1:]:
        if interval_start <= current_end:
            current_end = max(current_end, interval_end)
        else:
            total += current_end - current_start
            current_start, current_end = interval_start, interval_end
    total += current_end - current_start
    return max(0.0, total)


def compute_audio_features_for_scene(
    scene: Dict[str, float],
    whisper_segments: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Compute scene-local speech features from word timestamps when available."""
    start = float(scene["start"])
    end = float(scene["end"])
    duration = max(0.001, end - start)
    intervals: List[tuple] = []
    word_count = 0

    for segment in whisper_segments or []:
        words = segment.get("words") or []
        if isinstance(words, list) and words:
            for word in words:
                if not isinstance(word, dict):
                    continue
                try:
                    word_start = float(word.get("start"))
                    word_end = float(word.get("end"))
                except (TypeError, ValueError):
                    continue
                text = str(word.get("word") or word.get("text") or "")
                if word_end <= word_start or not text.strip():
                    continue
                midpoint = (word_start + word_end) / 2.0
                if _inside_scene(midpoint, start, end):
                    clipped_start = max(start, word_start)
                    clipped_end = min(end, word_end)
                    if clipped_end > clipped_start:
                        intervals.append((clipped_start, clipped_end))
                    # Whisper normally provides one lexical token per entry.
                    if re.search(r"\w", text, flags=re.UNICODE):
                        word_count += 1
        else:
            # No word alignment for this segment: use its midpoint, not overlap,
            # for both word count and ownership. This prevents duplicated counts.
            try:
                seg_start = float(segment["start"])
                seg_end = float(segment["end"])
            except (KeyError, TypeError, ValueError):
                continue
            midpoint = (seg_start + seg_end) / 2.0
            if _inside_scene(midpoint, start, end):
                clipped_start = max(start, seg_start)
                clipped_end = min(end, seg_end)
                if clipped_end > clipped_start:
                    intervals.append((clipped_start, clipped_end))
                word_count += len(str(segment.get("text") or "").strip().split())

    # An empty/no-speech scene is intentionally represented by zero speech features.
    speech_duration = min(duration, _union_duration(intervals))
    speech_ratio = min(1.0, speech_duration / duration)
    silence_ratio = max(0.0, 1.0 - speech_ratio)
    speaking_rate_wpm = (word_count / speech_duration * 60.0) if speech_duration > 0 else 0.0

    return {
        "speech_duration": round(speech_duration, 3),
        "speaking_rate_wpm": round(speaking_rate_wpm, 2),
        "speech_ratio": round(speech_ratio, 3),
        "silence_ratio": round(silence_ratio, 3),
    }


'''

def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {label}; found {count}.")
    return source.replace(old, new, 1)


def patch_audio_helpers(source: str) -> str:
    if "def transcript_for_scene(" in source and "def _word_records(" in source:
        return source
    pattern = re.compile(
        r"(?ms)^# --- AUDIO FEATURES ---\n.*?(?=^def analyze_emotion_from_frame\()"
    )
    source, count = pattern.subn(lambda _match: AUDIO_SECTION, source, count=1)
    if count != 1:
        raise ValueError("Could not replace the AUDIO FEATURES section before analyze_emotion_from_frame().")
    return source


def patch_word_timestamps(source: str) -> str:
    if "word_timestamps=word_timestamps" in source:
        return source
    # Add an API switch; keep it next to other audio controls and backed by settings.
    anchor = '    generate_txt: bool = Form(bool(setting("generate_txt", True))),\n'
    replacement = (
        anchor
        + '    generate_json: bool = Form(bool(setting("generate_json", True))),\n'
        + '    word_timestamps: bool = Form(\n'
        + '        bool(setting("word_timestamps", True)),\n'
        + '        description="Точные временные метки отдельных слов для распределения речи по сценам",\n'
        + '    ),\n'
    )
    if "word_timestamps: bool = Form(" not in source:
        source = replace_once(source, anchor, replacement, "generate_txt API parameter")
    elif "generate_json: bool = Form(" not in source:
        raise ValueError("word_timestamps exists but generate_json does not; inspect the API parameters before retrying.")

    if "word_timestamps=word_timestamps" not in source:
        transcribe_pattern = re.compile(
            r"(?m)^(?P<i>[ \t]*)audio_res = w_model\.transcribe\(\n"
            r"(?P<arg_i>[ \t]*)temp_path,\n"
        )
        def add_word_timestamp_argument(match: re.Match) -> str:
            return match.group(0) + match.group("arg_i") + "word_timestamps=word_timestamps,\n"
        source, count = transcribe_pattern.subn(add_word_timestamp_argument, source, count=1)
        if count != 1:
            raise ValueError("Could not locate the Whisper transcribe() call to enable word timestamps.")
    return source


def patch_scene_transcript(source: str) -> str:
    if "audio_text = transcript_for_scene(scene, whisper_segments)" in source:
        return source
    pattern = re.compile(
        r'(?ms)^(?P<i>[ \t]*)audio_parts = \[\]\n.*?^(?P=i)audio_text = " ".join\(audio_parts\)\.strip\(\)'
    )
    replacement = (
        r'\g<i>audio_text = transcript_for_scene(scene, whisper_segments) '
        r'if not skip_audio and whisper_segments else ""'
    )
    source, count = pattern.subn(replacement, source, count=1)
    if count != 1:
        raise ValueError("Could not find the per-scene audio_parts/audio_text block to replace.")
    # Feed synchronized speech into Qwen as context, alongside image and Apple OCR.
    if "СИНХРОНИЗИРОВАННАЯ РАСШИФРОВКА WHISPER" not in source:
        prompt_pattern = re.compile(
            r'(?m)^(?P<i>[ \t]*)t_vlm_start = time\.time\(\)\n'
            r'(?P=i)raw = vlm_engine\.describe_scene\(\n'
            r'(?P=i)    images,\n'
        )
        def add_audio_context(match: re.Match) -> str:
            indent = match.group("i")
            return (
                f"{indent}if audio_text.strip():\n"
                f"{indent}    full_visual_prompt += (\n"
                f'{indent}        "\\n\\nСИНХРОНИЗИРОВАННАЯ РАСШИФРОВКА WHISPER (контекст речи, не визуальное доказательство):\\n"\n'
                f"{indent}        + audio_text\n"
                f'{indent}        + "\\n\\nИспользуй речь для контекста темы, но описывай как визуальный факт только то, что подтверждается кадрами и OCR."\n'
                f"{indent}    )\n"
                f"{indent}t_vlm_start = time.time()\n"
                f"{indent}raw = vlm_engine.describe_scene(\n"
                f"{indent}    images,\n"
            )
        source, count = prompt_pattern.subn(add_audio_context, source, count=1)
        if count != 1:
            raise ValueError("Could not locate the per-scene Qwen call to add aligned Whisper context.")
    return source



def patch_json_autosave(source: str) -> str:
    # If this feature was already applied, leave the source untouched.
    if 'json_output_name = f"{sanitize_filename(source_name)}_context.json"' in source:
        return source

    if "generate_json: bool = Form(" not in source:
        raise ValueError("Cannot add JSON autosave before the generate_json form field is installed.")

    branch_start = source.find('        if response_format == "text":')
    if branch_start < 0:
        raise ValueError("Could not find response_format text/json return branch.")
    else_start = source.find('        else:\n            return {', branch_start)
    if else_start < 0:
        raise ValueError("Could not find the JSON return dictionary in the response branch.")
    try_end = source.find('\n    except HTTPException as he:', else_start)
    if try_end < 0:
        raise ValueError("Could not find the end of the response branch before HTTPException handling.")

    return_prefix = '        else:\n            return '
    expression_start = else_start + len(return_prefix)
    if not source.startswith("{", expression_start):
        raise ValueError("Unexpected JSON response syntax; expected a dictionary literal.")

    # Find the matching outer closing brace, respecting quoted strings in the literal.
    stack = []
    quote = None
    triple = False
    escaped = False
    expression_end = None
    for pos in range(expression_start, try_end):
        ch = source[pos]
        if quote:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if triple:
                if source.startswith(quote * 3, pos):
                    quote = None
                    triple = False
                continue
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            triple = source.startswith(ch * 3, pos)
            if triple:
                # Subsequent characters are handled by quote state.
                continue
        elif ch == "{":
            stack.append(ch)
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack:
                    expression_end = pos + 1
                    break
    if expression_end is None:
        raise ValueError("Could not find the end of the JSON response dictionary.")

    payload_expression = source[expression_start:expression_end]
    text_branch = source[branch_start:else_start].rstrip()
    replacement = (
        "        json_payload = " + payload_expression + "\n"
        "        json_payload[\"json_filename\"] = None\n"
        "        if generate_json:\n"
        "            json_output_name = f\"{sanitize_filename(source_name)}_context.json\"\n"
        "            json_payload[\"json_filename\"] = json_output_name\n"
        "            json_path = os.path.abspath(json_output_name)\n"
        "            with open(json_path, \"w\", encoding=\"utf-8\") as json_file:\n"
        "                json.dump(json_payload, json_file, ensure_ascii=False, indent=2)\n"
        "                json_file.write(\"\\n\")\n\n"
        + text_branch + "\n"
        "        return json_payload\n"
    )
    return source[:branch_start] + replacement + source[try_end:]


def patch_output_params(source: str) -> str:
    if '"word_timestamps": word_timestamps' in source and '"generate_json": generate_json' in source:
        return source
    anchor = (
        '                        "enable_audio_features": enable_audio_features,\n'
    )
    if anchor not in source:
        # Avoid modifying unrelated code; if fields are already present, this is idempotent.
        if '"word_timestamps": word_timestamps' in source:
            return source
        raise ValueError("Could not locate response metadata params to report word_timestamps/generate_json.")
    source = replace_once(
        source,
        anchor,
        anchor +
        '                        "word_timestamps": word_timestamps,\n'
        '                        "generate_json": generate_json,\n',
        "analysis parameters in JSON metadata",
    )
    return source




UI_HTML = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VideoContext Engine — обработка видео</title>
<style>
:root{color-scheme:light dark;--bg:#10151d;--panel:#191f2a;--line:#303949;--text:#eef3fa;--muted:#aab6c7;--accent:#6ea8fe;--good:#39c58a;--bad:#ff7272}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 18px 54px}h1{font-size:clamp(25px,4vw,36px);margin:0 0 8px}p{color:var(--muted)}.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px;margin-top:18px}.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}button,.button{border:1px solid var(--line);border-radius:10px;background:#252e3d;color:var(--text);padding:10px 14px;font:inherit;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:7px}button.primary{background:#245eb7;border-color:#397bdc}button:disabled{opacity:.45;cursor:not-allowed}.hint{font-size:13px;color:var(--muted)}#empty{border:1px dashed #4b586c;border-radius:14px;padding:28px;text-align:center;color:var(--muted);margin-top:14px}.file-card{border:1px solid var(--line);border-radius:14px;padding:14px;margin-top:12px}.file-top{display:flex;align-items:flex-start;gap:12px;justify-content:space-between}.file-name{font-weight:650;overflow-wrap:anywhere}.file-meta,.status,.progress-caption{font-size:13px;color:var(--muted)}.status{margin-top:8px}.status.good{color:var(--good)}.status.bad{color:var(--bad)}.progress-track{height:10px;background:#303848;border-radius:99px;overflow:hidden;margin-top:9px}.progress-fill{height:100%;width:0;background:var(--accent);border-radius:99px;transition:width .18s linear}.progress-fill.working{width:38%;background:linear-gradient(90deg,#3e79d5,#a9cdfd,#3e79d5);background-size:200% 100%;animation:scan 1.2s linear infinite}.progress-fill.finished{width:100%;background:var(--good)}.progress-fill.failed{width:100%;background:var(--bad)}@keyframes scan{from{background-position:100% 0}to{background-position:-100% 0}}.file-links{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}.file-links a{color:#9dc4ff;text-decoration:none;border-bottom:1px solid #597eaf}.remove{padding:5px 9px;font-size:13px}.topline{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px}.pill{font-size:12px;color:var(--muted);border:1px solid var(--line);padding:4px 8px;border-radius:99px}.alert{display:none;border:1px solid #814747;color:#ffbdbd;border-radius:10px;padding:10px;margin-top:12px}.footer{margin-top:24px;color:var(--muted);font-size:13px}.footer a{color:#9dc4ff}
</style>
</head>
<body>
<main>
  <div class="topline"><span class="pill">локальная обработка · Apple Silicon / MLX</span><a class="hint" href="/docs">API и настройки (Swagger) →</a></div>
  <h1>VideoContext Engine</h1>
  <p>Выберите видео, добавляйте следующие кнопкой ниже и запускайте пакетную обработку. Файлы анализируются последовательно, у каждого — собственный индикатор загрузки и состояния.</p>
  <section class="panel">
    <div class="topline"><div><strong>1. Видео для анализа</strong><div class="hint">MP4, MOV, AVI, MKV, WEBM и M4V</div></div><span class="pill" id="count">0 видео</span></div>
    <input id="picker" type="file" accept="video/*,.mp4,.mov,.avi,.mkv,.webm,.m4v" multiple hidden>
    <div class="actions">
      <button id="choose" class="primary">Выбрать первое видео</button>
      <button id="add" hidden>＋ Добавить ещё видео</button>
      <button id="start" disabled>Запустить обработку</button>
    </div>
    <div id="empty">Сначала выберите видео. После выбора появится кнопка «Добавить ещё видео».</div>
    <div id="files"></div>
    <div id="alert" class="alert"></div>
  </section>
  <section class="panel">
    <strong>2. Автоматическое сохранение</strong>
    <p style="margin-bottom:0">Для каждого успешного видео движок сохраняет <b>TXT и JSON</b> в рабочую папку VideoContext-Engine. Здесь будут доступны ссылки для скачивания готовых файлов. Whisper, Apple OCR и Qwen используют текущие настройки из <code>videocontext_settings.json</code>.</p>
  </section>
  <div class="footer">Индикатор загрузки показывает реальный процент передачи файла. Во время анализа отображается анимированный статус; он завершается на 100% только после получения результата. Очередь работает последовательно, чтобы не перегружать память и MLX.</div>
</main>
<script>
(() => {
  const picker = document.getElementById('picker');
  const choose = document.getElementById('choose');
  const add = document.getElementById('add');
  const start = document.getElementById('start');
  const empty = document.getElementById('empty');
  const filesEl = document.getElementById('files');
  const count = document.getElementById('count');
  const alertEl = document.getElementById('alert');
  const queue = [];
  let nextId = 1;
  let busy = false;
  const fmtSize = n => n < 1024*1024 ? `${(n/1024).toFixed(0)} KB` : `${(n/1024/1024).toFixed(1)} MB`;
  const setAlert = message => { alertEl.textContent = message || ''; alertEl.style.display = message ? 'block' : 'none'; };
  function refresh() {
    const total = queue.length;
    count.textContent = `${total} ${total===1?'видео':'видео'}`;
    choose.hidden = total > 0;
    add.hidden = total === 0;
    empty.style.display = total === 0 ? 'block' : 'none';
    start.disabled = busy || !queue.some(x => x.state === 'queued');
    choose.disabled = busy; add.disabled = busy;
  }
  function makeRow(entry) {
    const card = document.createElement('article');
    card.className = 'file-card';
    card.innerHTML = `<div class="file-top"><div style="min-width:0;flex:1"><div class="file-name"></div><div class="file-meta"></div></div><button class="remove" type="button">Убрать</button></div><div class="status">В очереди</div><div class="progress-track"><div class="progress-fill"></div></div><div class="progress-caption">Ожидает обработки</div><div class="file-links"></div>`;
    card.querySelector('.file-name').textContent = entry.file.name;
    card.querySelector('.file-meta').textContent = `${fmtSize(entry.file.size)} · ${entry.file.type || 'видеофайл'}`;
    entry.node = card;
    entry.statusEl = card.querySelector('.status');
    entry.fill = card.querySelector('.progress-fill');
    entry.caption = card.querySelector('.progress-caption');
    entry.links = card.querySelector('.file-links');
    card.querySelector('.remove').addEventListener('click', () => {
      if (busy || entry.state === 'processing') return;
      const i = queue.indexOf(entry); if (i >= 0) queue.splice(i, 1);
      card.remove(); refresh();
    });
    filesEl.appendChild(card);
  }
  function setState(entry, state, message) {
    entry.state = state;
    entry.statusEl.className = 'status' + (state === 'done' ? ' good' : state === 'error' ? ' bad' : '');
    entry.statusEl.textContent = message;
    entry.fill.className = 'progress-fill';
    entry.caption.textContent = message;
    if (state === 'processing') entry.fill.classList.add('working');
    else if (state === 'done') { entry.fill.classList.add('finished'); entry.fill.style.width = '100%'; entry.caption.textContent = '100% · Завершено'; }
    else if (state === 'error') { entry.fill.classList.add('failed'); entry.fill.style.width = '100%'; }
    else if (state === 'uploading') entry.fill.style.width = '0%';
    else if (state === 'queued') { entry.fill.style.width = '0%'; entry.caption.textContent = 'Ожидает обработки'; }
  }
  function addFiles(fileList) {
    setAlert('');
    const picked = Array.from(fileList || []);
    if (!picked.length) return;
    for (const file of picked) {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      if (!['mp4','mov','avi','mkv','webm','m4v'].includes(ext)) {
        setAlert(`Файл «${file.name}» пропущен: формат не поддерживается.`); continue;
      }
      const entry = {id: String(nextId++), file, state:'queued', node:null};
      queue.push(entry); makeRow(entry); setState(entry, 'queued', 'В очереди');
    }
    picker.value = '';
    refresh();
  }
  choose.addEventListener('click', () => picker.click());
  add.addEventListener('click', () => picker.click());
  picker.addEventListener('change', e => addFiles(e.target.files));

  function processOne(entry) {
    return new Promise(resolve => {
      const jobId = `ui_${Date.now()}_${entry.id}_${Math.random().toString(36).slice(2,8)}`;
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append('video_file', entry.file, entry.file.name);
      form.append('response_format', 'json');
      form.append('generate_txt', 'true');
      form.append('generate_json', 'true');
      form.append('word_timestamps', 'true');
      setState(entry, 'uploading', 'Загрузка файла…');
      entry.caption.textContent = 'Загрузка: 0%';
      xhr.open('POST', '/api/v1/analyze', true);
      xhr.upload.onprogress = e => {
        if (!e.lengthComputable) return;
        const pct = Math.max(0, Math.min(100, Math.round(100*e.loaded/e.total)));
        entry.fill.className = 'progress-fill'; entry.fill.style.width = `${pct}%`;
        entry.statusEl.textContent = `Загрузка · ${pct}%`;
        entry.caption.textContent = `Передано ${pct}% файла`;
        if (pct >= 100) setState(entry, 'processing', 'Анализ Whisper + Apple OCR + Qwen3-VL…');
      };
      xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = `HTTP ${xhr.status}`;
          try { const err = JSON.parse(xhr.responseText); detail = err.detail || detail; } catch (_) {}
          setState(entry, 'error', `Ошибка: ${detail}`); resolve(); return;
        }
        let data;
        try { data = JSON.parse(xhr.responseText); }
        catch (_) { setState(entry, 'error', 'Ошибка: сервер вернул невалидный JSON'); resolve(); return; }
        setState(entry, 'done', 'Готово · TXT и JSON сохранены');
        const links = [];
        if (data.txt_filename) links.push(['Скачать TXT', data.txt_filename]);
        if (data.json_filename) links.push(['Скачать JSON', data.json_filename]);
        for (const [label, filename] of links) {
          const a = document.createElement('a');
          a.textContent = label; a.href = '/api/v1/output/' + encodeURIComponent(filename); a.download = filename;
          entry.links.appendChild(a);
        }
        if (!links.length) entry.caption.textContent = '100% · Завершено, но имена выходных файлов не вернулись сервером.';
        resolve();
      };
      xhr.onerror = () => { setState(entry, 'error', 'Ошибка сети: проверьте, что движок работает.'); resolve(); };
      xhr.onabort = () => { setState(entry, 'error', 'Запрос прерван.'); resolve(); };
      // Upload progress is real. After upload, the moving bar honestly indicates analysis
      // without inventing a percentage while synchronous inference is running.
      xhr.upload.onload = () => setState(entry, 'processing', 'Анализ Whisper + Apple OCR + Qwen3-VL…');
      xhr.send(form);
    });
  }
  start.addEventListener('click', async () => {
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
  });
  refresh();
})();
</script>
</body>
</html>
'''


def patch_form_fields(source: str) -> str:
    anchor = '    generate_txt: bool = Form(bool(setting("generate_txt", True))),\n'
    replacement = (
        anchor
        + '    generate_json: bool = Form(bool(setting("generate_json", True))),\n'
        + '    word_timestamps: bool = Form(\n'
        + '        bool(setting("word_timestamps", True)),\n'
        + '        description="Временные метки отдельных слов Whisper для привязки речи к сценам",\n'
        + '    ),\n'
    )
    if "generate_json: bool = Form(" not in source:
        source = replace_once(source, anchor, replacement, "generate_txt parameter to add JSON and word timestamp switches")
    elif "word_timestamps: bool = Form(" not in source:
        raise ValueError("generate_json exists but word_timestamps is missing; inspect the form parameters.")
    return source


def patch_whisper_call(source: str) -> str:
    if "word_timestamps=word_timestamps" in source:
        return source
    pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)audio_res = w_model\.transcribe\(\n(?P<a>[ \t]*)temp_path,\n"
    )
    def repl(match: re.Match) -> str:
        return match.group(0) + match.group("a") + "word_timestamps=word_timestamps,\n"
    source, count = pattern.subn(repl, source, count=1)
    if count != 1:
        raise ValueError("Could not find the Whisper transcribe() call to enable word-level timing.")
    return source


def patch_cross_modal_context(source: str) -> str:
    if "СИНХРОНИЗИРОВАННАЯ РАСШИФРОВКА WHISPER" in source:
        return source
    pattern = re.compile(
        r"(?m)^(?P<i>[ \t]*)t_vlm_start = time\.time\(\)\n"
        r"(?P=i)raw = vlm_engine\.describe_scene\(\n"
        r"(?P=i)    images,\n"
    )
    def repl(match: re.Match) -> str:
        i = match.group("i")
        return (
            f"{i}if audio_text.strip():\n"
            f"{i}    full_visual_prompt += (\n"
            f'{i}        "\\n\\nСИНХРОНИЗИРОВАННАЯ РАСШИФРОВКА WHISPER (контекст речи, не визуальное доказательство):\\n"\n'
            f"{i}        + audio_text\n"
            f'{i}        + "\\n\\nИспользуй речь как контекст темы; утверждай о содержимом кадра только то, что подтверждается изображением и OCR."\n'
            f"{i}    )\n"
            f"{i}t_vlm_start = time.time()\n"
            f"{i}raw = vlm_engine.describe_scene(\n"
            f"{i}    images,\n"
        )
    source, count = pattern.subn(repl, source, count=1)
    if count != 1:
        raise ValueError("Could not find the per-scene Qwen call to connect scene-aligned audio context.")
    return source


def patch_frontend_routes(source: str) -> str:
    import_line = "from fastapi.responses import PlainTextResponse"
    replacement_import = "from fastapi.responses import PlainTextResponse, HTMLResponse, FileResponse"
    if "HTMLResponse, FileResponse" not in source:
        source = replace_once(source, import_line, replacement_import, "FastAPI response imports")
    if '@app.get("/ui", response_class=HTMLResponse' in source:
        return source
    anchor = "app.openapi = custom_openapi\n"
    routes = '''app.openapi = custom_openapi


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def video_context_web_ui():
    """Friendly local UI for selecting and processing multiple videos sequentially."""
    ui_path = Path(__file__).with_name("videocontext_web_ui.html")
    if not ui_path.is_file():
        return HTMLResponse(
            "UI file not found. Re-run patch_vce_ui_batch_progress.py from the project folder.",
            status_code=500,
        )
    return HTMLResponse(ui_path.read_text(encoding="utf-8"))


@app.get("/api/v1/output/{filename}", include_in_schema=False)
async def download_context_output(filename: str):
    """Serve only generated *_context.txt / *_context.json files from the working directory."""
    if Path(filename).name != filename or not filename.endswith(("_context.txt", "_context.json")):
        raise HTTPException(400, "Invalid output filename")
    output_path = Path.cwd() / filename
    if not output_path.is_file():
        raise HTTPException(404, "Output file not found in the engine working directory")
    return FileResponse(str(output_path), filename=filename)
'''
    if anchor not in source:
        raise ValueError("Could not find the app.openapi = custom_openapi route insertion point.")
    return source.replace(anchor, routes, 1)


def patch_json_writer(source: str) -> str:
    if 'json_output_name = f"{sanitize_filename(source_name)}_context.json"' in source:
        return source
    return patch_json_autosave(source)


def patch_settings_fields(settings_text: str) -> str:
    settings = json.loads(settings_text)
    if not isinstance(settings, dict):
        raise ValueError("Settings file must contain a JSON object.")
    settings.setdefault("generate_json", True)
    settings.setdefault("word_timestamps", True)
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    if not TARGET.is_file():
        print(f"ERROR: {TARGET.resolve()} not found. Extract the ZIP into the VideoContext-Engine project folder.")
        return 2
    if not CONFIG.is_file():
        print(f"ERROR: {CONFIG.resolve()} not found. No files changed.")
        return 2

    original_source = TARGET.read_text(encoding="utf-8")
    original_settings = CONFIG.read_text(encoding="utf-8")
    source = original_source
    try:
        required = [
            "def apple_vision_ocr(",
            "def resize_keep_aspect(",
            "def load_whisper_model(model_name: str, acceleration_enabled: bool = True)",
            "def load_user_settings()",
            "async def analyze_video(",
            'setting("generate_txt", True)',
            "enable_apple_ocr: bool",
        ]
        missing = [needle for needle in required if needle not in source]
        if missing:
            raise ValueError(
                "This is not the expected locally patched v3.19 file. Missing: " + ", ".join(missing)
            )

        source = patch_audio_helpers(source)
        source = patch_form_fields(source)
        source = patch_whisper_call(source)
        source = patch_scene_transcript(source)
        source = patch_output_params(source)
        source = patch_cross_modal_context(source)
        source = patch_json_writer(source)
        source = patch_frontend_routes(source)
        ast.parse(source, filename=str(TARGET))
        settings_text = patch_settings_fields(original_settings)

        checks = {
            "word timestamp switch": "word_timestamps: bool = Form(" in source,
            "Whisper per-word timestamps": "word_timestamps=word_timestamps" in source,
            "scene-aligned transcript": "def transcript_for_scene(" in source and "transcript_for_scene(scene, whisper_segments)" in source,
            "scene-local audio metrics": "def _union_duration(" in source,
            "Qwen receives the scene-aligned Whisper context": "СИНХРОНИЗИРОВАННАЯ РАСШИФРОВКА WHISPER" in source,
            "JSON autosave switch": "generate_json: bool = Form(" in source,
            "JSON writer": 'json.dump(json_payload, json_file, ensure_ascii=False, indent=2)' in source,
            "HTML UI route": '@app.get("/ui", response_class=HTMLResponse' in source,
            "safe output download route": '@app.get("/api/v1/output/{filename}"' in source,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise ValueError("Validation failed: " + ", ".join(failed))
    except Exception as exc:
        print(f"ERROR: {exc}")
        print("No files have been changed.")
        return 3

    # The UI itself is written alongside this patcher into the engine folder.
    ui_text = UI_HTML
    if source == original_source and settings_text == original_settings and UI_FILE.exists() and UI_FILE.read_text(encoding="utf-8") == ui_text:
        print("UI and requested features already appear to be installed; no changes needed.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    engine_backup = TARGET.with_name(f"{TARGET.name}.before_ui_batch_{stamp}.bak")
    config_backup = CONFIG.with_name(f"{CONFIG.name}.before_ui_batch_{stamp}.bak")
    shutil.copy2(TARGET, engine_backup)
    shutil.copy2(CONFIG, config_backup)
    TARGET.write_text(source, encoding="utf-8")
    CONFIG.write_text(settings_text, encoding="utf-8")
    UI_FILE.write_text(ui_text, encoding="utf-8")

    print("Patch installed successfully.")
    print(f"Engine backup:   {engine_backup.resolve()}")
    print(f"Settings backup: {config_backup.resolve()}")
    print(f"Web UI:          {UI_FILE.resolve()}")
    print("Open http://127.0.0.1:7555/ui after restarting the server.")
    print("The UI selects one or many files, offers Add video, processes sequentially, and shows a separate status bar per file.")
    print("Upload progress is measured; model inference is shown with an honest animated busy bar until the result returns.")
    print("Whisper word timestamps, scene-aligned speech, Qwen audio context, and JSON auto-save are enabled by default.")
    print("Swagger stays available at /docs. Existing user settings are preserved; generate_json and word_timestamps are added if absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
