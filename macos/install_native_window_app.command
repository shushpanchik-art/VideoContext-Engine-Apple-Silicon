#!/bin/bash
set -Eeuo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="${1:-$HOME/VideoContext-Engine}"
if [[ ! -x "$PROJECT_DIR/.venv/bin/python" || ! -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" ]]; then
  if [[ -x "$HOME/Downloads/VideoContext-Engine/.venv/bin/python" && -f "$HOME/Downloads/VideoContext-Engine/VideoContextEngine_v3.19.py" ]]; then
    PROJECT_DIR="$HOME/Downloads/VideoContext-Engine"
  else
    echo "ОШИБКА: не найден VCE-проект в $PROJECT_DIR."
    echo "Укажи путь явно: bash macos/install_native_window_app.command \"$HOME/VideoContext-Engine\""
    exit 1
  fi
fi

SOURCE_APP="$BUNDLE_DIR/macos/VideoContext Engine.app"
SWIFT_SOURCE="$BUNDLE_DIR/macos/VideoContextWindowApp.swift"
APP_TARGET="/Applications/VideoContext Engine.app"
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGING_ROOT="$(mktemp -d "$HOME/Library/Caches/vce-native-window.XXXXXX")"
STAGING_APP="$STAGING_ROOT/VideoContext Engine.app"
cleanup() { /bin/rm -rf "$STAGING_ROOT"; }
trap cleanup EXIT

[[ -d "$SOURCE_APP/Contents" ]] || { echo "ОШИБКА: не найден шаблон macOS .app: $SOURCE_APP"; exit 1; }
[[ -f "$SWIFT_SOURCE" ]] || { echo "ОШИБКА: не найден исходный файл Swift: $SWIFT_SOURCE"; exit 1; }
command -v xcrun >/dev/null 2>&1 || { echo "ОШИБКА: нужен Xcode Command Line Tools (xcrun/swiftc)."; exit 1; }

/usr/bin/ditto "$SOURCE_APP" "$STAGING_APP"
/bin/mkdir -p "$STAGING_APP/Contents/Resources"
/usr/bin/printf '%s\n' "$PROJECT_DIR" > "$STAGING_APP/Contents/Resources/project-path.txt"

# Preserve the icon from the installed app. If this app was already updated once,
# search its dated backups too; the original icon may only be there.
find_icon_in_app() {
  local app_dir="$1"
  local resources="$app_dir/Contents/Resources"
  local icon_name=""
  local icon_path=""
  [[ -d "$resources" ]] || return 1
  icon_name="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$app_dir/Contents/Info.plist" 2>/dev/null || true)"
  if [[ -n "$icon_name" ]]; then
    icon_path="$resources/$icon_name"
    if [[ "$icon_path" != *.icns ]]; then icon_path="$icon_path.icns"; fi
    if [[ -f "$icon_path" ]]; then printf '%s\n' "$icon_path"; return 0; fi
  fi
  find "$resources" -maxdepth 1 -type f -name '*.icns' -print -quit
}
LEGACY_ICON="$(find_icon_in_app "$APP_TARGET" || true)"
if [[ -z "$LEGACY_ICON" ]]; then
  while IFS= read -r backup_app; do
    LEGACY_ICON="$(find_icon_in_app "$backup_app" || true)"
    if [[ -n "$LEGACY_ICON" ]]; then break; fi
  done < <(find /Applications -maxdepth 1 -type d -name 'VideoContext Engine.app.backup.*' -print | sort -r)
fi
if [[ -n "$LEGACY_ICON" ]]; then
  icon_file="$(basename "$LEGACY_ICON")"
  icon_key="$(basename "$LEGACY_ICON" .icns)"
  /usr/bin/ditto "$LEGACY_ICON" "$STAGING_APP/Contents/Resources/$icon_file"
  plist="$STAGING_APP/Contents/Info.plist"
  if /usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile $icon_key" "$plist"
  else
    /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string $icon_key" "$plist"
  fi
  echo "Сохраняю иконку предыдущей версии: $icon_file"
fi


echo "[1/3] Сборка нативного окна на этом Mac…"
xcrun swiftc -parse-as-library -O -target arm64-apple-macos11.0 \
  -framework AppKit -framework WebKit -framework Foundation \
  -o "$STAGING_APP/Contents/MacOS/VideoContextLauncher" "$SWIFT_SOURCE"
/bin/chmod 755 "$STAGING_APP/Contents/MacOS/VideoContextLauncher"
/usr/libexec/PlistBuddy -c 'Set :CFBundleVersion 1.4' "$STAGING_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleShortVersionString 1.4' "$STAGING_APP/Contents/Info.plist"
/usr/bin/codesign --force --deep --sign - "$STAGING_APP"

echo "[2/3] Установка в /Applications…"
if [[ -e "$APP_TARGET" ]]; then
  echo "Сохраняю предыдущую копию: ${APP_TARGET}.backup.${STAMP}"
  sudo /bin/mv "$APP_TARGET" "${APP_TARGET}.backup.${STAMP}"
fi
sudo /bin/mkdir -p /Applications
sudo /usr/bin/ditto "$STAGING_APP" "$APP_TARGET"

echo "[3/3] Проверка подписи и путей…"
/usr/bin/codesign --verify --deep --strict "$APP_TARGET"
echo
echo "ГОТОВО: /Applications/VideoContext Engine.app"
echo "VCE-проект: $PROJECT_DIR"
echo "Интерфейс откроется в собственном окне приложения."
echo "Cmd+Q и красная кнопка закрытия запрашивают подтверждение."
echo "Ярлык рабочего стола, если он уже создан, продолжит открывать обновлённое приложение."
echo "Если сервер уже работает отдельно, приложение подключится к нему и не будет останавливать его при выходе."
