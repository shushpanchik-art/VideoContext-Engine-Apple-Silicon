#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'

APP_PATH="/Applications/VideoContext Engine.app"
APP_BINARY="$APP_PATH/Contents/MacOS/VideoContextStatusBar"
APP_SWIFT="$APP_PATH/Contents/Resources/VideoContextStatusBar.swift"
APP_SUPPORT_DIR="$HOME/Library/Application Support/VideoContext Engine"
PROJECT_PATH_FILE="$APP_SUPPORT_DIR/project_path"
SERVER_LOG="$HOME/Library/Logs/VideoContext Engine/server.log"
UI_URL="http://127.0.0.1:7555/ui"
SWIFT_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/36f5df21912ee2b8cbd9895b14c05180e4e11f10/macos/VideoContext%20Engine.app/Contents/Resources/VideoContextStatusBar.swift"

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }
canonical_dir() { (cd "$1" 2>/dev/null && pwd -P); }
valid_project() {
  [[ -d "$1" && -f "$1/VideoContextEngine_v3.19.py" &&
     -f "$1/videocontext_web_ui.html" && -x "$1/.venv/bin/python" ]]
}

printf '=== VideoContext Engine — запуск нативного приложения ===\n'
printf 'Дата: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
[[ "$(uname -s)" == "Darwin" ]] || fail "Этот запускатель предназначен для macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Ожидается Mac с Apple Silicon (arm64)."
[[ -d "$APP_PATH/Contents" && -x "$APP_BINARY" ]] || fail "Не найдено приложение $APP_PATH."

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"

PROJECT_DIR=""
if [[ -n "${VCE_PROJECT_DIR:-}" ]]; then
  valid_project "$VCE_PROJECT_DIR" || fail "VCE_PROJECT_DIR задан, но runtime, UI или .venv не найдены: $VCE_PROJECT_DIR"
  PROJECT_DIR="$(canonical_dir "$VCE_PROJECT_DIR")"
elif [[ -f "$PROJECT_PATH_FILE" ]]; then
  SAVED_PROJECT="$(/usr/bin/tr -d '\r' < "$PROJECT_PATH_FILE")"
  if valid_project "$SAVED_PROJECT"; then PROJECT_DIR="$(canonical_dir "$SAVED_PROJECT")"; fi
fi

if [[ -z "$PROJECT_DIR" ]]; then
  CANDIDATES=(
    "$HOME/VideoContext-Engine-Apple-Silicon"
    "$HOME/VideoContext-Engine"
    "$HOME/Downloads/VideoContext-Engine-Apple-Silicon"
    "$HOME/Downloads/VideoContext-Engine"
  )
  for candidate in "${CANDIDATES[@]}"; do
    if valid_project "$candidate"; then PROJECT_DIR="$(canonical_dir "$candidate")"; break; fi
  done
fi
[[ -n "$PROJECT_DIR" ]] || fail "Не найдена папка проекта с runtime, Web UI и .venv."
PYTHON="$PROJECT_DIR/.venv/bin/python"
ENGINE="$PROJECT_DIR/VideoContextEngine_v3.19.py"
printf 'Проект: %s\n' "$PROJECT_DIR"
printf 'Python: %s\n' "$("$PYTHON" --version 2>&1)"
command -v ffmpeg >/dev/null 2>&1 || fail "Не найден ffmpeg. Установи: brew install ffmpeg"
command -v ffprobe >/dev/null 2>&1 || fail "Не найден ffprobe. Установи: brew install ffmpeg"
printf 'FFmpeg: %s\nFFprobe: %s\n' "$(command -v ffmpeg)" "$(command -v ffprobe)"

# Persist the exact selected project path for the native app.
/bin/mkdir -p "$APP_SUPPORT_DIR" 2>/dev/null || /bin/mkdir -p "$APP_SUPPORT_DIR"
TMP_PATH_FILE="$PROJECT_PATH_FILE.tmp.$$"
printf '%s\n' "$PROJECT_DIR" > "$TMP_PATH_FILE"
/bin/chmod 600 "$TMP_PATH_FILE"
/bin/mv -f "$TMP_PATH_FILE" "$PROJECT_PATH_FILE"

# If the native app is already open, simply bring that native window forward.
if /usr/bin/pgrep -x VideoContextStatusBar >/dev/null 2>&1; then
  printf '\nНативное приложение уже запущено. Переключаюсь на его окно…\n'
  /usr/bin/open -a "$APP_PATH"
else
  # A healthy existing server must belong to the selected runtime.
  if /usr/bin/curl --silent --fail --max-time 2 "$UI_URL" >/dev/null 2>&1; then
    LISTENER_PIDS="$(/usr/sbin/lsof -tiTCP:7555 -sTCP:LISTEN 2>/dev/null | /usr/bin/sort -u || true)"
    [[ -n "$LISTENER_PIDS" ]] || fail "UI отвечает, но владелец порта 7555 не определён."
    while IFS= read -r pid; do
      [[ -n "$pid" ]] || continue
      LISTENER_COMMAND="$(/bin/ps -ww -p "$pid" -o command= 2>/dev/null || true)"
      [[ "$LISTENER_COMMAND" == *"$ENGINE"* ]] || \
        fail "Порт 7555 принадлежит не выбранному runtime. Ничего не останавливаю. PID=$pid: $LISTENER_COMMAND"
    done <<< "$LISTENER_PIDS"
    printf '\nНайден рабочий сервер этого проекта. Подключу приложение без перезапуска.\n'
  elif /usr/sbin/lsof -nP -iTCP:7555 -sTCP:LISTEN >/dev/null 2>&1; then
    /usr/sbin/lsof -nP -iTCP:7555 -sTCP:LISTEN
    fail "Порт 7555 занят, но UI VCE не отвечает. Чужие процессы не завершаю."
  fi

  # Rebuild the app with safe startup behavior: reuse a healthy backend instead
  # of the old unconditional project-process cleanup.
  command -v xcrun >/dev/null 2>&1 || fail "Нужны Xcode Command Line Tools. Установи: xcode-select --install"
  BUILD_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/vce-native-launch.XXXXXX")"
  trap '/bin/rm -rf "$BUILD_DIR"' EXIT
  printf '\nПолучаю исходник нативной оболочки…\n'
  /usr/bin/curl --fail --location --silent --show-error --retry 3 "$SWIFT_URL" \
    --output "$BUILD_DIR/VideoContextStatusBar.swift"
  /usr/bin/grep -q "VCE_REUSE_HEALTHY_ENGINE_V1" "$BUILD_DIR/VideoContextStatusBar.swift" || \
    fail "Полученный исходник не содержит исправления безопасного запуска."
  printf 'Собираю нативное приложение…\n'
  /usr/bin/xcrun swiftc -O "$BUILD_DIR/VideoContextStatusBar.swift" \
    -framework AppKit -framework WebKit -o "$BUILD_DIR/VideoContextStatusBar"

  if ! /usr/bin/cmp -s "$BUILD_DIR/VideoContextStatusBar" "$APP_BINARY"; then
    STAMP="$(date '+%Y%m%d_%H%M%S')"
    BACKUP_APP="$HOME/Downloads/VideoContext_Engine_app_backup_$STAMP.app"
    printf 'Резервная копия текущей оболочки: %s\n' "$BACKUP_APP"
    /usr/bin/ditto "$APP_PATH" "$BACKUP_APP"
    printf 'Устанавливаю исправление и переподписываю приложение. macOS запросит пароль администратора.\n'
    if ! /usr/bin/sudo /usr/bin/install -m 755 "$BUILD_DIR/VideoContextStatusBar" "$APP_BINARY" ||
       ! /usr/bin/sudo /usr/bin/install -m 644 "$BUILD_DIR/VideoContextStatusBar.swift" "$APP_SWIFT" ||
       ! /usr/bin/sudo /usr/bin/codesign --force --deep --sign - "$APP_PATH"; then
      printf '\nВосстанавливаю прежнее приложение…\n' >&2
      /usr/bin/sudo /bin/rm -rf "$APP_PATH"
      /usr/bin/sudo /usr/bin/ditto "$BACKUP_APP" "$APP_PATH"
      fail "Оболочка восстановлена из резервной копии: $BACKUP_APP"
    fi
    /usr/bin/codesign --verify --deep --strict "$APP_PATH" >/dev/null 2>&1 || \
      fail "Проверка подписи не прошла. Резервная копия: $BACKUP_APP"
    printf 'Резервная копия: %s\n' "$BACKUP_APP"
  else
    printf 'Нативная оболочка уже содержит нужное исправление.\n'
  fi

  printf '\nОткрываю именно /Applications/VideoContext Engine.app…\n'
  /usr/bin/open -a "$APP_PATH"
fi

# The UI is rendered inside the native app's WKWebView. Never open UI_URL in a browser.
READY=0
for _ in {1..60}; do
  if /usr/bin/curl --silent --fail --max-time 2 "$UI_URL" >/dev/null 2>&1; then READY=1; break; fi
  sleep 1
done
if [[ "$READY" == "1" ]]; then
  printf '\nГОТОВО: нативное приложение открыто:\n%s\n' "$APP_PATH"
  printf 'Интерфейс отображается внутри окна VideoContext Engine; отдельный браузер не запускался.\n'
  printf 'Проект: %s\nЖурнал: %s\n' "$PROJECT_DIR" "$SERVER_LOG"
  printf 'Для выхода закрой окно приложения или выбери выход в его меню.\n'
  exit 0
fi

printf '\nСервер не подтвердил готовность за 60 секунд.\n' >&2
if [[ -f "$SERVER_LOG" ]]; then /usr/bin/tail -n 100 "$SERVER_LOG" >&2; fi
fail "Проверь журнал. Исходный runtime и настройки не изменялись."
