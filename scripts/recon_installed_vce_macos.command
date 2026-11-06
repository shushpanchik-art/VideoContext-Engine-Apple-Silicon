#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$HOME/Downloads/VCE_MAC_RECON_$STAMP"
mkdir -p "$OUT"
exec > >(tee "$OUT/RECON_REPORT.txt") 2>&1
echo "=== VCE MAC READ-ONLY RECON ==="
echo "UTC=$STAMP"
echo "HOST=$(scutil --get ComputerName 2>/dev/null || hostname)"
echo "ARCH=$(uname -m)"
echo "MACOS=$(sw_vers -productVersion 2>/dev/null || true)"
echo "USER_HOME=$HOME"
echo "NOTE=Metadata and paths only; no app launch, no process termination, no settings changes."
echo
echo "=== 1. APP CANDIDATES ==="
for p in "/Applications/VideoContext Engine.app" "$HOME/Applications/VideoContext Engine.app" "/Applications/VideoContext.app" "$HOME/Applications/VideoContext.app"; do
  if [[ -d "$p" ]]; then echo "FOUND_APP=$p"; /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$p/Contents/Info.plist" 2>/dev/null || true; /usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$p/Contents/Info.plist" 2>/dev/null || true; /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$p/Contents/Info.plist" 2>/dev/null || true; du -sh "$p" 2>/dev/null || true; fi
done
echo "--- Spotlight matches ---"
mdfind 'kMDItemFSName == "VideoContext Engine.app"c' 2>/dev/null | head -100 || true
echo "--- Common install roots ---"
find /Applications "$HOME/Applications" "$HOME/Downloads" -maxdepth 4 -iname '*VideoContext*' -print 2>/dev/null | head -300 || true
echo
echo "=== 2. RUNNING PROCESSES (READ ONLY) ==="
ps -axo pid,ppid,user,etime,%cpu,%mem,command | grep -Ei '[V]ideoContext|[v]ce|[l]lama|[w]hisper|[u]vicorn|[f]astapi' | head -250 || true
echo
echo "=== 3. APP BUNDLE STRUCTURE ==="
APP="/Applications/VideoContext Engine.app"
if [[ ! -d "$APP/Contents" && -d "$HOME/Applications/VideoContext Engine.app/Contents" ]]; then APP="$HOME/Applications/VideoContext Engine.app"; fi
if [[ -d "$APP/Contents" ]]; then
  echo "SELECTED_APP=$APP"
  plutil -p "$APP/Contents/Info.plist" 2>/dev/null || true
  echo "--- Bundle files (relative paths, max depth 8) ---"
  find "$APP/Contents" -maxdepth 8 -print 2>/dev/null | sed "s#^$APP/##" | head -5000
  echo "--- Executable file metadata ---"
  EXE=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist" 2>/dev/null || true)
  [[ -n "$EXE" && -e "$APP/Contents/MacOS/$EXE" ]] && file "$APP/Contents/MacOS/$EXE" && ls -l "$APP/Contents/MacOS/$EXE" || true
  echo "--- Signature summary ---"
  codesign -dv --verbose=4 "$APP" 2>&1 || true
  codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 || true
else
  echo "APP_BUNDLE_NOT_FOUND_AT_STANDARD_PATHS"
fi
echo
echo "=== 4. VCE-RELATED SUPPORT DIRECTORIES (PATHS/SIZES ONLY) ==="
for root in "$HOME/Library/Application Support" "$HOME/Library/Preferences" "$HOME/Library/Logs" "$HOME/Library/Caches" "$HOME/Library/LaunchAgents" "/Library/Application Support" "/Library/LaunchAgents" "/Library/LaunchDaemons"; do
  [[ -d "$root" ]] || continue
  echo "--- ROOT=$root ---"
  find "$root" -maxdepth 4 \( -iname '*videocontext*' -o -iname '*vce*' \) -print 2>/dev/null | while IFS= read -r p; do ls -ld "$p" 2>/dev/null || true; du -sh "$p" 2>/dev/null || true; done
done
echo
echo "=== 5. LAUNCH ITEMS MATCHING VCE (FILENAMES ONLY) ==="
for root in "$HOME/Library/LaunchAgents" "/Library/LaunchAgents" "/Library/LaunchDaemons"; do
  [[ -d "$root" ]] && find "$root" -maxdepth 1 -type f -print 2>/dev/null | grep -Ei 'vce|videocontext' || true
done
echo
echo "=== 6. PROJECT / GIT CHECKOUT CANDIDATES ==="
for root in "$HOME" "$HOME/Projects" "$HOME/Developer" "$HOME/Downloads" "/Applications"; do
  [[ -d "$root" ]] || continue
  find "$root" -maxdepth 5 -type d \( -name .git -o -iname '*VideoContext*Engine*' \) -print 2>/dev/null | head -500
done
echo
echo "=== 7. INSTALLED TOOLCHAIN ==="
for c in python3 python node npm ffmpeg git gh brew; do command -v "$c" 2>/dev/null || true; done
python3 --version 2>&1 || true
ffmpeg -version 2>&1 | head -3 || true
echo
echo "=== 8. OUTPUT FILES ==="
find "$OUT" -maxdepth 1 -type f -exec ls -lh {} \;
echo
echo "RECON_COMPLETE=$OUT"
