#!/bin/bash
# One short, local-only VLM diagnostic. No full benchmark, no GitHub publication.
set -Eeuo pipefail

REPOSITORY="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
RUNNER_REF="650f3a1437ee0cdcab1c3ea41005a599a373d578"
PATCHER_REF="db54501d459569eaa3e75a8e22ef1385ecee68f9"
EXPECTED_ENGINE_SHA="eacd4eecfda2cc8a2c71203bf5ae3589611f5fd8d0405ded747146a78f147055"
EXPECTED_BACKUP_SUFFIX=".backup-20261105T000114"

PROJECT_DIR="${VCE_PROJECT_DIR:-$HOME/VideoContext-Engine-Apple-Silicon}"
ENGINE_SOURCE="${VCE_ENGINE_SOURCE:-$PROJECT_DIR/VideoContextEngine_v3.19.py}"
SERVER_LOG="${VCE_SERVER_LOG:-$HOME/Library/Logs/VideoContext Engine/server.log}"
RESULT_ROOT="${VCE_VLM_DIAG_RESULT_ROOT:-$HOME/Downloads/VCE_VLM_Diagnostic}"
SOURCE_VIDEO="${VCE_VIDEO_PATH:-}"
EXPLICIT_TEST_VIDEO="${VCE_VLM_TEST_VIDEO:-}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
note() { printf '[VCE VLM diagnostic] %s\n' "$*"; }

[[ "$(uname -s)" == "Darwin" ]] || fail "This diagnostic requires macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon (arm64) is required."
[[ -d "$PROJECT_DIR" ]] || fail "VCE project directory not found: $PROJECT_DIR"
[[ -f "$ENGINE_SOURCE" ]] || fail "VCE engine source not found: $ENGINE_SOURCE"
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || fail "Expected project venv is missing: $PROJECT_DIR/.venv/bin/python"
PYTHON="$PROJECT_DIR/.venv/bin/python"

for tool in curl gzip lsof ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || fail "Required tool '$tool' is missing."
done
command -v ffmpeg >/dev/null 2>&1 || [[ -n "$EXPLICIT_TEST_VIDEO" ]] || note "ffmpeg is not installed; an explicit <=30-second test clip will be required."

ACTUAL_ENGINE_SHA="$(/usr/bin/shasum -a 256 "$ENGINE_SOURCE" | awk '{print $1}')"
[[ "$ACTUAL_ENGINE_SHA" == "$EXPECTED_ENGINE_SHA" ]] || fail "Engine SHA256 differs from the reviewed patched source. Expected $EXPECTED_ENGINE_SHA, got $ACTUAL_ENGINE_SHA. Stop and review the new source before testing."
BACKUP="$ENGINE_SOURCE$EXPECTED_BACKUP_SUFFIX"
[[ -s "$BACKUP" ]] || fail "Expected timestamped pre-patch backup is missing: $BACKUP"
grep -q "VCE_METAL_RUNTIME_FAILURE_LATCH_V1" "$ENGINE_SOURCE" || fail "The Metal runtime latch marker is missing from the engine source."

"$PYTHON" - "$ENGINE_SOURCE" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1])
compile(source.read_text(encoding="utf-8"), str(source), "exec")
print("Engine syntax: OK (compiled in memory; no __pycache__ written)")
PY

# Find the one process currently listening on the VCE API port.
LISTENER_PIDS="$(/usr/sbin/lsof -nP -iTCP:7555 -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 {print $2}' | sort -u || true)"
[[ -n "$LISTENER_PIDS" ]] || fail "Nothing is listening on 127.0.0.1:7555. Start/restart VCE normally, then rerun this diagnostic."
PID_COUNT="$(printf '%s\n' "$LISTENER_PIDS" | sed '/^$/d' | wc -l | tr -d ' ')"
[[ "$PID_COUNT" == "1" ]] || fail "Expected exactly one VCE listener PID on port 7555; found: $LISTENER_PIDS"
SERVER_PID="$LISTENER_PIDS"
PROC_CMD="$(ps -p "$SERVER_PID" -o command= | sed 's/^[[:space:]]*//')"
PROC_CWD="$(/usr/sbin/lsof -a -p "$SERVER_PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true)"
note "VCE listener PID: $SERVER_PID"
note "Listener command: $PROC_CMD"
note "Listener working directory: ${PROC_CWD:-unavailable}"

# A server older than the patched source may still have the pre-patch code in memory.
"$PYTHON" - "$SERVER_PID" "$ENGINE_SOURCE" <<'PY'
from datetime import datetime
import os, subprocess, sys, time
pid, source = sys.argv[1], sys.argv[2]
started = subprocess.check_output(["ps", "-p", pid, "-o", "lstart="], text=True).strip()
try:
    process_epoch = time.mktime(datetime.strptime(started, "%a %b %d %H:%M:%S %Y").timetuple())
except ValueError as exc:
    raise SystemExit("Cannot parse VCE listener start time; refusing to assume the patched source is loaded: " + str(exc))
source_mtime = os.stat(source).st_mtime
if process_epoch < source_mtime:
    raise SystemExit("VCE listener started before the patched source was written. Restart VCE normally, then rerun this script.")
print("Listener start time is newer than the patched source file.")
PY

/usr/bin/curl --fail --silent --show-error "http://127.0.0.1:7555/openapi.json" -o /dev/null || fail "VCE API health check failed on port 7555."
[[ -r "$SERVER_LOG" ]] || fail "Cannot read the configured VCE server log: $SERVER_LOG"

STAMP="$(/bin/date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$RESULT_ROOT/$STAMP"
RUN_OUTPUT="$RUN_DIR/runner-output"
mkdir -p "$RUN_OUTPUT"
TMP_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/vce_vlm_diag.XXXXXX")"
cleanup() { /bin/rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# Use an explicit short clip when supplied. Otherwise reuse VCE_VIDEO_PATH if
# already short, or create a separate 20-second video-only excerpt (source untouched).
if [[ -n "$EXPLICIT_TEST_VIDEO" ]]; then
  [[ -f "$EXPLICIT_TEST_VIDEO" ]] || fail "VCE_VLM_TEST_VIDEO does not exist."
  VIDEO_PATH="$EXPLICIT_TEST_VIDEO"
elif [[ -n "$SOURCE_VIDEO" && -f "$SOURCE_VIDEO" ]]; then
  VIDEO_PATH="$SOURCE_VIDEO"
else
  fail "Set VCE_VIDEO_PATH to a local source video, or VCE_VLM_TEST_VIDEO to an existing short clip."
fi

duration_of() {
  ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$1" 2>/dev/null | head -n 1 || true
}
DURATION="$(duration_of "$VIDEO_PATH")"
DURATION_OK="$("$PYTHON" - "$DURATION" <<'PY'
import sys
try:
    value = float(sys.argv[1])
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if 0.5 <= value <= 30.0 else 1)
PY
)" || {
  if [[ -n "$EXPLICIT_TEST_VIDEO" ]]; then
    fail "VCE_VLM_TEST_VIDEO must be between 0.5 and 30 seconds; found duration: ${DURATION:-unknown}"
  fi
  command -v ffmpeg >/dev/null 2>&1 || fail "Source video exceeds 30 seconds and ffmpeg is unavailable. Provide VCE_VLM_TEST_VIDEO instead."
  VIDEO_PATH="$RUN_DIR/vlm-smoke-input.mp4"
  note "Creating a separate 20-second video-only excerpt; the source video will not be modified."
  ffmpeg -hide_banner -loglevel error -y -ss 0 -i "$SOURCE_VIDEO" -t 20 \
    -map 0:v:0 -an -c:v libx264 -preset ultrafast -crf 30 -movflags +faststart "$VIDEO_PATH" \
    || fail "Could not create the 20-second VLM test clip."
  DURATION="$(duration_of "$VIDEO_PATH")"
  "$PYTHON" - "$DURATION" <<'PY' || fail "Generated test clip duration is invalid."
import sys
value = float(sys.argv[1])
raise SystemExit(0 if 0.5 <= value <= 30.0 else 1)
PY
}
note "Test clip duration: $DURATION seconds (maximum allowed: 30 seconds)."

# Pin and patch the same runner/helper versions already used by PR #34.
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$RUNNER_REF"
curl --fail --location --silent --show-error --retry 3 \
  "$RAW_BASE/scripts/vce_full_profile_benchmark.py.gz" -o "$TMP_DIR/runner.py.gz"
gzip -t "$TMP_DIR/runner.py.gz" || fail "Downloaded benchmark runner is not a valid gzip file."
gzip -dc "$TMP_DIR/runner.py.gz" > "$TMP_DIR/runner.py"
curl --fail --location --silent --show-error --retry 3 \
  "https://raw.githubusercontent.com/$REPOSITORY/$PATCHER_REF/scripts/patch_vce_full_profile_benchmark.py" \
  -o "$TMP_DIR/patch_runner.py"
"$PYTHON" "$TMP_DIR/patch_runner.py" "$TMP_DIR/runner.py" || fail "Could not apply the reviewed v4 runner adapter."

# Add a final request guard after the v4 adapter. It refuses to send anything
# unless the actual in-memory fields are VLM-only, and writes only the flags.
"$PYTHON" - "$TMP_DIR/runner.py" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
marker = "# VCE_VLM_SMOKE_REQUEST_GUARD_V1"
if marker not in source:
    entry = 'if __name__ == "__main__":'
    if entry not in source:
        raise SystemExit("Cannot locate runner entrypoint; refusing to run without request guard.")
    wrapper = r'''# VCE_VLM_SMOKE_REQUEST_GUARD_V1
_vce_vlm_diag_original_build_base_fields = build_base_fields
def build_base_fields(*args, **kwargs):
    import json as _vce_diag_json
    import os as _vce_diag_os
    _fields, _missing = _vce_vlm_diag_original_build_base_fields(*args, **kwargs)
    if not isinstance(_fields, dict):
        raise RuntimeError("VLM diagnostic request fields are not a dictionary")
    _expected = {
        "skip_audio": True,
        "skip_visual": False,
        "enable_apple_ocr": False,
        "enable_ffmpeg_ocr": False,
        "text_llm_enabled": False,
        "generate_summary": False,
    }
    _actual = {key: _fields.get(key) for key in _expected}
    _bad = [key for key, expected in _expected.items() if _actual.get(key) is not expected]
    if _bad:
        raise RuntimeError("VLM-only request guard rejected fields: " + ", ".join(_bad))
    _manifest = {
        "analysis_mode": _fields.get("analysis_mode"),
        "skip_audio": _fields.get("skip_audio"),
        "skip_visual": _fields.get("skip_visual"),
        "enable_apple_ocr": _fields.get("enable_apple_ocr"),
        "enable_ffmpeg_ocr": _fields.get("enable_ffmpeg_ocr"),
        "text_llm_enabled": _fields.get("text_llm_enabled"),
        "generate_summary": _fields.get("generate_summary"),
    }
    _manifest_path = _vce_diag_os.environ.get("VCE_VLM_DIAG_REQUEST_MANIFEST")
    if not _manifest_path:
        raise RuntimeError("VCE_VLM_DIAG_REQUEST_MANIFEST is not set")
    with open(_manifest_path, "w", encoding="utf-8") as _handle:
        _vce_diag_json.dump(_manifest, _handle, ensure_ascii=False, indent=2)
        _handle.write("\n")
    print("[VCE VLM diagnostic] Actual request flags verified: " + _vce_diag_json.dumps(_manifest, sort_keys=True), flush=True)
    return _fields, _missing

'''
    source = source.replace(entry, wrapper + entry, 1)
compile(source, str(path), "exec")
path.write_text(source, encoding="utf-8")
PY

"$PYTHON" -m py_compile "$TMP_DIR/runner.py"
# py_compile writes only into the temporary directory, which is deleted on exit.

export VCE_BENCH_ONLY_BASELINE=1
export VCE_BENCH_VLM_ONLY=1
unset VCE_BENCH_WHISPER_ONLY || true
unset VCE_BENCH_SKIP_AUDIO || true
export VCE_BENCH_RESULT_ROOT="$RUN_OUTPUT"
export VCE_VLM_DIAG_REQUEST_MANIFEST="$RUN_DIR/request_flags.json"

LOG_OFFSET="$(wc -c < "$SERVER_LOG" | tr -d '[:space:]')"
RUN_STARTED_UTC="$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'server_pid=%s\nsource_sha256=%s\nbackup_exists=true\nrun_started_utc=%s\nserver_log_offset_bytes=%s\nvideo_duration_seconds=%s\n' \
  "$SERVER_PID" "$ACTUAL_ENGINE_SHA" "$RUN_STARTED_UTC" "$LOG_OFFSET" "$DURATION" > "$RUN_DIR/preflight.txt"

note "Starting exactly one baseline request with VLM enabled and audio/OCR/Text LLM disabled."
note "Raw request/response data and logs will remain local: $RUN_DIR"
set +e
"$PYTHON" "$TMP_DIR/runner.py" --project "$PROJECT_DIR" --video "$VIDEO_PATH" > "$RUN_DIR/launcher_console.log" 2>&1
RUN_EXIT=$?
set -e
RUN_FINISHED_UTC="$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"

# Snapshot only bytes appended during this request window, not historical server.log.
LOG_SIZE_NOW="$(wc -c < "$SERVER_LOG" | tr -d '[:space:]')"
if [[ "$LOG_SIZE_NOW" -ge "$LOG_OFFSET" ]]; then
  /usr/bin/tail -c "+$((LOG_OFFSET + 1))" "$SERVER_LOG" > "$RUN_DIR/server_log_delta.log"
else
  : > "$RUN_DIR/server_log_delta.log"
  note "WARNING: server.log was truncated/rotated during the request; byte-window capture is incomplete."
fi

AFTER_PIDS="$(/usr/sbin/lsof -nP -iTCP:7555 -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 {print $2}' | sort -u || true)"
PID_STABLE="false"
if [[ "$AFTER_PIDS" == "$SERVER_PID" ]]; then PID_STABLE="true"; fi

# Filter log lines that explicitly carry the serving PID. Keep the complete
# byte-window delta locally as well, because some log formats omit PID tokens.
"$PYTHON" - "$RUN_DIR" "$SERVER_PID" "$RUN_EXIT" "$RUN_STARTED_UTC" "$RUN_FINISHED_UTC" "$PID_STABLE" <<'PY'
from pathlib import Path
import csv, json, re, sys

run_dir = Path(sys.argv[1])
pid, exit_code, started, finished, pid_stable = sys.argv[2:]
manifest_path = run_dir / "request_flags.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None

log_path = run_dir / "server_log_delta.log"
lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.exists() else []
pid_re = re.compile(r"(?<!\d)" + re.escape(pid) + r"(?!\d)")
pid_lines = [line for line in lines if pid_re.search(line)]
(run_dir / ("server_log_pid_" + pid + ".log")).write_text(
    "\n".join(pid_lines) + ("\n" if pid_lines else ""), encoding="utf-8"
)
metal_re = re.compile(
    r"kIOGPUCommandBufferCallbackErrorTimeout|kIOGPUCommandBufferCallbackErrorSubmissionsIgnored|"
    r"SubmissionsIgnored|Command buffer execution failed|Command buffer.*OutOfMemory",
    re.I,
)
metal_lines = sum(1 for line in lines if metal_re.search(line))

def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

response_files = sorted((run_dir / "runner-output").rglob("response.json"))
response_path = response_files[-1] if response_files else None
payload = read_json(response_path) if response_path else None
meta = payload.get("meta") if isinstance(payload, dict) and isinstance(payload.get("meta"), dict) else {}
params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
timings = meta.get("timings") if isinstance(meta.get("timings"), dict) else {}
vlm_timing = timings.get("vlm") if isinstance(timings.get("vlm"), dict) else {}
run_vlm = params.get("run_vlm")
inference = vlm_timing.get("inference_time")
try:
    inference_number = float(inference) if inference is not None else 0.0
except (TypeError, ValueError):
    inference_number = 0.0

description_keys = {"visual_description", "visual_descriptions", "vlm_description", "vlm_descriptions"}
def count_descriptions(value):
    total = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in description_keys and child:
                if isinstance(child, list):
                    total += sum(1 for item in child if item)
                elif isinstance(child, str):
                    total += 1
                elif isinstance(child, dict):
                    total += 1
            total += count_descriptions(child)
    elif isinstance(value, list):
        for child in value:
            total += count_descriptions(child)
    return total

description_count = count_descriptions(payload) if payload is not None else 0
run_summary_files = sorted((run_dir / "runner-output").rglob("run_summary.json"))
record = read_json(run_summary_files[-1]) if run_summary_files else {}
record = record if isinstance(record, dict) else {}
http_status = record.get("http_status")
runner_status = record.get("status")
error_count = record.get("error_count", 0)
try:
    error_count = int(error_count or 0)
except (TypeError, ValueError):
    error_count = None
contradiction = run_vlm is False and (inference_number > 0 or description_count > 0)
report = {
    "server_pid_before": int(pid),
    "server_pid_stable_after_run": pid_stable == "true",
    "run_started_utc": started,
    "run_finished_utc": finished,
    "runner_exit_code": int(exit_code),
    "request_flags": manifest,
    "response_found": response_path is not None,
    "http_status": http_status,
    "runner_stage_status": runner_status,
    "runtime_error_count": error_count,
    "report_params_run_vlm": run_vlm,
    "vlm_inference_seconds": inference_number,
    "visual_description_count": description_count,
    "vlm_positive_completion": inference_number > 0 or description_count > 0,
    "run_vlm_metadata_contradiction": contradiction,
    "server_log_delta_line_count": len(lines),
    "server_log_lines_explicitly_containing_pid": len(pid_lines),
    "metal_signature_lines_in_new_log_delta": metal_lines,
    "raw_response_and_full_log_are_local_only": True,
}
(run_dir / "diagnostic_summary.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("----- VCE VLM diagnostic summary -----")
for key in (
    "server_pid_before", "server_pid_stable_after_run", "runner_exit_code",
    "http_status", "runner_stage_status", "runtime_error_count",
    "report_params_run_vlm", "vlm_inference_seconds", "visual_description_count",
    "run_vlm_metadata_contradiction", "server_log_delta_line_count",
    "server_log_lines_explicitly_containing_pid", "metal_signature_lines_in_new_log_delta",
):
    print(f"{key}={report.get(key)}")
print("request_flags_verified=" + str(manifest is not None))
print("response_found=" + str(response_path is not None))
print("Raw response, local errors, and time-window server log are not uploaded.")
PY

printf '\nRun finished. Runner exit code: %s\n' "$RUN_EXIT"
printf 'Local report: %s/diagnostic_summary.json\n' "$RUN_DIR"
printf 'PID-scoped log filter: %s/server_log_pid_%s.log\n' "$RUN_DIR" "$SERVER_PID"
printf 'New server.log byte-window: %s/server_log_delta.log\n' "$RUN_DIR"

if [[ ! -s "$RUN_DIR/request_flags.json" ]]; then
  fail "Request fields were not verified; inspect the local launcher_console.log. No result should be accepted."
fi
if [[ "$PID_STABLE" != "true" ]]; then
  fail "VCE listener PID changed during the test. Treat this run as non-attributable."
fi

"$PYTHON" - "$RUN_DIR/diagnostic_summary.json" <<'PY' || fail "The run did not satisfy VLM smoke-test acceptance."
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
problems = []
if not data.get("response_found"):
    problems.append("response.json missing")
if data.get("http_status") != 200:
    problems.append("HTTP status is not 200")
if data.get("runner_stage_status") != "completed":
    problems.append("stage classifier did not return completed")
if data.get("runtime_error_count") != 0:
    problems.append("runtime error count is non-zero or unavailable")
if not data.get("vlm_positive_completion"):
    problems.append("no positive VLM inference/description evidence")
if data.get("metal_signature_lines_in_new_log_delta", 0) != 0:
    problems.append("new server.log window contains a Metal failure signature")
if not data.get("server_pid_stable_after_run"):
    problems.append("listener PID changed during the request")
if problems:
    print("Acceptance failed: " + "; ".join(problems), file=sys.stderr)
    raise SystemExit(1)
PY

CONTRADICTION="$("$PYTHON" - "$RUN_DIR/diagnostic_summary.json" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print("true" if data.get("run_vlm_metadata_contradiction") else "false")
PY
)"
if [[ "$CONTRADICTION" == "true" ]]; then
  fail "Detected run_vlm=False together with positive VLM evidence. The report/request metadata contradiction is reproduced."
fi
if [[ "$RUN_EXIT" -ne 0 ]]; then
  fail "The single VLM-only diagnostic did not complete cleanly (runner exit $RUN_EXIT). Review the local sanitized summary and private error files."
fi

note "The short VLM-only request completed without a reported runtime error."
note "No full-video analysis, Text LLM, Whisper, concurrent run, benchmark matrix, GitHub commit, or GitHub upload was performed."
exit 0
