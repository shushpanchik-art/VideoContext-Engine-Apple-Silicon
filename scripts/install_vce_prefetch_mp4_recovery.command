#!/bin/bash
set -Eeuo pipefail

PROJECT="$HOME/VideoContext-Engine"
PATCH_DIR="$PROJECT/patches"
PYTHON="$PROJECT/.venv/bin/python"
BRANCH="feat/vce-parallel-cpu-asr-vlm-20261025"
MP4_PATCH="$PATCH_DIR/patch_vce_mp4_recovery_retry.py"
PREFETCH_PATCH="$PATCH_DIR/patch_vce_bounded_frame_prefetch.py"
MP4_RAW="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$BRANCH/patches/patch_vce_mp4_recovery_retry.py"
PREFETCH_RAW="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$BRANCH/patches/patch_vce_bounded_frame_prefetch.py"

fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 2; }
MASTER_BACKUP=""
WORK="/tmp/vce-prefetch-mp4.$$"

cleanup() { rm -rf "$WORK"; }
restore_engine() {
  if [[ -n "$MASTER_BACKUP" && -f "$MASTER_BACKUP" ]]; then
    cp -p "$MASTER_BACKUP" "$PROJECT/VideoContextEngine_v3.19.py"
    echo "ОШИБКА: установка завершилась неудачно; движок восстановлен из общей резервной копии." >&2
  fi
}
trap 'status=$?; if [[ $status -ne 0 ]]; then restore_engine; fi; cleanup' EXIT

printf '%s\n' \
  "VideoContext Engine — MP4 recovery/audio-only fallback + bounded frame prefetch" \
  "Проект: $PROJECT" \
  "Источник: GitHub / $BRANCH" \
  "Очередь предзагрузки: одна ожидающая сцена + максимум одна сцена в CPU worker" \
  "MP4 fallback: trun/tfhd → stream-copy remux → H.264/AAC transcode → audio-only, если аудио читается" \
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
Я не буду останавливать его автоматически, чтобы не прервать анализ.
Дождись завершения всех задач, нажми «Остановить сервер VCE» в интерфейсе,
затем снова выполни эту команду.
Файлы проекта не изменены.
MSG
  exit 2
fi

mkdir -p "$WORK" "$PATCH_DIR"
echo "[1/7] Загружаю патчеры из GitHub…"
curl -fL --retry 3 "$MP4_RAW" -o "$WORK/patch_vce_mp4_recovery_retry.py"
curl -fL --retry 3 "$PREFETCH_RAW" -o "$WORK/patch_vce_bounded_frame_prefetch.py"
grep -q "VCE_MP4_PREFLIGHT_RECOVERY_RETRY_V2" "$WORK/patch_vce_mp4_recovery_retry.py" || fail "MP4-патчер не прошёл проверку содержимого."
grep -q "VCE_FRAGMENTED_MP4_REMUX_FIRST" "$WORK/patch_vce_mp4_recovery_retry.py" || fail "MP4 recovery patch lacks updated trun/tfhd strategy."
grep -q "VCE_BOUNDED_KEYFRAME_PREFETCH_V1" "$WORK/patch_vce_bounded_frame_prefetch.py" || fail "Prefetch-патчер не прошёл проверку содержимого."
"$PYTHON" -m py_compile "$WORK/patch_vce_mp4_recovery_retry.py" "$WORK/patch_vce_bounded_frame_prefetch.py"

echo
echo "[2/7] Проверяю совместимость MP4 recovery (dry-run)…"
"$PYTHON" "$WORK/patch_vce_mp4_recovery_retry.py" --project "$PROJECT" --dry-run

echo
echo "[3/7] Проверяю совместимость bounded prefetch (dry-run)…"
"$PYTHON" "$WORK/patch_vce_bounded_frame_prefetch.py" --project "$PROJECT" --dry-run

echo
cat <<'MSG'
В состав входит:
  1. Восстановление MP4 до OpenCV preflight: сначала перекодирование в H.264/AAC,
     затем remux без перекодирования. Оригинал не перезаписывается.
  2. Если видеоряд восстановить нельзя, но аудио извлекается, анализ продолжается
     в audio-only режиме: Whisper и сводка остаются включены, VLM/OCR/визуальная
     детекция отключаются. В первом сегменте отчёта будет предупреждение.
  3. Если ни видео, ни аудио не читается, VCE вернёт диагностическую ошибку.
  4. Bounded frame prefetch — CPU готовит следующую сцену, пока GPU анализирует текущую.
     Очередь ограничена одной сценой, сцены не меняются местами, есть sync fallback.
MSG

read -r -p "Чтобы применить оба патча, введи APPLY: " answer
[[ "$answer" == "APPLY" ]] || { echo "Отменено; файлы проекта не изменены."; exit 0; }

echo
echo "[4/7] Устанавливаю проверенные патчеры…"
cp "$WORK/patch_vce_mp4_recovery_retry.py" "$MP4_PATCH"
cp "$WORK/patch_vce_bounded_frame_prefetch.py" "$PREFETCH_PATCH"
chmod 700 "$MP4_PATCH" "$PREFETCH_PATCH"

STAMP="$(date +%Y%m%d_%H%M%S)"
MASTER_BACKUP="$PROJECT/VideoContextEngine_v3.19.py.before_prefetch_mp4_bundle_$STAMP.bak"
cp -p "$PROJECT/VideoContextEngine_v3.19.py" "$MASTER_BACKUP"
echo "Общая резервная копия движка: $MASTER_BACKUP"

echo "[5/7] Применяю MP4 recovery/audio-only fallback…"
"$PYTHON" "$MP4_PATCH" --project "$PROJECT"

echo "[6/7] Применяю bounded frame prefetch…"
"$PYTHON" "$PREFETCH_PATCH" --project "$PROJECT"

echo
echo "[7/7] Проверяю синтаксис Python и JSON…"
"$PYTHON" -m py_compile "$PROJECT/VideoContextEngine_v3.19.py"
"$PYTHON" -m json.tool "$PROJECT/videocontext_settings.json" >/dev/null
echo "✅ Оба патча применены; Python и JSON проверены."
echo "Общая резервная копия: $MASTER_BACKUP"
echo "Патчеры и индивидуальные резервные копии находятся в проекте."
echo "Перезапусти VideoContext Engine.app. Проверь сначала проблемный MP4."
trap cleanup EXIT
