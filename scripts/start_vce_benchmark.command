#!/bin/bash
set -Eeuo pipefail

PROJECT="$HOME/VideoContext-Engine"
PYTHON="$PROJECT/.venv/bin/python"
SCRIPT="$PROJECT/scripts/vce_unattended_benchmark.py"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/VideoContext Engine benchmark.log"

if [[ $# -lt 1 ]]; then
  echo "Использование: $0 /полный/путь/к/видео.mp4"
  exit 2
fi
VIDEO="$1"
[[ -f "$VIDEO" ]] || { echo "ОШИБКА: видео не найдено: $VIDEO"; exit 2; }
[[ -f "$PROJECT/VideoContextEngine_v3.19.py" ]] || { echo "ОШИБКА: не найден проект $PROJECT"; exit 2; }
[[ -f "$SCRIPT" ]] || { echo "ОШИБКА: не найден benchmark runner $SCRIPT"; exit 2; }
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
[[ -n "$PYTHON" && -x "$PYTHON" ]] || { echo "ОШИБКА: Python 3 не найден."; exit 2; }

mkdir -p "$LOG_DIR"
echo "[1/3] Проверка API и параметров без загрузки видео…"
"$PYTHON" "$SCRIPT" --video "$VIDEO" --project "$PROJECT" --dry-run

echo "[2/3] Запуск benchmark в фоне."
echo "[3/3] Результаты останутся локально в ~/Downloads/VCE_benchmark_runs."
echo "Лог: $LOG_FILE"
nohup "$PYTHON" "$SCRIPT" --video "$VIDEO" --project "$PROJECT" >> "$LOG_FILE" 2>&1 < /dev/null &
PID=$!
printf '%s\\n' "$PID" > "$LOG_DIR/VideoContext Engine benchmark.pid"
echo "Запущено. PID=$PID"
echo "Наблюдать: tail -f \"$LOG_FILE\""
