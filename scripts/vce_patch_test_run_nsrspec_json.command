#!/bin/bash
# Pinned installer for the Qwen3.5 llama.cpp JSON prompting/diagnostics patch.
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="/Users/artemijvisnevskij/VideoContext-Engine-Apple-Silicon"
APP="/Applications/VideoContext Engine.app"
API="http://127.0.0.1:7555"
VIDEO="/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"
PATCH_COMMIT="1c47f198432efb7b112f15337d0631978a3b6e5e"
BASE_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$PATCH_COMMIT"
PY="$ROOT/.venv/bin/python"
STAMP="$(date '+%Y%m%d_%H%M%S')"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/vce-qwen35-json-patch.XXXXXX")"
LOG_PARENT="$HOME/Downloads/VCE_forensic_runs_NSRSPEC_AB"
LOG_DIR="$LOG_PARENT/patch_validation_$STAMP"
RUN_LOG="$LOG_DIR/patch_install.log"
BACKUP="$HOME/Downloads/VCE_patch_backups_NSRSPEC_AB/$STAMP"
PATCH_INSTALL_STARTED=0
REGRESSION_TESTS_PASSED=0
AB_STARTED=0
MISSING_FILES=""

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 2; }

FILES="
VideoContextEngine_v3.19.py e89b8aeba1a6ed52da4c4be2b5e358c793dafa2f
scripts/vce_observed_llama_cpp_run.py 6d61f261044bfdc78c39cc00f5a8c873208f95aa
scripts/vce_run_nsrspec_ab.command 6d88ccd51a6d600bca6c5f544185f46635594917
scripts/vce_llama_json_smoke_test.py dd33e2cba4988894e7142061bf5ec2d77f4cc655
tests/test_vce_llama_cpp_nsrspec_runner.py acba1194715cef8159c5f5fb81bb3efb4371cf13
"

upload_patch_validation_logs() {
  [[ -d "$LOG_DIR" ]] || return 1
  mkdir -p "$LOG_PARENT"
  local archive="$LOG_PARENT/VCE_PATCH_VALIDATION_$STAMP.zip"
  /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$LOG_DIR" "$archive" || return 1
  command -v rclone >/dev/null 2>&1 || return 1
  local remote="${VCE_DROPBOX_REMOTE:-}"
  if [[ -z "$remote" ]]; then
    local remotes
    remotes="$(rclone listremotes 2>/dev/null || true)"
    if printf '%s\n' "$remotes" | grep -Fxq 'vce-dropbox:'; then
      remote="vce-dropbox"
    elif printf '%s\n' "$remotes" | grep -Fxq 'dropbox:'; then
      remote="dropbox"
    else
      return 1
    fi
  fi
  [[ "$(rclone config show "$remote" 2>/dev/null | awk -F ' = ' '$1 == "type" {print $2; exit}')" == "dropbox" ]] || return 1
  local dest="$remote:20261104_224554_1f098d/VCE_forensic_runs_NSRSPEC_AB/$(basename "$archive")"
  rclone copyto "$archive" "$dest" --progress || return 1
  local local_size remote_size
  local_size="$(stat -f '%z' "$archive")"
  remote_size="$(rclone size "$dest" --json | sed -nE 's/.*"bytes"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p')"
  [[ -n "$remote_size" && "$remote_size" == "$local_size" ]] || return 1
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
      [[ "$status" -ne 0 ]] || status=3
    fi
  fi
  rm -rf "$STAGE"
  exit "$status"
}
trap cleanup EXIT

mkdir -p "$LOG_DIR"
exec > >(tee -a "$RUN_LOG") 2>&1
printf 'PATCH_COMMIT=%s\nPATCH_LOG=%s\n' "$PATCH_COMMIT" "$RUN_LOG"

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ "$(git -C "$ROOT" rev-parse --is-inside-work-tree 2>/dev/null || true)" == true ]] || fail "Не найден Git checkout: $ROOT"
[[ -x "$PY" ]] || fail "Не найден project Python: $PY"
[[ -f "$VIDEO" ]] || fail "Не найдено обязательное тестовое видео: $VIDEO"
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git."
[[ -d "$APP" ]] || fail "Не найдено приложение: $APP"

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
chmod 755 "$ROOT/scripts/vce_run_nsrspec_ab.command"

printf '\nPATCH_INSTALL=SUCCESS\nPATCH_BACKUP=%s\n' "$BACKUP"
cd "$ROOT"

printf '\n=== Python compile ===\n'
"$PY" -m py_compile \
  "$ROOT/VideoContextEngine_v3.19.py" \
  "$ROOT/scripts/vce_observed_llama_cpp_run.py" \
  "$ROOT/scripts/vce_llama_json_smoke_test.py"

printf '\n=== Full regression suite ===\n'
"$PY" -m pytest -q tests
printf '\n=== Shell syntax ===\n'
bash -n "$ROOT/scripts/vce_run_nsrspec_ab.command"
bash -n "$ROOT/scripts/vce_run_nsrspec_llama_cpp.command"
printf '\nREGRESSION_TESTS=PASSED\n'
REGRESSION_TESTS_PASSED=1

# Short real-model probe (256 tokens) is diagnostic only; full A/B still runs.
SMOKE_LOG="$LOG_DIR/qwen35_json_smoke.log"
set +e
"$PY" "$ROOT/scripts/vce_llama_json_smoke_test.py" --max-tokens 256 2>&1 | tee "$SMOKE_LOG"
SMOKE_EXIT=${PIPESTATUS[0]}
set -e
printf 'QWEN35_JSON_SMOKE_EXIT=%s\n' "$SMOKE_EXIT"

COMBINED_LOG="$LOG_DIR/patch_and_smoke_preflight.log"
{
  printf '=== PATCH INSTALL / REGRESSION LOG ===\n'
  cat "$RUN_LOG"
  printf '\n=== QWEN3.5 JSON SMOKE LOG ===\n'
  cat "$SMOKE_LOG"
} > "$COMBINED_LOG"

printf '\nStarting full NSR A/B. Its EXIT trap will restore API settings, collect logs, create SHA256SUMS, ZIP, upload to Dropbox, and verify remote size.\n'
AB_STARTED=1
rm -rf "$STAGE"
trap - EXIT
export VCE_PREFLIGHT_LOG="$COMBINED_LOG"
/bin/bash "$ROOT/scripts/vce_run_nsrspec_ab.command"
