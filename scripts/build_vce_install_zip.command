#!/bin/bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ $# != 1 ]]; then
  echo "Usage: $0 OUTPUT_DIRECTORY" >&2
  exit 2
fi
mkdir -p "$1"
OUTPUT="$(cd "$1" && pwd -P)"
REVISION="$(git -C "$ROOT" rev-parse HEAD)"
NAME="VideoContext-Engine-macOS-Apple-Silicon-${REVISION:0:7}"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/vce-distribution.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
PACKAGE="$STAGE/$NAME"
mkdir "$PACKAGE"
git -C "$ROOT" archive "$REVISION" -- \
  VideoContextEngine_v3.19.py videocontext_web_ui.html requirements.txt requirements-apple-silicon.txt \
  vce_core domain_profiles config/videocontext_settings.apple_silicon.example.json \
  'macos/VideoContext Engine.app' macos/install_video_context_mac.command \
  scripts/install_vce_clean.command scripts/install_vce_recognition_languages_patch.command \
  patches/patch_vce_stop_and_mps.py patches/vce-recognition-languages-patcher.tar.gz \
  LICENSE NOTICE.md THIRD_PARTY_LICENSES.md UPSTREAM.md docs/CLEAN_INSTALL_MACOS.md |
  tar -xf - -C "$PACKAGE"
printf '%s\n' "$REVISION" > "$PACKAGE/.vce-distribution"
cat > "$PACKAGE/Install VideoContext Engine.command" <<'LAUNCHER'
#!/bin/bash
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"
exec /bin/bash scripts/install_vce_clean.command
LAUNCHER
chmod +x "$PACKAGE/Install VideoContext Engine.command"
/usr/bin/ditto -c -k --keepParent "$PACKAGE" "$STAGE/$NAME.zip"
mv "$STAGE/$NAME.zip" "$OUTPUT/$NAME.zip"
(cd "$OUTPUT" && shasum -a 256 "$NAME.zip" > "$NAME.zip.sha256")
printf '%s\n' "$OUTPUT/$NAME.zip"
