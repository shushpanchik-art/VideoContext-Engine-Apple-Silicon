#!/bin/bash
set -Eeuo pipefail

REPO_RAW_BASE="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/macos-native-window-confirm-quit"
if [[ "$#" -gt 0 ]]; then
  APP_TARGET="$1"
else
  APP_TARGET="/Applications/VideoContext Engine.app"
fi
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$HOME/Downloads/VideoContext-Engine-native-window-backup-$STAMP"
TMP_DIR="$(/usr/bin/mktemp -d)"
trap '/bin/rm -rf "$TMP_DIR"' EXIT

fail() {
  echo "ОШИБКА: $*"
  echo "Проверьте сообщение выше. Python-движок и пользовательские настройки этот скрипт не меняет."
  exit 1
}

if [[ ! -d "$APP_TARGET/Contents" ]]; then
  for candidate in \
    "$HOME/VideoContext-Engine/VideoContext Engine.app" \
    "$HOME/Downloads/VideoContext-Engine/VideoContext Engine.app"; do
    if [[ -d "$candidate/Contents" ]]; then APP_TARGET="$candidate"; break; fi
  done
fi

[[ -d "$APP_TARGET/Contents" ]] || fail "Не найдено приложение .app. Укажите путь первым аргументом, например: \"$0\" \"$HOME/VideoContext-Engine/VideoContext Engine.app\""
[[ -x /usr/bin/swiftc ]] || fail "Не найден swiftc. Установите Command Line Tools командой: xcode-select --install"
[[ -f "$APP_TARGET/Contents/Info.plist" ]] || fail "Не найден Info.plist внутри $APP_TARGET"
[[ -f "$APP_TARGET/Contents/MacOS/VideoContextLauncher" ]] || fail "Не найден VideoContextLauncher. Эта версия рассчитана на подготовленный VCE .app."

if /bin/ps -axo command | /usr/bin/grep -F "$APP_TARGET/Contents/MacOS/VideoContextWebView" | /usr/bin/grep -v grep >/dev/null; then
  fail "Сначала закройте окно VideoContext Engine, затем повторите установку. Рабочий сервер в Terminal останавливать не нужно."
fi

echo "[1/4] Загружаю исходники отдельного окна…"
/usr/bin/curl -fL "$REPO_RAW_BASE/macos/VideoContextWebView.swift" -o "$TMP_DIR/VideoContextWebView.swift"
/usr/bin/curl -fL "$REPO_RAW_BASE/macos/VideoContextLauncher.sh" -o "$TMP_DIR/VideoContextLauncher"

echo "[2/4] Компилирую WKWebView-приложение средствами macOS…"
/usr/bin/swiftc -O -framework AppKit -framework WebKit \
  "$TMP_DIR/VideoContextWebView.swift" \
  -o "$TMP_DIR/VideoContextWebView" || fail "Swift-компиляция не прошла; установленное приложение не изменено."

echo "[3/4] Сохраняю резервную копию текущего .app…"
/bin/mkdir -p "$BACKUP_DIR"
/usr/bin/sudo /bin/cp -p "$APP_TARGET/Contents/MacOS/VideoContextLauncher" "$BACKUP_DIR/VideoContextLauncher"
if [[ -f "$APP_TARGET/Contents/MacOS/VideoContextWebView" ]]; then
  /usr/bin/sudo /bin/cp -p "$APP_TARGET/Contents/MacOS/VideoContextWebView" "$BACKUP_DIR/VideoContextWebView"
fi
if [[ -f "$APP_TARGET/Contents/Resources/VideoContextWebView.swift" ]]; then
  /usr/bin/sudo /bin/cp -p "$APP_TARGET/Contents/Resources/VideoContextWebView.swift" "$BACKUP_DIR/VideoContextWebView.swift"
fi

echo "[4/4] Устанавливаю отдельное окно…"
/usr/bin/sudo /usr/bin/install -m 755 "$TMP_DIR/VideoContextLauncher" "$APP_TARGET/Contents/MacOS/VideoContextLauncher"
/usr/bin/sudo /usr/bin/install -m 755 "$TMP_DIR/VideoContextWebView" "$APP_TARGET/Contents/MacOS/VideoContextWebView"
/usr/bin/sudo /usr/bin/install -m 644 "$TMP_DIR/VideoContextWebView.swift" "$APP_TARGET/Contents/Resources/VideoContextWebView.swift"
/usr/bin/sudo /bin/chmod 755 "$APP_TARGET/Contents/MacOS/VideoContextLauncher"
/usr/bin/touch "$APP_TARGET"

echo
echo "ГОТОВО: $APP_TARGET"
echo "Резервная копия: $BACKUP_DIR"
echo "Полностью закройте приложение и запустите его снова."
echo "VCE откроется в отдельном окне macOS; Cmd+Q и закрытие последнего окна покажут подтверждение."
echo "Сервер запускается в Terminal отдельно. Не закрывайте это окно Terminal, если обработка ещё идёт."
