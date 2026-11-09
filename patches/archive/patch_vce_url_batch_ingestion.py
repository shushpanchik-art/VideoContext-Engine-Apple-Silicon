#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add settings-aware URL ingestion and multi-source queue to VideoContext Engine v3.19."""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

ENGINE_NAME = "VideoContextEngine_v3.19.py"
UI_NAME = "videocontext_web_ui.html"
CONFIG_NAME = "videocontext_settings.json"
MARKER = "VCE_URL_BATCH_INGESTION_V1"
HELPER_MARKER = "# " + MARKER
UI_MARKER = "<!-- " + MARKER + " -->"


def auto_download_dimension(vlm_resolution, analysis_mode, skip_visual, enable_apple_ocr, enable_ffmpeg_ocr):
    """Choose a safe maximum raster side from the effective visual/OCR settings."""
    try:
        requested_vlm_side = int(vlm_resolution)
    except (TypeError, ValueError):
        requested_vlm_side = 1280

    mode = str(analysis_mode or "full").strip().lower()
    run_vlm = mode == "full" and not bool(skip_visual)
    run_ocr = bool(enable_apple_ocr) or bool(enable_ffmpeg_ocr)

    if run_vlm:
        target_side = max(640, requested_vlm_side)
    elif run_ocr:
        target_side = 1280
    else:
        # Scene detection still needs a modest video stream, but no reason to
        # download a high-resolution image when VLM and OCR are both disabled.
        target_side = 640

    if run_ocr:
        target_side = max(target_side, 1280)

    target_side = max(640, min(target_side, 3840))
    # A small aspect-ratio allowance admits 854x480 for a 768px VLM target
    # without accidentally admitting the next full resolution tier.
    return min(4320, int(round(target_side * 1.15)))


def download_format_selector(max_dimension):
    """Strict width/height cap. Never silently fall back to an unlimited 'best'."""
    if max_dimension is None:
        return "best[ext=mp4]/best"
    cap = max(480, min(int(max_dimension), 4320))
    return (
        f"best[width<={cap}][height<={cap}][ext=mp4]/"
        f"best[width<={cap}][height<={cap}]/"
        f"bestvideo[width<={cap}][height<={cap}]+bestaudio"
    )


def _replace_once(source, old, new, label):
    if new in source:
        return source
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Cannot patch {label}: expected one anchor, found {count}.")
    return source.replace(old, new, 1)


def patch_engine_downloads_folder(source):
    """Write reports from URL sources to the user's macOS Downloads folder."""
    marker = "# VCE_URL_REPORTS_TO_DOWNLOADS_V1"
    if marker in source:
        if 'output_directory = Path.home() / "Downloads"' not in source:
            raise ValueError("Partial Downloads-folder patch detected.")
        return source

    anchor = "        elif video_url.strip():\n            owns_temp_path = True\n"
    replacement = (
        "        elif video_url.strip():\n"
        "            " + marker + "\n"
        '            output_directory = Path.home() / "Downloads"\n'
        "            output_directory.mkdir(parents=True, exist_ok=True)\n"
        "            owns_temp_path = True\n"
    )
    return _replace_once(source, anchor, replacement, "URL output directory")


def patch_engine_startup_cleanup(source):
    """Remove only old VCE-owned temporary media after the new API server has bound its port."""
    marker = "# VCE_STARTUP_STALE_TEMP_CLEANUP_V1"
    if marker in source:
        required = (
            "def _vce_cleanup_stale_temp_artifacts(",
            "_vce_cleanup_stale_temp_artifacts()",
            "max_age_seconds=3600",
            "vce_ffmpeg_tesseract_",
        )
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("Partial startup cleanup patch detected; missing: " + ", ".join(missing))
        return source

    helper = r'''# VCE_STARTUP_STALE_TEMP_CLEANUP_V1
def _vce_cleanup_stale_temp_artifacts(
    project_root=None,
    temp_root=None,
    max_age_seconds=3600,
    now=None,
):
    """Delete only old VCE temporary media/partials; never touch model caches or reports."""
    import re as _vce_re
    import shutil as _vce_shutil
    import stat as _vce_stat
    import tempfile as _vce_tempfile
    import time as _vce_time
    from pathlib import Path as _vce_Path

    current_time = _vce_time.time() if now is None else float(now)
    cutoff = current_time - max(0, float(max_age_seconds))
    project_root = (
        _vce_Path(project_root).expanduser().resolve()
        if project_root is not None
        else _vce_Path(__file__).resolve().parent
    )
    temp_root = (
        _vce_Path(temp_root).expanduser().resolve()
        if temp_root is not None
        else _vce_Path(_vce_tempfile.gettempdir()).resolve()
    )

    removed_project = 0
    removed_system_temp = 0
    removed_temp_dirs = 0
    errors = 0

    media_suffixes = (
        ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi",
        ".m4a", ".mp3", ".wav", ".aac",
    )

    # URL downloads and multipart uploads are created in the engine project root
    # as temp_<unix-seconds>_<sanitized-name>.<extension>.
    try:
        project_items = list(project_root.iterdir())
    except OSError:
        project_items = []
        errors += 1

    for path in project_items:
        if not _vce_re.match(r"^temp_\d{10,}_", path.name):
            continue
        folded = path.name.casefold()
        is_partial = (
            folded.endswith((".part", ".ytdl"))
            or ".part-frag" in folded
        )
        is_media = folded.endswith(media_suffixes)
        if not (is_partial or is_media):
            continue
        try:
            info = path.lstat()
            if not _vce_stat.S_ISREG(info.st_mode) or info.st_mtime > cutoff:
                continue
            path.unlink()
            removed_project += 1
        except OSError:
            errors += 1

    # FFmpeg/OpenCV helper artifacts use these explicit VCE prefixes in macOS
    # temporary storage. Only entries older than the grace period are eligible.
    temp_file_prefixes = (
        "vce_compatible_",
        "vce_mp4_recovery_",
        "vce_audio_fallback_",
        "vce_audio_only_",
        "vce_ffmpeg_ocr_",
    )
    temp_dir_prefixes = ("vce_ffmpeg_tesseract_",)
    try:
        temp_items = list(temp_root.iterdir())
    except OSError:
        temp_items = []
        errors += 1

    for path in temp_items:
        if not path.name.startswith(temp_file_prefixes + temp_dir_prefixes):
            continue
        try:
            info = path.lstat()
            if info.st_mtime > cutoff:
                continue
            if _vce_stat.S_ISREG(info.st_mode):
                path.unlink()
                removed_system_temp += 1
            elif (
                _vce_stat.S_ISDIR(info.st_mode)
                and path.name.startswith(temp_dir_prefixes)
            ):
                _vce_shutil.rmtree(path)
                removed_temp_dirs += 1
        except OSError:
            errors += 1

    total = removed_project + removed_system_temp + removed_temp_dirs
    if total or errors:
        print(
            "[VCE cleanup] Старые временные артефакты: удалено "
            + str(total)
            + " (проект: " + str(removed_project)
            + ", системный temp: " + str(removed_system_temp)
            + ", папки: " + str(removed_temp_dirs)
            + "); ошибок: " + str(errors)
        )
    return {
        "removed_project": removed_project,
        "removed_system_temp": removed_system_temp,
        "removed_temp_dirs": removed_temp_dirs,
        "errors": errors,
    }


'''
    anchor = "@asynccontextmanager\nasync def lifespan(app: FastAPI):\n"
    source = _replace_once(source, anchor, helper + anchor, "startup cleanup helper insertion")
    lifespan_anchor = (
        "async def lifespan(app: FastAPI):\n"
        "    global vlm_engine, WHISPER_CACHE\n"
    )
    lifespan_replacement = (
        "async def lifespan(app: FastAPI):\n"
        "    global vlm_engine, WHISPER_CACHE\n"
        "    _vce_cleanup_stale_temp_artifacts()\n"
    )
    return _replace_once(source, lifespan_anchor, lifespan_replacement, "startup cleanup hook")


def patch_ui_report_download_links(ui):
    """Open report endpoints as normal attachment responses in a visible new tab."""
    marker = "// VCE_URL_REPORT_DOWNLOAD_TARGET_V1"
    if marker in ui:
        if "a.target = '_blank'" not in ui or "a.download = filename" in ui:
            raise ValueError("Partial report-download UI patch detected.")
        return ui

    old = (
        "          a.textContent = label; a.href = href; a.download = filename;\n"
        "          entry.links.appendChild(a);\n"
    )
    new = (
        "          a.textContent = label; a.href = href; a.target = '_blank'; a.rel = 'noopener';\n"
        "          entry.links.appendChild(a);\n"
    )
    return _replace_once(ui, old, marker + "\n" + new, "report download links")


def patch_engine(source):
    if HELPER_MARKER in source:
        required = (
            "def _vce_auto_download_dimension(",
            "def _vce_download_format_selector(",
            "await asyncio.to_thread(",
            "download_max_dimension",
            "progress_hook",
            "VCE_CANCELLED_BY_USER",
        )
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("Partial URL engine patch detected; missing: " + ", ".join(missing))
        source = patch_engine_downloads_folder(source)
        source = patch_engine_startup_cleanup(source)
        ast.parse(source, filename=ENGINE_NAME)
        return source

    required = (
        'def download_video_from_url(url: str, output_template: str) -> str:',
        '"format": "best[ext=mp4]/best",',
        'video_url: str = Form(',
        'vlm_resolution: int = Form(',
        'analysis_mode: str = Form(',
        'enable_apple_ocr: bool = Form(',
        'enable_ffmpeg_ocr: bool = Form(',
        'async def analyze_video(',
        'temp_path = download_video_from_url(',
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise ValueError(
            "Installed engine does not match the inspected VCE patchset. Missing anchors: "
            + "; ".join(missing)
        )
    if "import asyncio" not in source:
        raise ValueError("asyncio is not imported; refusing to change the async route.")

    helpers = f'''{HELPER_MARKER}
def _vce_auto_download_dimension(
    vlm_resolution: int,
    analysis_mode: str,
    skip_visual: bool,
    enable_apple_ocr: bool,
    enable_ffmpeg_ocr: bool,
) -> int:
    """Choose a source raster from the active VLM/OCR settings, not yt-dlp's largest format."""
    try:
        requested_vlm_side = int(vlm_resolution)
    except (TypeError, ValueError):
        requested_vlm_side = 1280

    mode = str(analysis_mode or "full").strip().lower()
    run_vlm = mode == "full" and not bool(skip_visual)
    run_ocr = bool(enable_apple_ocr) or bool(enable_ffmpeg_ocr)

    if run_vlm:
        target_side = max(640, requested_vlm_side)
    elif run_ocr:
        target_side = 1280
    else:
        target_side = 640

    if run_ocr:
        target_side = max(target_side, 1280)

    target_side = max(640, min(target_side, 3840))
    return min(4320, int(round(target_side * 1.15)))


def _vce_download_format_selector(max_dimension: int | None) -> str:
    """Select the best muxed format within a strict cap; merge A/V only within that cap."""
    if max_dimension is None:
        return "best[ext=mp4]/best"
    cap = max(480, min(int(max_dimension), 4320))
    return (
        f"best[width<={{cap}}][height<={{cap}}][ext=mp4]/"
        f"best[width<={{cap}}][height<={{cap}}]/"
        f"bestvideo[width<={{cap}}][height<={{cap}}]+bestaudio"
    )


'''
    source = _replace_once(source, "def download_video_from_url(", helpers + "def download_video_from_url(", "URL helper insertion")
    source = _replace_once(
        source,
        "def download_video_from_url(url: str, output_template: str) -> str:",
        "def download_video_from_url(url: str, output_template: str, max_dimension: int | None = None, progress_hook=None) -> str:",
        "download helper signature",
    )
    source = _replace_once(
        source,
        '"format": "best[ext=mp4]/best",',
        '"format": _vce_download_format_selector(max_dimension),',
        "unbounded yt-dlp selector",
    )

    quiet_anchor = '        "quiet": True,\n'
    extra_options = (
        quiet_anchor
        + '        "noplaylist": True,\n'
        + '        "retries": 5,\n'
        + '        "fragment_retries": 10,\n'
        + '        "socket_timeout": 30,\n'
        + '        "merge_output_format": "mp4",\n'
        + '        "progress_hooks": [progress_hook] if progress_hook is not None else [],\n'
    )
    if '"noplaylist": True' not in source:
        source = _replace_once(source, quiet_anchor, extra_options, "yt-dlp retry/no-playlist options")

    call_pattern = re.compile(
        r'(?m)^(?P<i>[ \t]*)temp_path = download_video_from_url\(\n'
        r'(?P<a>[ \t]+)video_url,\n'
        r'(?P=a)f"temp_\{int\(time\.time\(\)\)\}_\{clean_title\}\.%\(ext\)s",\n'
        r'(?P=i)\)'
    )
    matches = list(call_pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"Could not uniquely locate video_url download call (found {len(matches)}).")
    match = matches[0]
    indent, arg_indent = match.group("i"), match.group("a")
    new_call = (
        f"{indent}download_max_dimension = _vce_auto_download_dimension(\n"
        f"{arg_indent}vlm_resolution=vlm_resolution,\n"
        f"{arg_indent}analysis_mode=analysis_mode,\n"
        f"{arg_indent}skip_visual=skip_visual,\n"
        f"{arg_indent}enable_apple_ocr=enable_apple_ocr,\n"
        f"{arg_indent}enable_ffmpeg_ocr=enable_ffmpeg_ocr,\n"
        f"{indent})\n"
        f'{indent}print("[VCE URL] yt-dlp source raster cap: " + str(download_max_dimension) + "px; "\n'
        f'{indent}      + "mode=" + str(analysis_mode) + ", VLM=" + str(vlm_resolution)\n'
        f'{indent}      + ", Apple OCR=" + str(enable_apple_ocr) + ", FFmpeg OCR=" + str(enable_ffmpeg_ocr))\n'
        f"{indent}def _vce_url_download_progress(download_status):\n"
        f"{arg_indent}if cancel_event.is_set():\n"
        f'{arg_indent}    raise yt_dlp.utils.DownloadError("VCE_CANCELLED_BY_USER")\n'
        f"{arg_indent}if not isinstance(download_status, dict):\n"
        f"{arg_indent}    return\n"
        f'{arg_indent}if download_status.get("status") == "downloading":\n'
        f'{arg_indent}    downloaded = max(0, int(download_status.get("downloaded_bytes") or 0))\n'
        f'{arg_indent}    total = download_status.get("total_bytes") or download_status.get("total_bytes_estimate")\n'
        f'{arg_indent}    if total:\n'
        f'{arg_indent}        fraction = max(0.0, min(1.0, downloaded / max(1, int(total))))\n'
        f'{arg_indent}        set_job_progress(job_id, "download_video", 1.5 + fraction * 0.5,\n'
        f'{arg_indent}            f"yt-dlp: скачивание видео · {{fraction * 100:.0f}}%",\n'
        f'{arg_indent}            downloaded_bytes=downloaded, total_bytes=int(total))\n'
        f'{arg_indent}    else:\n'
        f'{arg_indent}        set_job_progress(job_id, "download_video", None, "yt-dlp: скачивание видео…", indeterminate=True)\n'
        f"{indent}try:\n"
        f"{arg_indent}temp_path = await asyncio.to_thread(\n"
        f"{arg_indent}    download_video_from_url,\n"
        f"{arg_indent}    video_url,\n"
        f'{arg_indent}    f"temp_{{int(time.time())}}_{{clean_title}}.%(ext)s",\n'
        f"{arg_indent}    download_max_dimension,\n"
        f"{arg_indent}    _vce_url_download_progress,\n"
        f"{arg_indent})\n"
        f"{indent}except yt_dlp.utils.DownloadError as download_error:\n"
        f"{arg_indent}if cancel_event.is_set() or 'VCE_CANCELLED_BY_USER' in str(download_error):\n"
        f'{arg_indent}    raise HTTPException(409, detail="Распознавание остановлено пользователем")\n'
        f'{arg_indent}raise HTTPException(400, detail="Не удалось скачать видео: " + str(download_error)[:700])'
    )
    source = source[:match.start()] + new_call + source[match.end():]

    ast.parse(source, filename=ENGINE_NAME)
    for item in (
        HELPER_MARKER,
        "_vce_download_format_selector(max_dimension)",
        "await asyncio.to_thread(",
        "download_max_dimension",
        "progress_hooks",
        "VCE_CANCELLED_BY_USER",
        "download_video",
        '"noplaylist": True',
        "width<=",
        "height<=",
    ):
        if item not in source:
            raise ValueError("Engine validation failed: missing " + item)
    source = patch_engine_downloads_folder(source)
    source = patch_engine_startup_cleanup(source)
    ast.parse(source, filename=ENGINE_NAME)
    return source


def patch_ui(ui):
    if UI_MARKER in ui:
        required = ('id="urlInput"', 'id="addUrls"', "form.append('video_url', entry.url)", "function addUrls()")
        missing = [item for item in required if item not in ui]
        if missing:
            raise ValueError("Partial URL queue UI patch detected; missing: " + ", ".join(missing))
        return patch_ui_report_download_links(ui)

    required = (
        '    <input id="picker" type="file" accept="video/*,.mp4,.mov,.avi,.mkv,.webm,.m4v" multiple hidden>\n',
        "  const picker = document.getElementById('picker');\n",
        "    count.textContent = ",
        "    card.querySelector('.file-name').textContent = entry.file.name;\n",
        "    card.querySelector('.file-meta').textContent = ",
        "      if (entry.localToken) form.append('local_video_token', entry.localToken);\n",
        "      else form.append('video_file', entry.file, entry.file.name);\n",
        "      setState(entry, 'uploading', 'Загрузка файла…');\n",
        "      xhr.upload.onprogress = e => {\n",
        "      if (!entry.localToken) xhr.upload.onload = () => setState(entry, 'processing', 'Whisper + Apple OCR + Qwen3-VL + смысловая постобработка…');\n",
        "  choose.addEventListener('click', pickLocalVideos);\n",
        "  add.addEventListener('click', pickLocalVideos);\n",
    )
    missing = [item for item in required if item not in ui]
    if missing:
        raise ValueError("Installed UI does not match inspected file queue: " + "; ".join(missing))

    if "Per-request settings make saved values effective immediately" not in ui:
        raise ValueError("Settings Button integration is missing; VLM/OCR settings are not submitted per request.")

    for marker in (
        "const cfg = readSettings();",
        "for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));",
        "id=\"cfg-vlm-resolution\"",
        "id=\"cfg-apple-ocr\"",
        "id=\"cfg-analysis-mode\"",
    ):
        if marker not in ui:
            raise ValueError("Current UI is missing saved-settings hook: " + marker)

    style = r'''
/* VCE URL queue */
.url-entry{margin:16px 0 8px;padding:14px;border:1px solid var(--line);border-radius:12px;background:rgba(110,168,254,.045)}
.url-entry label{display:block;font-weight:650;margin-bottom:7px}
.url-entry textarea{display:block;width:100%;min-height:88px;resize:vertical;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:#111720;color:var(--text);font:inherit}
.url-entry .hint{display:block;margin-top:7px}
'''
    if "/* VCE URL queue */" not in ui:
        ui = _replace_once(ui, "</style>", style + "\n</style>", "URL queue styling")

    url_markup = (
        f'    {UI_MARKER}\n'
        '    <div class="url-entry">\n'
        '      <label for="urlInput">Или добавьте ссылки на видео / подкасты</label>\n'
        '      <textarea id="urlInput" rows="3" placeholder="По одной ссылке на строку: VK Видео, YouTube, RuTube и другие сайты, поддерживаемые yt-dlp"></textarea>\n'
        '      <div class="actions"><button id="addUrls" type="button">＋ Добавить ссылки в очередь</button></div>\n'
        '      <span class="hint">Можно вставить несколько ссылок сразу. Разрешение загрузки подбирается автоматически из текущих настроек VLM и OCR.</span>\n'
        '    </div>\n'
    )
    picker_anchor = '    <input id="picker" type="file" accept="video/*,.mp4,.mov,.avi,.mkv,.webm,.m4v" multiple hidden>\n'
    ui = _replace_once(ui, picker_anchor, picker_anchor + url_markup, "URL input markup")

    picker_decl = "  const picker = document.getElementById('picker');\n"
    ui = _replace_once(
        ui, picker_decl,
        picker_decl
        + "  const urlInput = document.getElementById('urlInput');\n"
        + "  const addUrlsButton = document.getElementById('addUrls');\n",
        "URL input JS references",
    )

    tick = chr(96)
    old_count = "    count.textContent = " + tick + chr(36) + "{total} " + chr(36) + "{total===1?'видео':'видео'}" + tick + ";\n"
    ui = _replace_once(ui, old_count, "    count.textContent = 'В очереди: ' + total;\n", "queue item counter")

    ui = _replace_once(
        ui,
        "    choose.disabled = busy; add.disabled = busy;\n",
        "    choose.disabled = busy; add.disabled = busy;\n"
        "    addUrlsButton.disabled = busy; urlInput.disabled = busy;\n",
        "URL queue busy-state controls",
    )

    old_row = (
        "    card.querySelector('.file-name').textContent = entry.file.name;\n"
        "    card.querySelector('.file-meta').textContent = " + tick
        + chr(36) + "{fmtSize(entry.file.size)} · " + chr(36) + "{entry.file.type || 'видеофайл'}" + tick + ";\n"
    )
    new_row = (
        "    card.querySelector('.file-name').textContent = entry.file ? entry.file.name : entry.displayName;\n"
        "    card.querySelector('.file-meta').textContent = entry.file ? " + tick
        + chr(36) + "{fmtSize(entry.file.size)} · " + chr(36) + "{entry.file.type || 'видеофайл'}" + tick
        + " : entry.url;\n"
    )
    ui = _replace_once(ui, old_row, new_row, "queue row file/URL labels")

    ui = ui.replace(
        "Сначала выберите видео. После выбора появится кнопка «Добавить ещё видео».",
        "Выберите локальные видео или добавьте одну / несколько ссылок выше.",
        1,
    )

    add_urls_function = r'''  function normalizeVideoUrl(raw) {
    let value = String(raw || '').trim();
    const markdown = value.match(/^\[[^\]]+\]\((https?:\/\/[^)\s]+)\)$/i);
    if (markdown) value = markdown[1];
    if (value.startsWith('<') && value.endsWith('>')) value = value.slice(1, -1).trim();
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      throw new Error('разрешены только ссылки http:// и https://');
    }
    if (!parsed.hostname) throw new Error('в ссылке отсутствует адрес сайта');
    return parsed.href;
  }

  function addUrls() {
    setAlert('');
    const lines = urlInput.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    if (!lines.length) { setAlert('Вставьте хотя бы одну ссылку.'); return; }
    const existing = new Set(queue.filter(x => x.url).map(x => x.url));
    let addedCount = 0;
    for (const line of lines) {
      try {
        const url = normalizeVideoUrl(line);
        if (existing.has(url)) {
          setAlert('Повторная ссылка пропущена: ' + url);
          continue;
        }
        existing.add(url);
        const parsed = new URL(url);
        const shortPath = parsed.pathname.length > 64 ? parsed.pathname.slice(0, 64) + '…' : parsed.pathname;
        const entry = {
          id: String(nextId++),
          file: null,
          url,
          displayName: 'Ссылка: ' + parsed.hostname + shortPath,
          state: 'queued',
          node: null
        };
        queue.push(entry);
        makeRow(entry);
        setState(entry, 'queued', 'Ссылка добавлена в очередь');
        addedCount++;
      } catch (error) {
        setAlert('Некорректная ссылка пропущена: ' + line + ' (' + error.message + ')');
      }
    }
    urlInput.value = '';
    refresh();
    if (addedCount) setAlert('Добавлено ссылок в очередь: ' + addedCount);
  }

'''
    choose_anchor = "  choose.addEventListener('click', pickLocalVideos);\n"
    ui = _replace_once(ui, choose_anchor, add_urls_function + choose_anchor, "URL parsing and queue insertion")
    listener_anchor = "  picker.addEventListener('change', e => addFiles(e.target.files));\n"
    ui = _replace_once(
        ui, listener_anchor,
        listener_anchor + "  addUrlsButton.addEventListener('click', addUrls);\n",
        "URL add button listener",
    )

    ui = _replace_once(
        ui,
        "      else form.append('video_file', entry.file, entry.file.name);\n",
        "      else if (entry.url) form.append('video_url', entry.url);\n"
        "      else form.append('video_file', entry.file, entry.file.name);\n",
        "URL FormData branch",
    )

    ui = _replace_once(
        ui,
        "      if (!entry.localToken) {\n"
        "        setState(entry, 'uploading', 'Загрузка файла…');\n"
        "        entry.caption.textContent = 'Загрузка: 0%';\n"
        "      } else {\n"
        "        setState(entry, 'processing', 'Анализ локального видео…');\n"
        "        entry.caption.textContent = 'Файл читается с диска; отчёты сохранятся рядом с видео.';\n"
        "      }\n",
        "      if (!entry.localToken && entry.url) {\n"
        "        setState(entry, 'processing', 'yt-dlp: получение видео по ссылке…');\n"
        "        entry.caption.textContent = 'Ссылка отправлена движку; загрузка и анализ выполняются на этом Mac.';\n"
        "      } else if (!entry.localToken) {\n"
        "        setState(entry, 'uploading', 'Загрузка файла…');\n"
        "        entry.caption.textContent = 'Загрузка: 0%';\n"
        "      } else {\n"
        "        setState(entry, 'processing', 'Анализ локального видео…');\n"
        "        entry.caption.textContent = 'Файл читается с диска; отчёты сохранятся рядом с видео.';\n"
        "      }\n",
        "three-way source status",
    )

    ui = _replace_once(
        ui,
        "      if (!entry.localToken) {\n        xhr.upload.onprogress = e => {\n",
        "      if (!entry.localToken && !entry.url) {\n        xhr.upload.onprogress = e => {\n",
        "URL upload progress guard",
    )

    ui = _replace_once(
        ui,
        "      if (!entry.localToken) xhr.upload.onload = () => setState(entry, 'processing', 'Whisper + Apple OCR + Qwen3-VL + смысловая постобработка…');\n",
        "      if (!entry.localToken && !entry.url) xhr.upload.onload = () => setState(entry, 'processing', 'Whisper + Apple OCR + Qwen3-VL + смысловая постобработка…');\n",
        "URL upload completion guard",
    )
    return patch_ui_report_download_links(ui)


def write_atomic(path, content):
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def server_listener():
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP:7555", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return "\n".join(lines) if len(lines) > 1 else None


def self_test():
    checks = [
        ("VLM 1280 + OCR selects a 720p-class source",
         auto_download_dimension(1280, "full", False, True, False) == 1472),
        ("VLM 2048 selects a 1080p-class source",
         auto_download_dimension(2048, "full", False, False, False) == 2355),
        ("OCR-only retains a 720p-class source",
         auto_download_dimension(768, "ocr_only", True, True, False) == 1472),
        ("no VLM/OCR uses a small scene-detection source",
         auto_download_dimension(1280, "whisper_ocr", True, False, False) == 736),
        ("selector bounds both dimensions",
         "width<=1472" in download_format_selector(1472)
         and "height<=1472" in download_format_selector(1472)),
        ("selector has no unlimited best fallback",
         download_format_selector(1472).endswith("]+bestaudio")
         and not download_format_selector(1472).endswith("/best")),
    ]
    for label, ok in checks:
        print(("PASS" if ok else "FAIL") + "  " + label)
    if not all(ok for _, ok in checks):
        return 1
    print("All self-tests passed.")
    return 0


def prepare(project):
    engine = project / ENGINE_NAME
    ui_file = project / UI_NAME
    config = project / CONFIG_NAME
    for path in (engine, ui_file, config):
        if not path.is_file():
            raise FileNotFoundError(f"Required project file not found: {path}")
    settings = json.loads(config.read_text(encoding="utf-8-sig"))
    if not isinstance(settings, dict):
        raise ValueError("videocontext_settings.json must contain a JSON object.")

    engine_original = engine.read_text(encoding="utf-8")
    ui_original = ui_file.read_text(encoding="utf-8")
    if (HELPER_MARKER in engine_original) != (UI_MARKER in ui_original):
        raise ValueError("Partial URL patch detected across engine/UI. No files changed.")

    for marker in (
        "const cfg = readSettings();",
        "for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));",
        'id="cfg-vlm-resolution"',
        'id="cfg-apple-ocr"',
        'id="cfg-analysis-mode"',
    ):
        if marker not in ui_original:
            raise ValueError(
                "Saved VLM/OCR settings are not submitted with each analysis request. "
                "Missing UI hook: " + marker
            )

    engine_new = patch_engine(engine_original)
    ui_new = patch_ui(ui_original)
    ast.parse(engine_new, filename=str(engine))
    if "form.append('video_url', entry.url)" not in ui_new:
        raise ValueError("Final UI validation failed: URLs are not submitted to /api/v1/analyze.")
    return engine, engine_new, ui_file, ui_new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.home() / "VideoContext-Engine")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show changes without writing.")
    parser.add_argument("--self-test", action="store_true", help="Run local URL-selection self-tests.")
    parser.add_argument("--allow-running-server", action="store_true",
                        help="Allow patching while port 7555 is listening (not recommended).")
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    project = args.project.expanduser().resolve()
    try:
        engine, engine_new, ui_file, ui_new = prepare(project)
    except Exception as exc:
        print("PREFLIGHT FAILED:", exc)
        print("No project files were changed.")
        return 2

    engine_original = engine.read_text(encoding="utf-8")
    ui_original = ui_file.read_text(encoding="utf-8")
    if engine_new == engine_original and ui_new == ui_original:
        print("URL queue and bounded yt-dlp downloads are already installed.")
        return 0

    diff = []
    diff.extend(difflib.unified_diff(
        engine_original.splitlines(keepends=True), engine_new.splitlines(keepends=True),
        fromfile=engine.name, tofile=engine.name + " (URL ingestion)",
    ))
    diff.extend(difflib.unified_diff(
        ui_original.splitlines(keepends=True), ui_new.splitlines(keepends=True),
        fromfile=ui_file.name, tofile=ui_file.name + " (URL queue)",
    ))
    print("Prepared changes:")
    print(" - local files and URLs share one sequential queue")
    print(" - each URL uses the existing /api/v1/analyze route")
    print(" - yt-dlp resolution is derived from analysis_mode, vlm_resolution and OCR toggles")
    print(" - URL reports are saved to ~/Downloads; local-video reports stay beside the source")
    print(" - server startup removes only VCE temp media/partials older than 1 hour; model caches and reports are untouched")
    print(" - report links open the server's attachment response in a new tab")
    print(" - URL downloading moves to asyncio.to_thread; the UI and API remain responsive")
    print(" - existing Whisper/VLM/OCR/report/stop behavior is retained")
    print("".join(diff[:500]))

    if args.dry_run:
        print("\nDRY RUN: validation succeeded; no files were changed.")
        return 0

    if not args.allow_running_server:
        listener = server_listener()
        if listener:
            print("Port 7555 is in use. Stop VideoContext Engine with Ctrl+C (or quit its app), then rerun.")
            print(listener)
            print("No project files were changed.")
            return 3

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {
        engine: engine.with_name(engine.name + f".before_url_batch_{stamp}.bak"),
        ui_file: ui_file.with_name(ui_file.name + f".before_url_batch_{stamp}.bak"),
    }
    for original, backup in backups.items():
        shutil.copy2(original, backup)

    try:
        write_atomic(engine, engine_new)
        write_atomic(ui_file, ui_new)
        ast.parse(engine.read_text(encoding="utf-8"), filename=str(engine))
        check_ui = ui_file.read_text(encoding="utf-8")
        for needle in (UI_MARKER, 'id="urlInput"', 'id="addUrls"', "form.append('video_url', entry.url)"):
            if needle not in check_ui:
                raise RuntimeError("Post-write UI check failed: " + needle)
    except Exception as exc:
        for original, backup in backups.items():
            shutil.copy2(backup, original)
        print("WRITE FAILED:", exc)
        print("Original files restored from timestamped backups.")
        return 4

    print("\nPATCH INSTALLED.")
    print(f"Engine backup: {backups[engine]}")
    print(f"UI backup:     {backups[ui_file]}")
    print("Restart VideoContext Engine, open http://127.0.0.1:7555/ui and hard-refresh with Cmd+Shift+R.")
    print("Paste one URL per line, click 'Добавить ссылки в очередь', then start the queue.")
    print("VLM=1280 with OCR enabled caps the source around 720p; VLM=2048 caps around 1080p.")
    print("There is no silent fallback to unlimited 'best' quality.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
