#!/bin/bash
# Install the reviewed Text LLM / diagnostics patch, run regression tests, then launch A/B.
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="/Users/artemijvisnevskij/VideoContext-Engine-Apple-Silicon"
APP="/Applications/VideoContext Engine.app"
API="http://127.0.0.1:7555"
PATCH_COMMIT="506e36021e2de629f0eae1776f439319a94f8fbb"
BASE_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$PATCH_COMMIT"
PY="$ROOT/.venv/bin/python"
STAMP="$(date '+%Y%m%d_%H%M%S')"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/vce-nsrspec-patch.XXXXXX")"
BACKUP="$HOME/Downloads/VCE_patch_backups_NSRSPEC_AB/$STAMP"

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 2; }
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ "$(git -C "$ROOT" rev-parse --is-inside-work-tree 2>/dev/null || true)" == true ]] || fail "Не найден Git checkout: $ROOT"
[[ -x "$PY" ]] || fail "Не найден project Python: $PY"
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git."
command -v bash >/dev/null 2>&1 || fail "Не найден bash."

# The installed Python process must not keep the old module loaded while files are patched.
API_PIDS="$(/usr/sbin/lsof -nP -tiTCP:7555 -sTCP:LISTEN 2>/dev/null || true)"
APP_PIDS="$(/usr/bin/pgrep -f "$APP/Contents/MacOS/" 2>/dev/null || true)"
if [[ -n "$API_PIDS" || -n "$APP_PIDS" ]] || curl -fsS --max-time 2 "$API/openapi.json" >/dev/null 2>&1; then
  printf 'API listener PID(s): %s\n' "${API_PIDS:-не обнаружены}"
  printf 'VideoContext Engine PID(s): %s\n' "${APP_PIDS:-не обнаружены}"
  fail "Сначала полностью заверши VideoContext Engine через Quit (⌘Q) и дождись закрытия API на порту 7555."
fi

# Refuse to overwrite local edits in the exact files being installed.
FILES="
VideoContextEngine_v3.19.py fe76b7831dd75c133457ba3d91488ac65da8460b
scripts/vce_observed_llama_cpp_run.py 39fff57545c50500c3ce241fcb10ae3004204c0c
scripts/vce_run_nsrspec_ab.command a90639ab6f0f5fdec9f8960c8d5318ad35758b62
scripts/vce_runtime_watchdog.py 50e91ffabcf2cb6174b068a12eea0b6b909cba5e
scripts/install_vce_mtmd_loader_fix.py 934739bd0b5f70dc26665adc115a181d365330e0
scripts/vce_run_nsrspec_llama_cpp.command 31356e628780a37470d3d3c7c4321c45a3cbaef4
tests/test_vce_llama_cpp_nsrspec_runner.py dfa9573f5e0a6e67fee82d90d483500a524488e8
requirements-llama-cpp-metal.txt e0a8628d8d41510e17dc8c771330323c26a1d90e
"
while IFS=" " read -r REL SHA; do
  [[ -n "$REL" ]] || continue
  git -C "$ROOT" ls-files --error-unmatch "$REL" >/dev/null 2>&1 || fail "Файл не отслеживается Git; не перезаписываю его автоматически: $REL"
  if ! git -C "$ROOT" diff --quiet -- "$REL" || ! git -C "$ROOT" diff --cached --quiet -- "$REL"; then
    fail "Есть локальные изменения в $REL. Сохрани их отдельно/закоммить и повтори запуск."
  fi
  [[ -f "$ROOT/$REL" ]] || fail "Не найден локальный файл для безопасного backup: $ROOT/$REL"
  [[ -f "$ROOT/$REL" ]] || fail "Не найден локальный файл для безопасного backup: $ROOT/$REL"
  mkdir -p "$STAGE/$(dirname "$REL")"
  curl -fsSL "$BASE_URL/$REL" -o "$STAGE/$REL"
  ACTUAL="$(git -C "$ROOT" hash-object "$STAGE/$REL")"
  [[ "$ACTUAL" == "$SHA" ]] || fail "SHA mismatch для $REL: expected=$SHA actual=$ACTUAL"
done <<< "$FILES"

mkdir -p "$BACKUP"
while IFS=" " read -r REL SHA; do
  [[ -n "$REL" ]] || continue
  mkdir -p "$BACKUP/$(dirname "$REL")" "$ROOT/$(dirname "$REL")"
  cp -p "$ROOT/$REL" "$BACKUP/$REL"
  /usr/bin/install -m 0644 "$STAGE/$REL" "$ROOT/$REL"
done <<< "$FILES"
chmod 755 "$ROOT/scripts/vce_run_nsrspec_ab.command" "$ROOT/scripts/vce_run_nsrspec_llama_cpp.command"

printf '\nPATCH_INSTALL=SUCCESS\nPATCH_COMMIT=%s\nPATCH_BACKUP=%s\n' "$PATCH_COMMIT" "$BACKUP"
printf '\n=== Regression suite ===\n'
cd "$ROOT"
"$PY" "$ROOT/tests/test_vce_llama_cpp_nsrspec_runner.py"
bash -n "$ROOT/scripts/vce_run_nsrspec_ab.command"
bash -n "$ROOT/scripts/vce_run_nsrspec_llama_cpp.command"
printf '\nREGRESSION_TESTS=PASSED\n\n'

# The A/B launcher installs the venv-scoped MTMD resolver, performs real MTMD preflight,
# runs MLX baseline first, then llama.cpp comparison, restores settings and uploads ZIP.
exec /bin/bash "$ROOT/scripts/vce_run_nsrspec_ab.command"
