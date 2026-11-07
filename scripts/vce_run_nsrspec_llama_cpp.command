#!/bin/bash
# One-video forensic runner: PyTorch/MPS Whisper + llama.cpp/Metal VLM and Text LLM.
# Opens only the native macOS app. Full forensic archive is uploaded to private Dropbox.
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="/Users/artemijvisnevskij/VideoContext-Engine-Apple-Silicon"
VIDEO="/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"
APP="/Applications/VideoContext Engine.app"
API="http://127.0.0.1:7555"
SETTINGS="$ROOT/videocontext_settings.json"
PY="$ROOT/.venv/bin/python"
TOOLS_COMMIT="2b0b0a0cd86ac05f851af704db0f7c02e1a317ef"
TOOLS_DIR="$HOME/Library/Application Support/VideoContext Engine/forensic-tools/llama-cpp-$TOOLS_COMMIT"
OUT_PARENT="$HOME/Downloads/VCE_forensic_runs_NSRSPEC_llama_cpp"
STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_ROOT="$OUT_PARENT/$STAMP"
DROPBOX_REMOTE="${VCE_DROPBOX_REMOTE:-}"
DROPBOX_BASE="${VCE_DROPBOX_BASE:-20261104_224554_1f098d/VCE_Forensic_Runs}"
RUN_EXIT=0

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 2; }
note() { printf '\n[VCE llama.cpp forensic] %s\n' "$*"; }

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ -d "$APP" ]] || fail "Не найдено приложение: $APP"
[[ -f "$VIDEO" ]] || fail "Не найден видеофайл: $VIDEO"
[[ -x "$PY" ]] || fail "Не найден Python проекта: $PY"
[[ -f "$SETTINGS" ]] || fail "Не найден файл настроек: $SETTINGS"
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git."
command -v ffprobe >/dev/null 2>&1 || fail "Не найден ffprobe. Установи FFmpeg."
command -v rclone >/dev/null 2>&1 || fail "Не найден rclone; полный архив не сможет загрузиться в Dropbox."

mkdir -p "$TOOLS_DIR" "$OUT_ROOT"
exec > >(tee -a "$OUT_ROOT/launcher.log") 2>&1

note "Загружаю закреплённые forensic-инструменты из GitHub."
BASE_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$TOOLS_COMMIT/scripts"
curl -fsSL "$BASE_URL/vce_runtime_watchdog.py" -o "$TOOLS_DIR/vce_runtime_watchdog.py"
curl -fsSL "$BASE_URL/vce_observed_llama_cpp_run.py" -o "$TOOLS_DIR/vce_observed_llama_cpp_run.py"
[[ "$(git hash-object "$TOOLS_DIR/vce_runtime_watchdog.py")" == "277454bfe54df801ae879dfe1a925d4344431df9" ]] || fail "Не прошла проверка целостности watchdog."
[[ "$(git hash-object "$TOOLS_DIR/vce_observed_llama_cpp_run.py")" == "29b8e20916148bf50ef6fc2434cca02c46fe544d" ]] || fail "Не прошла проверка целостности observer."

note "Открываю только нативное приложение VideoContext Engine."
open -a "$APP"

note "Жду локальный API $API (до 120 секунд)."
API_READY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "$API/openapi.json" >/dev/null 2>&1; then API_READY=1; break; fi
  sleep 2
done
[[ "$API_READY" == "1" ]] || fail "Локальный API не поднялся. Проверь ~/Library/Logs/VideoContext Engine/server.log."

# Hard preflight: do not start an incomparable run on the old runtime.
"$PY" - "$API" <<'PY'
import json, sys, urllib.request
base = sys.argv[1].rstrip("/")
def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.load(r)
spec = get("/openapi.json")
op = spec.get("paths", {}).get("/api/v1/analyze", {}).get("post", {})
schema = op.get("requestBody", {}).get("content", {}).get("multipart/form-data", {}).get("schema", {})
ref = schema.get("$ref")
if ref:
    schema = spec.get("components", {}).get("schemas", {}).get(ref.rsplit("/", 1)[-1], {})
props = schema.get("properties", {})
if "runtime_profile" not in props:
    raise SystemExit("STOP: API lacks runtime_profile. Install PR #38 runtime first; no video was submitted.")
settings = get("/api/v1/ui-settings")
required = {"inference_backend", "llama_cpp_vlm_model", "llama_cpp_vlm_mmproj", "llama_cpp_text_llm_model"}
missing = sorted(required - set(settings))
if missing:
    raise SystemExit("STOP: API lacks llama.cpp backend settings: " + ", ".join(missing))
print("Runtime profile + llama.cpp settings schema: verified")
PY

# Whisper MPS DTW patch is required by the canonical sequential MPS profile.
"$PY" - <<'PY'
import inspect, whisper.timing
source = inspect.getsource(whisper.timing)
if "x.cpu().double().numpy()" not in source:
    raise SystemExit("STOP: Whisper MPS DTW fix is not installed. No video was submitted.")
print("Whisper MPS DTW fix: verified")
PY

# Verify this exact Python environment has the Qwen3-VL handler and Metal offload.
"$PY" - <<'PY'
from llama_cpp import llama_cpp
try:
    from llama_cpp.llama_multimodal import Qwen3VLChatHandler
except ImportError:
    from llama_cpp.llama_chat_format import Qwen3VLChatHandler
if not callable(getattr(llama_cpp, "llama_supports_gpu_offload", None)) or not llama_cpp.llama_supports_gpu_offload():
    raise SystemExit("STOP: llama-cpp-python was not built with Metal GPU offload.")
print("Qwen3VLChatHandler + Metal GPU offload: verified")
PY

note "Preflight passed. Запускаю один NSR прогон: Whisper large/PyTorch-MPS; VLM Qwen3-VL-8B Q4_K_M; Text LLM Qwen3.5-9B Q4_K_M; OCR cache ON; FFmpeg OCR 5s/3 workers."
printf 'Видео: %s\n' "$VIDEO"
printf 'Результаты: %s\n' "$OUT_ROOT"
printf 'Dropbox destination: %s:%s\n' "${DROPBOX_REMOTE:-auto-detect}" "$DROPBOX_BASE"
printf 'Во время прогона не перезапускай приложение и не запускай второй анализ.\n'

set +e
"$PY" "$TOOLS_DIR/vce_runtime_watchdog.py" \
  --video "$VIDEO" \
  --settings "$SETTINGS" \
  --runner "$TOOLS_DIR/vce_observed_llama_cpp_run.py" \
  --api-url "$API" \
  --out-root "$OUT_ROOT" \
  --interval 1.0 \
  --powermetrics \
  --vmmap \
  --sample-stacks
RUN_EXIT=$?
set -e

WATCHDOG="$(find "$OUT_ROOT" -maxdepth 1 -type d -name 'watchdog_*' -print | sort | tail -n 1)"
if [[ -z "$WATCHDOG" || ! -d "$WATCHDOG" ]]; then
  printf 'watchdog_exit_code=%s\nvideo=%s\n' "$RUN_EXIT" "$VIDEO" > "$OUT_ROOT/launcher_failure.txt"
  fail "Watchdog не создал каталог результатов. Локальные логи: $OUT_ROOT"
fi
cp "$OUT_ROOT/launcher.log" "$WATCHDOG/launcher.log"
ARCHIVE="$OUT_PARENT/VCE_NSRSPEC_LLAMA_CPP_$STAMP.zip"
note "Создаю единый ZIP forensic-архив."
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$ARCHIVE"
LOCAL_SIZE="$(stat -f '%z' "$ARCHIVE")"
printf 'Локальный архив: %s (%s bytes)\n' "$ARCHIVE" "$LOCAL_SIZE"

if [[ -z "$DROPBOX_REMOTE" ]]; then
  REMOTES="$(rclone listremotes)"
  if printf '%s\n' "$REMOTES" | grep -Fxq 'vce-dropbox:'; then
    DROPBOX_REMOTE='vce-dropbox'
  elif printf '%s\n' "$REMOTES" | grep -Fxq 'dropbox:'; then
    DROPBOX_REMOTE='dropbox'
  else
    printf 'Доступные rclone remotes:\n%s\n' "$REMOTES"
    fail "Dropbox remote не найден. Архив остался локально: $ARCHIVE"
  fi
fi
REMOTE_TYPE="$(rclone config show "$DROPBOX_REMOTE" 2>/dev/null | awk -F ' = ' '$1 == "type" {print $2; exit}')"
[[ "$REMOTE_TYPE" == dropbox ]] || fail "Remote $DROPBOX_REMOTE не является Dropbox. Архив остался локально: $ARCHIVE"
DEST="$DROPBOX_REMOTE:$DROPBOX_BASE"
REMOTE_FILE="$DEST/$(basename "$ARCHIVE")"
note "Загружаю полный forensic архив в приватную папку Dropbox: $REMOTE_FILE"
rclone mkdir "$DEST"
rclone copyto "$ARCHIVE" "$REMOTE_FILE" --progress
REMOTE_SIZE="$(rclone size "$REMOTE_FILE" --json | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("bytes", -1))')"
[[ "$REMOTE_SIZE" == "$LOCAL_SIZE" ]] || fail "Dropbox verification failed: local=$LOCAL_SIZE remote=$REMOTE_SIZE. Архив остался локально."
printf '\nDROPBOX_UPLOAD=SUCCESS\nDROPBOX_REMOTE=%s\nDROPBOX_PATH=%s\nDROPBOX_SIZE=%s\n' "$DROPBOX_REMOTE" "$REMOTE_FILE" "$REMOTE_SIZE"
printf '\n=== ГОТОВО ===\nКод анализа: %s\nПапка диагностики: %s\nАрхив: %s\nИсходное видео не загружалось. Полный архив предназначен только для приватного Dropbox.\n' "$RUN_EXIT" "$WATCHDOG" "$ARCHIVE"
exit "$RUN_EXIT"
