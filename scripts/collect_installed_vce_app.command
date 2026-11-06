#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'

# Collect the installed macOS application bundle as-is and upload it to a
# PRIVATE GitHub repository Release. This script does not modify or launch the app.

# Make Homebrew GitHub CLI discoverable when this script is launched from Finder.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

APP_PATH="${VCE_APP_PATH:-/Applications/VideoContext Engine.app}"
UPLOAD_REPO="${VCE_APP_UPLOAD_REPO:-shushpanchik-art/vce-local-review-20261102-170802}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$HOME/Downloads/VCE_INSTALLED_APP_$STAMP"
ARCHIVE_NAME="VideoContext_Engine_app_$STAMP.zip"
ARCHIVE="$OUT/$ARCHIVE_NAME"
MANIFEST="$OUT/APP_MANIFEST.txt"
FILE_HASHES="$OUT/APP_FILE_SHA256SUMS.txt"
SYMLINKS="$OUT/APP_SYMLINKS.txt"
PLIST_INFO="$OUT/APP_INFO_PLIST.txt"
SIGNATURE_REPORT="$OUT/CODE_SIGNATURE_REPORT.txt"
XATTR_REPORT="$OUT/EXTENDED_ATTRIBUTES.txt"
RELEASE_NOTES="$OUT/RELEASE_NOTES.txt"
RECEIPT="$OUT/UPLOAD_RECEIPT.txt"

mkdir -p "$OUT"
LOG="$OUT/collector.log"
exec > >(tee -a "$LOG") 2>&1

fail() { echo "ERROR: $*" >&2; exit 1; }

printf '%s\n' '=== VCE INSTALLED APP BUNDLE COLLECTOR ==='
echo "UTC=$STAMP"
echo "APP_PATH=$APP_PATH"
echo "PRIVATE_UPLOAD_REPO=$UPLOAD_REPO"
echo "OUTPUT=$OUT"

[[ "$(uname -s)" == "Darwin" ]] || fail "This script must run on macOS."
[[ -d "$APP_PATH/Contents" ]] || fail "Application bundle not found: $APP_PATH"
INFO_PLIST="$APP_PATH/Contents/Info.plist"
[[ -f "$INFO_PLIST" ]] || fail "Missing Contents/Info.plist in app bundle."
command -v ditto >/dev/null 2>&1 || fail "ditto is required."
command -v shasum >/dev/null 2>&1 || fail "shasum is required."
command -v plutil >/dev/null 2>&1 || fail "plutil is required."
command -v gh >/dev/null 2>&1 || fail "GitHub CLI 'gh' not found. Install with: brew install gh"

# Confirm GitHub authentication and refuse to upload to a public repository.
gh auth status --hostname github.com >/dev/null 2>&1 || fail "GitHub CLI is not authenticated. Run: gh auth login"
VISIBILITY="$(gh repo view "$UPLOAD_REPO" --json visibility --jq .visibility 2>/dev/null || true)"
[[ "$VISIBILITY" == "PRIVATE" ]] || fail "Upload destination is not confirmed PRIVATE (reported: '${VISIBILITY:-unknown}'). Nothing uploaded."

BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$INFO_PLIST" 2>/dev/null || true)"
APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$INFO_PLIST" 2>/dev/null || true)"
BUILD_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$INFO_PLIST" 2>/dev/null || true)"
EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$INFO_PLIST" 2>/dev/null || true)"

# Preserve a readable copy of Info.plist and native bundle metadata.
plutil -p "$INFO_PLIST" > "$PLIST_INFO" 2>&1 || true

# Record signature identity, verification outcome, Gatekeeper assessment and xattrs.
{
  echo '=== codesign display ==='
  codesign -dv --verbose=4 "$APP_PATH" 2>&1 || true
  echo
  echo '=== codesign verify --deep --strict ==='
  if codesign --verify --deep --strict --verbose=2 "$APP_PATH" 2>&1; then
    echo 'CODESIGN_VERIFY=PASS'
  else
    echo 'CODESIGN_VERIFY=FAILED_OR_UNSIGNED'
  fi
  echo
  echo '=== spctl assessment ==='
  if command -v spctl >/dev/null 2>&1; then
    spctl --assess --type execute -vv "$APP_PATH" 2>&1 || true
  else
    echo 'spctl unavailable'
  fi
} > "$SIGNATURE_REPORT"

if command -v xattr >/dev/null 2>&1; then
  xattr -lr "$APP_PATH" > "$XATTR_REPORT" 2>&1 || true
else
  echo 'xattr unavailable' > "$XATTR_REPORT"
fi

# Complete bundle inventory, including symbolic links and per-file SHA-256.
find "$APP_PATH" -type l -exec sh -c '
  for p do
    printf "%s -> %s\n" "$p" "$(readlink "$p")"
  done
' sh {} + > "$SYMLINKS"
(
  cd "$APP_PATH"
  find . -type f -print0 | xargs -0 shasum -a 256
) > "$FILE_HASHES"
FILE_COUNT="$(find "$APP_PATH" -type f | wc -l | tr -d ' ')"
DIR_SIZE_KB="$(du -sk "$APP_PATH" | awk '{print $1}')"

MAIN_EXECUTABLE='not declared in Info.plist'
MAIN_EXECUTABLE_FILE='not available'
if [[ -n "$EXECUTABLE" ]]; then
  MAIN_EXECUTABLE="$APP_PATH/Contents/MacOS/$EXECUTABLE"
  if [[ -f "$MAIN_EXECUTABLE" ]]; then
    MAIN_EXECUTABLE_FILE="$(file "$MAIN_EXECUTABLE" 2>&1 || true)"
  else
    MAIN_EXECUTABLE_FILE='declared executable path does not exist'
  fi
fi

cat > "$MANIFEST" <<EOF_MANIFEST
VCE INSTALLED APPLICATION BUNDLE SNAPSHOT
created_utc=$STAMP
machine=$(uname -m)
macos_version=$(sw_vers -productVersion 2>/dev/null || true)
app_path=$APP_PATH
bundle_id=${BUNDLE_ID:-unknown}
short_version=${APP_VERSION:-unknown}
bundle_version=${BUILD_VERSION:-unknown}
cf_bundle_executable=${EXECUTABLE:-unknown}
main_executable_path=$MAIN_EXECUTABLE
main_executable_file=$MAIN_EXECUTABLE_FILE
regular_file_count=$FILE_COUNT
bundle_directory_size_kb=$DIR_SIZE_KB
archive_format=macOS ditto ZIP; keepParent and sequesterRsrc enabled
upload_repository=$UPLOAD_REPO
repository_visibility=$VISIBILITY
contents=complete installed .app bundle; file hashes, symlink inventory, Info.plist, code-signature report and extended-attribute report are sidecar assets
not_included=Application Support data, user preferences, logs outside the app bundle, source project folder, original video, model caches outside the app bundle
EOF_MANIFEST

cat > "$RELEASE_NOTES" <<EOF_NOTES
Forensic snapshot of the installed macOS application bundle.

Created UTC: $STAMP
Bundle ID: ${BUNDLE_ID:-unknown}
Version: ${APP_VERSION:-unknown} (build ${BUILD_VERSION:-unknown})
The ZIP was created using Apple's ditto with resource-fork preservation and the parent app directory retained.
The release is uploaded only after confirming the destination repository visibility is PRIVATE.
EOF_NOTES

# Archive the complete .app bundle without altering it.
echo '--- Creating complete app bundle archive with ditto ---'
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ARCHIVE"
[[ -s "$ARCHIVE" ]] || fail 'Archive was not created or is empty.'

# Verify ZIP integrity and compute archive digest.
echo '--- Verifying archive ---'
unzip -tq "$ARCHIVE" || fail 'ZIP integrity test failed.'
ARCHIVE_SHA256="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
ARCHIVE_SIZE="$(stat -f '%z' "$ARCHIVE")"
printf '%s  %s\n' "$ARCHIVE_SHA256" "$ARCHIVE_NAME" > "$OUT/$ARCHIVE_NAME.sha256"

# Upload only to the private review repository.
TAG="installed-app-$STAMP"
echo '--- Uploading to private GitHub Release ---'
gh release create "$TAG" \
  "$ARCHIVE" \
  "$MANIFEST" \
  "$FILE_HASHES" \
  "$SYMLINKS" \
  "$PLIST_INFO" \
  "$SIGNATURE_REPORT" \
  "$XATTR_REPORT" \
  "$OUT/$ARCHIVE_NAME.sha256" \
  --repo "$UPLOAD_REPO" \
  --title "Installed VideoContext Engine.app $STAMP" \
  --notes-file "$RELEASE_NOTES"

RELEASE_URL="https://github.com/$UPLOAD_REPO/releases/tag/$TAG"

cat > "$RECEIPT" <<EOF_RECEIPT
status=uploaded
repository=$UPLOAD_REPO
repository_visibility=$VISIBILITY
tag=$TAG
release_url=$RELEASE_URL
app_path=$APP_PATH
bundle_id=${BUNDLE_ID:-unknown}
short_version=${APP_VERSION:-unknown}
bundle_version=${BUILD_VERSION:-unknown}
archive=$ARCHIVE
archive_name=$ARCHIVE_NAME
archive_size_bytes=$ARCHIVE_SIZE
archive_sha256=$ARCHIVE_SHA256
regular_file_count=$FILE_COUNT
created_utc=$STAMP
EOF_RECEIPT

# Upload receipt too, then verify the release contains the main archive and receipt.
gh release upload "$TAG" "$RECEIPT" --repo "$UPLOAD_REPO"
RELEASE_JSON="$(gh release view "$TAG" --repo "$UPLOAD_REPO" --json url,assets)"
printf '%s\n' "$RELEASE_JSON" | grep -F "$ARCHIVE_NAME" >/dev/null || fail 'Release verification failed: archive asset is not listed.'
printf '%s\n' "$RELEASE_JSON" | grep -F 'UPLOAD_RECEIPT.txt' >/dev/null || fail 'Release verification failed: receipt asset is not listed.'

printf '%s\n' '=== COMPLETE ==='
echo "Release: $RELEASE_URL"
echo "Archive: $ARCHIVE"
echo "Archive SHA-256: $ARCHIVE_SHA256"
echo "Receipt: $RECEIPT"
echo 'The installed app bundle was read and archived; it was not modified or launched.'
