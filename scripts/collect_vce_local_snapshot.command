#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'

if [[ -n "${VCE_PROJECT_DIR:-}" ]]; then
  PROJECT_DIR="$VCE_PROJECT_DIR"
elif [[ -f "$HOME/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py" ]]; then
  PROJECT_DIR="$HOME/VideoContext-Engine-Apple-Silicon"
else
  PROJECT_DIR="$HOME/VideoContext-Engine"
fi
UPLOAD_REPO="${VCE_SNAPSHOT_REPO:-shushpanchik-art/vce-local-review-20261102-170802}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$HOME/Downloads/VCE_LOCAL_SNAPSHOT_$STAMP"
STAGE="$OUT/stage"
ARCHIVE="$OUT/VCE_LOCAL_SNAPSHOT_$STAMP.tar.gz"
mkdir -p "$STAGE/project" "$STAGE/evidence"
LOG="$OUT/collector.log"
exec > >(tee -a "$LOG") 2>&1

echo "=== VCE LOCAL SNAPSHOT COLLECTOR ==="
echo "UTC=$STAMP"
echo "PROJECT_DIR=$PROJECT_DIR"
echo "PRIVATE_UPLOAD_REPO=$UPLOAD_REPO"
echo "OUTPUT=$OUT"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "ERROR: project folder not found: $PROJECT_DIR"
  echo "Set VCE_PROJECT_DIR to the exact local VideoContext-Engine folder and rerun."
  exit 2
fi
if [[ ! -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" ]]; then
  echo "ERROR: VideoContextEngine_v3.19.py not found under $PROJECT_DIR"
  exit 2
fi
command -v rsync >/dev/null || { echo "ERROR: rsync is required"; exit 2; }
command -v python3 >/dev/null || { echo "ERROR: python3 is required"; exit 2; }

echo "--- Git identity ---"
if git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$PROJECT_DIR" rev-parse HEAD > "$STAGE/evidence/git_head.txt" || true
  git -C "$PROJECT_DIR" status --short --branch > "$STAGE/evidence/git_status.txt" || true
  git -C "$PROJECT_DIR" log -n 100 --date=iso-strict --format='%H%x09%ad%x09%s' > "$STAGE/evidence/git_log.txt" || true
  git -C "$PROJECT_DIR" diff --binary HEAD > "$STAGE/evidence/local_changes.patch" || true
  git -C "$PROJECT_DIR" diff --cached --binary > "$STAGE/evidence/staged_changes.patch" || true
  git -C "$PROJECT_DIR" bundle create "$STAGE/evidence/repository.bundle" --all 2>&1 || true
else
  echo "No Git metadata in project directory" > "$STAGE/evidence/git_status.txt"
fi

echo "--- Copy project source (exclude caches, generated media and credentials) ---"
rsync -a --human-readable \
  --exclude='.git/' --exclude='.venv/' --exclude='venv/' --exclude='__pycache__/' \
  --exclude='*.pyc' --exclude='node_modules/' --exclude='models/' --exclude='model_cache/' \
  --exclude='hf_cache/' --exclude='.cache/' --exclude='cache/' --exclude='outputs/' \
  --exclude='output/' --exclude='results/' --exclude='benchmark-results/' \
  --exclude='*.mp4' --exclude='*.mov' --exclude='*.mkv' --exclude='*.wav' \
  --exclude='*.mp3' --exclude='*.m4a' --exclude='*.aac' --exclude='*.flac' \
  --exclude='videocontext_settings.json' --exclude='*.log' --exclude='.env' --exclude='.env.*' --exclude='*token*' --exclude='*secret*' \
  --exclude='*credential*' --exclude='*password*' \
  "$PROJECT_DIR/" "$STAGE/project/"

echo "--- Redacted local settings ---"
SETTINGS="$PROJECT_DIR/videocontext_settings.json"
if [[ -f "$SETTINGS" ]]; then
  python3 - "$SETTINGS" "$STAGE/evidence/videocontext_settings.redacted.json" <<'PY'
import json, re, sys
src, dst = sys.argv[1:3]
pattern = re.compile(r"(token|secret|password|passwd|api.?key|authorization|cookie|credential|private.?key)", re.I)
def clean(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if pattern.search(str(k)) else clean(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value
with open(src, encoding="utf-8") as f: data=json.load(f)
with open(dst, "w", encoding="utf-8") as f: json.dump(clean(data), f, ensure_ascii=False, indent=2)
PY
fi

echo "--- Runtime backups (for 25 October comparison) ---"
find "$PROJECT_DIR" -maxdepth 3 -type f \( -name 'VideoContextEngine_v3.19.py*.bak' -o -name 'VideoContextEngine_v3.19.py.before*' -o -name '*20261025*' \) -print > "$STAGE/evidence/historical_candidates.txt" || true
while IFS= read -r f; do
  [[ -f "$f" ]] || continue
  rel="${f#"$PROJECT_DIR"/}"
  mkdir -p "$STAGE/project/$(dirname "$rel")"
  cp -p "$f" "$STAGE/project/$rel"
done < "$STAGE/evidence/historical_candidates.txt"

echo "--- Machine/runtime facts ---"
{
  date
  sw_vers
  uname -a
  system_profiler SPHardwareDataType 2>/dev/null | head -80
  python3 --version
  command -v ffmpeg >/dev/null && ffmpeg -version | head -3 || true
  command -v ffprobe >/dev/null && ffprobe -version | head -2 || true
  command -v tesseract >/dev/null && tesseract --version | head -2 || true
  echo "--- Python packages ---"
  python3 -m pip freeze 2>/dev/null | sort || true
} > "$STAGE/evidence/environment.txt" 2>&1

echo "--- Installed app metadata/source ---"
APP="/Applications/VideoContext Engine.app"
if [[ -d "$APP/Contents" ]]; then
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null > "$STAGE/evidence/app_bundle_id.txt" || true
  /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null > "$STAGE/evidence/app_version.txt" || true
  find "$APP/Contents" -maxdepth 5 -type f \( -name '*.swift' -o -name '*.command' -o -name '*.plist' \) -print > "$STAGE/evidence/installed_app_source_files.txt" || true
fi

echo "--- Relevant server log tail ---"
SERVER_LOG="$HOME/Library/Logs/VideoContext Engine/server.log"
if [[ -f "$SERVER_LOG" ]]; then
  tail -n 20000 "$SERVER_LOG" > "$STAGE/evidence/server_log_tail.txt"
  shasum -a 256 "$SERVER_LOG" > "$STAGE/evidence/server_log_sha256.txt"
fi

echo "--- Source hashes and inventory ---"
find "$STAGE/project" -type f -print0 | xargs -0 shasum -a 256 > "$STAGE/evidence/project_sha256.txt"
shasum -a 256 "$PROJECT_DIR/VideoContextEngine_v3.19.py" > "$STAGE/evidence/local_runtime_sha256.txt"
{
  echo "project_dir=$PROJECT_DIR"
  echo "runtime=$PROJECT_DIR/VideoContextEngine_v3.19.py"
  echo "created_utc=$STAMP"
  echo "upload_repo=$UPLOAD_REPO"
  echo "privacy=private staging; no source media or model caches included"
} > "$STAGE/MANIFEST.txt"

echo "--- Archive ---"
tar -czf "$ARCHIVE" -C "$STAGE" .
shasum -a 256 "$ARCHIVE" | tee "$OUT/archive_sha256.txt"
du -h "$ARCHIVE"

echo "--- Upload to private GitHub Release ---"
if ! command -v gh >/dev/null 2>&1; then
  echo "UPLOAD_NOT_DONE: GitHub CLI 'gh' is missing."
  echo "Install with: brew install gh"
  echo "Then authenticate: gh auth login"
  echo "Archive retained at: $ARCHIVE"
  exit 3
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "UPLOAD_NOT_DONE: gh is not authenticated."
  echo "Run: gh auth login"
  echo "Archive retained at: $ARCHIVE"
  exit 3
fi
TAG="local-snapshot-$STAMP"
NOTES="$OUT/release_notes.txt"
cat > "$NOTES" <<EOF
Private local VCE source snapshot.
Created UTC: $STAMP
Local project path: $PROJECT_DIR
Contains source, local patches/backups, Git state, redacted settings and diagnostic metadata.
Excludes original media, model weights/caches, virtualenvs and raw credentials.
SHA-256: $(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
EOF

gh release create "$TAG" "$ARCHIVE" --repo "$UPLOAD_REPO" \
  --title "VCE local snapshot $STAMP" --notes-file "$NOTES"

URL="https://github.com/$UPLOAD_REPO/releases/tag/$TAG"
{
  echo "status=uploaded"
  echo "repository=$UPLOAD_REPO"
  echo "tag=$TAG"
  echo "release_url=$URL"
  echo "archive=$ARCHIVE"
  echo "sha256=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
} | tee "$OUT/UPLOAD_RECEIPT.txt"

echo "=== COMPLETE ==="
echo "Private release: $URL"
echo "Receipt: $OUT/UPLOAD_RECEIPT.txt"
echo "Archive retained locally: $ARCHIVE"
