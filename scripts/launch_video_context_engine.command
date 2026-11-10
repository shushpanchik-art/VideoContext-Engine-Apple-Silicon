#!/bin/bash
set -Eeuo pipefail

APP_PATH="/Applications/VideoContext Engine.app"
INFO_PLIST="${APP_PATH}/Contents/Info.plist"
LOG_FILE="$HOME/Library/Logs/VideoContext Engine/server.log"
HEALTH_URL="http://127.0.0.1:7555/openapi.json"
MAX_WAIT_SECONDS=45

say() { printf '%s\n' "$*"; }
fail() { say "ОШИБКА: $*" >&2; exit 1; }

[[ "$(uname -m)" == "arm64" ]] || fail "Этот launcher предназначен для macOS на Apple Silicon."
[[ -d "$APP_PATH" ]] || fail "Не найдено приложение: $APP_PATH. Сначала установите VideoContext Engine."
[[ -f "$INFO_PLIST" ]] || fail "В приложении отсутствует Contents/Info.plist."

BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$INFO_PLIST" 2>/dev/null || true)
[[ "$BUNDLE_ID" == "local.videocontext.engine.launcher" ]] || fail "Неожиданный bundle id: ${BUNDLE_ID:-пусто}. Приложение не запущено."

say "Запускаю нативное приложение: $APP_PATH"
say "Браузер не открывается. Проверяю локальный API: $HEALTH_URL"

/usr/bin/open -a "$APP_PATH"

for ((second=1; second<=MAX_WAIT_SECONDS; second++)); do
  if /usr/bin/curl --fail --silent --show-error --max-time 2 "$HEALTH_URL" --output /dev/null 2>/dev/null; then
    say "Готово: VideoContext Engine отвечает на 127.0.0.1:7555."
    say "Откройте окно приложения и выберите видео обычной кнопкой выбора файла."
    exit 0
  fi
  /bin/sleep 1
done

say "Приложение было открыто, но API не ответил за ${MAX_WAIT_SECONDS} секунд."
if [[ -f "$LOG_FILE" ]]; then
  say "Последние строки журнала ($LOG_FILE):"
  /usr/bin/tail -n 30 "$LOG_FILE" || true
else
  say "Журнал пока не найден: $LOG_FILE"
fi
exit 3
