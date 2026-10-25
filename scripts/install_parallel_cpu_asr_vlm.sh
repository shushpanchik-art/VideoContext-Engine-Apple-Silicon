#!/bin/bash
set -euo pipefail

# Download and run the parallel CPU Whisper + GPU VLM patcher from this repository.
# Usage:
#   bash install_parallel_cpu_asr_vlm.sh --dry-run
#   bash install_parallel_cpu_asr_vlm.sh --apply

MODE="\${1:---dry-run}"
case "$MODE" in
  --dry-run|--apply) ;;
  *) echo "Usage: $0 [--dry-run|--apply]" >&2; exit 2 ;;
esac

PROJECT="\${VCE_PROJECT_DIR:-$HOME/VideoContext-Engine}"
PYTHON="$PROJECT/.venv/bin/python"
ENGINE="$PROJECT/VideoContextEngine_v3.19.py"
SETTINGS="$PROJECT/videocontext_settings.json"
RAW_BASE="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/fix/vce-metal-memory-media-recovery-20261025"
PATCH_URL="$RAW_BASE/patches/patch_vce_parallel_cpu_asr_vlm.py"

if [[ ! -x "$PYTHON" || ! -f "$ENGINE" || ! -f "$SETTINGS" ]]; then
  echo "ERROR: не найден VCE project или его .venv: $PROJECT" >&2
  echo "Задайте VCE_PROJECT_DIR, если проект находится в другом месте." >&2
  exit 2
fi

if [[ "$MODE" == "--apply" ]] && command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:7555 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: порт 7555 занят. Сначала остановите VideoContext Engine и повторите." >&2
  exit 3
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
PATCH="$TMP_DIR/patch_vce_parallel_cpu_asr_vlm.py"

echo "==> Загружаю патч из GitHub"
curl -fsSL --retry 3 "$PATCH_URL" -o "$PATCH"
"$PYTHON" -m py_compile "$PATCH"

echo "==> Проверяю совместимость и показываю diff"
"$PYTHON" "$PATCH" --project "$PROJECT" --dry-run

if [[ "$MODE" == "--dry-run" ]]; then
  echo
  echo "DRY RUN завершён; файлы не изменены."
  echo "После остановки сервера запустите этот скрипт с --apply для установки."
  exit 0
fi

echo
echo "==> Применяю патч (создаётся резервная копия)"
"$PYTHON" "$PATCH" --project "$PROJECT"
"$PYTHON" -m py_compile "$ENGINE"
"$PYTHON" -m json.tool "$SETTINGS" >/dev/null

echo
echo "Установка и проверки завершены."
echo "Настройки: CPU Whisper, MLX Whisper выключен, тайминги включены."
echo "Теперь перезапустите VideoContext Engine и протестируйте короткое видео."
