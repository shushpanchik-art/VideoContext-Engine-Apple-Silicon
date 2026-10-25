#!/bin/bash
set -Eeuo pipefail
if [[ -f "$HOME/Downloads/VideoContext-Engine/VideoContextEngine_v3.19.py" ]]; then
  PROJECT_DIR="$HOME/Downloads/VideoContext-Engine"
elif [[ -f "$HOME/VideoContext-Engine/VideoContextEngine_v3.19.py" ]]; then
  PROJECT_DIR="$HOME/VideoContext-Engine"
else
  PROJECT_DIR="$HOME/Downloads/VideoContext-Engine"
fi
if [[ -d "$PROJECT_DIR/macos/VideoContext Engine.app/Contents" ]]; then
  APP_SOURCE="$PROJECT_DIR/macos/VideoContext Engine.app"
else
  APP_SOURCE="$PROJECT_DIR/VideoContext Engine.app"
fi
APP_TARGET="/Applications/VideoContext Engine.app"
DESKTOP_ALIAS="$HOME/Desktop/VideoContext Engine"
DESKTOP_LINK="$HOME/Desktop/VideoContext Engine.app"
STATUS_SOURCE="$APP_SOURCE/Contents/Resources/VideoContextStatusBar.swift"
APP_ICON="$APP_SOURCE/Contents/Resources/VideoContextEngine.icns"
ICON_SOURCE="$HOME/Downloads/VideoContext_UI_Weighted_Progress_v4/favicon.png"

fail() { echo "ОШИБКА: $*"; echo "Проверьте сообщение выше. Нажмите Enter для закрытия."; read -r || true; exit 1; }

[[ -d "$PROJECT_DIR" ]] || fail "Не найдена папка проекта $PROJECT_DIR"
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || fail "Не найден .venv/bin/python. Сначала восстановите виртуальное окружение проекта."
[[ -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" ]] || fail "Не найден VideoContextEngine_v3.19.py в папке проекта."
[[ -f "$PROJECT_DIR/videocontext_web_ui.html" ]] || fail "Не найден videocontext_web_ui.html. Сначала установите пакет пакетного интерфейса."
[[ -f "$PROJECT_DIR/patch_vce_stop_and_mps.py" ]] || fail "Не найден patch_vce_stop_and_mps.py. Распакуйте полный ZIP в папку проекта."
[[ -d "$APP_SOURCE/Contents" ]] || fail "Не найден комплект VideoContext Engine.app внутри папки проекта."
[[ -f "$STATUS_SOURCE" ]] || fail "Не найден исходник значка меню macOS: $STATUS_SOURCE. Обнови пакет патчей."
command -v xcrun >/dev/null 2>&1 || fail "Не найден xcrun/swiftc. Установи Xcode Command Line Tools: xcode-select --install."

echo "Важно: перед установкой закончи текущий анализ и останови сервер VCE сочетанием Ctrl+C в его Terminal-окне."
read -r -p "Если сервер остановлен, введи STOP для продолжения: " STOP_CONFIRM </dev/tty
if [[ "$STOP_CONFIRM" != "STOP" ]]; then
  echo "Установка отменена. Никакие файлы не менялись."
  exit 0
fi

printf '\n[1/4] Исправление Whisper/MPS и добавление кнопки остановки…\n'
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/patch_vce_stop_and_mps.py" || fail "Патч не прошёл проверку. Приложение не устанавливалось."

printf '\n[2/4] Сохранение фирменной иконки и сборка значка меню…\n'
# Keep the user's installed custom icon when replacing the app bundle.
if [[ ! -f "$APP_ICON" && -f "$APP_TARGET/Contents/Resources/VideoContextEngine.icns" ]]; then
  /bin/cp "$APP_TARGET/Contents/Resources/VideoContextEngine.icns" "$APP_ICON" || fail "Не удалось сохранить текущую иконку."
fi

# If there is no installed ICNS yet, build one from the old project favicon when available.
if [[ ! -f "$APP_ICON" && -f "$ICON_SOURCE" ]]; then
  ICON_TMP="$(/usr/bin/mktemp -d)"
  ICONSET="$ICON_TMP/VideoContextEngine.iconset"
  /bin/mkdir -p "$ICONSET"
  /usr/bin/sips -z 16 16 "$ICON_SOURCE" --out "$ICONSET/icon_16x16.png" >/dev/null
  /usr/bin/sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
  /usr/bin/sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_32x32.png" >/dev/null
  /usr/bin/sips -z 64 64 "$ICON_SOURCE" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
  /usr/bin/sips -z 128 128 "$ICON_SOURCE" --out "$ICONSET/icon_128x128.png" >/dev/null
  /usr/bin/sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
  /usr/bin/sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_256x256.png" >/dev/null
  /usr/bin/sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
  /usr/bin/sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_512x512.png" >/dev/null
  /usr/bin/sips -z 1024 1024 "$ICON_SOURCE" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
  /usr/bin/iconutil -c icns "$ICONSET" -o "$APP_ICON" || fail "Не удалось создать VideoContextEngine.icns."
  /bin/rm -rf "$ICON_TMP"
fi

# Build a real, persistent NSStatusItem app. The old shell launcher exits after opening the browser.
STATUS_BINARY="$APP_SOURCE/Contents/MacOS/VideoContextStatusBar"
/usr/bin/xcrun swiftc -O "$STATUS_SOURCE" -framework AppKit -o "$STATUS_BINARY" || fail "Не удалось собрать значок меню. Проверь Xcode Command Line Tools."
/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable VideoContextStatusBar" "$APP_SOURCE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :LSUIElement false" "$APP_SOURCE/Contents/Info.plist"
if [[ -f "$APP_ICON" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile VideoContextEngine" "$APP_SOURCE/Contents/Info.plist"
fi

printf '\n[3/4] Установка приложения в /Applications…\n'
STAMP="$(date +%Y%m%d_%H%M%S)"
if [[ -e "$APP_TARGET" ]]; then
  echo "Старая копия найдена; сохраняю её как: $APP_TARGET.backup.$STAMP"
  sudo /bin/mv "$APP_TARGET" "$APP_TARGET.backup.$STAMP" || fail "Не удалось сохранить старую копию приложения."
fi
sudo /bin/mkdir -p /Applications || fail "Нет разрешения создать /Applications."
sudo /usr/bin/ditto "$APP_SOURCE" "$APP_TARGET" || fail "Не удалось скопировать приложение в /Applications."
sudo /bin/chmod 755 "$APP_TARGET/Contents/MacOS/VideoContextStatusBar" "$APP_TARGET/Contents/Resources/start_server.command" || fail "Не удалось выставить права запуска."
sudo /usr/bin/codesign --force --deep --sign - "$APP_TARGET" || fail "Не удалось подписать установленное приложение."
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
/usr/bin/env "$LSREGISTER" -f "$APP_TARGET" >/dev/null 2>&1 || true

printf '\n[4/4] Создание ярлыка на рабочем столе…\n'
# Remove an older alias or symlink with this name only; do not touch the project.
if [[ -e "$DESKTOP_ALIAS" || -L "$DESKTOP_ALIAS" ]]; then
  /bin/rm -rf "$DESKTOP_ALIAS"
fi
if [[ -L "$DESKTOP_LINK" ]]; then
  /bin/rm -f "$DESKTOP_LINK"
fi
if ! /usr/bin/osascript <<'APPLESCRIPT'
set homePath to system attribute "HOME"
set appAlias to (POSIX file "/Applications/VideoContext Engine.app") as alias
set desktopFolder to (POSIX file (homePath & "/Desktop")) as alias
tell application "Finder"
    make new alias file at desktopFolder to appAlias with properties {name:"VideoContext Engine"}
end tell
APPLESCRIPT
then
  echo "Finder не смог создать системный alias; создаю функциональную ссылку на приложение вместо него."
  /bin/ln -s "/Applications/VideoContext Engine.app" "$DESKTOP_LINK"
fi

printf '\nГОТОВО.\nПриложение: /Applications/VideoContext Engine.app\nЗначок меню: панель меню macOS рядом с системными значками\nЯрлык: рабочий стол → VideoContext Engine\nИнтерфейс: http://127.0.0.1:7555/ui\nSwagger: http://127.0.0.1:7555/docs\n\nЗначок меню откроется автоматически; сервер будет запущен в Terminal, если он ещё не работает.\nНажмите Enter для закрытия этого установщика.\n'
read -r || true
/usr/bin/open "$APP_TARGET" || true
