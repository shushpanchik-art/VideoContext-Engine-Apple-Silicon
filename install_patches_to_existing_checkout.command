#!/bin/bash
set -Eeuo pipefail
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${1:-$HOME/Downloads/VideoContext-Engine}"
EXPECTED_PROJECT_DIR="$HOME/Downloads/VideoContext-Engine"
PYTHON="$PROJECT_DIR/.venv/bin/python"

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ "$PROJECT_DIR" == "$EXPECTED_PROJECT_DIR" ]] || fail "This launcher template is configured for $EXPECTED_PROJECT_DIR. Use that project path, or update macOS/start_server.command before installing elsewhere."
[[ -d "$PROJECT_DIR" ]] || fail "Project not found: $PROJECT_DIR"
[[ -x "$PYTHON" ]] || fail "Project virtualenv not found: $PYTHON. Set up the upstream project first."
[[ -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" ]] || fail "Expected VideoContextEngine_v3.19.py is missing. See upstream project README."
[[ -d "$BUNDLE_DIR/patches" ]] || fail "patches directory is missing from this bundle."

cat <<BANNER
VideoContext Engine Apple Silicon patch set
Project: $PROJECT_DIR

IMPORTANT: Run this orchestrator only on a fresh checkout of the matching upstream v3.19.
It is not intended to re-apply the entire series over an already-customized installation.
Timestamped backups are created by the individual patchers.
BANNER
read -r -p "Continue on this checkout? [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || exit 0

for f in "$BUNDLE_DIR"/patches/patch_vce_*.py; do
  cp "$f" "$PROJECT_DIR/$(basename "$f")"
done
cp -R "$BUNDLE_DIR/macos/VideoContext Engine.app" "$PROJECT_DIR/"
cp "$BUNDLE_DIR/macos/install_video_context_mac.command" "$PROJECT_DIR/"
chmod +x "$PROJECT_DIR/install_video_context_mac.command"

cd "$PROJECT_DIR"
run_patch() {
  echo
  echo "==> $1"
  "$PYTHON" "$PROJECT_DIR/$1"
}
run_patch patch_vce_optimization.py
run_patch patch_vce_apple_ocr.py
run_patch patch_vce_defaults.py
run_patch patch_vce_whisper_ocr_fullres_fixed.py
run_patch patch_vce_ui_batch_progress.py
run_patch patch_vce_settings_button.py
run_patch patch_vce_stop_and_mps.py
run_patch patch_vce_save_next_to_video.py

# Apply only the benchmarked performance/runtime fields, preserving the configured prompts.
"$PYTHON" - "$PROJECT_DIR/videocontext_settings.json" "$BUNDLE_DIR/config/videocontext_settings.apple_silicon.example.json" <<'PY'
import json, shutil, sys
from datetime import datetime
from pathlib import Path
current_path = Path(sys.argv[1])
profile_path = Path(sys.argv[2])
current = json.loads(current_path.read_text(encoding='utf-8'))
profile = json.loads(profile_path.read_text(encoding='utf-8'))
keys = [
    'vlm_model', 'whisper_model', 'response_format', 'vlm_resolution',
    'scene_threshold', 'min_scene_duration', 'max_scene_duration', 'analysis_mode',
    'keyframes_per_scene', 'vlm_max_tokens_scene', 'vlm_max_tokens_summary',
    'skip_audio', 'skip_visual', 'generate_txt', 'generate_json',
    'enable_audio_features', 'enable_apple_ocr', 'enable_ffmpeg_ocr', 'ffmpeg_ocr_interval', 'generate_summary',
    'ram_mode', 'mlx_device', 'whisper_acceleration', 'word_timestamps',
    'apple_ocr_recognition_level', 'apple_ocr_languages', 'apple_ocr_confidence'
]
backup = current_path.with_name(current_path.name + '.before_apple_silicon_profile_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.bak')
shutil.copy2(current_path, backup)
for key in keys:
    if key in profile:
        current[key] = profile[key]
current_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Apple Silicon benchmark profile applied. User prompts preserved.')
print('Settings backup:', backup)
PY

echo
bash "$PROJECT_DIR/install_video_context_mac.command"
