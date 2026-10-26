#!/bin/bash
set -Eeuo pipefail

SOURCE_ICON="${1:-$HOME/Downloads/VideoContext_UI_Weighted_Progress_v4/favicon.png}"
APP_TARGET="${2:-/Applications/VideoContext Engine.app}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$HOME/Downloads/VideoContext-Engine-icon-backup-$STAMP"
WORK_DIR="$(/usr/bin/mktemp -d)"
trap '/bin/rm -rf "$WORK_DIR"' EXIT

fail() {
  echo "ОШИБКА: $*" >&2
  exit 1
}

[[ -f "$SOURCE_ICON" ]] || fail "Не найден PNG-источник: $SOURCE_ICON"
[[ -d "$APP_TARGET/Contents" ]] || fail "Не найдено приложение: $APP_TARGET"
[[ -f "$APP_TARGET/Contents/Info.plist" ]] || fail "Не найден Info.plist внутри $APP_TARGET"
command -v sips >/dev/null 2>&1 || fail "Не найдена системная утилита sips."
command -v iconutil >/dev/null 2>&1 || fail "Не найдена системная утилита iconutil."

mkdir -p "$BACKUP_DIR"
echo "Резервная копия приложения: $BACKUP_DIR/VideoContext Engine.app"
/usr/bin/ditto "$APP_TARGET" "$BACKUP_DIR/VideoContext Engine.app" || fail "Не удалось создать резервную копию .app."

ICONSET="$WORK_DIR/VideoContextEngine.iconset"
/bin/mkdir -p "$ICONSET"

make_icon() {
  local filename="$1"
  local size="$2"
  /usr/bin/sips -s format png -z "$size" "$size" "$SOURCE_ICON" --out "$ICONSET/$filename" >/dev/null
}

echo "[1/3] Готовлю набор размеров macOS из: $SOURCE_ICON"
make_icon icon_16x16.png 16
make_icon icon_16x16@2x.png 32
make_icon icon_32x32.png 32
make_icon icon_32x32@2x.png 64
make_icon icon_128x128.png 128
make_icon icon_128x128@2x.png 256
make_icon icon_256x256.png 256
make_icon icon_256x256@2x.png 512
make_icon icon_512x512.png 512
make_icon icon_512x512@2x.png 1024

ICON_FILE="$WORK_DIR/VideoContextEngine.icns"
/usr/bin/iconutil -c icns "$ICONSET" -o "$ICON_FILE" || fail "Не удалось собрать .icns."

echo "[2/3] Устанавливаю иконку в $APP_TARGET"
/usr/bin/sudo /usr/bin/install -m 644 "$ICON_FILE" "$APP_TARGET/Contents/Resources/VideoContextEngine.icns"
PLIST="$APP_TARGET/Contents/Info.plist"
if /usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$PLIST" >/dev/null 2>&1; then
  /usr/bin/sudo /usr/libexec/PlistBuddy -c 'Set :CFBundleIconFile VideoContextEngine' "$PLIST"
else
  /usr/bin/sudo /usr/libexec/PlistBuddy -c 'Add :CFBundleIconFile string VideoContextEngine' "$PLIST"
fi
/usr/bin/plutil -lint "$PLIST" || fail "Info.plist не прошёл проверку."

# An ad-hoc signature refreshes the bundle seal after the icon and plist change.
/usr/bin/sudo /usr/bin/codesign --force --deep --sign - "$APP_TARGET" || fail "Не удалось обновить подпись приложения."
/usr/bin/codesign --verify --deep --strict "$APP_TARGET" || fail "Подпись приложения не прошла проверку."

echo "[3/3] Обновляю регистрацию приложения в macOS"
/usr/bin/sudo /usr/bin/touch "$APP_TARGET"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [[ -x "$LSREGISTER" ]]; then
  "$LSREGISTER" -f "$APP_TARGET" >/dev/null 2>&1 || true
fi

echo
echo "ГОТОВО: иконка подключена к $APP_TARGET"
echo "Источник: $SOURCE_ICON"
echo "Резервная копия: $BACKUP_DIR/VideoContext Engine.app"
echo "Если значок не изменился сразу, закрой и снова открой приложение; Dock может некоторое время хранить старый кэш."
echo "Примечание: источник 64×64 px, поэтому при крупном отображении значок может быть менее резким."
