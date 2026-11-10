#!/bin/bash
# VCE forensic single-run launcher. Selects one local video and records a full
# watchdog + observed-run bundle. It never uploads the video or run artifacts.
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
PY="$ROOT/.venv/bin/python"
WATCHDOG="$ROOT/scripts/vce_runtime_watchdog.py"
RUNNER="$ROOT/scripts/vce_observed_run.py"
SETTINGS="$ROOT/videocontext_settings.json"
OUT="$HOME/Downloads/VCE_forensic_runs"
API="http://127.0.0.1:7555"

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ -x "$PY" ]] || fail "Не найден Python venv: $PY"
[[ -f "$WATCHDOG" && -f "$RUNNER" ]] || fail "Не найдены scripts/vce_runtime_watchdog.py или scripts/vce_observed_run.py."
[[ -f "$SETTINGS" ]] || fail "Не найден локальный videocontext_settings.json: $SETTINGS"

for tool in curl ffmpeg ffprobe tesseract; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%-12s %s\n' "$tool" "$(command -v "$tool")"
  else
    printf '%-12s %s\n' "$tool" "NOT INSTALLED"
  fi
done

printf '\n=== VideoContext Engine — forensic run ===\n'
printf 'Project: %s\nPython: %s\n' "$ROOT" "$("$PY" --version 2>&1)"
mkdir -p "$OUT"

VIDEO="$(/usr/bin/osascript <<'APPLESCRIPT'
try
  set f to choose file with prompt "Выбери видео для forensic-прогона VideoContext Engine"
  return POSIX path of f
on error number -128
  return ""
end try
APPLESCRIPT
)"
[[ -n "$VIDEO" && -f "$VIDEO" ]] || fail "Выбор видео отменён или файл не найден."

/usr/bin/curl -fsS --max-time 4 "$API/openapi.json" >/dev/null || fail "VCE API недоступен на $API. Сначала запусти /Applications/VideoContext Engine.app."

printf '\nВидео: %s\n' "$VIDEO"
printf 'Настройки: %s\n' "$SETTINGS"
printf '\nБудет выполнен один анализ. Настройки и исходное видео не изменяются.\n'
printf 'Результаты останутся локально: %s\n' "$OUT"
printf 'Запускаю watchdog: process sampling, memory, vmmap, sample stacks, GPU power (если разрешён).\n\n'

exec "$PY" "$WATCHDOG" \
  --video "$VIDEO" \
  --settings "$SETTINGS" \
  --runner "$RUNNER" \
  --api-url "$API" \
  --out-root "$OUT" \
  --interval 1.0 \
  --powermetrics \
  --vmmap \
  --sample-stacks
