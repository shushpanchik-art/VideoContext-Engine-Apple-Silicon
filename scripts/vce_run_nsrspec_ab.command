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
PY="$ROOT/.venv/bin/python"

# Pin all downloaded tooling to the same reviewed commit, never to a moving branch.
TOOLS_COMMIT="5d41d71599c424f8601434111df9be6cce7dfe23"
TOOLS_DIR="$HOME/Library/Application Support/VideoContext Engine/forensic-tools/nsrspec-ab-$TOOLS_COMMIT"
OUT_PARENT="$HOME/Downloads/VCE_forensic_runs_NSRSPEC_AB"
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

fail() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 2; }
note() { printf '\n[VCE NSRSPEC A/B] %s\n' "$*"; }

restore_settings() {
  if [[ "$API_READY" != "1" || ! -f "$ORIGINAL_API_SETTINGS" ]]; then
    RESTORE_DONE=1
    RESTORE_STATUS="not_needed"
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
    RESTORE_STATUS="passed"
  else
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
  /usr/bin/ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$archive" || return 1
  local local_size
  local_size="$(stat -f "%z" "$archive")" || return 1
  if ! command -v rclone >/dev/null 2>&1; then
    note "rclone отсутствует; ZIP сохранён локально: $archive"
    return 1
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
  remote_type="$(rclone config show "$DROPBOX_REMOTE" 2>/dev/null | awk -F " = " '\$1 == "type" {print \$2; exit}')"
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
    note "Dropbox size mismatch: local=$local_size remote=\${remote_size:-unknown}; ZIP сохранён: $archive"
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
  if [[ "$RESTORE_DONE" != "1" && "$API_READY" == "1" ]]; then
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
    capture_external_logs
    printf "launcher_exit_code=%s\nsettings_restore_status=%s\nvideo_path=%s\ndropbox_upload=attempted_by_exit_trap\n" "$status" "$RESTORE_STATUS" "$VIDEO" > "$OUT_ROOT/finalization_status.txt"
    set +e
    upload_forensic_bundle
    upload_status=$?
    set -e
    if [[ "$upload_status" -ne 0 ]]; then
      note "Dropbox upload не подтверждён. Локальные материалы сохранены в $OUT_ROOT; архив — в $OUT_PARENT."
      if [[ "$status" -eq 0 ]]; then status=3; fi
    fi
  fi
  exit "$status"
}
trap finish EXIT

mkdir -p "$TOOLS_DIR" "$OUT_ROOT"
exec > >(tee -a "$OUT_ROOT/launcher.log") 2>&1

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail "Нужен macOS на Apple Silicon."
[[ -d "$APP" ]] || fail "Не найдено приложение: $APP"
[[ -f "$VIDEO" ]] || fail "Не найден видеофайл: $VIDEO"
[[ -x "$PY" ]] || fail "Не найден Python проекта: $PY"
[[ -f "$SETTINGS" ]] || fail "Не найден файл настроек: $SETTINGS"
command -v curl >/dev/null 2>&1 || fail "Не найден curl."
command -v git >/dev/null 2>&1 || fail "Не найден git."
command -v ffprobe >/dev/null 2>&1 || fail "Не найден ffprobe. Установи FFmpeg."
command -v rclone >/dev/null 2>&1 || fail "Не найден rclone; Dropbox upload обязателен."

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

note "Скачиваю watchdog и mtmd installer из pinned-коммита $TOOLS_COMMIT; проверяю установленный observer patch."
BASE_URL="https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/$TOOLS_COMMIT/scripts"
curl -fsSL "$BASE_URL/vce_runtime_watchdog.py" -o "$TOOLS_DIR/vce_runtime_watchdog.py"
curl -fsSL "$BASE_URL/install_vce_mtmd_loader_fix.py" -o "$TOOLS_DIR/install_vce_mtmd_loader_fix.py"
[[ "$(git hash-object "$TOOLS_DIR/vce_runtime_watchdog.py")" == "50e91ffabcf2cb6174b068a12eea0b6b909cba5e" ]] || fail "Не прошла проверка целостности watchdog."
[[ "$(git hash-object "$TOOLS_DIR/install_vce_mtmd_loader_fix.py")" == "934739bd0b5f70dc26665adc115a181d365330e0" ]] || fail "Не прошла проверка целостности mtmd installer."
RUNNER_BLOB_SHA="${RUNNER_BLOB_SHA:-${VCE_RUNNER_BLOB_SHA:-7364764a3c1c4f9369cc3bfb0f0e59fc7f4e9013}}"
[[ -f "$ROOT/scripts/vce_observed_llama_cpp_run.py" ]] || fail "Не найден patched observer: $ROOT/scripts/vce_observed_llama_cpp_run.py"
[[ "$(git -C "$ROOT" hash-object "$ROOT/scripts/vce_observed_llama_cpp_run.py")" == "$RUNNER_BLOB_SHA" ]] || fail "Observer patch не установлен или blob SHA не совпадает; запускай scripts/vce_patch_test_run_nsrspec_ab.command."

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
    "inference_backend", "enable_ocr_cache", "llama_cpp_vlm_model", "llama_cpp_vlm_mmproj",
    "llama_cpp_text_llm_model", "vlm_model", "text_llm_model",
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
  note "Стартую $backend; Whisper остаётся PyTorch/MPS, OCR cache OFF (подтверждается observer перед upload), FFmpeg OCR 5s/3 workers."
  "$PY" "$TOOLS_DIR/vce_runtime_watchdog.py" --video "$VIDEO" --settings "$SETTINGS" --runner "$ROOT/scripts/vce_observed_llama_cpp_run.py" --api-url "$API" --out-root "$pass_dir" --interval 1.0 --inference-backend "$backend" --powermetrics --vmmap --sample-stacks
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

  if [[ "$RUN1_EXIT" -ne 0 ]]; then
    RUN2_EXIT=125
    note "Прогон 1 MLX baseline не завершился успешно (exit=$RUN1_EXIT); прогон 2 llama.cpp пропущен, чтобы A/B не сравнивал backend с неработающим baseline."
  else
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

"$PY" - "$OUT_ROOT/ab_summary.json" "$RUN1_EXIT" "$RUN2_EXIT" "$TOOLS_COMMIT" "$MTMD_PREFLIGHT_EXIT" "$VIDEO" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

path, run1, run2, commit, preflight_exit, video_path = sys.argv[1:]
run1_exit = int(run1)
run2_exit = int(run2)
preflight_exit_code = int(preflight_exit)
if preflight_exit_code != 0:
    run2_skipped_reason = "MTMD real-model preflight failed; neither analysis pass was submitted."
elif run1_exit != 0:
    run2_skipped_reason = "MLX baseline did not finish successfully; llama.cpp pass was intentionally skipped."
else:
    run2_skipped_reason = None
data = {
    "created_at": datetime.now().astimezone().isoformat(),
    "tooling_commit": commit,
    "video_path": video_path,
    "baseline_gate_for_llama_cpp": True,
    "mtmd_preflight_exit_code": preflight_exit_code,
    "mtmd_preflight_report": "mtmd_context_preflight.json",
    "runs": [
        {"pass": 1, "inference_backend": "mlx_metal", "role": "reference baseline", "exit_code": run1_exit, "directory": "run1_mlx_metal"},
        {"pass": 2, "inference_backend": "llama_cpp_metal", "role": "comparison backend", "exit_code": run2_exit, "directory": "run2_llama_cpp_metal", "skipped_reason": run2_skipped_reason},
    ],
    "settings_restored": True,
    "ocr_cache_enabled_during_runs": False,
    "note": "Both passes use baseline_mps_sequential Whisper/PyTorch-MPS with enable_ocr_cache=false to avoid warm-cache bias. Original OCR cache setting is restored and verified after both passes. GGUF selectors are only in llama_cpp_*; vlm_model/text_llm_model remain MLX identifiers.",
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
