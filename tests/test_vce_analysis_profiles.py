#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import ast
import subprocess
import tempfile as tempfile_module
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

PATCH = Path(__file__).with_name('patch_vce_analysis_profiles.py')
spec = importlib.util.spec_from_file_location('vce_analysis_profiles', PATCH)
patch = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(patch)

ENGINE = r'''import re
import os
import time
import asyncio
import shutil
from pathlib import Path
from fastapi import Form, HTTPException

def setting(name, default=None):
    return default

@app.get("/api/v1/ui-settings", include_in_schema=False)
async def get_ui_settings():
    config_path = Path(__file__).with_name("videocontext_settings.json")
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = {
        "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        "whisper_model": "large", "vlm_resolution": 1280,
        "enable_apple_ocr": True, "whisper_acceleration": True,
        "enable_audio_features": True, "generate_summary": True,
        "generate_txt": True, "generate_json": True, "word_timestamps": True,
    }
    return {key: persisted.get(key, defaults[key]) for key in defaults}

@app.post("/api/v1/ui-settings", include_in_schema=False)
async def save_ui_settings(payload: Dict[str, Any]):
    int_ranges = {
        "vlm_resolution": (128, 2048), "keyframes_per_scene": (1, 5),
        "vlm_max_tokens_scene": (16, 1024), "vlm_max_tokens_summary": (16, 2048),
    }
    float_ranges = {"scene_threshold": (0.01, 1.0)}
    bool_keys = {
        "enable_apple_ocr", "whisper_acceleration", "enable_audio_features",
    }
    for key, value in payload.items():
        if key == "vlm_model":
            pass
        elif key == "whisper_model":
            pass
        elif key in int_ranges:
            pass
        elif key in float_ranges:
            pass
        elif key in bool_keys:
            pass
        else:
            raise HTTPException(422, "Unknown setting")
    return {"ok": True}

@app.post("/api/v1/analyze")
async def analyze_video(
    skip_audio: bool = Form(False),
    skip_visual: bool = Form(False),
    enable_apple_ocr: bool = Form(True),
    generate_summary: bool = Form(True),
):
    if isinstance(video_file, str):
        video_file = None
    if not video_file and not video_url.strip():
        raise HTTPException(400, "Missing video")
    request_start_time = time.time()
    whisper_infer_time = 0.0
    vlm_infer_time = 0.0
    whisper_load_time = 0.0
    video_duration = 30.0
    temp_path = "/tmp/input.mp4"
    final_segments = []
    global_summary_text = ""
    scenes_raw = detect_scenes(temp_path, threshold=scene_threshold, min_duration=min_scene_duration, max_duration=max_scene_duration)
    if skip_audio and skip_visual:
        for idx, scene in enumerate(scenes_raw):
            final_segments.append({"scene_id": idx + 1, "start": scene["start"], "end": scene["end"], "audio_transcript": "", "audio_features": {}, "visual_description": "", "visual_tags": {}, "emotion": {}})
    else:
        if not skip_audio:
            audio_res = w_model.transcribe(temp_path)
            whisper_segments = audio_res.get("segments", []) or []
        else:
            whisper_segments = []
        if not skip_visual or generate_summary:
            vlm_engine.load_model(target_vlm)
        cap = cv2.VideoCapture(temp_path)
        first_frame_img = None
        for idx, scene in enumerate(scenes_raw):
            scene_id = idx + 1
            start_s = scene["start"]
            end_s = scene["end"]
            audio_text = transcript_for_scene(scene, whisper_segments) if whisper_segments else ""
            visual_description = ""
            visual_tags = {}
            scene_ocr_text = ""
            if not skip_visual:
                keyframes = sample_keyframes_for_scene(
                    cap, scene, base_res=vlm_resolution, max_frames=keyframes_per_scene
                )
                if keyframes:
                    images = [kf["image"] for kf in keyframes]
                    full_visual_prompt = BASE_VISUAL_PROMPT
                    if enable_apple_ocr:
                        scene_ocr_text = apple_vision_ocr(keyframes[0]["image"])
                    t_vlm_start = time.time()
                    raw = vlm_engine.describe_scene(
                        images,
                        full_visual_prompt,
                        max_tokens=vlm_max_tokens_scene,
                    )
                    t_vlm_end = time.time()
                    vlm_infer_time += t_vlm_end - t_vlm_start
                    data = safe_json_parse(raw)
                    if data and isinstance(data, dict) and "description" in data:
                        visual_description = (data.get("description") or "").strip()
                        tags = data.get("tags") or {}
                        visual_tags = tags if isinstance(tags, dict) else {}
                    else:
                        visual_description = clean_raw_visual_text(raw)
                        visual_tags = {}
            final_segments.append({
                "scene_id": scene_id,
                "start": round(start_s, 2),
                "end": round(end_s, 2),
                "audio_transcript": audio_text,
                "visual_description": visual_description,
                "ocr_text": scene_ocr_text,
                "visual_tags": visual_tags,
            })
        if generate_summary:
            context_lines = []
            for s in final_segments:
                parts = []
                if s.get("audio_transcript"):
                    parts.append(f"Audio: {s['audio_transcript']}")
                vt = s.get("visual_tags") or {}
                if vt:
                    parts.append("Visuel: " + str(vt))
                elif s.get("visual_description"):
                    parts.append(f"Visuel: {s['visual_description']}")
                if s.get("ocr_text"):
                    parts.append(f"Texte à l’écran (Apple OCR): {s['ocr_text']}")
                if parts:
                    context_lines.append("- scene " + " | ".join(parts))
            context_log = "\n".join(context_lines)
            if not skip_visual and first_frame_img is not None:
                t_vlm_start = time.time()
                raw_summary = vlm_engine.describe_scene([first_frame_img], "prompt", max_tokens=500)
                t_vlm_end = time.time()
                vlm_infer_time += t_vlm_end - t_vlm_start
                global_summary_text = raw_summary
            else:
                global_summary_text = context_log[:2000]
    process_duration = time.time() - request_start_time
    params = {"keyframes_per_scene": keyframes_per_scene, "ram_mode": RAM_MODE}
    text_report_content = generate_text_report(source_name, video_duration, final_segments, process_duration, params, global_summary_text)
    timings = {
        "total_process_time": round(process_duration, 3),
        "whisper": {"model": target_whisper, "load_time": round(whisper_load_time, 3), "inference_time": round(whisper_infer_time, 3)},
        "vlm": {"model": target_vlm, "load_time": round(vlm_load_time, 3), "inference_time": round(vlm_infer_time, 3)},
        "ram_mode": RAM_MODE,
    }
    if response_format == "text":
        return PlainTextResponse(content=text_report_content)
    else:
        return {
            "meta": {
                "global_summary": global_summary_text,
                "params": {
                    "keyframes_per_scene": keyframes_per_scene,
                    "enable_audio_features": enable_audio_features,
                    "generate_summary": generate_summary,
                    "ram_mode": RAM_MODE,
                },
                "timings": timings,
            },
            "segments": final_segments,
        }


def run_text_llm_postprocess(payload):
    # Qwen3.5 semantic report post-processing stays independent.
    return payload
'''

UI = '''<style> .settings-grid{} </style>
<section class="panel" id="settings-panel">
      <label class="setting">VLM-модель
    <select id="cfg-vlm-model"></select>
  </label>
  <div class="setting-checks">
      <label><input id="cfg-apple-ocr" type="checkbox" checked> Apple Vision OCR</label>
      <label><input id="cfg-whisper-acceleration" type="checkbox" checked> MPS</label>
  </div>
  <button id="save-settings">Save</button><button id="reload-settings">Reload</button>
</section>
<script>
  const settingsPill = document.getElementById('settings-pill');
  const settingsStatus = document.getElementById('settings-status');
  const saveSettingsButton = document.getElementById('save-settings');
  const readSettings = () => ({
    vlm_model: document.getElementById('cfg-vlm-model').value,
    whisper_model: document.getElementById('cfg-whisper-model').value,
    vlm_resolution: Number(document.getElementById('cfg-vlm-resolution').value),
    enable_apple_ocr: document.getElementById('cfg-apple-ocr').checked,
    whisper_acceleration: document.getElementById('cfg-whisper-acceleration').checked,
  });
  function fillSettings(s) {
    const fields = {
      'cfg-vlm-model':'vlm_model','cfg-whisper-model':'whisper_model','cfg-vlm-resolution':'vlm_resolution',
    };
    for (const [id,key] of Object.entries(fields)) if (s[key] !== undefined) document.getElementById(id).value = s[key];
    const checks = {'cfg-apple-ocr':'enable_apple_ocr','cfg-whisper-acceleration':'whisper_acceleration',
      'cfg-audio-features':'enable_audio_features'};
    for (const [id,key] of Object.entries(checks)) if (s[key] !== undefined) document.getElementById(id).checked = Boolean(s[key]);
  }
  async function loadSettings() {
    const res = await fetch('/api/v1/ui-settings');
    const body = await res.json();
    fillSettings(body); settingsPill.textContent = 'Сохранённые настройки';
  }
  saveSettingsButton.addEventListener('click', async () => {
    try { await fetch('/api/v1/ui-settings', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readSettings())}); }
    finally { saveSettingsButton.disabled = false; }
  });
  document.getElementById('reload-settings').addEventListener('click', loadSettings);
  async function processOne(item) {
      const cfg = readSettings();
      for (const [key, value] of Object.entries(cfg)) form.append(key, String(value));
      xhr.open('POST', '/api/v1/analyze', true);
  }
</script>'''

class TestAnalysisProfiles(unittest.TestCase):
    def test_engine_keeps_qwen35_postprocess_and_guards_vlm(self):
        updated = patch.patch_engine(ENGINE)
        ast.parse(updated)
        self.assertIn('analysis_mode in {"whisper_ocr", "ocr_only"}', updated)
        self.assertIn('if run_vlm:  # VCE_ANALYSIS_PROFILES_VLM_GUARD', updated)
        self.assertIn('if run_vlm and first_frame_img is not None:', updated)
        self.assertIn('run_text_llm_postprocess(payload)', updated)
        self.assertIn('ffmpeg_ocr_available', updated)
        self.assertIn('"ffmpeg_ocr": {', updated)

    def test_qwen35_stage_body_is_not_modified(self):
        updated = patch.patch_engine(ENGINE)
        original_tail = ENGINE[ENGINE.index("def run_text_llm_postprocess(payload):"):]
        updated_tail = updated[updated.index("def run_text_llm_postprocess(payload):"):]
        self.assertEqual(updated_tail, original_tail)

    def test_ffmpeg_ocr_metadata_parser(self):
        def fake_run(args, **kwargs):
            if "-filters" in args:
                return type("Result", (), {"stdout": " T.. ocr V->V Optical Character Recognition\n", "stderr": "", "returncode": 0})()
            vf = next(arg for arg in args if "metadata=mode=print:file=" in arg)
            metadata_file = vf.split("metadata=mode=print:file=", 1)[1]
            Path(metadata_file).write_text(
                "frame:0 pts:0 pts_time:0.000000\nlavfi.ocr.text=Привет\\nмир\nlavfi.ocr.confidence=0.91\n"
                "frame:1 pts:500 pts_time:5.000000\nlavfi.ocr.text=Слайд 2\nlavfi.ocr.confidence=0.83\n",
                encoding="utf-8",
            )
            return type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()
        helper_ns = dict(vars(patch))
        helper_ns.update({"subprocess": subprocess, "tempfile": tempfile_module, "shutil": patch.shutil, "Path": Path, "re": patch.re})
        exec(patch.HELPERS, helper_ns)
        with mock.patch.object(patch.shutil, "which", return_value="/fake/ffmpeg"), mock.patch.object(subprocess, "run", side_effect=fake_run):
            records, reason = helper_ns["_vce_run_ffmpeg_ocr"]("/tmp/input.mp4", 5)
        self.assertEqual(reason, "")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["time"], 0.0)
        self.assertIn("Привет", records[0]["text"])
        self.assertEqual(records[1]["text"], "Слайд 2")

    def test_prepare_dry_run_transforms_all_three_files(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / patch.TARGET).write_text(ENGINE, encoding="utf-8")
            (project / patch.CONFIG).write_text(json.dumps({"custom": True}), encoding="utf-8")
            (project / patch.UI_FILE).write_text(UI, encoding="utf-8")
            changes, originals = patch.prepare(project)
            self.assertEqual(set(changes), {project / patch.TARGET, project / patch.CONFIG, project / patch.UI_FILE})
            ast.parse(changes[project / patch.TARGET])
            json.loads(changes[project / patch.CONFIG])
            self.assertEqual((project / patch.TARGET).read_text(encoding="utf-8"), ENGINE)

    def test_engine_patch_is_idempotent(self):
        once = patch.patch_engine(ENGINE)
        twice = patch.patch_engine(once)
        self.assertEqual(once, twice)

    def test_extended_skip_visual_form_anchor_supported(self):
        variants = [
            '    skip_visual: bool = Form(False, description="Skip visual analysis"),\\n',
            '    skip_visual: bool = Form(bool(setting("skip_visual", False))),\\n',
            '    skip_visual: bool = Form(\\n        False, description="Skip visual analysis"\\n    ),\\n',
        ]
        original = '    skip_visual: bool = Form(False),\\n'
        for variant in variants:
            with self.subTest(variant=variant):
                changed_fixture = ENGINE.replace(original, variant, 1)
                self.assertNotEqual(changed_fixture, ENGINE)
                updated = patch.patch_engine(changed_fixture)
                ast.parse(updated)
                self.assertIn('analysis_mode: str = Form("full"', updated)
                self.assertIn('enable_ffmpeg_ocr: bool = Form(False', updated)
                self.assertIn('ffmpeg_ocr_interval: int = Form(5', updated)

    def test_missing_analysis_skip_parameter_fails_safely(self):
        changed_fixture = ENGINE.replace('    skip_visual: bool = Form(False),\\n', '', 1)
        with self.assertRaisesRegex(ValueError, 'отсутствует параметр skip_visual'):
            patch.patch_engine(changed_fixture)

    def test_settings_routes_add_mode_and_ffmpeg_ocr(self):
        updated = patch.patch_settings_api(ENGINE)
        self.assertIn('"analysis_mode": "full"', updated)
        self.assertIn('"enable_ffmpeg_ocr"', updated)
        self.assertIn('"ffmpeg_ocr_interval": (1, 60)', updated)
        self.assertIn('_vce_ffmpeg_ocr_capability()', updated)

    def test_ui_has_mode_and_optional_ffmpeg_checkbox(self):
        updated = patch.patch_ui(UI)
        self.assertIn('cfg-analysis-mode', updated)
        self.assertIn('cfg-ffmpeg-ocr', updated)
        self.assertIn('Qwen3.5 текстовая постобработка не отключается', updated)
        self.assertIn("form.append('skip_visual', String(cfg.analysis_mode !== 'full'))", updated)
        self.assertEqual(updated.count(patch.UI_MARKER), 1)

    def test_ui_patch_is_idempotent(self):
        once = patch.patch_ui(UI)
        self.assertEqual(once, patch.patch_ui(once))

    def test_settings_json_migration_preserves_existing_preferences(self):
        original = json.dumps({'vlm_model':'custom', 'custom_flag':7})
        updated = json.loads(patch.patch_settings(original))
        self.assertEqual(updated['vlm_model'], 'custom')
        self.assertEqual(updated['custom_flag'], 7)
        self.assertEqual(updated['analysis_mode'], 'full')
        self.assertFalse(updated['enable_ffmpeg_ocr'])

if __name__ == '__main__':
    unittest.main(verbosity=2)

