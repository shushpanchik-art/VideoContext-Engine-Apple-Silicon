#!/bin/bash
# Isolated PyTorch/openai-whisper MPS smoke test. Does not start VCE or import MLX.
# This validates the Whisper MPS stack independently; it does not validate VCE integration.
set -Eeuo pipefail

VIDEO_PATH="${VCE_VIDEO_PATH:-}"
PROJECT_DIR="${VCE_PROJECT_DIR:-}"
MODEL="${VCE_WHISPER_MODEL:-large}"
CLIP_SECONDS="${VCE_WHISPER_SMOKE_SECONDS:-20}"
START_SECONDS="${VCE_WHISPER_START_SECONDS:-0}"
RESULT_ROOT="${VCE_WHISPER_RESULT_ROOT:-$HOME/Downloads/VCE_Whisper_MPS_Smoke}"
FP16="${VCE_WHISPER_FP16:-1}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

[[ "$(uname -s)" == "Darwin" ]] || fail "This smoke test requires macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon (arm64) is required for the MPS test."
[[ -n "$VIDEO_PATH" ]] || fail "Set VCE_VIDEO_PATH to a local video containing audio."
[[ -f "$VIDEO_PATH" ]] || fail "The input video was not found."
[[ "$CLIP_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "VCE_WHISPER_SMOKE_SECONDS must be a positive number."
[[ "$START_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "VCE_WHISPER_START_SECONDS must be zero or a positive number."
[[ "$FP16" == "0" || "$FP16" == "1" ]] || fail "VCE_WHISPER_FP16 must be 0 or 1."

if [[ -z "$PROJECT_DIR" ]]; then
  for candidate in "$HOME/VideoContext-Engine" "$HOME/VideoContext-Engine-Apple-Silicon"; do
    if [[ -x "$candidate/.venv/bin/python" ]]; then PROJECT_DIR="$candidate"; break; fi
  done
fi
PYTHON="${PROJECT_DIR:+$PROJECT_DIR/.venv/bin/python}"
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then PYTHON="$(command -v python3 || true)"; fi
[[ -n "$PYTHON" && -x "$PYTHON" ]] || fail "Python 3 was not found. Set VCE_PROJECT_DIR or install Python 3."

command -v ffmpeg >/dev/null 2>&1 || fail "FFmpeg is required. Install it with: brew install ffmpeg"
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe is required (it is included with FFmpeg)."

# Require the actual openai-whisper distribution and an available MPS backend.
# The test intentionally never imports mlx or mlx_whisper and never falls back to CPU.
"$PYTHON" - "$MODEL" <<'PY'
import importlib.metadata
import sys

try:
    import torch
except Exception as exc:
    raise SystemExit(f"PyTorch is unavailable in this Python environment: {type(exc).__name__}")
try:
    import whisper
except Exception as exc:
    raise SystemExit(f"openai-whisper is unavailable; install it with '{sys.executable} -m pip install openai-whisper' ({type(exc).__name__}).")

try:
    version = importlib.metadata.version("openai-whisper")
except importlib.metadata.PackageNotFoundError:
    raise SystemExit("The 'openai-whisper' package is missing; install it in this Python environment.")

if not hasattr(whisper, "load_model"):
    raise SystemExit("Imported 'whisper' module is not the expected openai-whisper package.")
if not torch.backends.mps.is_built() or not torch.backends.mps.is_available():
    raise SystemExit("PyTorch MPS is not built/available in this Python environment; refusing CPU fallback.")
if any(name == "mlx" or name.startswith("mlx.") or name == "mlx_whisper" or name.startswith("mlx_whisper.") for name in sys.modules):
    raise SystemExit("An MLX module was imported unexpectedly; refusing this supposedly MLX-free test.")
print(f"Preflight: torch={torch.__version__}; openai-whisper={version}; MPS=available; MLX imports=none")
PY

AUDIO_STREAM="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of default=noprint_wrappers=1:nokey=1 "$VIDEO_PATH" 2>/dev/null || true)"
[[ "$AUDIO_STREAM" == "audio" ]] || fail "No audio stream was found in the input video."

TMP_BASE="${TMPDIR:-/tmp}"
TMP_DIR="$(mktemp -d "$TMP_BASE/vce_whisper_mps.XXXXXX")"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM
AUDIO_FILE="$TMP_DIR/smoke-audio.wav"
ffmpeg -nostdin -hide_banner -loglevel error -ss "$START_SECONDS" -i "$VIDEO_PATH" \
  -t "$CLIP_SECONDS" -map 0:a:0 -vn -ac 1 -ar 16000 -c:a pcm_s16le "$AUDIO_FILE" \
  || fail "Could not extract the requested audio clip. Check the start offset and input media."
[[ -s "$AUDIO_FILE" ]] || fail "The extracted audio clip is empty."

mkdir -p "$RESULT_ROOT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$RESULT_ROOT/${STAMP}-$$"
mkdir -p "$RUN_DIR"
SUMMARY="$RUN_DIR/summary.json"

"$PYTHON" - "$MODEL" "$AUDIO_FILE" "$SUMMARY" "$FP16" <<'PY'
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path

model_name, audio_path, summary_path, fp16_text = sys.argv[1:]
fp16 = fp16_text == "1"

try:
    import torch
    import whisper

    if not torch.backends.mps.is_available():
        raise RuntimeError("PyTorch MPS is unavailable")
    if any(name == "mlx" or name.startswith("mlx.") or name == "mlx_whisper" or name.startswith("mlx_whisper.") for name in sys.modules):
        raise RuntimeError("MLX was imported; refusing to continue")

    if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
        torch.mps.synchronize()
    load_start = time.perf_counter()
    model = whisper.load_model(model_name, device="mps")
    if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
        torch.mps.synchronize()
    load_seconds = time.perf_counter() - load_start

    parameter = next(model.parameters(), None)
    actual_device = str(parameter.device) if parameter is not None else str(getattr(model, "device", "unknown"))
    if not actual_device.startswith("mps"):
        raise RuntimeError(f"Whisper model did not land on MPS (observed device type: {actual_device.split(':', 1)[0]})")

    inference_start = time.perf_counter()
    result = model.transcribe(
        audio_path,
        verbose=False,
        fp16=fp16,
        word_timestamps=True,
    )
    if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
        torch.mps.synchronize()
    inference_seconds = time.perf_counter() - inference_start

    segments = result.get("segments", []) or []
    words = sum(len(segment.get("words", []) or []) for segment in segments)
    summary = {
        "status": "completed",
        "backend": "openai-whisper",
        "device": "mps",
        "model": model_name,
        "torch_version": torch.__version__,
        "openai_whisper_version": importlib.metadata.version("openai-whisper"),
        "machine": platform.machine(),
        "macOS": platform.mac_ver()[0],
        "fp16": fp16,
        "word_timestamps": True,
        "load_seconds": round(load_seconds, 3),
        "inference_seconds": round(inference_seconds, 3),
        "segments": len(segments),
        "word_count": words,
        "mlx_imported": False,
    }
    Path(summary_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Whisper MPS smoke: COMPLETED")
    print(f"backend={summary['backend']} device={summary['device']} model={model_name} MLX=not-imported")
    print(f"load={summary['load_seconds']:.3f}s inference={summary['inference_seconds']:.3f}s words={words}")
except Exception as exc:
    failure = {
        "status": "failed",
        "backend": "openai-whisper",
        "device_requested": "mps",
        "model": model_name,
        "error_type": type(exc).__name__,
        "mlx_imported": any(name == "mlx" or name.startswith("mlx.") or name == "mlx_whisper" or name.startswith("mlx_whisper.") for name in sys.modules),
    }
    Path(summary_path).write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Whisper MPS smoke: FAILED ({type(exc).__name__}); no transcript was written.", file=sys.stderr)
    raise SystemExit(1)
PY

printf '\nLocal summary: %s\n' "$SUMMARY"
printf 'No transcript, source media, or VCE settings were saved or published.\n'
printf 'This test does not launch VCE and does not prove the VCE runtime is integrated with PyTorch/MPS.\n'
