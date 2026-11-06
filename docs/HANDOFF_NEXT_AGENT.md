# Handoff: restore the proven non-MLX VLM/LLM execution path

**Status:** implementation brief for the next coding agent. Do not merge or deploy until the full-video acceptance test passes on the user's Mac.

## Objective

Rework the local VideoContext Engine v3.19 runtime so that its inference architecture returns to the best-quality/best-measured behavior the user observed around **25 October 2026**, while retaining the later useful product features:

1. Remove **MLX and Metal inference for both VLM and Text LLM**. Restore the stable non-Metal backend that matches the historical working source (likely a CPU execution path, but verify before deciding). Do not merely hide UI options or disable VLM/LLM; keep both stages functional. A non-MLX backend that still executes on Metal does not satisfy the requirement.
2. Keep **Whisper on PyTorch MPS** (not mlx-whisper and not MLX CPU stream). Preserve acceleration controls and verify the effective device in logs and JSON.
3. Preserve Apple Vision OCR and its bounded image-result cache.
4. Preserve multi-language recognition selection / whitelist behavior across ASR, Apple Vision OCR, Tesseract OCR, VLM and Text LLM.
5. Preserve safe lifecycle management: identify and terminate only stale/orphaned VCE processes belonging to the exact project/runtime path. Never kill unrelated Python processes or another VCE clone.
6. Preserve the useful later work: reporting, API/UI, URL/local queue, stage timings, memory safety, audio resilience, OCR and output integrity unless a measured regression requires a targeted change.

This is a deliberate backend architecture rollback, **not** a broad rollback of all changes made after 25 October.

## Repository reconnaissance already completed

Repository: https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon  
Default branch: `main`. Public runtime source: `VideoContextEngine_v3.19.py` (about 314 KB). Local user's active runtime reported in the latest test: SHA-256 `7cf4f32cf6e63685ae1ea470f6a63f9b357105966c9b2de28a3156b552e99e23`.

Important source areas:

- Runtime: `VideoContextEngine_v3.19.py`, matching `videocontext_web_ui.html`.
- Installer / distribution: `scripts/install_video_context_engine.command`, `macos/install_video_context_mac.command`, `macos/VideoContext Engine.app/`.
- Recognition languages: PR #16, `patches/vce-recognition-languages-patcher.tar.gz`, `docs/RECOGNITION_LANGUAGES.md`, `tests/test_vce_recognition_languages_patch.py`.
- OCR cache / opt-in performance controls: PR #10, `patches/patch_vce_optimization_checkboxes.py` (bounded `_VCE_OCR_CACHE_MAX = 256`), `tests/test_vce_optimization_checkboxes.py`. Keep the Apple Vision cache behavior even if its opt-in setting remains off by default.
- Whisper MPS recovery / device handling: `patches/patch_vce_stop_and_mps.py`, `tests/test_vce_stop_mps_patch.py`.
- Orphan process lifecycle: PR #27, `macos/VideoContext Engine.app/`, `macos/repair_installed_native_app.command`, `tests/test_vce_native_app_lifecycle.py`.
- MLX/Metal architecture: PR #11 and its branch history, plus `docs/MLX_METAL_DIAGNOSTICS.md`; do not retain the PR #11 switch to mlx-whisper CPU stream as the desired Whisper implementation. The 6 November local run also used llama.cpp on Metal for VLM, so inspect this separate backend path too.
- Benchmarks: `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md`, `docs/APPLE_SILICON_BENCHMARKS.md`, `docs/VCE_FULL_PROFILE_BENCHMARK.md`, `scripts/vce_benchmark.py`, `scripts/vce_compare_runs.py`, `scripts/vce_runtime_watchdog.py`.

Relevant PR history:
- PR #8: balanced/speed profile, VLM-preserving behavior and macOS menu-bar app.
- PR #10: detailed stage timings, adaptive keyframes, bounded Apple Vision OCR image cache, optional MLX Whisper and fast scene scan.
- PR #11: changed Whisper to mlx-whisper CPU stream and routed VLM + final text analysis to MLX/Metal. This is the architecture the user now wants removed.
- PR #16: multi-language recognition selection.
- PR #27: project-scoped stale process cleanup at app launch/termination.

The public repo's `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md` records a 108.23 s video, 32 GB M1 Max, baseline total 278.97 s, 14 scenes, with Qwen3-VL-8B MLX visual inference 139.63 s, Qwen3.5 text post-processing 84.31 s, Whisper large/MPS 29.41 s, FFmpeg/Tesseract OCR 9.32 s, Apple Vision OCR 4.67 s. It explicitly warns that this was one run per variant on one sample, not a controlled multi-video benchmark. Treat it as historical evidence, not proof of universal superiority. The user specifically identifies the 25 October build/run as the best observed quality reference; locate the exact historical source snapshot/commit and compare it with the local snapshot before editing.

**Important distinction / do not silently resolve the apparent conflict:** the published 25 October *benchmark profile* itself records MLX for Qwen3-VL and Qwen3.5, while Whisper was MPS. The user's requested *target architecture* is explicitly different: no MLX/Metal for VLM or Text LLM, Whisper on PyTorch MPS. Therefore use the 25 October run/output as the quality-and-coverage reference, but do not assume that the exact backend in that benchmark is the desired final backend. Identify the exact local 25 October source build and its backend dispatch from the Mac snapshot/backups. If the public history has no exact non-MLX VLM/LLM implementation, locate it in local backups or upstream history before choosing a replacement. Do not claim the benchmark document proves a non-MLX backend was used.

## Latest full-length forensic run (2026-11-06)

User's required source:
`/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4`

Source facts:
- 108.233333 s, 1920×1080, H.264 + AAC, 30 fps, 17 MB.
- File extension was `.mp4`, actual container MPEG-TS.
- Source SHA-256 before and after: `66d6b95e69e2c1c151942a7f35540444a88bda50bf6b8d63ca81d22588ae515d`.
- Remuxed stream-copy to a real MP4; no re-encoding; full video and audio decode both passed.
- Exactly one POST to `/api/v1/analyze`, full 108.233 s; HTTP 200; curl exit 0; 402.424 s request.
- Server PID 7624 remained stable; RAM free 82% before / 73% after; no swap; no OOM signature.
- Response: VLM enabled, 108.857 s VLM inference, 14 scenes and 14 descriptions; Whisper inference 0; Apple OCR 0; FFmpeg OCR 0; audio skipped true; visual skipped false; Text LLM disabled.
- llama.cpp/Metal model startup took 289.443 s. This is a measured load-time concern, not proof of inference failure.
- The attributable log delta also reports `llama.cpp` with `device=Metal` for the VLM model; do not mislabel this specific run as mlx-vlm. The user's requested end state is no MLX **and no Metal** inference for VLM/Text LLM, while Whisper remains PyTorch MPS. This run demonstrates the current full-video VLM path can complete successfully; it does **not** validate the proposed CPU/non-Metal replacement or compare output quality.

Earlier failed test logs showed model download connection failures, an explicit cancel request, `Shutting down` and `exit -15`. The logs mixed UI polling and multiple requests; do not attribute every event to one request without timestamp/PID/request correlation. Current full-length run did not reproduce those failures.

Evidence folder on user's Mac:
`~/Downloads/VCE_FULL_FORENSIC_20261106T171521Z/`
Key files: `response.json`, `diagnostic_summary.json`, `process_monitor.log`, `server_log_delta.log`, `server_log_key_events.txt`, `system_before.txt`, `system_after.txt`, `ffmpeg_remux.log`, `normalized_probe.json`.

## Non-negotiable test policy

**Never use a short clip or trimmed excerpt as the acceptance test.** Unit tests and tiny synthetic fixtures are allowed for fast regression coverage, but they do not count as runtime acceptance.

The required end-to-end acceptance input is always the exact full source path above. Do not substitute a 20-second segment, sample frames, a proxy, or another video. A lossless container remux is allowed only when required by container compatibility; preserve full duration and streams, record both hashes, and never modify the original.

For every candidate build:
1. Run the full 108.233 s source through the complete intended profile, with VLM and Text LLM enabled and Whisper on MPS; OCR and selected language behavior enabled as specified in the acceptance profile.
2. Record a single request identity, start/end timestamps, request ID, PID tree, listener PID, child model-server PID(s), process exit statuses and every cancel/stop event. Do not mix unrelated UI activity into the attributable request log.
3. Record per-stage load/inference times, model/backend/device, peak RSS and system memory pressure, swap, Metal/MPS diagnostics, network/download errors, HTTP status, response body, scene count, description count, transcript/OCR output counts and report paths.
4. Compare the output against the 25 October quality reference: same source, scene coverage, technical terms/numbers/names, OCR, ASR and final report. Include manual side-by-side review; do not infer quality from speed alone.
5. Compare cold-start and warm-cache runs separately. Model download/load time must not be hidden in total wall-clock time.
6. Verify clean app close, forced app quit/reopen, and stale process cleanup. Confirm no orphan VCE/llama processes remain and unrelated processes are untouched.
7. Keep all raw evidence local until the run is complete, then automatically upload a sanitized, timestamped evidence archive to the private staging repository or an explicitly configured Dropbox folder. Print the exact upload URL/path in the terminal and write it to `UPLOAD_RECEIPT.txt`. No silent opt-out and no manual hunt for files.
8. CI/synthetic tests are necessary but never sufficient for hardware acceptance. Do not claim success until the user's full-video run is inspected.

## Implementation sequence

1. Retrieve the user's Mac snapshot from the private staging release and compare its runtime, UI, settings schema, installed patch markers and backups with repository `main` and the 25 October historical reference.
2. Establish the exact 25 October commit/source state. Use Git history/PRs and local backups; do not guess from the benchmark markdown timestamp.
3. Trace all backend dispatch points for VLM and Text LLM. Remove MLX imports, MLX thread/stream setup, Metal server lifecycle and MLX-only fallback logic from those two inference paths. Replace with the most reliable previously working non-MLX backend, preserving model/prompt/output contracts.
4. Restore Whisper to PyTorch MPS and prove it is actually MPS at runtime (not CPU fallback and not mlx-whisper). Keep MPS cleanup/recovery safe.
5. Retain the Apple Vision OCR cache, language selection, language availability checks and whitelist enforcement. Add explicit tests that fail if these regressions disappear.
6. Retain and audit the PR #27 orphan cleanup. Scope termination by exact canonical project path and verified process ancestry/command line; use graceful SIGTERM with bounded wait, then SIGKILL only for confirmed owned orphan processes. Never broad `pkill python`, port-only killing, or matching only a generic process name.
7. Add backend contract tests, device assertions, cache tests, language whitelist tests, lifecycle tests, and full-run report validation.
8. Provide a Mac Terminal one-liner that downloads the runner/script from GitHub and executes it. The runner must upload the evidence archive automatically after completion.
9. Open a separate implementation PR; keep this handoff PR documentation-only.

## Required output from the next agent

- A concise architecture decision explaining the exact non-MLX VLM/LLM backend and why it matches the historical working version.
- A source diff and tests; no speculative mass rewrite.
- A migration/rollback path with backups and preserved settings.
- A full-video Mac test script with one unambiguous Terminal command.
- An automatically uploaded evidence bundle, with exact private release URL and checksum.
- A comparison report against the 25 October reference and the 2026-11-06 full-video forensic run.
- Explicit pass/fail results for quality, speed, device, cache, languages and process lifecycle.
- No merge to main until the user reviews the full-video output.

## Privacy / artifact policy

The engine source snapshot belongs in the private staging repo, not the public source tree. Do not upload original videos, audio, model weights/caches, API tokens, passwords, cookies, personal settings in raw form, or unrelated home-directory files. Include sanitized settings, runtime source, UI, patchers, local backups needed for historical comparison, Git metadata/patches, dependency versions and relevant logs. Redact secrets recursively before upload.
