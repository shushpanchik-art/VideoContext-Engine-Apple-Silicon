#!/bin/bash
# One-video forensic runner for the NSR Specification MP4.
# Runs only the native macOS app/API; never opens a browser.
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="/Users/artemijvisnevskij/VideoContext-Engine-Apple-Silicon"
VIDEO="/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"
APP="/Applications/VideoContext Engine.app"
API="http://127.0.0.1:7555"
SETTINGS="$ROOT/videocontext_settings.json"
PY="$ROOT/.venv/bin/python"
TOOLS_COMMIT="da8f1c0d22b4f97e21da4d6d3cb039520f95e633"
TOOLS_DIR="$HOME/Library/Application Support/VideoContext Engine/forensic-tools/$TOOLS_COMMIT"
OUT_PARENT="$HOME/Downloads/VCE_forensic_runs_NSRSPEC"
STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_ROOT="$OUT_PARENT/$STAMP"
WATCHDOG=""
RUN_EXIT=0

# Dropbox upload configuration. Set VCE_DROPBOX_REMOTE if your rclone alias differs.
DROPBOX_REMOTE="${VCE_DROPBOX_REMOTE:-dropbox}"
DROPBOX_BASE="${VCE_DROPBOX_BASE:-20261104_224554_1f098d/VCE_Forensic_Runs}"

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 2; }
note() { printf '\n[VCE forensic] %s\n' "$*"; }

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ -d "$APP" ]] || fail "Не найдено приложение: $APP"
[[ -f "$VIDEO" ]] || fail "Не найден видеофайл: $VIDEO"
[[ -x "$PY" ]] || fail "Не найден Python проекта: $PY"
[[ -f "$SETTINGS" ]] || fail "Не найден файл настроек: $SETTINGS"
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git (нужен для проверки целостности загруженных скриптов)."
command -v ffprobe >/dev/null 2>&1 || fail "Не найден ffprobe. Установи FFmpeg и повтори запуск."

mkdir -p "$TOOLS_DIR" "$OUT_ROOT"
note "Проверяю и загружаю закреплённую версию диагностических скриптов из GitHub."
BASE_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$TOOLS_COMMIT/scripts"
curl -fsSL "$BASE_URL/vce_runtime_watchdog.py" -o "$TOOLS_DIR/vce_runtime_watchdog.py"
curl -fsSL "$BASE_URL/vce_observed_run.py" -o "$TOOLS_DIR/vce_observed_run.py"

# Verify the downloaded files against the Git blob IDs returned by GitHub.
WATCHDOG_BLOB="$(git hash-object "$TOOLS_DIR/vce_runtime_watchdog.py")"
RUNNER_BLOB="$(git hash-object "$TOOLS_DIR/vce_observed_run.py")"
[[ "$WATCHDOG_BLOB" == "277454bfe54df801ae879dfe1a925d4344431df9" ]] || fail "Проверка целостности watchdog не прошла."
[[ "$RUNNER_BLOB" == "1f69f948b1a2623aa85d64883a036ed189990f3e" ]] || fail "Проверка целостности observed runner не прошла."

note "Открываю только нативное приложение VideoContext Engine."
open -a "$APP"

note "Жду локальный API $API (до 120 секунд)."
API_READY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "$API/openapi.json" >/dev/null 2>&1; then API_READY=1; break; fi
  sleep 2
done
[[ "$API_READY" == "1" ]] || fail "Локальный API не поднялся. Проверь приложение и лог ~/Library/Logs/VideoContext Engine/server.log."

note "Предварительная проверка завершена. Запускаю один анализ указанного видео."
printf 'Видео: %s\n' "$VIDEO"
printf 'Размер: %s bytes\n' "$(stat -f '%z' "$VIDEO")"
printf 'Python: %s\n' "$("$PY" --version 2>&1)"
printf 'Настройки: %s\n' "$SETTINGS"
printf 'Логи сервера: %s\n' "$HOME/Library/Logs/VideoContext Engine/server.log"
printf 'Результаты: %s\n' "$OUT_ROOT"
printf '\nВо время прогона не перезапускай приложение и не запускай второй анализ.\n'

set +e
"$PY" "$TOOLS_DIR/vce_runtime_watchdog.py" \
  --video "$VIDEO" \
  --settings "$SETTINGS" \
  --runner "$TOOLS_DIR/vce_observed_run.py" \
  --api-url "$API" \
  --out-root "$OUT_ROOT" \
  --interval 1.0 \
  --powermetrics \
  --vmmap \
  --sample-stacks
RUN_EXIT=$?
set -e

WATCHDOG="$(find "$OUT_ROOT" -maxdepth 1 -type d -name 'watchdog_*' -print | sort | tail -n 1)"
if [[ -z "$WATCHDOG" || ! -d "$WATCHDOG" ]]; then
  note "Не найдена папка watchdog-результатов. Сохраняю локальный лог ошибки."
  printf 'watchdog_exit_code=%s\nvideo=%s\n' "$RUN_EXIT" "$VIDEO" > "$OUT_ROOT/launcher_failure.txt"
  exit "$RUN_EXIT"
fi

ARCHIVE="$OUT_PARENT/VCE_NSRSPEC_$STAMP.zip"
note "Создаю единый ZIP-архив полного forensic-прогона."
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$WATCHDOG" "$ARCHIVE"
printf '\nЛокальный архив: %s\n' "$ARCHIVE"
printf 'Размер архива: %s bytes\n' "$(stat -f '%z' "$ARCHIVE")"

if command -v rclone >/dev/null 2>&1 && rclone listremotes | grep -Fxq "$DROPBOX_REMOTE:"; then
  DEST="$DROPBOX_REMOTE:$DROPBOX_BASE"
  note "Загружаю полный архив в Dropbox: $DEST/"
  if rclone mkdir "$DEST" && rclone copyto "$ARCHIVE" "$DEST/$(basename "$ARCHIVE")" --progress; then
    printf '\nDROPBOX_UPLOAD=SUCCESS\nDROPBOX_PATH=%s/%s\n' "$DEST" "$(basename "$ARCHIVE")"
  else
    printf '\nDROPBOX_UPLOAD=FAILED\nАрхив сохранён локально: %s\n' "$ARCHIVE"
  fi
else
  printf '\nDROPBOX_UPLOAD=NOT_CONFIGURED\n'
  printf 'Для автоматической выгрузки установи rclone, выполни rclone config и создай Dropbox remote с именем "%s".\n' "$DROPBOX_REMOTE"
  printf 'После настройки повторно запусти этот же скрипт. Текущий архив уже сохранён локально: %s\n' "$ARCHIVE"
fi

printf '\n=== ГОТОВО ===\n'
printf 'Код анализа: %s\n' "$RUN_EXIT"
printf 'Папка диагностики: %s\n' "$WATCHDOG"
printf 'Архив: %s\n' "$ARCHIVE"
printf 'Исходное видео никуда не загружалось. Полный response/log archive предназначен только для приватного Dropbox.\n'
exit "$RUN_EXIT"
