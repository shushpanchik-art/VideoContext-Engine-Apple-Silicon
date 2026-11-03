#!/bin/bash
set -u
PROJECT_PATH_FILE="$HOME/Library/Application Support/VideoContext Engine/project_path"
if [[ -n "${VCE_PROJECT_DIR:-}" ]]; then
  PROJECT_DIR="$VCE_PROJECT_DIR"
elif [[ -f "$PROJECT_PATH_FILE" ]]; then
  IFS= read -r PROJECT_DIR < "$PROJECT_PATH_FILE"
elif [[ -f "$HOME/VideoContext-Engine/VideoContextEngine_v3.19.py" ]]; then
  PROJECT_DIR="$HOME/VideoContext-Engine"
elif [[ -f "$HOME/Downloads/VideoContext-Engine/VideoContextEngine_v3.19.py" ]]; then
  PROJECT_DIR="$HOME/Downloads/VideoContext-Engine"
else
  PROJECT_DIR="$HOME/VideoContext-Engine"
fi
ENGINE="$PROJECT_DIR/VideoContextEngine_v3.19.py"
PYTHON="$PROJECT_DIR/.venv/bin/python"

show_error() {
  /usr/bin/osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1 || true
  echo "$1"
}

if [[ ! -d "$PROJECT_DIR" ]]; then
  show_error "Не найдена папка проекта: $PROJECT_DIR"
  echo "Нажми Enter для закрытия окна."; read -r || true; exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  show_error "Не найден Python из .venv: $PYTHON. Восстанови виртуальное окружение проекта."
  echo "Нажми Enter для закрытия окна."; read -r || true; exit 1
fi
if [[ ! -f "$ENGINE" ]]; then
  show_error "Не найден файл движка: $ENGINE"
  echo "Нажми Enter для закрытия окна."; read -r || true; exit 1
fi

cd "$PROJECT_DIR" || exit 1
source .venv/bin/activate

# VCE_HOMEBREW_GUI_PATH_V1
# LaunchServices does not load interactive shell profiles, so GUI-launched apps
# otherwise miss Homebrew's binaries (notably ffmpeg used by Whisper).
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
hash -r 2>/dev/null || true

if ! command -v ffmpeg >/dev/null 2>&1; then
  show_error "Не найден ffmpeg в PATH приложения. Проверь /opt/homebrew/bin/ffmpeg или выполни в Terminal: brew install ffmpeg."
  exit 1
fi
if ! command -v ffprobe >/dev/null 2>&1; then
  show_error "Не найден ffprobe в PATH приложения. Переустанови FFmpeg командой: brew install ffmpeg."
  exit 1
fi
echo "FFmpeg: $(command -v ffmpeg)"
echo "FFprobe: $(command -v ffprobe)"
export PYTORCH_ENABLE_MPS_FALLBACK=1
export SSL_CERT_FILE="$(python -m certifi 2>/dev/null || true)"
if [[ -n "$SSL_CERT_FILE" && -f "$SSL_CERT_FILE" ]]; then export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"; else unset SSL_CERT_FILE REQUESTS_CA_BUNDLE; fi

echo "============================================"
echo " VideoContext Engine v3.19 — локальный запуск"
echo " Проект: $PROJECT_DIR"
echo " Web UI: http://127.0.0.1:7555/ui"
echo " Swagger: http://127.0.0.1:7555/docs"
echo " Остановка сервера: Ctrl+C"
echo "============================================"
# VCE_MANAGED_ENGINE_PROCESS_LIFECYCLE_V1
exec "$PYTHON" "$ENGINE"
RESULT=$?
echo
echo "Сервер завершился с кодом: $RESULT"
echo "Это окно можно закрыть. Нажми Enter."
read -r || true
exit "$RESULT"
