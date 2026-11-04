#!/bin/bash
set -euo pipefail

REPOSITORY="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY}/main"
SCRIPT_URL="${RAW_BASE}/scripts/vce_full_profile_benchmark.py.gz"
PATCH_URL="${RAW_BASE}/scripts/patch_vce_full_profile_benchmark.py"
VIDEO_DEFAULT="${VCE_VIDEO_PATH:-$HOME/Downloads/video.mp4}"
PROJECT_DIR="${VCE_PROJECT_DIR:-$HOME/VideoContext-Engine}"

TMP_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/vce_full_profile_benchmark.XXXXXX")"
TMP_GZIP="${TMP_DIR}/runner.py.gz"
TMP_SCRIPT="${TMP_DIR}/runner.py"
TMP_PATCH="${TMP_DIR}/patch_runner.py"
cleanup() { /bin/rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM

/usr/bin/curl --fail --location --silent --show-error --retry 3 "$SCRIPT_URL" --output "$TMP_GZIP"
/usr/bin/gzip -dc "$TMP_GZIP" > "$TMP_SCRIPT"

PYTHON="${PROJECT_DIR}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "ERROR: не найден Python 3.10+. Установите проект VCE по README или задайте VCE_PROJECT_DIR." >&2
  exit 2
fi

/usr/bin/curl --fail --location --silent --show-error --retry 3 "$PATCH_URL" --output "$TMP_PATCH"
"$PYTHON" "$TMP_PATCH" "$TMP_SCRIPT"

"$PYTHON" "$TMP_SCRIPT" --project "$PROJECT_DIR" --video "$VIDEO_DEFAULT" "$@"
