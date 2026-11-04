# Changelog

## 0.2.5 — Full macOS installer and clean source distribution (2026-11-03)

- Added a one-command Apple Silicon installer: Homebrew prerequisites, Python 3.11 environment, runtime dependencies, settings migration, recognition-language patch, smoke tests and local native-app build.
- Added `mlx-whisper` to the Apple Silicon dependency set and `recognition_languages` to the example settings.
- Made native-app installation stage the `.app` outside the Git checkout, so compiling the menu-bar executable no longer dirties the source bundle.
- Persisted the selected project path for the installed macOS app and made the native installer respect `VCE_PROJECT_DIR`.
- Documented GitHub ZIP download, model-cache exclusions, Tesseract language data and installation safety/backup behavior.

## Post-0.2.5 maintenance (2026-11-03—2026-11-04)

- PR #18 added the persistent merged-topic cleanup workflow. On pushes to `main`, it removes only merged PR branches from this repository, while preserving the default branch and branches used by open PRs. This is the active replacement for the earlier one-time cleanup process described in the 0.2.1 history below.
- PR #22 and PR #26 hardened Metal recovery: command-buffer timeouts are not treated as retryable allocation/OOM failures, and VLM generation stops after a timed-out Metal queue while other report stages can continue.
- PR #23 refreshed the publication manifest after the Metal recovery change.
- PR #24 made the managed macOS app patcher refresh independently when local runtime edits cause the installer to skip `git pull`.
- PR #25 fixed Homebrew PATH handling for GUI-launched servers and added explicit `ffmpeg`/`ffprobe` checks.
- PR #27 made the native app own the Python engine process lifecycle, with project-scoped stale-process cleanup on launch and shutdown.
- PR #28 reconciled repository publication metadata and added publication-manifest JSON validation to the full distribution CI workflow.
- PR #29 finalized the first repository-audit snapshot. PR #30 reconciled post-merge counts/checkpoint, added safe orphan/reused-branch cleanup rules, and merged the final repository hygiene pass. Cleanup run 41868806204 succeeded; only `main` remained.

## 0.2.3 — Remove per-video outputs from public tree (2026-11-02)

- Deleted full VCE API response JSONs, manifests containing local video identity, raw Text LLM generated-output bundles, and a generated comparison report with an absolute home path.
- Retained anonymized aggregate benchmark metrics and a concise parameter-tuning summary in documentation.
- Updated benchmark documentation and removed links to deleted feature branches.
- Added ignore rules for per-video benchmark folders and generated model-output files.

## 0.2.2 — Local benchmark safety and final repository tidy (2026-11-02)

- Removed the retired unattended-benchmark dependency on a deleted feature branch; input video is now required explicitly and all reports stay local.
- Removed the legacy publication helper and upstream overlay installer, superseded by the full-runtime installation path.
- Removed redundant root-level copies of utilities; canonical scripts, patches and macOS tools remain in their dedicated directories.
- Fixed the native-app repair utility to fetch its Swift source from `main`.

## 0.2.1 — Quality audit, diagnostics and repository cleanup (2026-11-02)

- Added a universal Text LLM structural audit and a source-aware lexical evidence screen with explicit limitations; neither is presented as a semantic truth verifier.
- Added raw-model versus finalized-output provenance, invalid-JSON diagnostics and regression coverage.
- Hardened the Text LLM benchmark: local video/JSON input must be selected explicitly; GitHub publication is disabled by default; generated outputs require a second explicit upload flag.
- Added local-only MLX/Metal observer and watchdog tools with regression tests and a guide for interpreting device/stream telemetry.
- Removed developer-specific video paths from the published benchmark and watchdog scripts.
- Moved reusable code and tests from historical PRs #13 and #14 into `main`; excluded raw video-specific benchmark outputs, telemetry dumps and installed-app archives.
- Closed superseded PRs #13 and #14; PR #15 remains the full-runtime publication merge.
- Deleted all 14 superseded research/feature branches; only `main` remains. The earlier one-time cleanup workflow was subsequently replaced by the persistent PR-merge cleanup workflow recorded above.

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
