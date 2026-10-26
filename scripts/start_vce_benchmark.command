#!/bin/bash
set -euo pipefail

PROJECT="$HOME/VideoContext-Engine"
SCRIPT="$PROJECT/vce_unattended_benchmark.py"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/VideoContext Engine benchmark.log"
RAW="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/automated-vce-benchmark/scripts/vce_unattended_benchmark.py"

mkdir -p "$PROJECT" "$LOG_DIR"
if ! command -v python3 >/dev/null 2>&1; then
  echo "ОШИБКА: python3 не найден."; exit 2
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "ОШИБКА: GitHub CLI (gh) не установлен. Установите gh и выполните gh auth login один раз."; exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "ОШИБКА: GitHub CLI не авторизован. Один раз выполните gh auth login, затем повторите команду."; exit 2
fi

echo "[1/3] Загружаю проверяемый benchmark runner…"
curl -fL --retry 3 "$RAW" -o "$SCRIPT"
chmod 700 "$SCRIPT"

echo "[2/3] Проверяю видео, API, параметры и отсутствие активного анализа…"
python3 "$SCRIPT" --dry-run

echo "[3/3] Запускаю автономный benchmark в фоне."
echo "Лог: $LOG_FILE"
echo "Результаты будут коммититься по мере завершения каждого прогона."
nohup python3 "$SCRIPT" >> "$LOG_FILE" 2>&1 < /dev/null &
PID=$!
printf '%s\n' "$PID" > "$HOME/Library/Logs/VideoContext Engine benchmark.pid"
echo "Запущено в фоне. PID=$PID"
echo "Смотреть ход работы: tail -f \"$LOG_FILE\""
