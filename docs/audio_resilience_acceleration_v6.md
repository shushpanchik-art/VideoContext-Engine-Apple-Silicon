# VCE Audio Resilience + CPU/OCR Overlap (V6)

This patch targets a local VideoContext Engine v3.19 checkout that already has the analysis profiles and \`CPU/Tesseract OCR v5 patch\` installed.

## Install

From a terminal on the Mac:

\`\`\`bash
python3 "$HOME/Downloads/patch_vce_audio_resilience_acceleration_v6.py" \
  --project "$HOME/VideoContext-Engine" \
  --dry-run
\`\`\`

Review the diff. If the preflight passes, apply it without \`--dry-run\`:

\`\`\`bash
python3 "$HOME/Downloads/patch_vce_audio_resilience_acceleration_v6.py" \
  --project "$HOME/VideoContext-Engine"
\`\`\`

The patcher creates a timestamped \`.bak\` before replacing the engine file. It refuses to change files if expected v5 anchors are missing, and it validates the patched Python with \`ast.parse\`.

## Behavior

- Probes the first audio stream using \`ffprobe\`; if unavailable, it tries to inspect streams with FFmpeg.
- If the video has no audio track, it sets the run's audio-skip flag automatically, so Whisper is not loaded and the visual/OCR/Qwen3.5 stages continue.
- If a later Whisper transcribe call encounters a recognized FFmpeg audio-decode failure, it falls back to an empty transcript and continues. Other exceptions, including unrelated runtime failures, are not silently swallowed.
- Finds \`ffmpeg\`, \`ffprobe\`, and \`tesseract\` via the process \`PATH\` and common Homebrew/macOS locations, including when an app launcher's PATH is minimal.
- Runs optional FFmpeg/Tesseract OCR in a background task and waits before scene-level OCR merge, allowing CPU frame decode/OCR to overlap with Whisper inference or model loading.
- Adds audio-track status, whether audio was automatically skipped, and a diagnostic message to JSON metadata. The raw transcript/OCR and Qwen3.5 post-processing implementation are not rewritten by this patch.

This patch does not automatically install Homebrew packages. When Tesseract is unavailable, the optional OCR path stays non-fatal and Apple Vision OCR remains available.

## Tests

\`\`\`bash
python3 -m unittest discover -s tests -p "test_vce_audio_resilience_acceleration_v6.py" -v
\`\`\`
