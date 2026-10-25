#!/bin/bash
# Add the installed FFmpeg binary directory to the GUI-launched VCE server's PATH.
# This changes only the app's start_server.command; engine settings and results are untouched.
set -Eeuo pipefail

APP="/Applications/VideoContext Engine.app"
RESOURCE_SCRIPT="$APP/Contents/Resources/start_server.command"
PROJECT="$HOME/VideoContext-Engine"
LOG_DIR="$HOME/Library/Logs"
SERVER_LOG="$HOME/Library/Logs/VideoContext Engine/server.log"
PORT=7555
STAMP="$(date +%Y%m%d_%H%M%S)"
TMP_DIR="$(/usr/bin/mktemp -d)"
cleanup() { /bin/rm -rf "$TMP_DIR"; }
trap cleanup EXIT

fail() { echo; echo "ОШИБКА: $*"; exit 1; }

[[ -f "$RESOURCE_SCRIPT" ]] || fail "Не найден скрипт сервера внутри приложения: $RESOURCE_SCRIPT"
[[ -f "$PROJECT/VideoContextEngine_v3.19.py" ]] || fail "Не найден движок в $PROJECT"

FFMPEG_BIN="$(command -v ffmpeg 2>/dev/null || true)"
for candidate in /opt/homebrew/bin/ffmpeg /usr/local/bin/ffmpeg /opt/local/bin/ffmpeg; do
  if [[ -z "$FFMPEG_BIN" && -x "$candidate" ]]; then FFMPEG_BIN="$candidate"; fi
done
if [[ -z "$FFMPEG_BIN" ]]; then
  for brew_bin in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$brew_bin" ]]; then
      prefix="$("$brew_bin" --prefix ffmpeg 2>/dev/null || true)"
      if [[ -n "$prefix" && -x "$prefix/bin/ffmpeg" ]]; then
        FFMPEG_BIN="$prefix/bin/ffmpeg"
        break
      fi
    fi
  done
fi
[[ -n "$FFMPEG_BIN" && -x "$FFMPEG_BIN" ]] || fail "Не найден исполняемый ffmpeg. Сначала установи FFmpeg (например, brew install ffmpeg), затем повтори эту команду."
FFMPEG_DIR="$(cd "$(dirname "$FFMPEG_BIN")" && pwd -P)"
echo "Найден FFmpeg: $FFMPEG_BIN"
"$FFMPEG_BIN" -version | head -n 1

PORT_PIDS="$(/usr/sbin/lsof -nP -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$PORT_PIDS" ]]; then
  if ! /usr/bin/curl -fsS --max-time 3 "http://127.0.0.1:$PORT/openapi.json" -o "$TMP_DIR/openapi.json"; then
    fail "Порт $PORT занят, но OpenAPI недоступен. Не буду останавливать неизвестный процесс."
  fi
  /usr/bin/grep -q '"/api/v1/analyze"' "$TMP_DIR/openapi.json" || fail "На порту $PORT отвечает не тот API; остановка отменена."
  echo
  echo "Будет перезапущен VCE сервер, чтобы он унаследовал PATH с FFmpeg."
  echo "Убедись, что сейчас НЕ выполняется анализ видео."
  read -r -p 'Для подтверждения введи NO_ACTIVE_ANALYSIS: ' CONFIRM
  [[ "$CONFIRM" == "NO_ACTIVE_ANALYSIS" ]] || fail "Подтверждение не получено; сервер не остановлен."
fi

python3 - "$RESOURCE_SCRIPT" "$TMP_DIR/start_server.command" "$FFMPEG_DIR" <<'PY'
from pathlib import Path
import shlex
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
destination = Path(sys.argv[2])
ffmpeg_dir = sys.argv[3]
begin = "# VCE_FFMPEG_PATH_PATCH_BEGIN"
end = "# VCE_FFMPEG_PATH_PATCH_END"
block = (
    f"{begin}\n"
    "# LaunchServices-launched apps may not inherit the interactive Homebrew PATH.\n"
    f"export PATH={shlex.quote(ffmpeg_dir)}:\"$PROJECT_DIR/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH\"\n"
    "FFMPEG_BIN=\"$(command -v ffmpeg || true)\"\n"
    "if [[ -z \"$FFMPEG_BIN\" ]]; then\n"
    "  show_error \"Не найден ffmpeg в PATH серверного процесса. Проверь установленный FFmpeg.\"\n"
    "  exit 1\n"
    "fi\n"
    "echo \"FFmpeg executable: $FFMPEG_BIN\"\n"
    f"{end}\n"
)
if begin in source and end in source:
    before = source.split(begin, 1)[0]
    after = source.split(end, 1)[1]
    source = before + block + after.lstrip("\n")
else:
    anchor = "source .venv/bin/activate\n"
    if anchor not in source:
        raise SystemExit("Не найден ожидаемый anchor source .venv/bin/activate; файл не изменён.")
    source = source.replace(anchor, anchor + block, 1)
destination.write_text(source, encoding="utf-8")
PY

/bin/bash -n "$TMP_DIR/start_server.command" || fail "Синтаксическая проверка обновлённого start_server.command не пройдена."
BACKUP="$HOME/Desktop/VideoContext Engine start_server backup $STAMP.command"
/bin/cp -p "$RESOURCE_SCRIPT" "$BACKUP"
echo "Резервная копия: $BACKUP"
echo "Устанавливаю исправленный PATH в launcher приложения (потребуется пароль macOS)…"
/usr/bin/sudo /usr/bin/install -m 755 "$TMP_DIR/start_server.command" "$RESOURCE_SCRIPT"

if [[ -n "$PORT_PIDS" ]]; then
  for pid in $PORT_PIDS; do
    /bin/kill -TERM "$pid" 2>/dev/null || true
  done
  for _ in $(/usr/bin/seq 1 15); do
    remaining="$(/usr/sbin/lsof -nP -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)"
    [[ -z "$remaining" ]] && break
    /bin/sleep 1
  done
  remaining="$(/usr/sbin/lsof -nP -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "$remaining" ]] || fail "Сервер не освободил порт $PORT. PATH исправлен, но сервер не перезапущен; проверь процесс вручную."
fi

mkdir -p "$LOG_DIR"
echo "Запускаю сервер с исправленным PATH в фоне…"
/usr/bin/nohup /bin/bash "$RESOURCE_SCRIPT" >> "$SERVER_LOG" 2>&1 < /dev/null &
SERVER_PID=$!
echo "$SERVER_PID" > "$LOG_DIR/VideoContext Engine server-restart.pid"

READY=0
for _ in $(/usr/bin/seq 1 90); do
  if /usr/bin/curl -fsS --max-time 2 "http://127.0.0.1:$PORT/openapi.json" -o "$TMP_DIR/verify.json" 2>/dev/null \
     && /usr/bin/grep -q '"/api/v1/analyze"' "$TMP_DIR/verify.json"; then
    READY=1
    break
  fi
  /bin/sleep 1
done

if [[ "$READY" != "1" ]]; then
  echo
  echo "Сервер не подтвердил готовность за 90 секунд. Последние строки журнала:"
  /usr/bin/tail -n 60 "$SERVER_LOG" || true
  fail "Launcher исправлен, но API не поднялся. Не запускай benchmark, пока журнал не будет проверен."
fi

echo
echo "Готово: VCE API отвечает на порту $PORT, новый сервер унаследовал PATH с FFmpeg."
echo "Журнал сервера: $SERVER_LOG"
echo "Резервная копия launcher: $BACKUP"
open "$APP" >/dev/null 2>&1 || true
