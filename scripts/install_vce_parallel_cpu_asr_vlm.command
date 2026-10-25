#!/bin/bash
set -Eeuo pipefail

PROJECT="$HOME/VideoContext-Engine"
PATCH_DIR="$PROJECT/patches"
PATCH="$PATCH_DIR/patch_vce_parallel_cpu_asr_vlm.py"
PYTHON="$PROJECT/.venv/bin/python"
BRANCH="feat/vce-parallel-cpu-asr-vlm-20261025"
RAW="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$BRANCH/patches/patch_vce_parallel_cpu_asr_vlm.py"

fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 2; }

printf '%s\n' \
  "VideoContext Engine — установка параллельного CPU Whisper + GPU VLM" \
  "Проект: $PROJECT" \
  "Патч берётся из GitHub: $BRANCH" \
  ""

[[ -d "$PROJECT" ]] || fail "Не найден проект: $PROJECT"
[[ -x "$PYTHON" ]] || fail "Не найден Python окружения VCE: $PYTHON"
[[ -f "$PROJECT/VideoContextEngine_v3.19.py" ]] || fail "Не найден VideoContextEngine_v3.19.py"
[[ -f "$PROJECT/videocontext_settings.json" ]] || fail "Не найден videocontext_settings.json"
command -v curl >/dev/null 2>&1 || fail "curl не найден."
command -v lsof >/dev/null 2>&1 || fail "lsof не найден."

if lsof -nP -tiTCP:7555 -sTCP:LISTEN >/dev/null 2>&1; then
  cat <<'MSG'
ОШИБКА: локальный сервер VCE всё ещё слушает порт 7555.
Я не буду останавливать его автоматически: это могло бы прервать анализ.
Дождись завершения всех задач, нажми в интерфейсе «Остановить сервер VCE»,
затем снова выполни эту команду.
Файлы проекта не изменены.
MSG
  exit 2
fi

mkdir -p "$PATCH_DIR"
DOWNLOAD="$PATCH.download.$$"
trap 'rm -f "$DOWNLOAD"' EXIT

echo "[1/5] Загружаю патч из GitHub…"
curl -fL --retry 3 "$RAW" -o "$DOWNLOAD"
grep -q "VCE_PARALLEL_CPU_ASR_GPU_VLM_V1" "$DOWNLOAD" || fail "Загруженный файл не похож на ожидаемый патч."
"$PYTHON" -m py_compile "$DOWNLOAD"

echo
echo "[2/5] Проверяю совместимость и показываю diff (ничего не записывается)…"
"$PYTHON" "$DOWNLOAD" --project "$PROJECT" --dry-run

echo
cat <<'MSG'
Проверь diff выше. Патч включает:
  • Whisper на CPU в фоне, параллельно с загрузкой и анализом VLM;
  • объединение транскрипции с результатами сцен перед сводкой;
  • ожидание фонового Whisper перед удалением временного файла;
  • защиту Metal-памяти при построении общей сводки;
  • резервные копии перед записью.
Для VLM-подсказок отдельных сцен речь может быть ещё не готова.
MSG
read -r -p "Чтобы применить патч, введи APPLY: " answer
[[ "$answer" == "APPLY" ]] || { echo "Отменено; файлы проекта не изменены."; exit 0; }

echo
echo "[3/5] Устанавливаю проверенный патчер…"
mv "$DOWNLOAD" "$PATCH"
trap - EXIT
chmod 700 "$PATCH"

echo "[4/5] Применяю патч и создаю резервные копии…"
"$PYTHON" "$PATCH" --project "$PROJECT"

echo
echo "[5/5] Проверяю Python и JSON…"
"$PYTHON" -m py_compile "$PROJECT/VideoContextEngine_v3.19.py"
"$PYTHON" -m json.tool "$PROJECT/videocontext_settings.json" >/dev/null
echo "✅ Установка и проверки завершены."
echo "Теперь запусти VideoContext Engine.app заново и проверь сначала короткий ролик."
