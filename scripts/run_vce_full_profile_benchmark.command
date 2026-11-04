#!/bin/bash
set -euo pipefail

REPOSITORY="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
SOURCE_REF="9579f659d7543dadcb795dd82187d4ab5573608c"
RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY}/${SOURCE_REF}"
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

echo "VCE benchmark pinned source: ${SOURCE_REF}"
echo "Patcher URL: ${PATCH_URL}"
/usr/bin/curl --fail --location --silent --show-error --retry 3 -H "Cache-Control: no-cache" -H "Pragma: no-cache" "$SCRIPT_URL" --output "$TMP_GZIP"
/usr/bin/gzip -t "$TMP_GZIP"
/usr/bin/curl --fail --location --silent --show-error --retry 3 -H "Cache-Control: no-cache" -H "Pragma: no-cache" "$PATCH_URL" --output "$TMP_PATCH"
"$PYTHON" "$TMP_PATCH" "$TMP_SCRIPT"

"$PYTHON" "$TMP_SCRIPT" --project "$PROJECT_DIR" --video "$VIDEO_DEFAULT" "$@"
