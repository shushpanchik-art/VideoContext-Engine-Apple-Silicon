# Fast profile / Whisper MPS handoff — 2026-11-05

## Purpose

Continue the user's request to restore the earlier fast Apple Silicon profile on an M1 Max MacBook Pro (32 GiB unified memory): PyTorch Whisper on MPS, MLX VLM and Text LLM on Metal, Apple Vision OCR, FFmpeg/Tesseract OCR, with OCR cache as the only experimental optimization enabled.

This document records verified repository/runtime facts and the current target-Mac state. It is a handoff, **not evidence that the MPS change has been applied or tested**.

## Current target-Mac state

At 2026-11-05 15:10 local, the user called the loopback VCE shutdown endpoint. It returned `{"status":"stopping"}`; after three seconds, `lsof -nP -iTCP:7555 -sTCP:LISTEN` showed no listener. A duplicate shutdown call also returned successfully while the port remained free. VCE is therefore stopped at the end of the user's latest terminal capture.

The earlier proposed local patch/launcher script aborted at its initial port-7555 guard while the server was running. The user has **not** reported executing that script after stopping the server. Therefore:
- no source patch from that script is confirmed applied;
- no settings/profile write from that script is confirmed applied;
- no backup from that script is confirmed created;
- do not assume Whisper currently uses PyTorch/MPS.

The user explicitly wants actual removal of experimental functions/toggles from the runtime/UI, while retaining OCR cache and ensuring FFmpeg/Tesseract works. The previous proposed local script only disabled experimental flags in settings; it did **not** remove experimental implementation code or UI controls. Do not describe that script as having completed the requested code cleanup.

## Repository inspection

Repository: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`

Existing Draft PR: [#34 — Apple Silicon benchmark launcher: diagnostics and safe result classification](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34)

The PR branch's `VideoContextEngine_v3.19.py` currently contains:
- `resolve_whisper_device(...): return "cpu"`;
- `load_whisper_model(...): return _VCEWhisperMLXCPU(model_name)`;
- Whisper stage named `_vce_run_whisper_cpu_stage`, with both model-loading call sites explicitly passing `acceleration_enabled=False`;
- Whisper progress/log text explicitly says CPU and parallel execution with MLX/Metal VLM;
- settings defaults include `enable_ocr_cache=false`, `enable_ffmpeg_ocr=false`, `enable_mlx_whisper=false`, `enable_adaptive_keyframes=false`, and `enable_fast_scene_detection=false`;
- FFmpeg/Tesseract capability is exposed separately and the OCR execution path is gated by `enable_ffmpeg_ocr`.

Thus the current source does not honor the intended PyTorch/MPS Whisper profile, even if the persisted `whisper_acceleration` setting is true. The `enable_mlx_whisper` flag is not a GPU switch; the current MLX Whisper implementation is pinned to `mx.cpu`.

## Historical same-video reference

The repository's 2026-10-25 baseline for the same 108.23-second, 14-scene input reported:
- Whisper: openai-whisper large, PyTorch MPS, 29.41 s stage time (9.495 s model load);
- Qwen3-VL MLX: 139.633 s;
- Apple Vision OCR: 4.668 s;
- FFmpeg/Tesseract OCR: enabled, 5-second interval, 3 workers, 22 samples, 9.324 s;
- Qwen3.5 Text LLM: 84.314 s;
- total VCE: 278.535 s.

Latest 2026-11-05 diagnostic for the same input reported:
- Whisper: `mlx-whisper` on CPU / `mx.cpu`, stage timing 145.762 s;
- Qwen3-VL: 258.439 s;
- Apple Vision OCR: 4.849 s;
- FFmpeg/Tesseract OCR: not requested, no samples;
- Qwen3.5 Text LLM: 84.777 s;
- total VCE: 354.61 s.

**Timing caveat:** the latest `stage_comparison.csv` also reports `inference_seconds_including_first_load=31.733` for Whisper, which conflicts with the 145.762-second Whisper stage timing. Resolve this metric inconsistency before claiming an exact Whisper slowdown or calculating a speedup. The historical and latest totals are not strictly equivalent because FFmpeg/Tesseract OCR was enabled in the historical run and disabled in the latest run.

## Required implementation work

1. Make Whisper backend explicit and truthful:
   - when `whisper_acceleration=true` on macOS Apple Silicon and `torch.backends.mps.is_available()`, load `openai-whisper` with `device="mps"`;
   - when acceleration is false, use PyTorch CPU;
   - fail clearly if MPS was requested but unavailable; do not silently route to MLX CPU;
   - remove the misleading MLX Whisper adapter/validation flag and stale CPU-only labels once all references are updated.
2. Avoid simultaneous heavy MPS Whisper and MLX/Metal VLM work initially. Serialize the GPU-bound stages (Whisper MPS first, then VLM/Metal) and release accelerator memory at a safe stage boundary. Do not assert this is faster until measured.
3. Keep Qwen3-VL and Qwen3.5 Text LLM on MLX/Metal.
4. Keep Apple Vision OCR enabled. Enable FFmpeg/Tesseract OCR with a configurable interval and worker count; add tests for dependency availability, requested-vs-executed stage reporting, and graceful failure if Tesseract/FFmpeg is missing.
5. Keep `enable_ocr_cache=true`. Remove the other experimental toggles/functions from settings UI and runtime only after auditing all references and migrating existing settings safely. Keep normal stage timing only if it is not an experiment and does not change inference behavior.
6. Set a clear fast-profile preset/default for the requested values (large Whisper; VLM Qwen3-VL-8B 4-bit; Text LLM Qwen3.5-9B 4-bit; Apple Vision + FFmpeg/Tesseract OCR; OCR cache on; adaptive keyframes, MLX Whisper experiment and fast scene detection absent/off).
7. Add unit/static tests for backend selection, MPS-unavailable behavior, serialized scheduling, settings migration, OCR cache, and FFmpeg/Tesseract stage execution.
8. Validate with py_compile/unit tests first. On the target Mac, run a short Whisper-only MPS smoke test and a short VLM-only MLX/Metal smoke test separately before any combined/full-video run. Confirm runtime log says `backend=openai-whisper device=mps model=large`; capture truthful stage timings and avoid publishing private video paths, filenames, transcripts, or raw logs.

## Safety / acceptance

- Do not rerun the 108-second full-video analysis until isolated Whisper and VLM checks pass.
- Do not merge based on code inspection alone. Require current-head CI and target-Mac smoke evidence.
- Do not claim per-process GPU utilization without suitable per-process telemetry.
- Preserve timestamped backups and make local profile changes atomically; do not overwrite unrelated user preferences.
- The user has not authorized publishing private video-derived content. Only sanitized aggregate timing/status data may enter the public PR.

## Current status

- VCE server stopped; port 7555 was verified free.
- MPS Whisper patch: **not applied / not validated**.
- Fast profile settings: **not applied / not validated**.
- Experimental-code removal: **not implemented**.
- FFmpeg/Tesseract on the target Mac: earlier diagnostics only checked tool availability; the latest full diagnostic did not request OCR samples.
- PR #34 should remain Draft until the implementation and validation above are complete.
