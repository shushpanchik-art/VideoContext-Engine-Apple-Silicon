#!/bin/bash
set -Eeuo pipefail

fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TARGET="$HOME/VideoContext-Engine-Apple-Silicon"
APP="/Applications/VideoContext Engine.app"
SUPPORT="$HOME/Library/Application Support/VideoContext Engine"
LOGS="$HOME/Library/Logs/VideoContext Engine"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен Mac с Apple Silicon."
[[ -f "$SOURCE/.vce-distribution" && ! -e "$SOURCE/.git" && ! -e "$SOURCE/.venv" &&
   -f "$SOURCE/VideoContextEngine_v3.19.py" && -f "$SOURCE/patches/vce-recognition-languages-patcher.tar.gz" ]] ||
  fail "Распакуйте ZIP целиком и запустите установщик из распакованной папки."
command -v brew >/dev/null || fail "Установите Homebrew: https://brew.sh/"
xcode-select -p >/dev/null 2>&1 && /usr/bin/xcrun --find swiftc >/dev/null ||
  fail "Установите Xcode Command Line Tools: xcode-select --install"

assert_stopped() {
  if /usr/sbin/lsof -nP -iTCP:7555 -sTCP:LISTEN >/dev/null 2>&1 ||
     /usr/bin/pgrep -x VideoContextStatusBar >/dev/null 2>&1; then
    fail "Завершите анализ, закройте VideoContext Engine и остановите его сервер."
  fi
}

OLD_PROJECTS=()
add_project() {
  local path="$1" resolved origin existing
  [[ -e "$path" || -L "$path" ]] || return 0
  [[ -d "$path" && ! -L "$path" ]] || fail "Ожидалась обычная папка проекта: $path"
  resolved="$(cd "$path" && pwd -P)"
  [[ "$resolved" == "$HOME/"* && ! -L "$resolved/VideoContextEngine_v3.19.py" &&
     -f "$resolved/VideoContextEngine_v3.19.py" && -f "$resolved/videocontext_web_ui.html" ]] ||
    fail "Не удалось распознать установленный проект: $path"
  case "$SOURCE/" in "$resolved/"*) fail "Распакуйте пакет вне старой установки: $resolved" ;; esac
  [[ ! -f "$resolved/.git" ]] || fail "Это связанное рабочее дерево Git: $resolved"
  origin="$(git -C "$resolved" remote get-url origin 2>/dev/null || true)"
  case "${origin%.git}" in
    https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon|https://github.com/dolphin-creator/VideoContext-Engine|git@github.com:shushpanchik-art/VideoContext-Engine-Apple-Silicon|git@github.com:dolphin-creator/VideoContext-Engine) ;;
    *) [[ -f "$resolved/.vce-clean-install" && ! -d "$resolved/.git" ]] ||
         fail "Не удалось подтвердить принадлежность каталога VideoContext Engine: $resolved" ;;
  esac
  for existing in "${OLD_PROJECTS[@]+"${OLD_PROJECTS[@]}"}"; do
    [[ "$existing" != "$resolved" ]] || return 0
    if [[ "$resolved/" == "$existing/"* || "$existing/" == "$resolved/"* ]]; then
      fail "Каталоги старых установок вложены друг в друга."
    fi
  done
  OLD_PROJECTS+=("$resolved")
}

assert_stopped
[[ ! -L "$SUPPORT" && ! -L "$LOGS" ]] || fail "Папка настроек или журналов является символической ссылкой."
if [[ -f "$SUPPORT/project_path" ]]; then
  OLD_PROJECT=""
  IFS= read -r OLD_PROJECT < "$SUPPORT/project_path" || true
  [[ -z "$OLD_PROJECT" ]] || add_project "$OLD_PROJECT"
fi
for project in "$TARGET" "$HOME/VideoContext-Engine" "$HOME/Downloads/VideoContext-Engine"; do
  if [[ "$project" == "$TARGET" || -f "$project/VideoContextEngine_v3.19.py" ]]; then
    add_project "$project"
  fi
done
if [[ -e "$APP" || -L "$APP" ]]; then
  [[ ! -L "$APP" ]] || fail "Путь приложения является символической ссылкой: $APP"
  bundle_id="$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$APP/Contents/Info.plist")"
  [[ "$bundle_id" == local.videocontext.engine.launcher ]] || fail "Не удалось распознать приложение: $APP"
fi

printf 'Старая установка будет убрана из рабочих путей. Настройки и .venv будут созданы заново.\n'
printf 'Резервная папка в домашнем каталоге сохранит старые файлы, в том числе видео и отчёты внутри проекта.\n'
for project in "${OLD_PROJECTS[@]+"${OLD_PROJECTS[@]}"}"; do printf '  %s\n' "$project"; done
printf 'Новая папка: %s\n' "$TARGET"
read -r -p 'Введите CLEAN для чистой установки: ' answer </dev/tty
[[ "$answer" == CLEAN ]] || exit 0

brew list --versions python@3.11 >/dev/null 2>&1 || brew install python@3.11
brew list --versions ffmpeg >/dev/null 2>&1 || brew install ffmpeg
PYTHON="$(brew --prefix python@3.11)/bin/python3.11"
[[ -x "$PYTHON" ]] || fail "Python 3.11 не найден."
sudo -v
assert_stopped

umask 077
BACKUP="$(mktemp -d "$HOME/VideoContext-Engine-previous.XXXXXX")"
trap 'printf "\nУстановка прервана. Перемещённые файлы предыдущей версии: %s\n" "$BACKUP" >&2' ERR
index=0
for project in "${OLD_PROJECTS[@]+"${OLD_PROJECTS[@]}"}"; do
  index=$((index + 1))
  printf '%s\t%s\n' "project-$index" "$project" >> "$BACKUP/locations.tsv"
  /bin/mv -n "$project" "$BACKUP/project-$index"
  [[ ! -e "$project" ]] || fail "Не удалось убрать старый проект: $project"
  if [[ -d "$BACKUP/project-$index/.git/worktrees" ]]; then
    git -C "$BACKUP/project-$index" worktree repair
  fi
done
if [[ -e "$APP" ]]; then sudo /bin/mv "$APP" "$BACKUP/VideoContext Engine.app"; fi
if [[ -e "$SUPPORT" ]]; then /bin/mv "$SUPPORT" "$BACKUP/Application Support"; fi
if [[ -e "$LOGS" ]]; then /bin/mv "$LOGS" "$BACKUP/Logs"; fi

# Reserve the destination exclusively, then copy only the packaged source.
mkdir "$TARGET"
touch "$TARGET/.vce-clean-install"
/usr/bin/ditto "$SOURCE" "$TARGET"
"$PYTHON" -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/python" -m pip install -r "$TARGET/requirements.txt" -r "$TARGET/requirements-apple-silicon.txt"
"$TARGET/.venv/bin/python" -m pip check
cp "$TARGET/config/videocontext_settings.apple_silicon.example.json" "$TARGET/videocontext_settings.json"
VCE_PROJECT_DIR="$TARGET" VCE_LANGUAGE_BUNDLE="$TARGET/patches/vce-recognition-languages-patcher.tar.gz" \
  bash "$TARGET/scripts/install_vce_recognition_languages_patch.command"
"$TARGET/.venv/bin/python" -m py_compile "$TARGET/VideoContextEngine_v3.19.py"
VCE_PROJECT_DIR="$TARGET" VCE_CLEAN_INSTALL=1 VCE_PREVIOUS_APP_ICON="$BACKUP/VideoContext Engine.app/Contents/Resources/VideoContextEngine.icns" bash "$TARGET/macos/install_video_context_mac.command"
trap - ERR
printf '\nУстановка завершена. Предыдущие файлы: %s\n' "$BACKUP"
