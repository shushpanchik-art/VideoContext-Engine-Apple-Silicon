#!/bin/bash
set -euo pipefail

REPOSITORY="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
BRANCH="main"
PROJECT_DIR="${VCE_PROJECT_DIR:-$HOME/VideoContext-Engine}"
BUNDLE_URL="https://raw.githubusercontent.com/${REPOSITORY}/${BRANCH}/patches/vce-recognition-languages-patcher.tar.gz"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Не найдена папка проекта: $PROJECT_DIR" >&2
  echo "Задайте VCE_PROJECT_DIR=/путь/к/VideoContext-Engine и повторите запуск." >&2
  exit 2
fi
if [[ ! -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" || ! -f "$PROJECT_DIR/videocontext_web_ui.html" ]]; then
  echo "В $PROJECT_DIR не найдены runtime и Web UI VideoContext Engine v3.19." >&2
  exit 2
fi

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="${VCE_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
else
  PYTHON_BIN="${VCE_PYTHON:-python3}"
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Не найден Python: $PYTHON_BIN" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vce-recognition-languages.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
BUNDLE_FILE="$TMP_DIR/vce-recognition-languages-patcher.tar.gz"
echo "Скачиваю патчер из GitHub..."
curl --fail --location --silent --show-error "$BUNDLE_URL" --output "$BUNDLE_FILE"
tar -xzf "$BUNDLE_FILE" -C "$TMP_DIR" patch_vce_recognition_languages.py
PATCH_FILE="$TMP_DIR/patch_vce_recognition_languages.py"

echo
echo "=== DRY RUN: проверка совместимости и diff ==="
"$PYTHON_BIN" "$PATCH_FILE" --project "$PROJECT_DIR" --dry-run

echo
echo "=== APPLY: установка патча ==="
"$PYTHON_BIN" "$PATCH_FILE" --project "$PROJECT_DIR" --apply

echo
echo "Готово. Перезапустите VideoContext Engine и проверьте блок языков в настройках."
