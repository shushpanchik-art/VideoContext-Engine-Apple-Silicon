# Changelog

## 0.2.1 — Quality audit, diagnostics and repository cleanup (2026-11-02)

- Added a universal Text LLM structural audit and a source-aware lexical evidence screen with explicit limitations; neither is presented as a semantic truth verifier.
- Added raw-model versus finalized-output provenance, invalid-JSON diagnostics and regression coverage.
- Hardened the Text LLM benchmark: local video/JSON input must be selected explicitly; GitHub publication is disabled by default; generated outputs require a second explicit upload flag.
- Added local-only MLX/Metal observer and watchdog tools with regression tests and a guide for interpreting device/stream telemetry.
- Removed developer-specific video paths from the published benchmark and watchdog scripts.
- Moved reusable code and tests from historical PRs #13 and #14 into `main`; excluded raw video-specific benchmark outputs, telemetry dumps and installed-app archives.
- Closed superseded PRs #13 and #14; PR #15 remains the full-runtime publication merge.
- Deleted all 14 superseded research/feature branches; only `main` remains. Removed the temporary one-time cleanup workflow after it completed.

## 0.2.0 — Full public runtime distribution (2026-11-02)

- Published the complete customized VideoContext Engine v3.19 runtime and matching web UI in this repository.
- Added URL batch ingestion for VK Video, YouTube and RuTube, with queue progress, cancellation, and TXT/JSON reports.
- Included Apple Silicon/macOS patchers, native AppKit/WebKit app sources and a clean-install path for Python 3.11.
- Added dependency manifests, sanitized settings examples, publication/privacy manifest, and third-party MIT attribution.
- Removed local backup files, private benchmark logs, and other non-distribution artifacts from the published source tree.
- Merged as PR #15 into `main` using squash commit `549186bbf9c4653955cb8d8f4d1e967055e80b55`.

## Earlier development

- Analysis profiles: Full, Whisper + Apple Vision OCR without Qwen3-VL, and OCR-only.
- Optional FFmpeg/Tesseract OCR with runtime capability detection and non-fatal fallback.
- ASR/OCR name reconciliation prompts and cross-field consistency improvements.
- Apple Vision OCR, Whisper acceleration/MPS fixes, word-level timestamps, audio features, and persistent JSON settings.
- Sequential multi-video processing, cooperative cancellation, TXT/JSON autosave and native macOS launcher.
- Benchmark-informed Apple Silicon quality/speed profile and memory-safety improvements.
