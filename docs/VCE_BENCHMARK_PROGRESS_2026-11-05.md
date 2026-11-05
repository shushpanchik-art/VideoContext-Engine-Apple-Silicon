# VCE benchmark progress update (2026-11-05)

## Scope and evidence

This note records the additional target-Mac benchmark observations received on 2026-11-05. It supplements, and does not replace, the diagnostic handoffs:

- [Metal failure handoff](VCE_METAL_FAILURE_HANDOFF_2026-11-04.md)
- [Next-agent handoff](VCE_NEXT_AGENT_HANDOFF_2026-11-04.md)

The captured run used the pinned benchmark runner source `650f3a1437ee0cdcab1c3ea41005a599a373d578` and patch helper v4 `db54501d459569eaa3e75a8e22ef1385ecee68f9`.

No video, filename, local path, transcript, OCR/model output, raw API response, process/server log, or private diagnostic CSV is included here.

## Sequential matrix: confirmed completed profiles

The supplied terminal/system-monitor capture reports the following first three profiles completed. Each reported 14 scenes and zero runtime errors.

| Profile | Wall time | VCE process time | Scenes | Runtime errors |
|---|---:|---:|---:|---:|
| `baseline` | 398.6 s | 396.87 s | 14 | 0 |
| `adaptive_keyframes` | 410.5 s | 408.92 s | 14 | 0 |
| `ocr_cache` | 395.9 s | 395.48 s | 14 | 0 |

These are single-run observations, not repeated measurements. Relative to this baseline, adaptive keyframes took about 3.0% longer wall time, while OCR cache took about 0.7% less. These small differences do not establish a performance improvement or regression. No performance winner should be declared from this sample.

## Fourth profile: progress stopped advancing

The next profile enabled the `enable_mlx_whisper` validation flag.

- At elapsed time 449.7 s, the capture showed the Whisper CPU stage reporting completion with 19 speech chunks.
- After that, the progress output repeatedly reported the same 15.3% state at approximately 2.1-second intervals.
- The last captured repetition was at elapsed time 1502.1 s: a flat interval of about 1052.4 s (17 min 32 s).
- A system-monitor snapshot showed a Python process at approximately 100.2% CPU and 12.72 GB real memory. This is a single snapshot and does not establish the cause of the unchanged progress output.
- The capture ends without a final profile result, runner exit code, or evidence that the VCE-side request was cancelled.

**Classification:** incomplete / stalled-progress observation; not a completed profile and not an accepted benchmark result. The evidence establishes that progress stopped changing for the captured interval. It does not establish whether the VCE server was deadlocked, still doing unreported work, or whether the runner's progress reporting had stopped.

The `enable_mlx_whisper` flag must not be described as Whisper GPU acceleration. The current VCE runtime telemetry resolves Whisper to `mlx-whisper` on CPU stream `mx.cpu`; the flag is a preflight/validation switch and does not change this backend.

## Profiles not reached in this capture

The sequential plan listed these profiles after the fourth one:

- `fast_scene_detection`
- `all_experimental`

The supplied capture contains no completed result for either profile. They must remain **not run / not evidenced** for this matrix. The combined profile includes the Whisper validation flag and could reproduce the same stalled-progress symptom.

Before submitting any further request, confirm that the previous launcher and the server-side request have ended, inspect the VCE/API state, and restart VCE cleanly when appropriate. A client runner timeout or termination alone does not prove that a server-side request was cancelled. Do not submit a second profile while the old request may still be active.

## Runtime and measurement caveats

- The capture warned that local `settings.json` differed from the live API settings. The requests used the active API configuration; do not assume the local file represented the effective request settings.
- `powermetrics` was unavailable in this capture. There is no valid per-process GPU telemetry here.
- A system-wide GPU activity value, if shown by a monitor, is not process attribution and cannot prove that VCE used or did not use the GPU.
- MLX GPU device/stream smoke tests demonstrate configuration and stream availability, not that every model operator executed on the GPU.
- Whisper timing in this runtime belongs to the CPU `mx.cpu` path; it is not an MPS-versus-MLX GPU comparison.
- Scene counts and Whisper chunk/word counts are coverage observations, not accuracy metrics. No WER/CER conclusion is possible without a labelled reference transcript.

## Separate earlier Whisper-only observation

A separate earlier one-profile run (`20261104_210814_a712a1`) reported wall time 188.9 s, VCE process time 187.51 s, 14 scenes, 139 Whisper words, Whisper time 183.37 s, and one runtime error. Its telemetry identified `mlx-whisper`, device `cpu`, stream `mx.cpu`. It remains **DEGRADED / NOT ACCEPTED** because the runner reported a non-zero error count; it is not part of the three zero-error profiles above.

## Acceptance and next steps

1. Preserve the three zero-error profiles as preliminary observations only; do not publish a performance ranking from one sample per profile.
2. Keep the fourth profile incomplete until its result and the server-side job state are verified.
3. Run `fast_scene_detection` and `all_experimental` only after verifying that the previous request has ended and VCE has been restarted cleanly.
4. Keep detailed logs, error rows, API payloads and all video-derived data local. Publish only sanitized aggregate status and stage timing data.
5. Keep PR #34 in Draft. This note does not assert that all benchmark profiles completed, that Whisper runs on GPU, or that the stalled-progress cause is known.
