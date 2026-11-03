#!/bin/bash
# Repair the installed native VideoContext Engine.app without touching engine settings.
set -Eeuo pipefail

APP_TARGET="/Applications/VideoContext Engine.app"
RESOURCE_DIR="$APP_TARGET/Contents/Resources"
BINARY="$APP_TARGET/Contents/MacOS/VideoContextStatusBar"
SWIFT_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/main/macos/VideoContext%20Engine.app/Contents/Resources/VideoContextStatusBar.swift"
START_SERVER_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/main/macos/VideoContext%20Engine.app/Contents/Resources/start_server.command"
STAMP="$(date +%Y%m%d_%H%M%S)"
TMP_DIR="$(/usr/bin/mktemp -d)"
cleanup() { /bin/rm -rf "$TMP_DIR"; }
trap cleanup EXIT

fail() {
  echo
  echo "ОШИБКА: $*"
  echo "Приложение и настройки анализа не были намеренно сброшены."
  exit 1
}

[[ -d "$APP_TARGET/Contents" ]] || fail "Не найдено $APP_TARGET."
[[ -d "$RESOURCE_DIR" ]] || fail "В приложении отсутствует Contents/Resources."
[[ -f "$RESOURCE_DIR/start_server.command" ]] || fail "В приложении отсутствует start_server.command."
command -v xcrun >/dev/null 2>&1 || fail "Не найден swiftc. Установи Xcode Command Line Tools."
command -v curl >/dev/null 2>&1 || fail "Не найден curl."

if /usr/sbin/lsof -nP -iTCP:7555 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Порт 7555 занят. После установки новая оболочка завершит старые процессы этого проекта."
  echo "Не продолжай, пока активный анализ не остановлен или не сохранён."
  read -r -p "Если активной обработки сейчас НЕТ и старый сервер можно завершить, введи NO_ACTIVE_ANALYSIS: " IDLE_CONFIRM
  [[ "$IDLE_CONFIRM" == "NO_ACTIVE_ANALYSIS" ]] || fail "Порт занят; подтверждение не получено. Ничего не менялось."
fi

if /usr/bin/pgrep -f '/Applications/VideoContext Engine.app/Contents/MacOS/VideoContextStatusBar' >/dev/null 2>&1; then
  fail "Сначала полностью закрой VideoContext Engine.app (Cmd+Q), затем повтори установку."
fi

echo "Обновляется только нативная оболочка приложения."
echo "Настройки VCE, выбор TXT/JSON и результаты анализа не меняются."
echo "Введи INSTALL для подтверждения:"
read -r -p "> " CONFIRM
[[ "$CONFIRM" == "INSTALL" ]] || { echo "Отменено; ничего не менялось."; exit 0; }

echo "[1/6] Загрузка кода оболочки и управляемого launcher…"
curl -fL --retry 3 "$SWIFT_URL" -o "$TMP_DIR/VideoContextStatusBar.swift" || fail "Не удалось загрузить Swift-код."
curl -fL --retry 3 "$START_SERVER_URL" -o "$TMP_DIR/start_server.command" || fail "Не удалось загрузить start_server.command."
grep -q "VCE_MANAGED_ENGINE_PROCESS_LIFECYCLE_V1" "$TMP_DIR/VideoContextStatusBar.swift" || fail "Swift-код не содержит ожидаемый lifecycle marker."
bash -n "$TMP_DIR/start_server.command" || fail "Новый start_server.command не прошёл bash -n."
grep -q 'exec "$PYTHON" "$ENGINE"' "$TMP_DIR/start_server.command" || fail "Launcher не передаёт PID Python-серверу через exec."

echo "[2/6] Сборка нативного приложения…"
xcrun swiftc -O "$TMP_DIR/VideoContextStatusBar.swift" \
  -framework AppKit -framework WebKit \
  -o "$TMP_DIR/VideoContextStatusBar" || fail "Сборка Swift завершилась ошибкой."

echo "[3/6] Резервная копия текущей оболочки и launcher…"
BACKUP_DIR="$HOME/Desktop/VideoContext Engine native backup $STAMP"
mkdir -p "$BACKUP_DIR"
if [[ -f "$BINARY" ]]; then cp -p "$BINARY" "$BACKUP_DIR/VideoContextStatusBar.old"; fi
if [[ -f "$RESOURCE_DIR/VideoContextStatusBar.swift" ]]; then cp -p "$RESOURCE_DIR/VideoContextStatusBar.swift" "$BACKUP_DIR/VideoContextStatusBar.swift.old"; fi
if [[ -f "$RESOURCE_DIR/start_server.command" ]]; then cp -p "$RESOURCE_DIR/start_server.command" "$BACKUP_DIR/start_server.command.old"; fi

echo "[4/6] Установка новой оболочки и launcher…"
sudo install -m 755 "$TMP_DIR/VideoContextStatusBar" "$BINARY" || fail "Не удалось установить бинарный файл."
sudo install -m 644 "$TMP_DIR/VideoContextStatusBar.swift" "$RESOURCE_DIR/VideoContextStatusBar.swift" || fail "Не удалось установить Swift-код."
sudo install -m 755 "$TMP_DIR/start_server.command" "$RESOURCE_DIR/start_server.command" || fail "Не удалось установить start_server.command."
sudo /usr/libexec/PlistBuddy -c "Set :CFBundleExecutable VideoContextStatusBar" "$APP_TARGET/Contents/Info.plist"
sudo /usr/libexec/PlistBuddy -c "Set :LSUIElement false" "$APP_TARGET/Contents/Info.plist"
sudo /usr/bin/codesign --force --deep --sign - "$APP_TARGET" || fail "Не удалось переподписать приложение."

echo "[5/6] Подпись приложения и регистрация…"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_TARGET" >/dev/null 2>&1 || true
echo "[6/6] Запуск приложения и очистка старых процессов…"
open "$APP_TARGET"

echo
echo "✅ Готово. При закрытии приложения сервер будет завершаться."
echo "При следующем запуске приложение сначала очистит осиротевшие процессы этого проекта."
echo "Журнал сервера: $HOME/Library/Logs/VideoContext Engine/server.log"
echo "Резервная копия оболочки и launcher: $BACKUP_DIR"
echo "Настройки и исходный Python-файл движка не изменялись."
