#!/bin/bash
# Canonical installer for the reviewed NSR A/B / Qwen3.5 JSON consistency patch.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${VCE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
APP="${VCE_APP:-/Applications/VideoContext Engine.app}"
API="${VCE_API_URL:-http://127.0.0.1:7555}"
VIDEO="${VCE_VIDEO:-$HOME/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4}"
PATCH_REF="${VCE_PATCH_REF:-main}"
BASE_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$PATCH_REF"
SMOKE_SHA="dd33e2cba4988894e7142061bf5ec2d77f4cc655"
PY="$ROOT/.venv/bin/python"
LOG_PARENT="${VCE_OUT_PARENT:-$HOME/Downloads/VCE_forensic_runs_NSRSPEC_AB}"
SKIP_AB="${VCE_SKIP_AB:-0}"

usage() { echo "Usage: $0 [--root PATH] [--app PATH] [--api URL] [--video PATH] [--out-parent PATH] [--skip-ab]"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) [[ $# -ge 2 ]] || { echo "--root requires a path" >&2; exit 2; }; ROOT="$2"; shift 2 ;;
    --app) [[ $# -ge 2 ]] || { echo "--app requires a path" >&2; exit 2; }; APP="$2"; shift 2 ;;
    --api) [[ $# -ge 2 ]] || { echo "--api requires a URL" >&2; exit 2; }; API="$2"; shift 2 ;;
    --video) [[ $# -ge 2 ]] || { echo "--video requires a path" >&2; exit 2; }; VIDEO="$2"; shift 2 ;;
    --out-parent) [[ $# -ge 2 ]] || { echo "--out-parent requires a path" >&2; exit 2; }; LOG_PARENT="$2"; shift 2 ;;
    --skip-ab) SKIP_AB=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
ROOT="$(cd "$ROOT" 2>/dev/null && pwd)" || { echo "Project root not found: $ROOT" >&2; exit 2; }
PY="$ROOT/.venv/bin/python"
STAMP="$(date '+%Y%m%d_%H%M%S')"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/vce-qwen35-json-patch.XXXXXX")"
LOG_DIR="$LOG_PARENT/patch_validation_$STAMP"
RUN_LOG="$LOG_DIR/patch_install.log"
BACKUP="$HOME/Downloads/VCE_patch_backups_NSRSPEC_AB/$STAMP"
PATCH_INSTALL_STARTED=0
REGRESSION_TESTS_PASSED=0
AB_STARTED=0
MISSING_FILES=""
FILES="
VideoContextEngine_v3.19.py 58c79194f0626d18d739febf6f4924a837d5005d
tools/bench/vce_observed_llama_cpp_run.py 8d97e3a36f7d9bd37768af81bd4ac4925e7cda6d
tools/bench/vce_run_nsrspec_ab.command f2e4bb3cef5f6d080567bd15d82674200e0b1e9a
tools/bench/vce_runtime_watchdog.py 6d60468a5ba2251d0c639fb917018234d5a0bf89
tools/bench/test_vce_llama_cpp_nsrspec_runner.py 6955aa7dfed5759518ad451e7603cd34fd17bb81
tools/bench/vce_patch_test_run_nsrspec_ab.command 4e1b23a108bc986131c0ba2b56e78d5d64d2a267
domain_profiles/__init__.py 00c837b7966783e071ec17ebfecf347cfe6dd12d
domain_profiles/nsrspec.py aada467c7060b44487e9d2e3ee038c1b67b7f098
tools/bench/install_vce_mtmd_loader_fix.py 934739bd0b5f70dc26665adc115a181d365330e0
"

upload_patch_validation_logs() {
  [[ -d "$LOG_DIR" ]] || return 0
  mkdir -p "$LOG_PARENT" || return 1
  local archive="$LOG_PARENT/VCE_PATCH_VALIDATION_$STAMP.zip"
  /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$LOG_DIR" "$archive" || return 1
  if ! command -v rclone >/dev/null 2>&1; then
    printf "Patch validation archive kept locally: %s\n" "$archive"
    return 0
  fi
  local remote="${VCE_DROPBOX_REMOTE:-}"
  if [[ -z "$remote" ]]; then
    local remotes
    remotes="$(rclone listremotes 2>/dev/null || true)"
    if printf '%s\n' "$remotes" | grep -Fxq 'vce-dropbox:'; then
      remote="vce-dropbox"
    elif printf '%s\n' "$remotes" | grep -Fxq 'dropbox:'; then
      remote="dropbox"
    else
      printf "No Dropbox remote; validation archive kept locally: %s\n" "$archive"
      return 0
    fi
  fi
  if [[ "$(rclone config show "$remote" 2>/dev/null | awk -F ' = ' '$1 == "type" {print $2; exit}')" != "dropbox" ]]; then
    printf "Remote %s is not Dropbox; validation archive kept locally: %s\n" "$remote" "$archive"
    return 0
  fi
  local dest="$remote:20261104_224554_1f098d/VCE_forensic_runs_NSRSPEC_AB/$(basename "$archive")"
  if ! rclone copyto "$archive" "$dest" --progress; then
    printf "Dropbox upload failed; validation archive kept locally: %s\n" "$archive"
    return 0
  fi
  local local_size remote_size
  local_size="$(stat -f '%z' "$archive")"
  remote_size="$(rclone size "$dest" --json 2>/dev/null | sed -nE 's/.*"bytes"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p')"
  if [[ -z "$remote_size" || "$remote_size" != "$local_size" ]]; then
    printf "Dropbox verification failed; local validation archive retained: %s\n" "$archive"
    return 0
  fi
  printf 'PATCH_VALIDATION_DROPBOX_UPLOAD=SUCCESS\nPATCH_VALIDATION_DROPBOX_PATH=%s\nPATCH_VALIDATION_DROPBOX_SIZE=%s\n' "$dest" "$remote_size"
}
cleanup() {
  local status=$?
  trap - EXIT
  if [[ "$PATCH_INSTALL_STARTED" == "1" && "$REGRESSION_TESTS_PASSED" != "1" && -d "$BACKUP" ]]; then
    printf '\nRegression validation failed; restoring original files from %s\n' "$BACKUP" >&2
    while IFS=' ' read -r REL SHA; do
      [[ -n "$REL" ]] || continue
      if [[ -f "$BACKUP/$REL" ]]; then
        mkdir -p "$ROOT/$(dirname "$REL")"
        /usr/bin/install -m 0644 "$BACKUP/$REL" "$ROOT/$REL" || status=2
      elif [[ "$MISSING_FILES" == *"|$REL|"* ]]; then
        rm -f "$ROOT/$REL" || status=2
      fi
    done <<< "$FILES"
  fi
  if [[ "$AB_STARTED" != "1" && -d "$LOG_DIR" ]]; then
    if upload_patch_validation_logs; then
      :
    else
      printf '\nPatch validation logs remain local: %s\n' "$LOG_DIR" >&2
      :
    fi
  fi
  rm -rf "$STAGE"
  exit "$status"
}
trap cleanup EXIT

mkdir -p "$LOG_DIR"
exec > >(tee -a "$RUN_LOG") 2>&1
printf 'PATCH_REF=%s\nPATCH_LOG=%s\n' "$PATCH_REF" "$RUN_LOG"

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ "$(git -C "$ROOT" rev-parse --is-inside-work-tree 2>/dev/null || true)" == true ]] || fail "Не найден Git checkout: $ROOT"
[[ -x "$PY" ]] || fail "Не найден project Python: $PY"
if [[ "$SKIP_AB" != "1" ]]; then
  [[ -f "$VIDEO" ]] || fail "Не найдено обязательное тестовое видео: $VIDEO"
  [[ -d "$APP" ]] || fail "Не найдено приложение: $APP"
fi
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git."
API_PIDS="$(/usr/sbin/lsof -nP -tiTCP:7555 -sTCP:LISTEN 2>/dev/null || true)"
APP_PIDS="$(/usr/bin/pgrep -f "$APP/Contents/MacOS/" 2>/dev/null || true)"
if [[ -n "$API_PIDS" || -n "$APP_PIDS" ]] || curl -fsS --max-time 2 "$API/openapi.json" >/dev/null 2>&1; then
  printf 'API listener PID(s): %s\nApp PID(s): %s\n' "${API_PIDS:-none}" "${APP_PIDS:-none}"
  fail "Полностью закройте VideoContext Engine (⌘Q) и дождитесь освобождения порта 7555."
fi

# Refuse to overwrite local edits or untracked files in patch targets.
while IFS=' ' read -r REL SHA; do
  [[ -n "$REL" ]] || continue
  if git -C "$ROOT" ls-files --error-unmatch "$REL" >/dev/null 2>&1; then
    if ! git -C "$ROOT" diff --quiet -- "$REL" || ! git -C "$ROOT" diff --cached --quiet -- "$REL"; then
      fail "Есть локальные изменения в $REL; сохраните их отдельно и повторите."
    fi
    [[ -f "$ROOT/$REL" ]] || fail "Отслеживаемый файл отсутствует: $REL"
  else
    [[ ! -e "$ROOT/$REL" ]] || fail "Новый путь уже занят локальным файлом: $REL"
    MISSING_FILES="${MISSING_FILES}|$REL|"
  fi
  mkdir -p "$STAGE/$(dirname "$REL")"
  curl -fsSL "$BASE_URL/$REL" -o "$STAGE/$REL"
  ACTUAL="$(git -C "$ROOT" hash-object "$STAGE/$REL")"
  [[ "$ACTUAL" == "$SHA" ]] || fail "SHA mismatch для $REL: expected=$SHA actual=$ACTUAL"
done <<< "$FILES"

SMOKE_SOURCE="$STAGE/tools/bench/vce_llama_json_smoke_test.py"
if [[ "$SKIP_AB" != "1" ]]; then
  mkdir -p "$(dirname "$SMOKE_SOURCE")"
  curl -fsSL "$BASE_URL/tools/bench/vce_llama_json_smoke_test.py" -o "$SMOKE_SOURCE"
  ACTUAL="$(git -C "$ROOT" hash-object "$SMOKE_SOURCE")"
  [[ "$ACTUAL" == "$SMOKE_SHA" ]] || fail "SHA mismatch для tools/bench/vce_llama_json_smoke_test.py: expected=$SMOKE_SHA actual=$ACTUAL"
fi

mkdir -p "$BACKUP"
while IFS=' ' read -r REL SHA; do
  [[ -n "$REL" ]] || continue
  if [[ -f "$ROOT/$REL" ]]; then
    mkdir -p "$BACKUP/$(dirname "$REL")"
    cp -p "$ROOT/$REL" "$BACKUP/$REL"
  fi
done <<< "$FILES"

PATCH_INSTALL_STARTED=1
while IFS=' ' read -r REL SHA; do
  [[ -n "$REL" ]] || continue
  mkdir -p "$ROOT/$(dirname "$REL")"
  /usr/bin/install -m 0644 "$STAGE/$REL" "$ROOT/$REL"
done <<< "$FILES"
chmod 755 "$ROOT/tools/bench/vce_run_nsrspec_ab.command" "$ROOT/tools/bench/vce_patch_test_run_nsrspec_ab.command"

printf '\nPATCH_INSTALL=SUCCESS\nPATCH_BACKUP=%s\n' "$BACKUP"
cd "$ROOT"

printf '\n=== Python compile ===\n'
"$PY" -m py_compile \
  "$ROOT/VideoContextEngine_v3.19.py" \
  "$ROOT/tools/bench/vce_observed_llama_cpp_run.py" \
  "$ROOT/tools/bench/vce_runtime_watchdog.py" \
  "$ROOT/domain_profiles/__init__.py" \
  "$ROOT/domain_profiles/nsrspec.py"
if [[ "$SKIP_AB" != "1" ]]; then
  "$PY" -m py_compile "$SMOKE_SOURCE"
fi

printf '\n=== Full regression suite ===\n'
"$PY" -m pytest -q tests
"$PY" -m pytest -q "$ROOT/tools/bench/test_vce_llama_cpp_nsrspec_runner.py"
printf '\n=== Shell syntax ===\n'
bash -n "$ROOT/tools/bench/vce_run_nsrspec_ab.command"
bash -n "$ROOT/tools/bench/vce_run_nsrspec_llama_cpp.command"
printf '\nREGRESSION_TESTS=PASSED\n'
REGRESSION_TESTS_PASSED=1

# Short real-model probe (256 tokens) is diagnostic only; full A/B still runs.
SMOKE_LOG="$LOG_DIR/qwen35_json_smoke.log"
if [[ "$SKIP_AB" == "1" ]]; then
  printf "SKIPPED: VCE_SKIP_AB=1; no real model was loaded during installer validation.\\n" | tee "$SMOKE_LOG"
  SMOKE_EXIT=125
else
  set +e
  "$PY" "$SMOKE_SOURCE" --max-tokens 256 2>&1 | tee "$SMOKE_LOG"
  SMOKE_EXIT=${PIPESTATUS[0]}
  set -e
fi
printf 'QWEN35_JSON_SMOKE_EXIT=%s\n' "$SMOKE_EXIT"

COMBINED_LOG="$LOG_DIR/patch_and_smoke_preflight.log"
{
  printf '=== PATCH INSTALL / REGRESSION LOG ===\n'
  cat "$RUN_LOG"
  printf '\n=== QWEN3.5 JSON SMOKE LOG ===\n'
  cat "$SMOKE_LOG"
} > "$COMBINED_LOG"

printf '\nStarting NSR A/B. Settings will be restored; a local forensic ZIP is retained and Dropbox upload is optional.\n'
AB_STARTED=1
rm -rf "$STAGE"
trap - EXIT
export VCE_ROOT="$ROOT" VCE_VIDEO="$VIDEO" VCE_APP="$APP" VCE_API_URL="$API" VCE_OUT_PARENT="$LOG_PARENT" VCE_PREFLIGHT_LOG="$COMBINED_LOG"
if [[ "$SKIP_AB" == "1" ]]; then
  printf "VCE_SKIP_AB=1; installer validation completed; analysis launch skipped.\n"
  exit 0
fi
/bin/bash "$ROOT/tools/bench/vce_run_nsrspec_ab.command"
