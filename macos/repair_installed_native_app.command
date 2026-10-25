#!/bin/bash
# Repair the installed native VideoContext Engine.app without touching engine settings.
set -Eeuo pipefail

APP_TARGET="/Applications/VideoContext Engine.app"
RESOURCE_DIR="$APP_TARGET/Contents/Resources"
BINARY="$APP_TARGET/Contents/MacOS/VideoContextStatusBar"
SWIFT_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/fix/vce-speed-profile-and-menubar-20261024/macos/VideoContext%20Engine.app/Contents/Resources/VideoContextStatusBar.swift"
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
  fail "Сервер ещё слушает порт 7555. Сначала дождись окончания анализа и останови его (Ctrl+C в старом Terminal). Я не буду прерывать задачу."
fi

if /usr/bin/pgrep -x VideoContextStatusBar >/dev/null 2>&1; then
  fail "Сначала полностью закрой VideoContext Engine.app (Cmd+Q), затем повтори установку."
fi

echo "Обновляется только нативная оболочка приложения."
echo "Настройки VCE, выбор TXT/JSON и результаты анализа не меняются."
echo "Введи INSTALL для подтверждения:"
read -r -p "> " CONFIRM
[[ "$CONFIRM" == "INSTALL" ]] || { echo "Отменено; ничего не менялось."; exit 0; }

echo "[1/5] Загрузка исправленного Swift-кода…"
curl -fL --retry 3 "$SWIFT_URL" -o "$TMP_DIR/VideoContextStatusBar.swift" || fail "Не удалось загрузить Swift-код."

echo "[2/5] Сборка нативного приложения…"
xcrun swiftc -O "$TMP_DIR/VideoContextStatusBar.swift" \
  -framework AppKit -framework WebKit \
  -o "$TMP_DIR/VideoContextStatusBar" || fail "Сборка Swift завершилась ошибкой."

echo "[3/5] Резервная копия текущей оболочки…"
BACKUP_DIR="$HOME/Desktop/VideoContext Engine native backup $STAMP"
mkdir -p "$BACKUP_DIR"
if [[ -f "$BINARY" ]]; then cp -p "$BINARY" "$BACKUP_DIR/VideoContextStatusBar.old"; fi
if [[ -f "$RESOURCE_DIR/VideoContextStatusBar.swift" ]]; then cp -p "$RESOURCE_DIR/VideoContextStatusBar.swift" "$BACKUP_DIR/VideoContextStatusBar.swift.old"; fi

echo "[4/5] Установка новой оболочки…"
sudo install -m 755 "$TMP_DIR/VideoContextStatusBar" "$BINARY" || fail "Не удалось установить бинарный файл."
sudo install -m 644 "$TMP_DIR/VideoContextStatusBar.swift" "$RESOURCE_DIR/VideoContextStatusBar.swift" || fail "Не удалось установить Swift-код."
sudo /usr/libexec/PlistBuddy -c "Set :CFBundleExecutable VideoContextStatusBar" "$APP_TARGET/Contents/Info.plist"
sudo /usr/libexec/PlistBuddy -c "Set :LSUIElement false" "$APP_TARGET/Contents/Info.plist"
sudo /usr/bin/codesign --force --deep --sign - "$APP_TARGET" || fail "Не удалось переподписать приложение."

echo "[5/5] Регистрация и запуск…"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_TARGET" >/dev/null 2>&1 || true
open "$APP_TARGET"

echo
echo "✅ Готово. Приложение запускает локальный сервер без открытия Terminal."
echo "Журнал сервера: $HOME/Library/Logs/VideoContext Engine/server.log"
echo "Резервная копия старой оболочки: $BACKUP_DIR"
echo "Серверный порт 7555 проверяется приложением; анализ и настройки не сбрасывались."
