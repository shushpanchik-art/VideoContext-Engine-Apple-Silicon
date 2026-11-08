#!/bin/bash
# Deterministic NSR A/B: MLX/Metal baseline, then llama.cpp/Metal; Whisper remains PyTorch/MPS.
# The private forensic bundle contains both passes and is uploaded only to Dropbox.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${VCE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
VIDEO="${VCE_VIDEO:-$HOME/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4}"
APP="${VCE_APP:-/Applications/VideoContext Engine.app}"
API="${VCE_API_URL:-http://127.0.0.1:7555}"
OUT_PARENT="${VCE_OUT_PARENT:-$HOME/Downloads/VCE_forensic_runs_NSRSPEC_AB}"
DIAGNOSTIC_LLAMA_CPP="${VCE_DIAGNOSTIC_LLAMA_CPP:-0}"

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 2; }
note() { printf '\n[VCE NSRSPEC A/B] %s\n' "$*"; }
usage() { echo "Usage: $0 [--root PATH] [--video PATH] [--app PATH] [--api URL] [--settings PATH] [--out-parent PATH] [--diagnostic-llama-cpp]"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) [[ $# -ge 2 ]] || fail "--root требует путь"; ROOT="$2"; shift 2 ;;
    --video) [[ $# -ge 2 ]] || fail "--video требует путь"; VIDEO="$2"; shift 2 ;;
    --app) [[ $# -ge 2 ]] || fail "--app требует путь"; APP="$2"; shift 2 ;;
    --api) [[ $# -ge 2 ]] || fail "--api требует URL"; API="$2"; shift 2 ;;
    --settings) [[ $# -ge 2 ]] || fail "--settings требует путь"; SETTINGS_OVERRIDE="$2"; shift 2 ;;
    --out-parent) [[ $# -ge 2 ]] || fail "--out-parent требует путь"; OUT_PARENT="$2"; shift 2 ;;
    --diagnostic-llama-cpp) DIAGNOSTIC_LLAMA_CPP=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Неизвестный аргумент: $1" ;;
  esac
done
ROOT="$(cd "$ROOT" 2>/dev/null && pwd)" || fail "Не найден каталог проекта: $ROOT"
SETTINGS="${SETTINGS_OVERRIDE:-${VCE_SETTINGS:-$ROOT/videocontext_settings.json}}"
PY="$ROOT/.venv/bin/python"
OBSERVER="$ROOT/tools/bench/vce_observed_llama_cpp_run.py"

MTMD_INSTALLER="$ROOT/tools/bench/install_vce_mtmd_loader_fix.py"
MTMD_INSTALLER_BLOB_SHA="934739bd0b5f70dc26665adc115a181d365330e0"
STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_ROOT="$OUT_PARENT/$STAMP"
ORIGINAL_API_SETTINGS="$OUT_ROOT/active_settings_before.json"
DROPBOX_REMOTE="${VCE_DROPBOX_REMOTE:-}"
DROPBOX_BASE="${VCE_DROPBOX_BASE:-20261104_224554_1f098d/VCE_forensic_runs_NSRSPEC_AB}"
LLAMA_CPP_PINNED_COMMIT="1dd04befbaf1277853eb188cc4513d6089a931dd"
VLM_HF_REPO_REVISION="f982a07559d4a2f6c8744d840bf6fccab30eea96"
API_READY=0
RESTORE_DONE=0
RESTORE_STATUS="not_attempted"
UPLOAD_DONE=0
RUN1_EXIT=99
RUN2_EXIT=99
RUN2_DIAGNOSTIC=0
DROPBOX_AVAILABLE=0

restore_settings() {
  if [[ ! -f "$ORIGINAL_API_SETTINGS" ]]; then
    RESTORE_DONE=1
    RESTORE_STATUS="not_needed"
    return 0
  fi
  if [[ ! -f "$SETTINGS" ]]; then
    RESTORE_DONE=0
    RESTORE_STATUS="failed"
    note "Не найден локальный settings-файл для резервного восстановления: $SETTINGS"
    return 1
  fi
  if [[ "$API_READY" != "1" ]]; then
    note "API недоступен; восстанавливаю исходные настройки напрямую в локальном JSON."
    if "$PY" "$OBSERVER" --restore-settings-offline --settings "$SETTINGS" --restore-from "$ORIGINAL_API_SETTINGS" --restore-report "$OUT_ROOT/settings_restore_report.json"; then
      RESTORE_DONE=1
      RESTORE_STATUS="restored_offline"
      return 0
    fi
    RESTORE_DONE=0
    RESTORE_STATUS="failed"
    return 1
  fi
  note "Восстанавливаю исходные API-настройки (недопустимые старые GGUF в vlm_model/text_llm_model нормализуются в MLX)."
  if "$PY" - "$API" "$ORIGINAL_API_SETTINGS" "$OUT_ROOT/settings_restore_report.json" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

api, source, report_path = sys.argv[1:]
original = json.loads(Path(source).read_text(encoding="utf-8"))
def request(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(api.rstrip("/") + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

# Use the current API setting surface as the restore allowlist so newly added settings are included.
current_before = request("GET", "/api/v1/ui-settings")
read_only_keys = {"ffmpeg_ocr_available", "ffmpeg_ocr_reason", "qwen35_text_stage_note"}
payload = {key: value for key, value in original.items() if key in current_before and key not in read_only_keys}
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
    RESTORE_STATUS="passed"
  else
    note "API-восстановление не удалось; перехожу к резервному восстановлению локального JSON."
    if "$PY" "$OBSERVER" --restore-settings-offline --settings "$SETTINGS" --restore-from "$ORIGINAL_API_SETTINGS" --restore-report "$OUT_ROOT/settings_restore_report.json"; then
      RESTORE_DONE=1
      RESTORE_STATUS="restored_offline"
      return 0
    fi
    RESTORE_DONE=0
    RESTORE_STATUS="failed"
    return 1
  fi
}

capture_external_logs() {
  local server_log="$HOME/Library/Logs/VideoContext Engine/server.log"
  local report="$OUT_ROOT/external_log_capture.txt"
  if [[ -f "$server_log" ]]; then
    if /bin/cp -p "$server_log" "$OUT_ROOT/server.log"; then
      printf "status=copied\nsource=%s\ndestination=%s\n" "$server_log" "$OUT_ROOT/server.log" > "$report"
    else
      printf "status=copy_failed\nsource=%s\n" "$server_log" > "$report"
      note "Не удалось скопировать app server.log; продолжаю сбор остальных логов."
    fi
  else
    printf "status=not_present\nsource=%s\n" "$server_log" > "$report"
  fi
}

upload_forensic_bundle() {
  [[ "$UPLOAD_DONE" == "1" ]] && return 0
  [[ -d "$OUT_ROOT" ]] || return 0
  mkdir -p "$OUT_PARENT" || return 1
  (cd "$OUT_ROOT" && find . -type f ! -name "SHA256SUMS.txt" -exec shasum -a 256 {} + > SHA256SUMS.txt) || return 1
  local archive="$OUT_PARENT/VCE_NSRSPEC_AB_$STAMP.zip"
  local file_count
  file_count="$(find "$OUT_ROOT" -type f ! -name "SHA256SUMS.txt" | wc -l | tr -d ' ')"
  if [[ "$file_count" -eq 0 ]]; then
    note "OUT_ROOT пуст; архив не создаю, upload отменён."
    return 1
  fi

  /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$archive" || return 1
  local local_size
  local_size="$(stat -f "%z" "$archive")" || return 1
  if [[ "$DROPBOX_AVAILABLE" != "1" ]]; then
    note "Dropbox недоступен или не настроен; forensic ZIP сохранён локально: $archive"
    UPLOAD_DONE=1
    return 0
  fi
  if [[ -z "$DROPBOX_REMOTE" ]]; then
    local remotes
    remotes="$(rclone listremotes 2>/dev/null || true)"
    if printf "%s\n" "$remotes" | grep -Fxq "vce-dropbox:"; then
      DROPBOX_REMOTE="vce-dropbox"
    elif printf "%s\n" "$remotes" | grep -Fxq "dropbox:"; then
      DROPBOX_REMOTE="dropbox"
    else
      note "Dropbox remote не найден; ZIP сохранён локально: $archive"
      return 1
    fi
  fi
  local remote_type
  remote_type="$(rclone config show "$DROPBOX_REMOTE" 2>/dev/null | awk -F " = " '$1 == "type" {print $2; exit}')"
  if [[ "$remote_type" != "dropbox" ]]; then
    note "Remote $DROPBOX_REMOTE не подтверждён как Dropbox; ZIP сохранён локально: $archive"
    return 1
  fi
  local destination="$DROPBOX_REMOTE:$DROPBOX_BASE"
  local remote_file="$destination/$(basename "$archive")"
  rclone mkdir "$destination" || return 1
  note "Загружаю полный forensic ZIP в Dropbox: $remote_file"
  rclone copyto "$archive" "$remote_file" --progress || return 1
  local remote_json remote_size
  remote_json="$(rclone size "$remote_file" --json)" || return 1
  remote_size="$(printf "%s" "$remote_json" | sed -nE "s/.*\"bytes\"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p")"
  if [[ -z "$remote_size" || "$remote_size" != "$local_size" ]]; then
    note "Dropbox size mismatch: local=$local_size remote=${remote_size:-unknown}; ZIP сохранён: $archive"
    return 1
  fi
  UPLOAD_DONE=1
  printf "DROPBOX_UPLOAD=SUCCESS\nDROPBOX_REMOTE=%s\nDROPBOX_PATH=%s\nDROPBOX_SIZE=%s\n" "$DROPBOX_REMOTE" "$remote_file" "$remote_size"
}

finish() {
  local status=$?
  local restore_status=0
  local upload_status=0
  trap - EXIT
  if [[ "$RESTORE_DONE" != "1" ]]; then
    set +e
    restore_settings
    restore_status=$?
    set -e
    if [[ "$restore_status" -ne 0 ]]; then
      RESTORE_STATUS="failed"
      note "Не удалось подтвердить восстановление API settings; сбор логов и Dropbox upload всё равно продолжаются."
      if [[ "$status" -eq 0 ]]; then status=2; fi
    fi
  fi
  if [[ -d "$OUT_ROOT" ]]; then
    capture_external_logs || true
    printf "launcher_exit_code=%s\\nsettings_restore_status=%s\\nvideo_path=%s\\ndropbox_target=%s:%s\\ndropbox_upload=attempted_by_exit_trap\\n" "$status" "$RESTORE_STATUS" "$VIDEO" "$DROPBOX_REMOTE" "$DROPBOX_BASE" > "$OUT_ROOT/finalization_status.txt" || true
    set +e
    upload_forensic_bundle
    upload_status=$?
    set -e
    if [[ "$upload_status" -ne 0 ]]; then
      note "Не удалось завершить ZIP/Dropbox upload; исходные forensic-файлы остались локально в $OUT_ROOT."
    fi
  fi
  exit "$status"
}
trap finish EXIT

mkdir -p "$OUT_ROOT"
exec > >(tee -a "$OUT_ROOT/launcher.log") 2>&1

if [[ -n "${VCE_PREFLIGHT_LOG:-}" && -f "$VCE_PREFLIGHT_LOG" ]]; then
  /bin/cp -p "$VCE_PREFLIGHT_LOG" "$OUT_ROOT/llama_json_smoke_preflight.log"
  note "Короткий llama.cpp JSON smoke test включён в forensic bundle."
fi

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ -d "$APP" ]] || fail "Не найдено приложение: $APP"
[[ -f "$VIDEO" ]] || fail "Не найден видеофайл: $VIDEO"
[[ -x "$PY" ]] || fail "Не найден Python проекта: $PY"
[[ -f "$SETTINGS" ]] || fail "Не найден файл настроек: $SETTINGS"
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git."
command -v ffprobe >/dev/null 2>&1 || fail "Не найден ffprobe. Установи FFmpeg."
if command -v rclone >/dev/null 2>&1; then
  if [[ -z "$DROPBOX_REMOTE" ]]; then
    REMOTES="$(rclone listremotes 2>/dev/null || true)"
    if printf '%s\n' "$REMOTES" | grep -Fxq 'vce-dropbox:'; then
      DROPBOX_REMOTE='vce-dropbox'
    elif printf '%s\n' "$REMOTES" | grep -Fxq 'dropbox:'; then
      DROPBOX_REMOTE='dropbox'
    fi
  fi
  if [[ -n "$DROPBOX_REMOTE" ]]; then
    REMOTE_TYPE="$(rclone config show "$DROPBOX_REMOTE" 2>/dev/null | awk -F ' = ' '$1 == "type" {print $2; exit}')"
    if [[ "$REMOTE_TYPE" == "dropbox" ]]; then
      DROPBOX_AVAILABLE=1
      note "Dropbox upload доступен через $DROPBOX_REMOTE."
    else
      note "Remote $DROPBOX_REMOTE не является Dropbox; будет создан локальный ZIP."
      DROPBOX_REMOTE=""
    fi
  else
    note "Dropbox remote не найден; будет создан локальный ZIP."
  fi
else
  note "rclone не установлен; Dropbox upload отключён, будет создан локальный ZIP."
fi

note "Использую MTMD installer из проверяемого checkout."
[[ -f "$MTMD_INSTALLER" ]] || fail "Не найден MTMD installer: $MTMD_INSTALLER"
[[ "$(git -C "$ROOT" hash-object "$MTMD_INSTALLER")" == "$MTMD_INSTALLER_BLOB_SHA" ]] || fail "Не прошла проверка целостности MTMD installer."
WATCHDOG="$ROOT/tools/bench/vce_runtime_watchdog.py"
[[ -f "$WATCHDOG" ]] || fail "Не найден watchdog в checkout: $WATCHDOG"
[[ -f "$ROOT/tools/bench/vce_observed_llama_cpp_run.py" ]] || fail "Не найден observer: $ROOT/tools/bench/vce_observed_llama_cpp_run.py"

# Install the .pth hook only while the app/API is fully stopped. Download and
# verify the pinned tools first, so a broken checksum is reported before app-state checks.
API_PIDS="$(/usr/sbin/lsof -nP -tiTCP:7555 -sTCP:LISTEN 2>/dev/null || true)"
APP_PIDS="$(/usr/bin/pgrep -f "$APP/Contents/MacOS/" 2>/dev/null || true)"
if [[ -n "$API_PIDS" || -n "$APP_PIDS" ]] || curl -fsS --max-time 2 "$API/openapi.json" >/dev/null 2>&1; then
  printf 'API listener PID(s): %s\n' "${API_PIDS:-не обнаружены}"
  printf 'VideoContext Engine PID(s): %s\n' "${APP_PIDS:-не обнаружены}"
  fail "VideoContext Engine ещё работает. Заверши приложение через VideoContext Engine → Quit (⌘Q), дождись закрытия API на порту 7555 и запусти команду снова. Нельзя подключить .pth-хук к уже работающему Python-процессу."
fi

note "Ставлю venv-scoped mtmd resolver hook до запуска API."
"$PY" "$MTMD_INSTALLER" --report "$OUT_ROOT/mtmd_installer_report.json"

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
    "inference_backend", "enable_ocr_cache", "llama_cpp_vlm_model", "llama_cpp_vlm_mmproj",
    "llama_cpp_vlm_context", "llama_cpp_text_llm_model", "vlm_model", "text_llm_model", "domain_profile",
}
missing = sorted(required - set(settings))
if missing:
    raise SystemExit("STOP: API lacks A/B settings: " + ", ".join(missing))
Path(snapshot).write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("API runtime_profile + MLX/llama.cpp setting schema: verified")
print("Original active settings snapshot:", snapshot)
PY

# Verify the pinned llama.cpp fork and the exact package-native library pair.
"$PY" - "$LLAMA_CPP_PINNED_COMMIT" <<'PY'
import ctypes.util
import importlib.metadata
import json
import sys
from pathlib import Path
import llama_cpp
import llama_cpp.llama_cpp as llama_core
import llama_cpp.mtmd_cpp as mtmd_cpp

expected_commit = sys.argv[1]
try:
    dist = importlib.metadata.distribution("llama-cpp-python")
    direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
except Exception as exc:
    raise SystemExit(
        "STOP: llama-cpp-python has no PEP 610 direct_url.json; reinstall from "
        "requirements-llama-cpp-metal.txt pinned to " + expected_commit + ". " + repr(exc)
    )
actual_commit = (direct_url.get("vcs_info") or {}).get("commit_id")
if actual_commit != expected_commit:
    raise SystemExit(
        f"STOP: installed llama-cpp-python commit={actual_commit!r}; expected pinned commit={expected_commit}. "
        "Reinstall with CMAKE_ARGS='-DGGML_METAL=on' FORCE_CMAKE=1 python -m pip install "
        "--force-reinstall --no-cache-dir -r requirements-llama-cpp-metal.txt"
    )
if getattr(llama_cpp, "__version__", None) != "0.4.6":
    raise SystemExit(
        f"STOP: unexpected llama_cpp.__version__={getattr(llama_cpp, '__version__', None)!r}; "
        "expected 0.4.6 from the pinned fork."
    )
for name in ("mtmd", "llama", "ggml", "ggml-base"):
    resolved = ctypes.util.find_library(name)
    if resolved is not None:
        raise SystemExit(f"STOP: system find_library({name!r}) still resolves {resolved!r}.")
package_lib_dir = (Path(llama_cpp.__file__).resolve().parent / "lib").resolve()
loaded = {
    "llama": Path(str(llama_core._lib._name)).resolve(),
    "mtmd": Path(str(mtmd_cpp._libmtmd._name)).resolve(),
}
for name, path in loaded.items():
    if path.parent != package_lib_dir or not path.is_file():
        raise SystemExit(
            f"STOP: {name} loaded from {path}; expected a library in {package_lib_dir}. "
            "Reinstall the pinned fork into this project's .venv."
        )
expected_mtmd = (package_lib_dir / "libmtmd.dylib").resolve()
if loaded["mtmd"] != expected_mtmd:
    raise SystemExit(f"STOP: wrong libmtmd path {loaded['mtmd']}; expected {expected_mtmd}")
getattr(mtmd_cpp._libmtmd, "mtmd_get_video_config")
if not callable(getattr(llama_core, "llama_supports_gpu_offload", None)) or not llama_core.llama_supports_gpu_offload():
    raise SystemExit("STOP: pinned llama-cpp-python was built without Metal GPU offload.")
print(json.dumps({
    "status": "passed",
    "llama_cpp_version": llama_cpp.__version__,
    "llama_cpp_vcs_commit": actual_commit,
    "libllama_path": str(loaded["llama"]),
    "libmtmd_path": str(loaded["mtmd"]),
    "mtmd_get_video_config": "present",
    "metal_gpu_offload": True,
}, ensure_ascii=False))
PY

# Resolve the pinned model and projector; initialize the native MTMD context with the
# same 8B Q4 model, handler, GPU layers and context size used by LlamaCppEngine.load_model.
# HF Hub requests get finite network timeouts; public model downloads do not require HF_TOKEN.
export HF_HUB_ETAG_TIMEOUT="${HF_HUB_ETAG_TIMEOUT:-60}"
export HF_HUB_DOWNLOAD_TIMEOUT="${HF_HUB_DOWNLOAD_TIMEOUT:-120}"
set +e
"$PY" - "$OUT_ROOT/mtmd_context_preflight.json" "$LLAMA_CPP_PINNED_COMMIT" "$VLM_HF_REPO_REVISION" <<'PY'
import inspect
import importlib.metadata
import json
import sys
from pathlib import Path

report_path, expected_fork_commit, hf_revision = sys.argv[1:]
report = {
    "status": "failed",
    "code": "MTMD_CONTEXT_FAILED",
    "fork_expected_commit": expected_fork_commit,
    "hf_repo": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
    "hf_revision": hf_revision,
    "model_filename": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
    "mmproj_filename": "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
}
llm = None
handler = None
try:
    import llama_cpp
    import llama_cpp.mtmd_cpp as mtmd_cpp
    from llama_cpp import Llama
    try:
        from llama_cpp.llama_multimodal import Qwen3VLChatHandler
    except ImportError:
        from llama_cpp.llama_chat_format import Qwen3VLChatHandler

    report["llama_cpp_version"] = getattr(llama_cpp, "__version__", None)
    dist = importlib.metadata.distribution("llama-cpp-python")
    direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
    report["llama_cpp_vcs_commit"] = (direct_url.get("vcs_info") or {}).get("commit_id")
    report["mtmd_cpp_module_path"] = str(Path(mtmd_cpp.__file__).resolve())
    native_mtmd_path = Path(str(mtmd_cpp._libmtmd._name)).resolve()
    report["libmtmd_path"] = str(native_mtmd_path)
    if not native_mtmd_path.is_file():
        raise FileNotFoundError("mtmd_cpp native library is not a file: " + str(native_mtmd_path))

    # Check both the Python mtmd_cpp bindings and their underlying symbols before
    # loading large model weights. The real context init below remains the definitive test.
    mtmd_required_symbols = ("mtmd_init_from_file", "mtmd_get_video_config")
    missing_mtmd_bindings = [
        name for name in mtmd_required_symbols
        if not callable(getattr(mtmd_cpp, name, None))
    ]
    missing_mtmd_native_symbols = []
    for name in mtmd_required_symbols:
        try:
            getattr(mtmd_cpp._libmtmd, name)
        except AttributeError:
            missing_mtmd_native_symbols.append(name)
    report["mtmd_cpp_python_bindings"] = {
        name: callable(getattr(mtmd_cpp, name, None))
        for name in mtmd_required_symbols
    }
    report["mtmd_cpp_native_symbols"] = {
        name: name not in missing_mtmd_native_symbols
        for name in mtmd_required_symbols
    }
    if missing_mtmd_bindings or missing_mtmd_native_symbols:
        raise RuntimeError(
            "mtmd_cpp binding/native symbol mismatch; "
            f"missing_python={missing_mtmd_bindings}, "
            f"missing_native={missing_mtmd_native_symbols}"
        )

    signature = inspect.signature(Qwen3VLChatHandler.__init__)
    signature_parameters = signature.parameters
    accepts_arbitrary_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature_parameters.values()
    )
    report["qwen3vl_handler_signature"] = str(signature)
    report["qwen3vl_handler_accepts_kwargs"] = accepts_arbitrary_kwargs

    from huggingface_hub import hf_hub_download
    print("HF_PREFLIGHT: resolving pinned Qwen3-VL Q4 model; this may download several GB on first run.", flush=True)
    model_path = hf_hub_download(
        repo_id=report["hf_repo"],
        filename=report["model_filename"],
        revision=hf_revision,
    )
    report["model_path"] = str(Path(model_path).resolve())
    report["model_size_bytes"] = Path(model_path).stat().st_size
    print("HF_PREFLIGHT: model ready (" + str(report["model_size_bytes"]) + " bytes); resolving F16 mmproj.", flush=True)
    mmproj_path = hf_hub_download(
        repo_id=report["hf_repo"],
        filename=report["mmproj_filename"],
        revision=hf_revision,
    )
    report["mmproj_path"] = str(Path(mmproj_path).resolve())
    report["mmproj_size_bytes"] = Path(mmproj_path).stat().st_size
    print("HF_PREFLIGHT: model and mmproj files are present; initializing Qwen3-VL.", flush=True)
    if not Path(model_path).is_file() or not Path(mmproj_path).is_file():
        raise FileNotFoundError("Pinned HF model or mmproj did not resolve to a local file.")

    if "mmproj_path" in signature_parameters:
        init_keyword = "mmproj_path"
    elif accepts_arbitrary_kwargs:
        # Pinned JamePeng fork exposes (force_reasoning, add_vision_id, **kwargs)
        # and forwards **kwargs to MTMDChatHandler/MTMDBaseHandler, whose canonical
        # projector argument is mmproj_path. A VAR_KEYWORD signature is not rejection.
        init_keyword = "mmproj_path"
    elif "clip_model_path" in signature_parameters:
        init_keyword = "clip_model_path"
    else:
        raise TypeError(
            "Qwen3VLChatHandler accepts neither mmproj_path nor **kwargs: " + str(signature)
        )
    report["handler_init_keyword"] = init_keyword
    handler = Qwen3VLChatHandler(**{init_keyword: mmproj_path, "verbose": True, "use_gpu": True})
    report["handler_constructor"] = "passed"

    report["model_load"] = "started"
    llm = Llama(
        model_path=model_path,
        n_gpu_layers=-1,
        n_ctx=8192,
        verbose=True,
    )
    report["model_load"] = "passed"

    # Constructor alone only checks that the file exists. This call reaches
    # mtmd_init_from_file and capability detection against the loaded Qwen3-VL model.
    handler._init_mtmd_context(llm)
    report["mtmd_context"] = "passed"
    report["vision_capability"] = bool(getattr(handler, "is_support_vision", False))
    report["video_capability"] = bool(getattr(handler, "is_support_video", False))
    if not report["vision_capability"]:
        raise RuntimeError("The real mmproj initialized but reported no vision capability.")
    if not report["video_capability"]:
        raise RuntimeError("The real mmproj initialized but mtmd_cpp reported no video capability.")
    report["status"] = "passed"
    report["code"] = "MTMD_CONTEXT_READY"
except Exception as exc:
    report["error_type"] = type(exc).__name__
    report["error"] = str(exc)
    if report.get("model_load") == "passed":
        report["code"] = "MTMD_CONTEXT_FAILED"
    elif report.get("handler_init_keyword"):
        report["code"] = "VLM_MODEL_LOAD_FAILED"
    else:
        report["code"] = "REAL_MMPROJ_PREFLIGHT_FAILED"
finally:
    if handler is not None:
        try:
            handler.close()
        except Exception as close_exc:
            report.setdefault("cleanup_warnings", []).append("handler.close: " + repr(close_exc))
    if llm is not None:
        try:
            llm.close()
        except Exception as close_exc:
            report.setdefault("cleanup_warnings", []).append("llm.close: " + repr(close_exc))
Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
if report["status"] != "passed":
    print("STOP: " + report["code"] + ": " + str(report.get("error", "real mmproj preflight failed")), file=sys.stderr)
    raise SystemExit(42)
PY
MTMD_PREFLIGHT_EXIT=$?
set -e


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
  note "Стартую $backend с воспроизводимым NSR профилем; активная настройка OCR cache сохраняется и записывается observer."
  "$PY" "$WATCHDOG" --video "$VIDEO" --settings "$SETTINGS" --runner "$OBSERVER" --api-url "$API" --out-root "$pass_dir" --interval 1.0 --inference-backend "$backend" --observer-profile nsrspec --powermetrics --vmmap --sample-stacks
}

if [[ "$MTMD_PREFLIGHT_EXIT" -ne 0 ]]; then
  RUN1_EXIT=125
  RUN2_EXIT=125
  note "Реальный mmproj/MTMD preflight не прошёл. Оба анализа пропущены; видео не отправлялось."
else
  note "Прогон 1/2: mlx_metal (MLX/Metal VLM + Text LLM; PyTorch/MPS Whisper)."
  set +e
  run_backend "mlx_metal" "$OUT_ROOT/run1_mlx_metal"
  RUN1_EXIT=$?
  set -e
  printf 'RUN1_MLX_METAL_EXIT=%s\n' "$RUN1_EXIT"

  if [[ "$RUN1_EXIT" -ne 0 && "$DIAGNOSTIC_LLAMA_CPP" != "1" ]]; then
    RUN2_EXIT=125
    note "Прогон 1 MLX baseline завершился ошибкой; llama.cpp пропущен. Для независимой диагностики используй --diagnostic-llama-cpp."
  else
    if [[ "$RUN1_EXIT" -ne 0 ]]; then
      RUN2_DIAGNOSTIC=1
      note "MLX baseline завершился ошибкой; llama.cpp запускается только для диагностики, не для A/B-сравнения."
    fi
    note "Прогон 2/2: llama_cpp_metal (GGUF VLM + Text LLM; PyTorch/MPS Whisper)."
    set +e
    run_backend "llama_cpp_metal" "$OUT_ROOT/run2_llama_cpp_metal"
    RUN2_EXIT=$?
    set -e
    printf 'RUN2_LLAMA_CPP_METAL_EXIT=%s\n' "$RUN2_EXIT"
  fi
fi

# Restore even if either analysis failed; the trap retries restoration on an early exit.
restore_settings || fail "Не удалось восстановить исходные API-настройки. Проверь settings_restore_report.json и videocontext_settings.json."

"$PY" - "$OUT_ROOT/ab_summary.json" "$RUN1_EXIT" "$RUN2_EXIT" "$MTMD_INSTALLER_BLOB_SHA" "$MTMD_PREFLIGHT_EXIT" "$VIDEO" "$RUN2_DIAGNOSTIC" "$OUT_ROOT/run1_mlx_metal" "$OUT_ROOT/run2_llama_cpp_metal" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

path, run1, run2, mtmd_installer_blob_sha, preflight_exit, video_path, diagnostic_flag, run1_dir, run2_dir = sys.argv[1:]
run1_exit = int(run1)
run2_exit = int(run2)
preflight_exit_code = int(preflight_exit)
diagnostic_only = bool(int(diagnostic_flag))
def read_ocr_cache_enabled(run_dir):
    try:
        request = json.loads((Path(run_dir) / "request_settings.json").read_text(encoding="utf-8"))
        fields = request.get("effective_fields", {})
        value = fields.get("enable_ocr_cache") if isinstance(fields, dict) else None
        return value if isinstance(value, bool) else None
    except Exception:
        return None

ocr_cache_run1 = read_ocr_cache_enabled(run1_dir)
ocr_cache_run2 = read_ocr_cache_enabled(run2_dir)
ocr_cache_values_match = ocr_cache_run1 is not None and ocr_cache_run1 == ocr_cache_run2
if preflight_exit_code != 0:
    run2_skipped_reason = "MTMD real-model preflight failed; neither analysis pass was submitted."
elif run1_exit != 0 and not diagnostic_only:
    run2_skipped_reason = "MLX baseline did not finish successfully; llama.cpp comparison pass was skipped."
elif diagnostic_only:
    run2_skipped_reason = "Diagnostic-only llama.cpp pass; MLX baseline failed, so no A/B comparison is valid."
else:
    run2_skipped_reason = None
data = {
    "created_at": datetime.now().astimezone().isoformat(),
    "mtmd_installer_blob_sha": mtmd_installer_blob_sha,
    "video_path": video_path,
    "baseline_gate_for_llama_cpp": not diagnostic_only,
    "diagnostic_only": diagnostic_only,
    "comparison_result_valid": preflight_exit_code == 0 and run1_exit == 0 and run2_exit == 0 and not diagnostic_only and ocr_cache_values_match,
    "ocr_cache_values_match": ocr_cache_values_match,
    "mtmd_preflight_exit_code": preflight_exit_code,
    "mtmd_preflight_report": "mtmd_context_preflight.json",
    "runs": [
        {"pass": 1, "inference_backend": "mlx_metal", "role": "reference baseline", "exit_code": run1_exit, "directory": "run1_mlx_metal"},
        {"pass": 2, "inference_backend": "llama_cpp_metal", "role": "diagnostic-only" if diagnostic_only else "comparison backend", "exit_code": run2_exit, "directory": "run2_llama_cpp_metal", "skipped_reason": run2_skipped_reason},
    ],
    "settings_restored": True,
    "ocr_cache_enabled_during_runs": ocr_cache_run1 if ocr_cache_values_match else None,
    "note": ("Diagnostic-only llama.cpp run after an unsuccessful MLX baseline; no A/B winner may be declared. " if diagnostic_only else "Both passes use the explicit nsrspec comparison profile. ") + "Original API settings are restored and verified. GGUF selectors are only in llama_cpp_*; vlm_model/text_llm_model remain MLX identifiers."
}
Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(data, ensure_ascii=False))
PY

note "Упаковка и Dropbox upload выполняются в EXIT-trap при любом коде завершения."

if [[ "$MTMD_PREFLIGHT_EXIT" -ne 0 ]]; then
  exit 42
fi
if [[ "$RUN1_EXIT" -eq 0 && "$RUN2_EXIT" -eq 0 ]]; then
  exit 0
fi
exit 1
