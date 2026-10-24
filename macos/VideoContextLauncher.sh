#!/bin/bash
set -eu

APP_CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
START_SCRIPT="$APP_CONTENTS/Resources/start_server.command"
WEBVIEW_BIN="$APP_CONTENTS/MacOS/VideoContextWebView"
UI_URL="http://127.0.0.1:7555/ui"
DOCS_URL="http://127.0.0.1:7555/docs"

launch_native_window() {
  if [[ ! -x "$WEBVIEW_BIN" ]]; then
    /usr/bin/osascript -e 'display dialog "Не найден встроенный компонент отдельного окна VideoContext Engine. Запустите install_native_window_app.command, чтобы обновить приложение." buttons {"OK"} default button 1 with icon stop' >/dev/null 2>&1 || true
    exit 1
  fi
  exec "$WEBVIEW_BIN"
}

# Reuse a running server; never open another Safari/browser tab.
if /usr/bin/curl -fsS "$UI_URL" >/dev/null 2>&1; then
  launch_native_window
fi

# Detect a server on this port that is too old or has no /ui route.
if /usr/bin/curl -fsS "$DOCS_URL" >/dev/null 2>&1; then
  /usr/bin/osascript -e 'display dialog "VideoContext Engine уже запущен, но не отвечает на /ui. Остановите старый сервер в Terminal (Ctrl+C), затем запустите приложение ещё раз." buttons {"Открыть окно", "OK"} default button 1' >/dev/null 2>&1 || true
  launch_native_window
fi

if [[ ! -f "$START_SCRIPT" ]]; then
  /usr/bin/osascript -e 'display dialog "Не найден встроенный скрипт запуска VideoContext Engine." buttons {"OK"} default button 1 with icon stop'
  exit 1
fi

# Start the existing VCE server in Terminal so logs and Ctrl+C remain available.
/usr/bin/osascript - "$START_SCRIPT" <<'APPLESCRIPT'
on run argv
    set scriptPath to item 1 of argv
    tell application "Terminal"
        activate
        do script "bash " & quoted form of scriptPath
    end tell
end run
APPLESCRIPT

for _ in {1..120}; do
  if /usr/bin/curl -fsS "$UI_URL" >/dev/null 2>&1; then
    launch_native_window
  fi
  sleep 1
done

/usr/bin/osascript -e 'display dialog "Команда запуска отправлена в Terminal, но интерфейс не ответил за 120 секунд. Проверьте ошибки в окне Terminal." buttons {"OK"} default button 1 with icon caution' >/dev/null 2>&1 || true
exit 1
