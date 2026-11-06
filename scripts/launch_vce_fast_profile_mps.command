#!/bin/bash
# Safe launcher for the baseline Apple-Silicon VCE profile:
# PyTorch Whisper/MPS -> one shared llama.cpp/Metal Qwen3.5 GGUF server for VLM + Text LLM,
# Apple Vision OCR + OCR cache + FFmpeg/Tesseract OCR.
set -Eeuo pipefail

REPOSITORY="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
PATCHER_REF="5a6a7dd105e4fb758c13a80fb240db0109c09881"
PATCHER_PATH="patches/patch_vce_fast_profile_mps.py"

PROJECT_DIR="${VCE_PROJECT_DIR:-$HOME/VideoContext-Engine-Apple-Silicon}"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/VideoContext Engine fast MPS.log"
PID_FILE="$LOG_DIR/VideoContext Engine fast MPS.pid"
BASE_URL="http://127.0.0.1:7555"

export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:$PATH"
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

[[ "$(uname -s)" == "Darwin" ]] || fail "This launcher requires macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Native Apple Silicon (arm64) is required."
[[ -d "$PROJECT_DIR" ]] || fail "VCE project directory not found: $PROJECT_DIR"
[[ -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" ]] || fail "VideoContextEngine_v3.19.py not found in $PROJECT_DIR"
[[ -f "$PROJECT_DIR/videocontext_web_ui.html" ]] || fail "videocontext_web_ui.html not found in $PROJECT_DIR"
[[ -x "$PYTHON" ]] || fail "Project virtualenv Python not found: $PYTHON. Do not use a system Python for this VCE install."

for tool in curl lsof ffmpeg ffprobe tesseract; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool not found. Install missing tools with Homebrew before starting VCE."
done
command -v llama-server >/dev/null 2>&1 || fail "llama-server not found. Install the Metal-enabled llama.cpp backend with: brew install llama.cpp"

printf '[1/6] Checking PyTorch MPS, llama.cpp Metal, Whisper and Apple Vision dependencies...\n'
"$PYTHON" - <<'PY'
import importlib.metadata
import importlib.util
import platform
import re
import subprocess

required = {
    "torch": "torch",
    "openai-whisper": "whisper",
    "ocrmac": "ocrmac",
}
missing = [dist for dist, module in required.items() if importlib.util.find_spec(module) is None]
if missing:
    raise SystemExit("Missing packages in the project venv: " + ", ".join(missing))

import torch
import whisper
from ocrmac import ocrmac

if platform.system() != "Darwin" or platform.machine().lower() != "arm64":
    raise SystemExit("The current Python process is not native Apple Silicon.")
mps = torch.backends.mps
if not mps.is_built() or not mps.is_available():
    raise SystemExit("PyTorch MPS is not built/available; refusing CPU fallback.")

try:
    device_probe = subprocess.run(
        ["llama-server", "--list-devices"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
except Exception as exc:
    raise SystemExit("Could not inspect llama-server Metal support: " + type(exc).__name__)
device_output = (device_probe.stdout or "") + "\n" + (device_probe.stderr or "")
# llama.cpp 0.7.0 labels Apple Metal devices as MTL0/MTL1 rather than "Metal".
# Accept either the explicit backend name or a Metal device identifier.
has_metal_device = any(
    re.search(r"(?i)(?:\bmetal\b|\bMTL\d+\s*:)", line)
    for line in device_output.splitlines()
)
if device_probe.returncode != 0 or not has_metal_device:
    raise SystemExit(
        "llama-server does not advertise a Metal device. "
        "Install/rebuild llama.cpp with macOS Metal support; CPU fallback is disabled."
    )

try:
    whisper_version = importlib.metadata.version("openai-whisper")
except importlib.metadata.PackageNotFoundError:
    whisper_version = "unknown"

print(
    f"Preflight OK: torch={torch.__version__}; openai-whisper={whisper_version}; "
    "MPS=available; llama.cpp/Metal=available; Apple Vision=importable; MLX=not used"
)
PY

printf '[2/6] Checking FFmpeg/Tesseract OCR and Russian/English language data...\n'
TESS_LANGS="$(tesseract --list-langs 2>&1 || true)"
printf '%s\n' "$TESS_LANGS" | grep -Fxq "rus" || fail "Tesseract language data 'rus' is missing. Install it with: brew install tesseract-lang"
printf '%s\n' "$TESS_LANGS" | grep -Fxq "eng" || fail "Tesseract language data 'eng' is missing. Install it with: brew install tesseract-lang"
ffmpeg -version >/dev/null 2>&1 || fail "FFmpeg did not pass its version check."
ffprobe -version >/dev/null 2>&1 || fail "ffprobe did not pass its version check."
printf 'OCR tools OK: FFmpeg + Tesseract rus+eng.\n'

LISTENER="$(lsof -nP -iTCP:7555 -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 {print $2}' | sort -u || true)"
[[ -z "$LISTENER" ]] || fail "Port 7555 is already occupied by PID(s): $LISTENER. Stop the existing VCE instance first; this launcher will not replace a running server."

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vce_fast_mps_launcher.XXXXXX")"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM
PATCHER="$TMP_DIR/patch_vce_fast_profile_mps.py"
PATCHER_URL="https://raw.githubusercontent.com/$REPOSITORY/$PATCHER_REF/$PATCHER_PATH"

printf '[3/6] Downloading the reviewed, commit-pinned runtime/profile patch...\n'
curl --fail --location --silent --show-error --retry 3 "$PATCHER_URL" -o "$PATCHER" \
  || fail "Could not download the pinned MPS profile patcher."
"$PYTHON" -m py_compile "$PATCHER" || fail "The downloaded patcher did not compile."

printf '[4/6] Applying the fast profile with timestamped backups...\n'
"$PYTHON" "$PATCHER" --project-dir "$PROJECT_DIR" \
  || fail "The patcher refused the current source/settings. Inspect its error; do not start VCE with a partial profile."
"$PYTHON" - "$PROJECT_DIR/VideoContextEngine_v3.19.py" "$PROJECT_DIR/videocontext_web_ui.html" <<'PY'
from pathlib import Path
import ast
import sys

engine_path = Path(sys.argv[1])
ui_path = Path(sys.argv[2])
source = engine_path.read_text(encoding="utf-8")
ui = ui_path.read_text(encoding="utf-8")
ast.parse(source, filename=str(engine_path))

required = (
    "VCE_PYTORCH_WHISPER_MPS_SERIAL_V1",
    "VCE_LLAMA_CPP_METAL_SHARED_MODEL_V1",
    "backend=openai-whisper",
    'whisper.load_model(model_name, device=device)',
    "await _vce_whisper_task",
    '"whisper_backend": "openai-whisper"',
    "class LlamaServerEngine(VLMProvider):",
    "_vce_ensure_llama_server",
    "_vce_llama_chat_completion",
    'LLAMA_VLM_MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M"',
    'LLAMA_TEXT_MODEL_ID = "gguf/Qwen3.5-9B-GGUF:Q4_K_M"',
    "enable_ocr_cache",
    "_vce_cached_apple_vision_ocr",
)
missing = [item for item in required if item not in source]
if missing:
    raise SystemExit("Patched runtime validation failed: " + ", ".join(missing))

tree = ast.parse(source)
bad = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        bad.extend(alias.name for alias in node.names if alias.name == "mlx" or alias.name.startswith(("mlx.", "mlx_vlm", "mlx_whisper")))
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module == "mlx" or module.startswith(("mlx.", "mlx_vlm", "mlx_whisper")):
            bad.append(module)
    elif isinstance(node, ast.Name) and (node.id == "MLXEngine" or node.id.startswith("_vce_mlx")):
        bad.append(node.id)
if bad:
    raise SystemExit("MLX runtime imports/names remain: " + ", ".join(sorted(set(bad))))

legacy = (
    "enable_adaptive_keyframes",
    "enable_mlx_whisper",
    "enable_fast_scene_detection",
    "_VCEWhisperMLXCPU",
    "_vce_detect_scenes_fast",
    "mlx-community/",
)
for removed in legacy:
    if removed in source:
        raise SystemExit("Legacy runtime/model reference remains: " + removed)

ui_required = (
    "VCE_FAST_MPS_LLAMA_CPP_UI_V1",
    "Apple Silicon / llama.cpp Metal",
    'id="cfg-vlm-model" type="text" value="Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M"',
    'id="cfg-text-llm-model" type="text" value="gguf/Qwen3.5-9B-GGUF:Q4_K_M"',
    'id="cfg-ocr-cache"',
    "enable_ocr_cache: document.getElementById('cfg-ocr-cache').checked",
    'id="cfg-apple-ocr"',
    'id="cfg-ffmpeg-ocr"',
    'id="cfg-whisper-acceleration"',
)
ui_missing = [item for item in ui_required if item not in ui]
if ui_missing:
    raise SystemExit("Patched UI validation failed: " + ", ".join(ui_missing))
for removed in ("MLX", "mlx-community/", "mlx_vlm", "cfg-mlx-whisper", "enable_mlx_whisper"):
    if removed in ui:
        raise SystemExit("Legacy MLX control/reference remains in UI: " + removed)

print("Runtime syntax, PyTorch MPS, llama.cpp Metal, and OCR markers: OK")
print("UI llama.cpp model selectors and preserved OCR/Whisper controls: OK")
PY
PY

printf '[5/6] Starting VCE server...\n'
mkdir -p "$LOG_DIR"
nohup "$PYTHON" "$PROJECT_DIR/VideoContextEngine_v3.19.py" >> "$LOG_FILE" 2>&1 < /dev/null &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" > "$PID_FILE"

READY=0
for _ in $(seq 1 45); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  if curl --fail --silent "$BASE_URL/openapi.json" -o "$TMP_DIR/openapi.json"; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" != "1" ]]; then
  kill "$SERVER_PID" 2>/dev/null || true
  sleep 1
  printf '\nLast server log lines:\n' >&2
  tail -n 80 "$LOG_FILE" >&2 || true
  fail "VCE did not become healthy on port 7555. The launcher stopped its server process."
fi

printf '[6/6] Verifying the live API settings before opening the UI...\n'
"$PYTHON" - "$BASE_URL/api/v1/ui-settings" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=10) as response:
    data = json.loads(response.read().decode("utf-8"))

expected = {
    "analysis_mode": "full",
    "vlm_model": "Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M",
    "whisper_model": "large",
    "text_llm_enabled": True,
    "text_llm_model": "gguf/Qwen3.5-9B-GGUF:Q4_K_M",
    "whisper_acceleration": True,
    "enable_apple_ocr": True,
    "enable_ocr_cache": True,
    "enable_ffmpeg_ocr": True,
    "ffmpeg_ocr_interval": 5,
    "ffmpeg_ocr_workers": 3,
    "enable_stage_timing": True,
}
bad = {key: {"expected": value, "actual": data.get(key)} for key, value in expected.items() if data.get(key) != value}
removed = [key for key in (
    "enable_adaptive_keyframes",
    "enable_mlx_whisper",
    "enable_fast_scene_detection",
) if key in data]
if bad or removed:
    print(json.dumps({"mismatched_settings": bad, "experimental_keys_still_exposed": removed}, indent=2), file=sys.stderr)
    raise SystemExit("Live VCE settings do not match the requested profile.")
if not data.get("ffmpeg_ocr_available"):
    raise SystemExit("The live VCE API reports FFmpeg/Tesseract OCR unavailable: " + str(data.get("ffmpeg_ocr_reason")))
print("Live API profile verified:")
for key in expected:
    print(f"  {key}={data[key]}")
print("  ffmpeg_ocr_available=True")
PY

open "$BASE_URL/ui" >/dev/null 2>&1 || true
printf '\nVCE is running.\n'
printf 'UI:      %s/ui\n' "$BASE_URL"
printf 'PID:     %s\n' "$SERVER_PID"
printf 'Log:     %s\n' "$LOG_FILE"
printf 'Stop:    kill %s\n' "$SERVER_PID"
printf '\nThe launcher configures the profile and starts the server only. It does not submit a video for analysis.\n'
