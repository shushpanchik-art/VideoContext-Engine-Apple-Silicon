#!/bin/bash
# Deterministic NSR A/B: MLX/Metal baseline, then llama.cpp/Metal; Whisper remains PyTorch/MPS.
# The private forensic bundle contains both passes and is uploaded only to Dropbox.
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="/Users/artemijvisnevskij/VideoContext-Engine-Apple-Silicon"
VIDEO="/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"
APP="/Applications/VideoContext Engine.app"
API="http://127.0.0.1:7555"
SETTINGS="$ROOT/videocontext_settings.json"

# Pin all downloaded tooling to the same reviewed commit, never to a moving branch.
TOOLS_COMMIT="108a6d06dcadada647c8184d0c86d34b7049cd73"
TOOLS_DIR="$HOME/Library/Application Support/VideoContext Engine/forensic-tools/nsrspec-ab-$TOOLS_COMMIT"
OUT_PARENT="$HOME/Downloads/VCE_forensic_runs_NSRSPEC_AB"
STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_ROOT="$OUT_PARENT/$STAMP"
ORIGINAL_API_SETTINGS="$OUT_ROOT/active_settings_before.json"
DROPBOX_REMOTE="${VCE_DROPBOX_REMOTE:-}"
DROPBOX_BASE="${VCE_DROPBOX_BASE:-20261104_224554_1f098d/VCE_Forensic_Runs}"
API_READY=0
RESTORE_DONE=0
RUN1_EXIT=99
RUN2_EXIT=99

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 2; }
note() { printf '\n[VCE NSRSPEC A/B] %s\n' "$*"; }

restore_settings() {
  if [[ "$API_READY" != "1" || ! -f "$ORIGINAL_API_SETTINGS" ]]; then
    RESTORE_DONE=1
    return 0
  fi
  note "Восстанавливаю исходные API-настройки (недопустимые старые GGUF в vlm_model/text_llm_model нормализуются в MLX)."
  if "$PY" - "$API" "$ORIGINAL_API_SETTINGS" "$OUT_ROOT/settings_restore_report.json" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

api, source, report_path = sys.argv[1:]
original = json.loads(Path(source).read_text(encoding="utf-8"))
restore_keys = {
    "runtime_profile", "enable_mlx_whisper", "inference_backend",
    "vlm_model", "text_llm_model", "whisper_model", "whisper_acceleration",
    "word_timestamps", "vlm_resolution", "enable_ocr_cache", "enable_ffmpeg_ocr",
    "ffmpeg_ocr_interval", "ffmpeg_ocr_workers", "enable_apple_ocr",
    "enable_stage_timing", "text_llm_enabled",
    "llama_cpp_vlm_model", "llama_cpp_vlm_mmproj",
    "llama_cpp_text_llm_model", "llama_cpp_text_context",
}
payload = {key: original[key] for key in restore_keys if key in original}
normalizations = {}
valid_vlm = {
    "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    "mlx-community/Qwen3-VL-4B-Instruct-4bit",
}
if "vlm_model" in payload and payload["vlm_model"] not in valid_vlm:
    normalizations["vlm_model"] = {"original": payload["vlm_model"], "restored": "mlx-community/Qwen3-VL-8B-Instruct-4bit"}
    payload["vlm_model"] = "mlx-community/Qwen3-VL-8B-Instruct-4bit"
if "text_llm_model" in payload and payload["text_llm_model"] != "mlx-community/Qwen3.5-9B-MLX-4bit":
    normalizations["text_llm_model"] = {"original": payload["text_llm_model"], "restored": "mlx-community/Qwen3.5-9B-MLX-4bit"}
    payload["text_llm_model"] = "mlx-community/Qwen3.5-9B-MLX-4bit"

def request(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(api.rstrip("/") + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

if not payload:
    raise SystemExit("No restorable backend settings were present in the original API snapshot.")
post_result = request("POST", "/api/v1/ui-settings", payload)
if not post_result.get("ok"):
    raise SystemExit("Settings restore endpoint did not confirm success: " + repr(post_result))
current = request("GET", "/api/v1/ui-settings")
mismatches = {key: {"expected": value, "actual": current.get(key)} for key, value in payload.items() if current.get(key) != value}
report = {
    "status": "passed" if not mismatches else "failed",
    "restored_keys": sorted(payload),
    "normalizations": normalizations,
    "mismatches": mismatches,
}
Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
if mismatches:
    raise SystemExit("Settings restore verification failed.")
PY
  then
    RESTORE_DONE=1
  else
    RESTORE_DONE=0
    return 1
  fi
}

finish() {
  status=$?
  trap - EXIT
  if [[ "$RESTORE_DONE" != "1" && "$API_READY" == "1" ]]; then
    set +e
    restore_settings
    restore_status=$?
    set -e
    if [[ "$restore_status" -ne 0 && "$status" -eq 0 ]]; then status=2; fi
  fi
  exit "$status"
}
trap finish EXIT

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ -d "$APP" ]] || fail "Не найдено приложение: $APP"
[[ -f "$VIDEO" ]] || fail "Не найден видеофайл: $VIDEO"
[[ -x "$ROOT/.venv/bin/python" ]] || fail "Не найден Python проекта: $ROOT/.venv/bin/python"
PY="$ROOT/.venv/bin/python"
[[ -f "$SETTINGS" ]] || fail "Не найден файл настроек: $SETTINGS"
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git."
command -v ffprobe >/dev/null 2>&1 || fail "Не найден ffprobe. Установи FFmpeg."
command -v rclone >/dev/null 2>&1 || fail "Не найден rclone; Dropbox upload обязателен."

mkdir -p "$TOOLS_DIR" "$OUT_ROOT"
exec > >(tee -a "$OUT_ROOT/launcher.log") 2>&1

# Resolve and validate the Dropbox remote before spending time on either analysis.
if [[ -z "$DROPBOX_REMOTE" ]]; then
  REMOTES="$(rclone listremotes)"
  if printf '%s\n' "$REMOTES" | grep -Fxq 'vce-dropbox:'; then
    DROPBOX_REMOTE='vce-dropbox'
  elif printf '%s\n' "$REMOTES" | grep -Fxq 'dropbox:'; then
    DROPBOX_REMOTE='dropbox'
  else
    printf 'Доступные rclone remotes:\n%s\n' "$REMOTES"
    fail "Dropbox remote не найден; задай VCE_DROPBOX_REMOTE."
  fi
fi
REMOTE_TYPE="$(rclone config show "$DROPBOX_REMOTE" 2>/dev/null | awk -F ' = ' '$1 == "type" {print $2; exit}')"
[[ "$REMOTE_TYPE" == dropbox ]] || fail "Remote $DROPBOX_REMOTE не является Dropbox."

# The .pth hook must be loaded when the app's Python process starts.
if curl -fsS --max-time 2 "$API/openapi.json" >/dev/null 2>&1; then
  fail "Локальный API уже запущен. Полностью закрой VideoContext Engine и повтори запуск, чтобы mtmd .pth-хук загрузился до старта сервера."
fi
if /usr/bin/pgrep -f "$APP/Contents/MacOS/" >/dev/null 2>&1; then
  fail "Процесс VideoContext Engine уже запущен. Полностью закрой приложение, затем повтори запуск."
fi

note "Скачиваю watchdog, observer и mtmd-патч из одного pinned-коммита $TOOLS_COMMIT."
BASE_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$TOOLS_COMMIT/scripts"
curl -fsSL "$BASE_URL/vce_runtime_watchdog.py" -o "$TOOLS_DIR/vce_runtime_watchdog.py"
curl -fsSL "$BASE_URL/vce_observed_llama_cpp_run.py" -o "$TOOLS_DIR/vce_observed_llama_cpp_run.py"
curl -fsSL "$BASE_URL/install_vce_mtmd_loader_fix.py" -o "$TOOLS_DIR/install_vce_mtmd_loader_fix.py"
[[ "$(git hash-object "$TOOLS_DIR/vce_runtime_watchdog.py")" == "50e91ffabcf2cb6174b068a12eea0b6b909cba5e" ]] || fail "Не прошла проверка целостности watchdog."
[[ "$(git hash-object "$TOOLS_DIR/vce_observed_llama_cpp_run.py")" == "c087d7c5fa00f697453bd917bcae7d4d6947c6be" ]] || fail "Не прошла проверка целостности observer."
[[ "$(git hash-object "$TOOLS_DIR/install_vce_mtmd_loader_fix.py")" == "16b1f22af3af081940d556d98020640ca4051c2e" ]] || fail "Не прошла проверка целостности mtmd installer."

note "Ставлю venv-scoped mtmd resolver hook до запуска API."
"$PY" "$TOOLS_DIR/install_vce_mtmd_loader_fix.py" --report "$OUT_ROOT/mtmd_installer_report.json"

note "Запускаю нативное приложение VideoContext Engine."
open -a "$APP"

note "Жду локальный API $API (до 120 секунд)."
API_READY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "$API/openapi.json" >/dev/null 2>&1; then API_READY=1; break; fi
  sleep 2
done
[[ "$API_READY" == "1" ]] || fail "Локальный API не поднялся. Проверь ~/Library/Logs/VideoContext Engine/server.log."

# API and settings schema preflight happens before either video request.
"$PY" - "$API" "$ORIGINAL_API_SETTINGS" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

api, snapshot = sys.argv[1:]
def get(path):
    with urllib.request.urlopen(api.rstrip("/") + path, timeout=15) as response:
        return json.load(response)

spec = get("/openapi.json")
operation = spec.get("paths", {}).get("/api/v1/analyze", {}).get("post", {})
schema = operation.get("requestBody", {}).get("content", {}).get("multipart/form-data", {}).get("schema", {})
ref = schema.get("$ref")
if ref:
    schema = spec.get("components", {}).get("schemas", {}).get(ref.rsplit("/", 1)[-1], {})
props = schema.get("properties", {})
if "runtime_profile" not in props:
    raise SystemExit("STOP: API lacks runtime_profile; no video was submitted.")
settings = get("/api/v1/ui-settings")
required = {
    "inference_backend", "llama_cpp_vlm_model", "llama_cpp_vlm_mmproj",
    "llama_cpp_text_llm_model", "vlm_model", "text_llm_model",
}
missing = sorted(required - set(settings))
if missing:
    raise SystemExit("STOP: API lacks A/B settings: " + ", ".join(missing))
Path(snapshot).write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("API runtime_profile + MLX/llama.cpp setting schema: verified")
print("Original active settings snapshot:", snapshot)
PY

# The .pth hook must affect the exact interpreter used by the app and observer.
"$PY" - <<'PY'
import ctypes.util
from pathlib import Path
from llama_cpp import llama_cpp
import llama_cpp.mtmd_cpp as mtmd_cpp

if ctypes.util.find_library("mtmd") is not None:
    raise SystemExit("STOP: find_library('mtmd') still resolves a system library.")
package_lib = (Path(llama_cpp.__file__).resolve().parent / "lib" / "libmtmd.dylib").resolve()
loaded_lib = Path(str(mtmd_cpp._libmtmd._name)).resolve()
if not package_lib.is_file() or loaded_lib != package_lib:
    raise SystemExit(f"STOP: wrong/missing mtmd library: {loaded_lib}; expected {package_lib}")
getattr(mtmd_cpp._libmtmd, "mtmd_get_video_config")
from llama_cpp.llama_multimodal import Qwen3VLChatHandler
if not callable(getattr(llama_cpp, "llama_supports_gpu_offload", None)) or not llama_cpp.llama_supports_gpu_offload():
    raise SystemExit("STOP: llama-cpp-python was not built with Metal GPU offload.")
print("mtmd_cpp bundled ABI + Qwen3VL handler import + Metal offload: verified")
PY

# Whisper MPS DTW patch is required by the shared sequential profile.
"$PY" - <<'PY'
import inspect
import whisper.timing
source = inspect.getsource(whisper.timing)
if "x.cpu().double().numpy()" not in source:
    raise SystemExit("STOP: Whisper MPS DTW fix is not installed; no video was submitted.")
print("Whisper MPS DTW fix: verified")
PY

run_backend() {
  backend="$1"
  pass_dir="$2"
  mkdir -p "$pass_dir"
  note "Стартую $backend; Whisper остаётся PyTorch/MPS, OCR cache ON, FFmpeg OCR 5s/3 workers."
  "$PY" "$TOOLS_DIR/vce_runtime_watchdog.py" --video "$VIDEO" --settings "$SETTINGS" --runner "$TOOLS_DIR/vce_observed_llama_cpp_run.py" --api-url "$API" --out-root "$pass_dir" --interval 1.0 --inference-backend "$backend" --powermetrics --vmmap --sample-stacks
}

note "Прогон 1/2: mlx_metal (MLX/Metal VLM + Text LLM; PyTorch/MPS Whisper)."
set +e
run_backend "mlx_metal" "$OUT_ROOT/run1_mlx_metal"
RUN1_EXIT=$?
set -e
printf 'RUN1_MLX_METAL_EXIT=%s\n' "$RUN1_EXIT"

if [[ "$RUN1_EXIT" -eq 130 ]]; then
  RUN2_EXIT=125
  note "Прогон 1 прерван пользователем; прогон 2 пропущен, чтобы не запускать анализ при потенциально активной серверной задаче."
else
  note "Прогон 2/2: llama_cpp_metal (GGUF VLM + Text LLM; PyTorch/MPS Whisper)."
  set +e
  run_backend "llama_cpp_metal" "$OUT_ROOT/run2_llama_cpp_metal"
  RUN2_EXIT=$?
  set -e
  printf 'RUN2_LLAMA_CPP_METAL_EXIT=%s\n' "$RUN2_EXIT"
fi

# Restore even if either analysis failed; the trap retries restoration on an early exit.
restore_settings || fail "Не удалось восстановить исходные API-настройки. Проверь settings_restore_report.json и videocontext_settings.json."

"$PY" - "$OUT_ROOT/ab_summary.json" "$RUN1_EXIT" "$RUN2_EXIT" "$TOOLS_COMMIT" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

path, run1, run2, commit = sys.argv[1:]
data = {
    "created_at": datetime.now().astimezone().isoformat(),
    "tooling_commit": commit,
    "runs": [
        {"pass": 1, "inference_backend": "mlx_metal", "exit_code": int(run1), "directory": "run1_mlx_metal"},
        {"pass": 2, "inference_backend": "llama_cpp_metal", "exit_code": int(run2), "directory": "run2_llama_cpp_metal"},
    ],
    "settings_restored": True,
    "note": "Both passes use baseline_mps_sequential Whisper/PyTorch-MPS. GGUF selectors are only in llama_cpp_*; vlm_model/text_llm_model remain MLX identifiers.",
}
Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(data, ensure_ascii=False))
PY

ARCHIVE="$OUT_PARENT/VCE_NSRSPEC_AB_$STAMP.zip"
note "Создаю один архив с обоими прогонами и preflight."
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$ARCHIVE"
LOCAL_SIZE="$(stat -f '%z' "$ARCHIVE")"
printf 'Локальный архив: %s (%s bytes)\n' "$ARCHIVE" "$LOCAL_SIZE"

DEST="$DROPBOX_REMOTE:$DROPBOX_BASE"
REMOTE_FILE="$DEST/$(basename "$ARCHIVE")"
note "Загружаю общий приватный A/B архив в Dropbox: $REMOTE_FILE"
rclone mkdir "$DEST"
rclone copyto "$ARCHIVE" "$REMOTE_FILE" --progress
REMOTE_SIZE="$(rclone size "$REMOTE_FILE" --json | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("bytes", -1))')"
[[ "$REMOTE_SIZE" == "$LOCAL_SIZE" ]] || fail "Dropbox verification failed: local=$LOCAL_SIZE remote=$REMOTE_SIZE. Архив остался локально."
printf '\nDROPBOX_UPLOAD=SUCCESS\nDROPBOX_REMOTE=%s\nDROPBOX_PATH=%s\nDROPBOX_SIZE=%s\n' "$DROPBOX_REMOTE" "$REMOTE_FILE" "$REMOTE_SIZE"
printf '\n=== A/B ГОТОВО ===\nПрогон 1 mlx_metal: %s\nПрогон 2 llama_cpp_metal: %s\nАрхив: %s\nИсходное видео не копировалось в Dropbox; загружен только forensic архив.\n' "$RUN1_EXIT" "$RUN2_EXIT" "$ARCHIVE"

if [[ "$RUN1_EXIT" -eq 0 && "$RUN2_EXIT" -eq 0 ]]; then
  exit 0
fi
exit 1
