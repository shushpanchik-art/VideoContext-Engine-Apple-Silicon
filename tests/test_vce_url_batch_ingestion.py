#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "patches"))
import patch_vce_url_batch_ingestion as patch


ENGINE_FIXTURE = '''import asyncio

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
    if video_url.strip():
        clean_title = "fixture"
        try:
            temp_path = download_video_from_url(
                video_url,
                f"temp_{int(time.time())}_{clean_title}.%(ext)s",
            )
        except ValueError:
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
  const alertEl = document.getElementById('alert');
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
  function setAlert(x) { alertEl.textContent = x; }
  function setState(entry, state, msg) { entry.state = state; }
  function addFiles(fileList) {}
  choose.addEventListener('click', () => picker.click());
  add.addEventListener('click', () => picker.click());
  picker.addEventListener('change', e => addFiles(e.target.files));

  function processOne(entry) {
    return new Promise(resolve => {
      const jobId = 'fixture';
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
      };
      xhr.onload = () => {};
      xhr.onerror = () => {};
      xhr.onabort = () => {};
      xhr.upload.onload = () => setState(entry, 'processing', 'Анализ Whisper + Apple OCR + Qwen3-VL…');
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
        self.assertEqual(patch.patch_engine(updated), updated)

    def test_ui_patch_queues_urls_and_local_files(self):
        updated = patch.patch_ui(UI_FIXTURE)
        self.assertIn('id="urlInput"', updated)
        self.assertIn('id="addUrls"', updated)
        self.assertIn("function addUrls()", updated)
        self.assertIn("form.append('video_url', entry.url)", updated)
        self.assertIn("if (!entry.file) return;", updated)
        self.assertIn("entry.file ? entry.file.name : entry.displayName", updated)
        self.assertEqual(patch.patch_ui(updated), updated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
