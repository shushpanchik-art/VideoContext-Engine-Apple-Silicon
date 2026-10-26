#!/bin/bash
set -Eeuo pipefail

PROJECT="$HOME/VideoContext-Engine"
PATCH_DIR="$PROJECT/patches"
PATCH="$PATCH_DIR/patch_vce_bounded_frame_prefetch.py"
PYTHON="$PROJECT/.venv/bin/python"
BRANCH="feat/vce-parallel-cpu-asr-vlm-20261025"
RAW="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$BRANCH/patches/patch_vce_bounded_frame_prefetch.py"

fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 2; }

printf '%s\n' \
  "VideoContext Engine — bounded prefetch кадров CPU → GPU" \
  "Проект: $PROJECT" \
  "Очередь кадров: 1 ожидающая сцена + не более 1 сцены в работе" \
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
grep -q "VCE_BOUNDED_KEYFRAME_PREFETCH_V1" "$DOWNLOAD" || fail "Загруженный файл не похож на ожидаемый патч."
"$PYTHON" -m py_compile "$DOWNLOAD"

echo
echo "[2/5] Проверяю совместимость и показываю diff (ничего не записывается)…"
"$PYTHON" "$DOWNLOAD" --project "$PROJECT" --dry-run

echo
cat <<'MSG'
Патч создаёт отдельный CPU-поток, который заранее декодирует/подготавливает
кадры следующих сцен. VLM/OCR продолжают обрабатывать текущую сцену.
Ограничение памяти: 1 ожидающая сцена + не более 1 сцены в работе; порядок сцен сохраняется.
При ошибке подготовки есть синхронный fallback; исходное видео не меняется.
MSG
read -r -p "Чтобы применить патч, введи APPLY: " answer
[[ "$answer" == "APPLY" ]] || { echo "Отменено; файлы проекта не изменены."; exit 0; }

echo
echo "[3/5] Сохраняю патчер в проекте…"
mv "$DOWNLOAD" "$PATCH"
trap - EXIT
chmod 700 "$PATCH"

echo "[4/5] Применяю патч и создаю резервную копию…"
"$PYTHON" "$PATCH" --project "$PROJECT"

echo
echo "[5/5] Проверяю Python и JSON…"
"$PYTHON" -m py_compile "$PROJECT/VideoContextEngine_v3.19.py"
"$PYTHON" -m json.tool "$PROJECT/videocontext_settings.json" >/dev/null
echo "✅ Установка и проверки завершены."
echo "Перезапусти VideoContext Engine.app перед тестом."
