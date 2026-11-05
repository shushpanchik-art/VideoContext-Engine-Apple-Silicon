#!/bin/bash
# One-request VCE stage/device diagnostic for Apple Silicon.
# Runs one guarded full end-to-end baseline: VLM + Whisper + Text LLM + Apple Vision OCR.
# FFmpeg/Tesseract OCR and experimental MLX Whisper validation stay disabled.
set -uo pipefail

REPOSITORY="shushpanchik-art/VideoContext-Engine-Apple-Silicon"
PUBLISH_BRANCH="feat/experimental-benchmark-launcher"
RUNNER_REF="650f3a1437ee0cdcab1c3ea41005a599a373d578"
PATCHER_REF="10f8c706e9aae494f6d6a092db7ba7d34a21897a"
VIDEO_PATH="$(printenv VCE_VIDEO_PATH 2>/dev/null || true)"
PROJECT_DIR="$(printenv VCE_PROJECT_DIR 2>/dev/null || true)"
RESULT_ROOT="$HOME/Downloads/VCE_Full_Profile_Benchmark"
CUSTOM_RESULT_ROOT="$(printenv VCE_BENCH_RESULT_ROOT 2>/dev/null || true)"
DIAG_RESULT_ROOT="$(printenv VCE_DIAGNOSTIC_RESULT_ROOT 2>/dev/null || true)"
if [[ -n "$CUSTOM_RESULT_ROOT" ]]; then RESULT_ROOT="$CUSTOM_RESULT_ROOT"; fi
if [[ -n "$DIAG_RESULT_ROOT" ]]; then RESULT_ROOT="$DIAG_RESULT_ROOT"; fi
TMP_BASE="$(printenv TMPDIR 2>/dev/null || echo /tmp)"

TMP_DIR="$(/usr/bin/mktemp -d "$TMP_BASE/vce_parallel_diag.XXXXXX")"
TMP_GZIP="$TMP_DIR/runner.py.gz"
TMP_RUNNER="$TMP_DIR/runner.py"
TMP_PATCHER="$TMP_DIR/patch_runner.py"
RUN_MARKER="$TMP_DIR/run-start.marker"
cleanup() { /bin/rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

[[ "$(uname -s)" == "Darwin" ]] || fail "This diagnostic requires macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon (arm64) is required."
[[ -n "$VIDEO_PATH" ]] || fail "VCE_VIDEO_PATH is empty. Pass the local MP4 path in the command."
[[ -f "$VIDEO_PATH" ]] || fail "Input video does not exist: $VIDEO_PATH"

# Prefer the checkout containing the v3.19 runtime, then the standard install path.
if [[ -z "$PROJECT_DIR" ]]; then
  for candidate in "$HOME/VideoContext-Engine-Apple-Silicon" "$HOME/VideoContext-Engine"; do
    if [[ -f "$candidate/VideoContextEngine_v3.19.py" || -x "$candidate/.venv/bin/python" ]]; then
      PROJECT_DIR="$candidate"
      break
    fi
  done
fi
[[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]] || fail "Cannot locate the VCE project. Set VCE_PROJECT_DIR to its installed directory."

PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then PYTHON="$(command -v python3 || true)"; fi
[[ -n "$PYTHON" && -x "$PYTHON" ]] || fail "Python 3 is required."
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || fail "Python 3.10+ is required."

command -v curl >/dev/null 2>&1 || fail "curl is required."
command -v gzip >/dev/null 2>&1 || fail "gzip is required."
command -v git >/dev/null 2>&1 || fail "git is required."
if ! command -v gh >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing GitHub CLI (gh) with Homebrew..."
    brew install gh || fail "Could not install GitHub CLI."
  else
    fail "GitHub CLI is missing. Install it with: brew install gh"
  fi
fi
gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated. Run gh auth login once, then rerun this command."
gh auth setup-git >/dev/null 2>&1 || fail "Could not configure Git credential helper."
git ls-remote --exit-code --heads "https://github.com/$REPOSITORY.git" "$PUBLISH_BRANCH" >/dev/null 2>&1 || fail "Cannot access publication branch $PUBLISH_BRANCH. Check GitHub write permissions."

mkdir -p "$RESULT_ROOT"
printf '%s\n' "Video: <local input; redacted from publication>" "Project: $PROJECT_DIR" "Runner revision: $RUNNER_REF" "Patch helper revision: $PATCHER_REF" "Plan: one normal full-profile request only" "enable_mlx_whisper: forced false" ""
echo "Downloading the pinned diagnostic runner..."
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$RUNNER_REF"
/usr/bin/curl --fail --location --silent --show-error --retry 3 "$RAW_BASE/scripts/vce_full_profile_benchmark.py.gz" --output "$TMP_GZIP" || fail "Could not download the pinned benchmark runner."
/usr/bin/gzip -t "$TMP_GZIP" || fail "The downloaded benchmark runner is not a valid gzip file."
/usr/bin/gzip -dc "$TMP_GZIP" > "$TMP_RUNNER" || fail "Could not unpack the benchmark runner."
/usr/bin/curl --fail --location --silent --show-error --retry 3 \
  "https://raw.githubusercontent.com/$REPOSITORY/$PATCHER_REF/scripts/patch_vce_full_profile_benchmark.py" \
  --output "$TMP_PATCHER" || fail "Could not download the pinned runner patch helper."
"$PYTHON" "$TMP_PATCHER" "$TMP_RUNNER" || fail "The compatibility/stage classifier patch failed."

# Fail-closed request guard: override persisted UI toggles in memory for one complete baseline.
"$PYTHON" - "$TMP_RUNNER" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
marker = "# VCE_FULL_E2E_REQUEST_GUARD_V1"
if marker not in source:
    entry = 'if __name__ == "__main__":'
    if entry not in source:
        raise SystemExit("Cannot locate runner entrypoint; refusing an unguarded full run")
    wrapper = r'''# VCE_FULL_E2E_REQUEST_GUARD_V1
_vce_full_e2e_original_build_base_fields = build_base_fields
def build_base_fields(*args, **kwargs):
    import json as _vce_e2e_json
    import os as _vce_e2e_os
    _result = _vce_full_e2e_original_build_base_fields(*args, **kwargs)
    if not isinstance(_result, tuple) or len(_result) != 2 or not isinstance(_result[0], dict):
        raise RuntimeError("Unexpected build_base_fields() result; refusing full end-to-end request")
    _fields = dict(_result[0])
    _required = {
        "analysis_mode": "full",
        "skip_audio": False,
        "skip_visual": False,
        "word_timestamps": True,
        "enable_audio_features": True,
        "enable_apple_ocr": True,
        "enable_ffmpeg_ocr": False,
        "text_llm_enabled": True,
        "generate_summary": True,
        "enable_stage_timing": True,
        "generate_txt": False,
        "generate_json": False,
        "enable_mlx_whisper": False,
        "enable_fast_scene_detection": False,
        "enable_adaptive_keyframes": False,
    }
    _fields.update(_required)
    _manifest = {key: _fields.get(key) for key in _required}
    _manifest_path = _vce_e2e_os.environ.get("VCE_FULL_E2E_REQUEST_MANIFEST")
    if not _manifest_path:
        raise RuntimeError("VCE_FULL_E2E_REQUEST_MANIFEST is not set")
    with open(_manifest_path, "w", encoding="utf-8") as _handle:
        _vce_e2e_json.dump(_manifest, _handle, ensure_ascii=False, indent=2)
        _handle.write("\\n")
    print("[VCE full E2E] Verified request flags: " + _vce_e2e_json.dumps(_manifest, sort_keys=True), flush=True)
    return _fields, _result[1]

'''
    source = source.replace(entry, wrapper + entry, 1)
compile(source, str(path), "exec")
path.write_text(source, encoding="utf-8")
PY

# Exactly one full baseline request. No settings file is edited.
export VCE_BENCH_ONLY_BASELINE=1
export VCE_BENCH_RESULT_ROOT="$RESULT_ROOT"
export VCE_FULL_E2E_REQUEST_MANIFEST="$TMP_DIR/request_flags.json"
unset VCE_BENCH_VLM_ONLY VCE_BENCH_WHISPER_ONLY

touch "$RUN_MARKER"
echo "Starting one full end-to-end request: VLM + Whisper + Text LLM + Apple Vision OCR."
set +e
"$PYTHON" "$TMP_RUNNER" --project "$PROJECT_DIR" --video "$VIDEO_PATH" 2>&1 | /usr/bin/tee "$TMP_DIR/runner_console.log"
RUN_EXIT=${PIPESTATUS[0]}
set -e
printf '\nRunner exit code: %s\n' "$RUN_EXIT"

RUN_DIR="$(/usr/bin/find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d -newer "$RUN_MARKER" -print 2>/dev/null | /usr/bin/sort | /usr/bin/tail -n 1)"
if [[ -z "$RUN_DIR" || ! -d "$RUN_DIR" ]]; then
  FALLBACK_STAMP="$(/bin/date -u +%Y%m%dT%H%M%SZ)"
  RUN_DIR="$RESULT_ROOT/launcher-failed-$FALLBACK_STAMP"
  mkdir -p "$RUN_DIR"
fi
cp "$TMP_DIR/runner_console.log" "$RUN_DIR/launcher_console.log" 2>/dev/null || true
[[ -s "$TMP_DIR/request_flags.json" ]] || fail "Full E2E request flags were not captured."
cp "$TMP_DIR/request_flags.json" "$RUN_DIR/request_flags.json"

# Build a publication-safe error digest. Raw messages, tracebacks, API payloads,
# transcripts, OCR text, model output and raw logs remain in RUN_DIR only.
STAMP="$(/bin/date -u +%Y%m%dT%H%M%SZ)"
PUBLISH_DIR="$TMP_DIR/publish-repo"
if ! git clone --quiet --branch "$PUBLISH_BRANCH" "https://github.com/$REPOSITORY.git" "$PUBLISH_DIR"; then
  echo "ERROR: could not clone the publication branch. Full diagnostics remain local: $RUN_DIR" >&2
  exit 4
fi
DEST="$PUBLISH_DIR/benchmark-results/parallel-diagnostic-$STAMP"
mkdir -p "$DEST"

"$PYTHON" - "$RUN_DIR" "$DEST" "$VIDEO_PATH" "$HOME" "$RUN_EXIT" "$RUNNER_REF" "$PATCHER_REF" <<'PY'
from pathlib import Path
import csv, json, re, sys
from collections import Counter

run_dir, dest, video, home, exit_code, runner_ref, patch_ref = map(str, sys.argv[1:])
run_dir, dest = Path(run_dir), Path(dest)

def redact_text(text: str) -> str:
    text = text.replace(video, "<INPUT_VIDEO>")
    text = text.replace(Path(video).name, "<INPUT_VIDEO>")
    return text.replace(home, "<HOME>")

for name in ("summary.md", "stage_comparison.csv", "device_monitoring.csv"):
    src = run_dir / name
    if src.is_file():
        (dest / name).write_text(redact_text(src.read_text(encoding="utf-8-sig", errors="replace")), encoding="utf-8")

def classify(raw: str) -> str:
    s = raw.lower()
    if any(x in s for x in ("kiogpu", "command buffer", "submissionsignored", "metal")):
        return "METAL_SUBMISSION"
    if any(x in s for x in ("mlx", "mx.eval", "mx.synchronize")):
        return "MLX_RUNTIME"
    if any(x in s for x in ("whisper", "audio", "transcrib")):
        return "WHISPER_AUDIO"
    if any(x in s for x in ("text llm", "qwen3.5", "post-process")):
        return "TEXT_LLM"
    if any(x in s for x in ("vlm", "vision-language")):
        return "VLM"
    if any(x in s for x in ("ocr", "tesseract", "vision framework")):
        return "OCR"
    if any(x in s for x in ("http", "api", "connection", "timeout")):
        return "API_TRANSPORT"
    return "PYTHON_OR_OTHER"

stage_names = ("scene_detection", "whisper", "vlm", "apple_ocr", "ffmpeg", "tesseract", "text_llm", "post-process", "postprocess", "api")
def stage_from(raw: str, row: dict | None = None) -> str:
    if row:
        for key in ("stage", "process", "component", "subsystem", "worker"):
            value = str(row.get(key, "")).strip()
            if value:
                return re.sub(r"[^A-Za-z0-9_.:-]", "_", value)[:64]
    s = raw.lower()
    for name in stage_names:
        if name in s:
            return name.upper().replace("-", "_")
    return "UNKNOWN"

def safe_exception(raw: str) -> str:
    match = re.search(r"\b([A-Z][A-Za-z0-9_.]*(?:Error|Exception|Failure))\b", raw)
    return match.group(1)[:80] if match else "Unclassified"

def timestamp_from(raw: str, row: dict | None = None) -> str:
    if row:
        for key in ("timestamp", "time", "created_at", "datetime", "ts"):
            value = str(row.get(key, "")).strip()
            if value and len(value) <= 48 and re.match(r"^[0-9T:+Z. _-]+$", value):
                return value
    match = re.search(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", raw)
    return match.group(0) if match else ""

items = []
errors_csv = run_dir / "errors.csv"
if errors_csv.is_file():
    try:
        with errors_csv.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                raw = " ".join(str(v) for v in row.values() if v is not None)
                items.append({"timestamp": timestamp_from(raw, row), "stage": stage_from(raw, row),
                              "category": classify(raw), "exception_type": safe_exception(raw)})
    except Exception:
        pass

# Fallback if no structured error rows: inspect logs but retain only safe metadata.
if not items:
    for log_name in ("server_log_delta.log", "errors.log", "launcher_console.log"):
        log_path = run_dir / log_name
        if not log_path.is_file():
            continue
        try:
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                low = line.lower()
                if not any(x in low for x in ("error", "exception", "traceback", "failed", "submissionsignored", "command buffer")):
                    continue
                items.append({"timestamp": timestamp_from(line), "stage": stage_from(line),
                              "category": classify(line), "exception_type": safe_exception(line)})
        except Exception:
            continue
        if items:
            break

counts = Counter((x["category"], x["stage"], x["exception_type"]) for x in items)
with (dest / "error_summary.csv").open("w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(("category", "stage", "exception_type", "observed_lines"))
    for (category, stage, exc), count in sorted(counts.items()):
        writer.writerow((category, stage, exc, count))

first = items[0] if items else None
local_index = {"error_observations": len(items), "first_observed": first,
               "note": "Raw excerpts and tracebacks remain local; repeated cascade lines may be counted."}
(run_dir / "first_error_index.json").write_text(json.dumps(local_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

status = [
    "diagnostic_timestamp_utc=" + dest.name.replace("parallel-diagnostic-", ""),
    "runner_exit_code=" + exit_code,
    "runner_revision=" + runner_ref,
    "patch_helper_revision=" + patch_ref,
    "profiles=baseline_only",
    "enable_mlx_whisper=false",
    "error_observations=" + str(len(items)),
    "first_error_category=" + (first["category"] if first else "none_observed"),
    "first_error_stage=" + (first["stage"] if first else "none_observed"),
    "input_video=<redacted>",
    "raw_logs_and_full_response=local_only",
]
(dest / "launcher_status.txt").write_text("\n".join(status) + "\n", encoding="utf-8")

notes = [
    "# Parallel stage diagnostic",
    "",
    "This publication contains aggregate stage timings, processor/backend classifications, available device-monitoring samples and a sanitized error digest.",
    "Raw messages, tracebacks, local paths, source media, transcripts, OCR text, prompts, model output and full API responses are intentionally excluded.",
    "GPU power/residency samples, when present, are system-wide and are not per-process or per-kernel attribution.",
    "Configured MLX GPU/Metal targets are not proof that every operator ran on GPU. Apple Vision hardware attribution may remain unknown.",
    "",
    "## Error digest",
    "",
    f"Sanitized error observations: {len(items)}",
]
if first:
    notes.append(f"First observed category/stage/type: {first['category']} / {first['stage']} / {first['exception_type']}")
else:
    notes.append("No error signature was found in the structured error CSV or captured server log delta.")
(dest / "diagnostic_notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
PY

# Publish the sanitized table and summary, never local raw diagnostics.
git -C "$PUBLISH_DIR" add "benchmark-results/parallel-diagnostic-$STAMP"
GH_LOGIN="$(gh api user --jq .login 2>/dev/null)"
GH_ID="$(gh api user --jq .id 2>/dev/null)"
[[ -n "$GH_LOGIN" && -n "$GH_ID" ]] || { echo "ERROR: could not read GitHub identity; diagnostics remain local: $RUN_DIR" >&2; exit 5; }
if git -C "$PUBLISH_DIR" -c user.name="$GH_LOGIN" -c user.email="$GH_ID+$GH_LOGIN@users.noreply.github.com" \
  commit -m "Add parallel stage diagnostic $STAMP"; then
  git -C "$PUBLISH_DIR" push origin "$PUBLISH_BRANCH" || {
    echo "ERROR: commit was created locally but push failed. Full diagnostics: $RUN_DIR" >&2
    exit 6
  }
else
  echo "ERROR: could not create the report commit. Full diagnostics: $RUN_DIR" >&2
  exit 7
fi

echo "Published sanitized diagnostic: https://github.com/$REPOSITORY/tree/$PUBLISH_BRANCH/benchmark-results/parallel-diagnostic-$STAMP"
echo "Local full diagnostics (private): $RUN_DIR"
echo "Runner exit code: $RUN_EXIT. Non-zero exit is not treated as successful recognition."
exit "$RUN_EXIT"
