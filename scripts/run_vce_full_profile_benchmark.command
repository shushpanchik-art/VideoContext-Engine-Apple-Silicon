#!/bin/bash
set -uo pipefail

REPOSITORY="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
SOURCE_REF="650f3a1437ee0cdcab1c3ea41005a599a373d578"
PUBLISH_BRANCH="feat/experimental-benchmark-launcher"
RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY}/${SOURCE_REF}"
SCRIPT_URL="${RAW_BASE}/scripts/vce_full_profile_benchmark.py.gz"
PATCH_URL="${RAW_BASE}/scripts/patch_vce_full_profile_benchmark.py"
VIDEO_DEFAULT="${VCE_VIDEO_PATH:-/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4}"
PROJECT_DIR="${VCE_PROJECT_DIR:-$HOME/VideoContext-Engine}"
RESULT_ROOT="${VCE_BENCH_RESULT_ROOT:-$HOME/Downloads/VCE_Full_Profile_Benchmark}"

TMP_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/vce_full_profile_benchmark.XXXXXX")"
TMP_GZIP="${TMP_DIR}/runner.py.gz"
TMP_SCRIPT="${TMP_DIR}/runner.py"
TMP_PATCH="${TMP_DIR}/patch_runner.py"
MARKER="${TMP_DIR}/run-start.marker"
cleanup() { /bin/rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM

fail() { echo "ERROR: $*" >&2; exit 2; }

[[ "$(uname -s)" == "Darwin" ]] || fail "Этот benchmark предназначен для macOS на Apple Silicon."
[[ "$(uname -m)" == "arm64" ]] || fail "Обнаружена не arm64 архитектура. Нужен Apple Silicon Mac."
[[ -f "$VIDEO_DEFAULT" ]] || fail "Видео не найдено: $VIDEO_DEFAULT"
[[ -d "$PROJECT_DIR" ]] || fail "Не найден проект VCE: $PROJECT_DIR. Установите проект или задайте VCE_PROJECT_DIR."
[[ -f "$PROJECT_DIR/.venv/bin/python" ]] || echo "NOTICE: .venv/bin/python не найден; будет использован системный python3."

PYTHON="${PROJECT_DIR}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then PYTHON="$(command -v python3 || true)"; fi
[[ -n "$PYTHON" && -x "$PYTHON" ]] || fail "Не найден Python 3.10+."
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || fail "Требуется Python 3.10+."

[[ -n "$(command -v git || true)" ]] || fail "Не найден git."
command -v gh >/dev/null 2>&1 || fail "Для автоматической публикации установите GitHub CLI: brew install gh"
gh auth status >/dev/null 2>&1 || fail "GitHub CLI не авторизован. Один раз выполните: gh auth login"
gh auth setup-git >/dev/null
git ls-remote --exit-code --heads "https://github.com/$REPOSITORY.git" "$PUBLISH_BRANCH" >/dev/null 2>&1 || fail "Не найдена ветка публикации $PUBLISH_BRANCH или нет доступа к ней."

echo "VCE benchmark pinned source: ${SOURCE_REF}"
echo "Input video: ${VIDEO_DEFAULT}"
echo "Project: ${PROJECT_DIR}"
echo "Run plan: baseline -> each experimental feature separately -> all experimental features."
/usr/bin/curl --fail --location --silent --show-error --retry 3 -H "Cache-Control: no-cache" -H "Pragma: no-cache" "$SCRIPT_URL" --output "$TMP_GZIP"
/usr/bin/gzip -t "$TMP_GZIP" || fail "Сжатый benchmark runner повреждён."
/usr/bin/gzip -dc "$TMP_GZIP" > "$TMP_SCRIPT"
/usr/bin/curl --fail --location --silent --show-error --retry 3 -H "Cache-Control: no-cache" -H "Pragma: no-cache" "$PATCH_URL" --output "$TMP_PATCH"
"$PYTHON" "$TMP_PATCH" "$TMP_SCRIPT" || fail "Не удалось применить совместимый patch к runner."
touch "$MARKER"
mkdir -p "$RESULT_ROOT"

set +e
"$PYTHON" "$TMP_SCRIPT" --project "$PROJECT_DIR" --video "$VIDEO_DEFAULT" "$@" 2>&1 | /usr/bin/tee "$TMP_DIR/runner_console.log"
RUN_EXIT=${PIPESTATUS[0]}
set -e
echo "Benchmark runner exit code: $RUN_EXIT"

# Publish only aggregate tables and error summaries. Never publish video, transcript,
# full API response, OCR text, model output, or raw process/server logs.
RUN_DIR="$(/usr/bin/find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d -newer "$MARKER" -print 2>/dev/null | /usr/bin/sort | /usr/bin/tail -n 1)"
if [[ -z "$RUN_DIR" || ! -d "$RUN_DIR" ]]; then
  STAMP_FALLBACK="$(/bin/date -u +%Y%m%dT%H%M%SZ)"
  RUN_DIR="$RESULT_ROOT/launcher-failed-$STAMP_FALLBACK"
  mkdir -p "$RUN_DIR"
fi
if [[ -f "$TMP_DIR/runner_console.log" ]]; then
  cp "$TMP_DIR/runner_console.log" "$RUN_DIR/launcher_console.log"
fi

PUBLISH_DIR="${TMP_DIR}/publish-repo"
git clone --quiet --branch "$PUBLISH_BRANCH" "https://github.com/$REPOSITORY.git" "$PUBLISH_DIR" || {
  echo "ERROR: не удалось клонировать ветку $PUBLISH_BRANCH. Результаты: $RUN_DIR"
  exit 4
}

STAMP="$(/bin/date -u +%Y%m%dT%H%M%SZ)"
DEST="$PUBLISH_DIR/benchmark-results/$STAMP"
mkdir -p "$DEST"
for name in summary.md stage_comparison.csv run_summary.csv errors.csv; do
  if [[ -f "$RUN_DIR/$name" ]]; then
    "$PYTHON" - "$RUN_DIR/$name" "$DEST/$name" "$VIDEO_DEFAULT" "$HOME" <<'PY'
import pathlib, sys
src, dst, video, home = map(str, sys.argv[1:])
data = pathlib.Path(src).read_text(encoding="utf-8", errors="replace")
data = data.replace(video, "<INPUT_VIDEO>").replace(home, "<HOME>")
pathlib.Path(dst).write_text(data, encoding="utf-8")
PY
  fi
done
if [[ -f "$TMP_DIR/runner_console.log" ]]; then
  "$PYTHON" - "$TMP_DIR/runner_console.log" "$DEST/launcher_errors.txt" "$VIDEO_DEFAULT" "$HOME" <<'PY'
import pathlib, re, sys
src, dst, video, home = map(str, sys.argv[1:])
lines = pathlib.Path(src).read_text(encoding="utf-8", errors="replace").splitlines()
pattern = re.compile(r"error|exception|traceback|failed|fatal|timed out|segmentation fault", re.I)
selected = [line.replace(video, "<INPUT_VIDEO>").replace(home, "<HOME>") for line in lines if pattern.search(line)]
pathlib.Path(dst).write_text("\n".join(selected[-500:]) + ("\n" if selected else ""), encoding="utf-8")
PY
fi
{
  echo "benchmark_timestamp_utc=$STAMP"
  echo "runner_exit_code=$RUN_EXIT"
  echo "source_revision=$SOURCE_REF"
  echo "profiles=baseline,adaptive_keyframes,ocr_cache,mlx_whisper,fast_scene_detection,all_experimental"
  echo "input_video=<redacted>"
} > "$DEST/launcher_status.txt"

git -C "$PUBLISH_DIR" add "benchmark-results/$STAMP"
if git -C "$PUBLISH_DIR" -c user.name="$(gh api user --jq .login)" -c user.email="$(gh api user --jq .id)+$(gh api user --jq .login)@users.noreply.github.com" commit -m "Add local Apple Silicon benchmark results $STAMP"; then
  git -C "$PUBLISH_DIR" push origin "$PUBLISH_BRANCH" || {
    echo "ERROR: commit создан локально, но push не выполнен. Результаты: $RUN_DIR"
    exit 5
  }
  echo "Published aggregate benchmark report: benchmark-results/$STAMP"
else
  echo "ERROR: не удалось создать commit с результатами."
  exit 6
fi

echo "Local full diagnostics (private; not uploaded): $RUN_DIR"
exit "$RUN_EXIT"
