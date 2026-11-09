#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "patches" / "archive"))
import patch_vce_url_batch_ingestion as patch


ENGINE_FIXTURE = '''import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pathlib import Path

@asynccontextmanager
async def lifespan(app: FastAPI):
    global vlm_engine, WHISPER_CACHE
    vlm_engine = get_vlm_engine()
    yield

def download_video_from_url(url: str, output_template: str) -> str:
    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
    }
    return output_template

@app.post("/api/v1/analyze")
async def analyze_video(
    video_url: str = Form(""),
    vlm_resolution: int = Form(1280),
    analysis_mode: str = Form("full"),
    skip_visual: bool = Form(False),
    enable_apple_ocr: bool = Form(True),
    enable_ffmpeg_ocr: bool = Form(False),
):
    temp_path = None
    local_video_token = ""
    output_directory = Path.cwd()
    owns_temp_path = False
    try:
        if local_video_token.strip():
            pass
        elif video_url.strip():
            owns_temp_path = True
            clean_title = "fixture"
            try:
                temp_path = download_video_from_url(
                    video_url,
                    f"temp_{int(time.time())}_{clean_title}.%(ext)s",
                )
            except ValueError:
                raise
    except Exception:
        raise
    return {"ok": True}
'''


UI_FIXTURE = r'''<!doctype html>
<html><head><style></style></head><body>
    <input id="picker" type="file" accept="video/*,.mp4,.mov,.avi,.mkv,.webm,.m4v" multiple hidden>
    <div class="actions">
      <button id="choose" class="primary">Выбрать первое видео</button>
      <button id="add" hidden>Добавить ещё</button>
      <button id="start" disabled>Запустить обработку</button>
    </div>
    <div id="empty">Сначала выберите видео. После выбора появится кнопка «Добавить ещё видео».</div>
    <div id="files"></div>
<script>
(() => {
  const picker = document.getElementById('picker');
  const choose = document.getElementById('choose');
  const add = document.getElementById('add');
  const start = document.getElementById('start');
  const count = document.getElementById('count');
  const queue = [];
  let nextId = 1;
  let busy = false;
  function refresh() {
    const total = queue.length;
    COUNT_LINE
    choose.hidden = total > 0;
    add.hidden = total === 0;
    start.disabled = busy || !queue.some(x => x.state === 'queued');
    choose.disabled = busy; add.disabled = busy;
  }
  function makeRow(entry) {
    const card = document.createElement('article');
    card.querySelector('.file-name').textContent = entry.file.name;
    META_LINE
  }
  function setAlert(x) {}
  function setState(entry, state, msg) { entry.state = state; }
  function pickLocalVideos() {}
  // Per-request settings make saved values effective immediately in this Web UI
  const cfg = readSettings();
  for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));
  // id="cfg-vlm-resolution" id="cfg-apple-ocr" id="cfg-analysis-mode"
  function readSettings() { return {}; }
  function addFiles(fileList) {}
  choose.addEventListener('click', pickLocalVideos);
  add.addEventListener('click', pickLocalVideos);
  picker.addEventListener('change', e => addFiles(e.target.files));

  function processOne(entry) {
    return new Promise(resolve => {
      const jobId = 'fixture';
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      if (entry.localToken) form.append('local_video_token', entry.localToken);
      else form.append('video_file', entry.file, entry.file.name);
      form.append('response_format', 'json');
      // Per-request settings make saved values effective immediately in this Web UI.
      const cfg = readSettings();
      for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));
      if (!entry.localToken) {
        setState(entry, 'uploading', 'Загрузка файла…');
        entry.caption.textContent = 'Загрузка: 0%';
      } else {
        setState(entry, 'processing', 'Анализ локального видео…');
        entry.caption.textContent = 'Файл читается с диска; отчёты сохранятся рядом с видео.';
      }
      xhr.open('POST', '/api/v1/analyze', true);
      if (!entry.localToken) {
        xhr.upload.onprogress = e => {
          if (!e.lengthComputable) return;
        };
      }
      xhr.onload = () => {
        const links = [];
        for (const [label, filename, href] of links) {
          const a = document.createElement('a');
          a.textContent = label; a.href = href; a.download = filename;
          entry.links.appendChild(a);
        }
      };
      xhr.onerror = () => {};
      xhr.onabort = () => {};
      if (!entry.localToken) xhr.upload.onload = () => setState(entry, 'processing', 'Whisper + Apple OCR + Qwen3-VL + смысловая постобработка…');
      xhr.send(form);
    });
  }
})();
</script></body></html>
'''
UI_FIXTURE = UI_FIXTURE.replace(
    "COUNT_LINE",
    "count.textContent = " + chr(96) + chr(36) + "{total} " + chr(36) + "{total===1?'видео':'видео'}" + chr(96) + ";",
)
UI_FIXTURE = UI_FIXTURE.replace(
    "META_LINE",
    "card.querySelector('.file-meta').textContent = " + chr(96) + chr(36)
    + "{fmtSize(entry.file.size)} · " + chr(36) + "{entry.file.type || 'видеофайл'}" + chr(96) + ";",
)



class TestUrlBatchIngestion(unittest.TestCase):
    def test_dimension_policy_uses_vlm_and_ocr_settings(self):
        self.assertEqual(patch.auto_download_dimension(1280, "full", False, True, False), 1472)
        self.assertEqual(patch.auto_download_dimension(2048, "full", False, False, False), 2355)
        self.assertEqual(patch.auto_download_dimension(768, "ocr_only", True, True, False), 1472)
        self.assertEqual(patch.auto_download_dimension(1280, "whisper_ocr", True, False, False), 736)

    def test_format_selector_is_strictly_bounded(self):
        selector = patch.download_format_selector(1472)
        self.assertIn("width<=1472", selector)
        self.assertIn("height<=1472", selector)
        self.assertTrue(selector.endswith("]+bestaudio"))
        self.assertFalse(selector.endswith("/best"))

    def test_engine_patch_is_valid_and_async(self):
        updated = patch.patch_engine(ENGINE_FIXTURE)
        import ast
        ast.parse(updated)
        self.assertIn("await asyncio.to_thread(", updated)
        self.assertIn("_vce_auto_download_dimension(", updated)
        self.assertIn("_vce_download_format_selector(max_dimension)", updated)
        self.assertIn('"noplaylist": True', updated)
        self.assertIn('"progress_hooks": [progress_hook]', updated)
        self.assertIn("def _vce_url_download_progress(download_status):", updated)
        self.assertIn("VCE_CANCELLED_BY_USER", updated)
        self.assertIn('set_job_progress(job_id, "download_video"', updated)
        self.assertIn("except yt_dlp.utils.DownloadError as download_error:", updated)
        self.assertIn("HTTPException(409", updated)
        self.assertIn('output_directory = Path.home() / "Downloads"', updated)
        self.assertIn("output_directory.mkdir(parents=True, exist_ok=True)", updated)
        self.assertIn("# VCE_URL_REPORTS_TO_DOWNLOADS_V1", updated)
        self.assertIn("# VCE_STARTUP_STALE_TEMP_CLEANUP_V1", updated)
        self.assertIn("_vce_cleanup_stale_temp_artifacts()", updated)
        self.assertIn("max_age_seconds=3600", updated)
        self.assertIn("vce_ffmpeg_tesseract_", updated)
        self.assertIn("never touch model caches or reports", updated)
        self.assertEqual(patch.patch_engine(updated), updated)


    def test_startup_cleanup_removes_only_old_vce_temp_artifacts(self):
        import ast
        import os
        import tempfile
        import time

        updated = patch.patch_engine(ENGINE_FIXTURE)
        tree = ast.parse(updated)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_vce_cleanup_stale_temp_artifacts"
        )
        module = ast.Module(body=[function], type_ignores=[])
        namespace = {"__file__": str(ROOT / "VideoContextEngine_v3.19.py")}
        exec(compile(module, "<vce-cleanup-test>", "exec"), namespace)
        cleanup = namespace["_vce_cleanup_stale_temp_artifacts"]

        with tempfile.TemporaryDirectory() as workspace:
            project = Path(workspace) / "project"
            temp_root = Path(workspace) / "system-temp"
            project.mkdir()
            temp_root.mkdir()
            now = time.time()
            old_time = now - 7200

            old_media = project / "temp_1700000000_old.mp4"
            old_partial = project / "temp_1700000000_interrupted.webm.part"
            recent_media = project / "temp_1700000000_recent.mp4"
            unrelated_temp = project / "temp_notes.mp4"
            report = project / "lecture_context.json"
            settings = project / "videocontext_settings.json"
            venv_file = project / ".venv" / "bin" / "python"
            model_file = project / "models" / "weights.bin"
            for path in (old_media, old_partial, recent_media, unrelated_temp, report, settings, venv_file, model_file):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test-data")
            for path in (old_media, old_partial, unrelated_temp, report, settings, venv_file, model_file):
                os.utime(path, (old_time, old_time))
            os.utime(recent_media, (now - 60, now - 60))

            orphan_tmp_file = temp_root / "vce_audio_only_orphan.mp4"
            unrelated_tmp_file = temp_root / "unrelated_cache.bin"
            orphan_tmp_file.write_bytes(b"temp")
            unrelated_tmp_file.write_bytes(b"keep")
            orphan_tmp_dir = temp_root / "vce_ffmpeg_tesseract_orphan"
            orphan_tmp_dir.mkdir()
            (orphan_tmp_dir / "frame.jpg").write_bytes(b"frame")
            for path in (orphan_tmp_file, unrelated_tmp_file, orphan_tmp_dir):
                os.utime(path, (old_time, old_time))

            result = cleanup(
                project_root=project,
                temp_root=temp_root,
                max_age_seconds=3600,
                now=now,
            )

            self.assertFalse(old_media.exists())
            self.assertFalse(old_partial.exists())
            self.assertFalse(orphan_tmp_file.exists())
            self.assertFalse(orphan_tmp_dir.exists())
            self.assertTrue(recent_media.exists())
            self.assertTrue(unrelated_temp.exists())
            self.assertTrue(report.exists())
            self.assertTrue(settings.exists())
            self.assertTrue(venv_file.exists())
            self.assertTrue(model_file.exists())
            self.assertTrue(unrelated_tmp_file.exists())
            self.assertEqual(result["removed_project"], 2)
            self.assertEqual(result["removed_system_temp"], 1)
            self.assertEqual(result["removed_temp_dirs"], 1)
            self.assertEqual(result["errors"], 0)

    def test_ui_patch_queues_urls_and_local_files(self):
        updated = patch.patch_ui(UI_FIXTURE)
        self.assertIn('id="urlInput"', updated)
        self.assertIn('id="addUrls"', updated)
        self.assertIn("function addUrls()", updated)
        self.assertIn("form.append('video_url', entry.url)", updated)
        self.assertIn("if (!entry.localToken && !entry.url)", updated)
        self.assertIn("entry.file ? entry.file.name : entry.displayName", updated)
        self.assertIn(": entry.url", updated)
        self.assertIn("form.append('local_video_token', entry.localToken)", updated)
        self.assertIn("else if (entry.url) form.append('video_url', entry.url)", updated)
        self.assertIn("a.target = '_blank'", updated)
        self.assertIn("a.rel = 'noopener'", updated)
        self.assertNotIn("a.download = filename", updated)
        self.assertIn("// VCE_URL_REPORT_DOWNLOAD_TARGET_V1", updated)
        self.assertEqual(patch.patch_ui(updated), updated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
