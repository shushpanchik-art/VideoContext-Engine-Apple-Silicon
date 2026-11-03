#!/bin/bash
set -Eeuo pipefail
REPOSITORY="https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon.git"
PROJECT_DIR="${VCE_PROJECT_DIR:-$HOME/VideoContext-Engine}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OLD_SETTINGS=""
REQUESTED_PROJECT_DIR="${VCE_PROJECT_DIR:-}"
fail() { echo "ОШИБКА: $*" >&2; echo "Существующие пользовательские данные не удалялись." >&2; exit 1; }

# Detect a pre-existing clone before installing system packages. Never reuse
# an unrelated checkout as the destination for this distribution.
select_safe_project_dir() {
  [[ -e "$PROJECT_DIR" ]] || return 0
  git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  local existing_origin
  existing_origin="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
  case "$existing_origin" in
    *shushpanchik-art/VideoContext-Engine-Apple-Silicon.git|*shushpanchik-art/VideoContext-Engine-Apple-Silicon)
      return 0
      ;;
    *)
      if [[ -n "$REQUESTED_PROJECT_DIR" ]]; then
        fail "В указанной папке другой Git origin ($existing_origin). Задайте VCE_PROJECT_DIR для отдельной папки или уберите этот параметр, чтобы использовать безопасный путь по умолчанию."
      fi
      local original_dir="$PROJECT_DIR"
      PROJECT_DIR="$HOME/VideoContext-Engine-Apple-Silicon"
      if [[ -e "$PROJECT_DIR" ]]; then
        local alternate_origin
        alternate_origin="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
        case "$alternate_origin" in
          *shushpanchik-art/VideoContext-Engine-Apple-Silicon.git|*shushpanchik-art/VideoContext-Engine-Apple-Silicon) ;;
          *) PROJECT_DIR="$HOME/VideoContext-Engine-Apple-Silicon-$STAMP" ;;
        esac
      fi
      echo "Найден другой Git-клон: $original_dir"
      echo "Старый каталог останется без изменений."
      echo "Новая программа будет установлена отдельно: $PROJECT_DIR"
      ;;
  esac
}

echo "VideoContext Engine — установщик macOS / Apple Silicon"
[[ "$(uname -s)" == "Darwin" ]] || fail "Установщик предназначен для macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Нужен Mac с Apple Silicon (M1 или новее)."
command -v brew >/dev/null 2>&1 || fail "Homebrew не найден. Установите его с https://brew.sh/ и повторите команду."
command -v git >/dev/null 2>&1 || fail "Не найден git."
select_safe_project_dir
echo "Проект: $PROJECT_DIR"
if ! xcode-select -p >/dev/null 2>&1 || ! command -v xcrun >/dev/null 2>&1; then
  echo "Для сборки .app нужны Xcode Command Line Tools."
  xcode-select --install || true
  fail "Завершите установку Command Line Tools и повторите эту команду."
fi
if /usr/sbin/lsof -nP -iTCP:7555 -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Порт 7555 занят. Завершите текущий анализ и остановите сервер."
fi

echo "[1/7] Системные зависимости"
brew list --versions python@3.11 >/dev/null 2>&1 || brew install python@3.11
brew list --versions ffmpeg >/dev/null 2>&1 || brew install ffmpeg
read -r -p "Установить Tesseract и дополнительные языковые данные? [Y/n] " TESSERACT_CHOICE </dev/tty
if [[ ! "$TESSERACT_CHOICE" =~ ^[Nn]$ ]]; then
  brew list --versions tesseract >/dev/null 2>&1 || brew install tesseract
  if brew info --formula tesseract-lang >/dev/null 2>&1; then
    brew list --versions tesseract-lang >/dev/null 2>&1 || brew install tesseract-lang
  else
    echo "Формула tesseract-lang сейчас недоступна; дополнительные traineddata можно установить позже."
  fi
fi

echo "[2/7] Получение полной программы"
if [[ -e "$PROJECT_DIR" ]]; then
  if git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    ORIGIN="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
    case "$ORIGIN" in
      *shushpanchik-art/VideoContext-Engine-Apple-Silicon.git|*shushpanchik-art/VideoContext-Engine-Apple-Silicon) ;;
      *) fail "В указанной папке другой Git origin ($ORIGIN). Укажите отдельный VCE_PROJECT_DIR." ;;
    esac
    if [[ -z "$(git -C "$PROJECT_DIR" status --porcelain --untracked-files=no)" ]]; then
      git -C "$PROJECT_DIR" fetch origin main
      if git -C "$PROJECT_DIR" show-ref --verify --quiet refs/heads/main; then
        git -C "$PROJECT_DIR" switch main
      else
        git -C "$PROJECT_DIR" switch -c main --track origin/main
      fi
      git -C "$PROJECT_DIR" pull --ff-only origin main
    elif grep -q "_vce_transcribe_allowed_language_chunks" "$PROJECT_DIR/VideoContextEngine_v3.19.py" \
      && grep -q 'cfg-recognition-languages' "$PROJECT_DIR/videocontext_web_ui.html"; then
      echo "Обнаружена установленная языковая модификация; Git pull пропущен, чтобы не затереть патчи."
    else
      fail "Есть изменённые отслеживаемые файлы Git. Обновление отменено для защиты ваших изменений."
    fi
  else
    BACKUP_DIR="${PROJECT_DIR}.backup.${STAMP}"
    echo "Сохраняю существующую папку целиком: $BACKUP_DIR"
    [[ ! -f "$PROJECT_DIR/videocontext_settings.json" ]] || OLD_SETTINGS="$BACKUP_DIR/videocontext_settings.json"
    mv "$PROJECT_DIR" "$BACKUP_DIR"
    git clone --branch main --single-branch "$REPOSITORY" "$PROJECT_DIR"
  fi
else
  git clone --branch main --single-branch "$REPOSITORY" "$PROJECT_DIR"
fi
[[ -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" ]] || fail "Не найден Python runtime."
[[ -f "$PROJECT_DIR/videocontext_web_ui.html" ]] || fail "Не найден Web UI."
if [[ -n "$OLD_SETTINGS" && ! -f "$PROJECT_DIR/videocontext_settings.json" ]]; then
  cp -p "$OLD_SETTINGS" "$PROJECT_DIR/videocontext_settings.json"
  echo "Старые пользовательские настройки перенесены."
fi

# The language patch intentionally makes tracked files dirty, so the full
# git pull above may be skipped to protect those local edits. Always refresh
# this repository-managed app patcher independently; otherwise a stale copy
# can still reject the newer async runtime during step 7/7.
echo "Проверка актуальности служебного macOS-патчера"
PATCHER_PATH="$PROJECT_DIR/patches/patch_vce_stop_and_mps.py"
PATCHER_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/main/patches/patch_vce_stop_and_mps.py"
[[ -d "$(dirname "$PATCHER_PATH")" ]] || fail "Не найдена папка patches в проекте."
PATCHER_TMP="$(mktemp "${TMPDIR:-/tmp}/vce-stop-patcher.XXXXXX")"
if ! curl --fail --location --silent --show-error --retry 3 "$PATCHER_URL" --output "$PATCHER_TMP"; then
  rm -f "$PATCHER_TMP"
  fail "Не удалось получить актуальный macOS-патчер из GitHub. Проверьте интернет и повторите установку."
fi
if ! python3 - "$PATCHER_TMP" <<'PY'
import ast
import pathlib
import sys
source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
ast.parse(source, filename="patch_vce_stop_and_mps.py")
required = (
    "current_stop_runtime_markers",
    "def patch_engine(source: str)",
    "def patch_ui(ui: str)",
)
if not all(marker in source for marker in required):
    raise SystemExit("Downloaded app patcher is not the expected current version.")
PY
then
  rm -f "$PATCHER_TMP"
  fail "Загруженный macOS-патчер не прошёл проверку целостности."
fi
if ! cmp -s "$PATCHER_TMP" "$PATCHER_PATH"; then
  if [[ -f "$PATCHER_PATH" ]]; then
    PATCHER_BACKUP="$PATCHER_PATH.before_refresh.$STAMP.bak"
    cp -p "$PATCHER_PATH" "$PATCHER_BACKUP" || {
      rm -f "$PATCHER_TMP"
      fail "Не удалось создать резервную копию старого патчера."
    }
    echo "Старая версия патчера сохранена: $PATCHER_BACKUP"
  fi
  chmod 644 "$PATCHER_TMP"
  mv "$PATCHER_TMP" "$PATCHER_PATH"
  echo "Служебный macOS-патчер обновлён из main."
else
  rm -f "$PATCHER_TMP"
  echo "Служебный macOS-патчер уже актуален."
fi
echo "Патчер: $(shasum -a 256 "$PATCHER_PATH" | awk '{print $1}')"

echo "[3/7] Python 3.11 и зависимости"
PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
[[ -x "$PYTHON_BIN" ]] || fail "Python 3.11 не найден: $PYTHON_BIN"
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  VENV_VERSION="$("$PROJECT_DIR/.venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  if [[ "$VENV_VERSION" != "3.11" ]]; then mv "$PROJECT_DIR/.venv" "$PROJECT_DIR/.venv.backup.$STAMP"; fi
fi
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_DIR/.venv/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt" -r "$PROJECT_DIR/requirements-apple-silicon.txt"
"$PROJECT_DIR/.venv/bin/python" -m pip check

echo "[4/7] Конфигурация"
[[ -f "$PROJECT_DIR/videocontext_settings.json" ]] || cp "$PROJECT_DIR/config/videocontext_settings.apple_silicon.example.json" "$PROJECT_DIR/videocontext_settings.json"

echo "[5/7] Языковой патч: dry-run и установка"
VCE_PROJECT_DIR="$PROJECT_DIR" bash "$PROJECT_DIR/scripts/install_vce_recognition_languages_patch.command"

echo "[6/7] Smoke tests"
"$PROJECT_DIR/.venv/bin/python" -m py_compile "$PROJECT_DIR/VideoContextEngine_v3.19.py"
"$PROJECT_DIR/.venv/bin/python" -m unittest discover -s "$PROJECT_DIR/tests" -p "test_vce_recognition_languages_patch.py" -v
chmod +x "$PROJECT_DIR/macos/install_video_context_mac.command"

echo "[7/7] Сборка и установка VideoContext Engine.app"
echo "Введите STOP в установщике приложения, затем нажмите Enter по завершении."
VCE_PROJECT_DIR="$PROJECT_DIR" bash "$PROJECT_DIR/macos/install_video_context_mac.command"

echo
echo "УСТАНОВКА ЗАВЕРШЕНА"
echo "Проект: $PROJECT_DIR"
echo "Приложение: /Applications/VideoContext Engine.app"
echo "Интерфейс: http://127.0.0.1:7555/ui"
echo "API: http://127.0.0.1:7555/docs"
echo "Журнал: ~/Library/Logs/VideoContext Engine/server.log"
echo "Модели Whisper/VLM загружаются при первом анализе и не входят в исходный ZIP."
