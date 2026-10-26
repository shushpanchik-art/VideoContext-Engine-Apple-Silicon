#!/bin/bash
set -Eeuo pipefail

PROJECT="$HOME/VideoContext-Engine"
PATCH_DIR="$PROJECT/patches"
PYTHON="$PROJECT/.venv/bin/python"
BRANCH="feat/vce-parallel-cpu-asr-vlm-20261025"
REPO="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$BRANCH"
STAMP="$(date +%Y%m%d_%H%M%S)"
ENGINE="$PROJECT/VideoContextEngine_v3.19.py"
ROLLBACK="$PROJECT/VideoContextEngine_v3.19.py.before_prefetch_media_recovery_\${STAMP}.bak"
TMPDIR_VCE="$(mktemp -d "\${TMPDIR:-/tmp}/vce-prefetch-recovery.XXXXXX")"
PREFETCH="$TMPDIR_VCE/patch_vce_bounded_frame_prefetch.py"
RECOVERY="$TMPDIR_VCE/patch_vce_mp4_recovery_retry.py"
ROLLBACK_READY=0
SUCCESS=0

fail() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 2; }

cleanup() {
  local status=$?
  if [[ "$ROLLBACK_READY" == "1" && "$SUCCESS" != "1" && -f "$ROLLBACK" ]]; then
    cp -p "$ROLLBACK" "$ENGINE"
    printf '\n⚠️ Установка завершилась с ошибкой. Исходный VideoContextEngine_v3.19.py восстановлен из:\n%s\n' "$ROLLBACK" >&2
  fi
  rm -rf "$TMPDIR_VCE"
  return "$status"
}
trap cleanup EXIT

printf '%s\n' \
  "VideoContext Engine — bounded frame prefetch + MP4/audio recovery" \
  "Проект: $PROJECT" \
  "Ветка GitHub: $BRANCH" \
  ""

[[ -d "$PROJECT" ]] || fail "Не найден проект: $PROJECT"
[[ -x "$PYTHON" ]] || fail "Не найден Python окружения VCE: $PYTHON"
[[ -f "$ENGINE" ]] || fail "Не найден движок: $ENGINE"
[[ -f "$PROJECT/videocontext_settings.json" ]] || fail "Не найден videocontext_settings.json"
command -v curl >/dev/null 2>&1 || fail "curl не найден."
command -v lsof >/dev/null 2>&1 || fail "lsof не найден."

if lsof -nP -tiTCP:7555 -sTCP:LISTEN >/dev/null 2>&1; then
  cat <<'MSG'
ОШИБКА: сервер VCE всё ещё слушает порт 7555.
Дождись окончания анализа, нажми в интерфейсе «Остановить сервер VCE»
и повтори команду. Установщик не будет завершать сервер принудительно.
Файлы проекта не изменены.
MSG
  exit 2
fi

echo "[1/6] Загружаю patchers из GitHub…"
curl -fL --retry 3 "$REPO/patches/patch_vce_mp4_recovery_retry.py" -o "$RECOVERY"
curl -fL --retry 3 "$REPO/patches/patch_vce_bounded_frame_prefetch.py" -o "$PREFETCH"
grep -q "VCE_MP4_PREFLIGHT_RECOVERY_RETRY_V2" "$RECOVERY" || fail "MP4 recovery patcher имеет неожиданный формат."
grep -q "VCE_BOUNDED_KEYFRAME_PREFETCH_V1" "$PREFETCH" || fail "Prefetch patcher имеет неожиданный формат."
"$PYTHON" -m py_compile "$RECOVERY" "$PREFETCH"

echo
echo "[2/6] Проверяю совместимость MP4 recovery (dry-run)…"
"$PYTHON" "$RECOVERY" --project "$PROJECT" --dry-run

echo
echo "[3/6] Проверяю совместимость bounded prefetch (dry-run)…"
"$PYTHON" "$PREFETCH" --project "$PROJECT" --dry-run

cat <<'MSG'

Что будет применено:
  • ограниченный prefetch keyframes следующей сцены (очередь 1, один CPU worker);
  • при ошибках контейнера/кодека — попытка FFmpeg remux, затем H.264/AAC transcode;
  • если видео не восстановить, но аудио читается, переход в audio-only режим:
    Whisper и сводка по аудио остаются доступны, VLM и OCR выключаются только для этого видео;
  • исходное видео никогда не перезаписывается;
  • если аудио тоже не удастся прочитать, файл получит диагностическую ошибку, а другие файлы не должны
    потерять уже сохранённые результаты; автоматическое продолжение пакетной очереди зависит от UI/API.
  • резервные копии и восстановление движка, если установка не завершится.
MSG

echo
read -r -p "Чтобы применить оба патча, введи APPLY: " answer
[[ "$answer" == "APPLY" ]] || { echo "Отменено; файлы проекта не изменены."; exit 0; }

echo
echo "[4/6] Создаю резервную копию движка и сохраняю patchers…"
cp -p "$ENGINE" "$ROLLBACK"
ROLLBACK_READY=1
mkdir -p "$PATCH_DIR"
for spec in \
  "patch_vce_mp4_recovery_retry.py:$RECOVERY" \
  "patch_vce_bounded_frame_prefetch.py:$PREFETCH"; do
  name="\${spec%%:*}"
  src="\${spec#*:}"
  dest="$PATCH_DIR/$name"
  if [[ -f "$dest" ]] && ! cmp -s "$src" "$dest"; then
    cp -p "$dest" "$dest.before_prefetch_media_recovery_\${STAMP}.bak"
  fi
  cp "$src" "$dest"
  chmod 700 "$dest"
done

echo
echo "[5/6] Применяю MP4 recovery и bounded prefetch…"
"$PYTHON" "$PATCH_DIR/patch_vce_mp4_recovery_retry.py" --project "$PROJECT"
"$PYTHON" "$PATCH_DIR/patch_vce_bounded_frame_prefetch.py" --project "$PROJECT"

echo
echo "[6/6] Проверяю итоговый движок и настройки…"
"$PYTHON" -m py_compile "$ENGINE"
"$PYTHON" -m json.tool "$PROJECT/videocontext_settings.json" >/dev/null
grep -q "VCE_MP4_PREFLIGHT_RECOVERY_RETRY_V2" "$ENGINE" || fail "Не найден marker MP4 recovery после установки."
grep -q "VCE_BOUNDED_KEYFRAME_PREFETCH_V1" "$ENGINE" || fail "Не найден marker bounded prefetch после установки."
SUCCESS=1

printf '\n✅ Установка завершена.\n'
printf 'Резервная копия исходного движка: %s\n' "$ROLLBACK"
printf '%s\n' \
  "Перезапусти VideoContext Engine.app и сначала протестируй проблемный MP4 и короткий обычный ролик." \
  "В логах ищи [MP4 recovery], [Audio-only fallback] и [Frame prefetch]."
