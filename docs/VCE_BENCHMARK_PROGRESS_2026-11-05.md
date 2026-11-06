# VCE benchmark progress update (2026-11-05)

## Scope and evidence

This note records the completed six-profile target-Mac run captured in the supplied terminal/monitoring export on 2026-11-05 UTC (2026-11-04 local, UTC-03:00). It supersedes the earlier preliminary note that ended while the fourth profile was still reporting unchanged progress.

The run used:
- benchmark runner source: `650f3a1437ee0cdcab1c3ea41005a599a373d578`
- stage-classification patch helper v4: `db54501d459569eaa3e75a8e22ef1385ecee68f9`

The aggregate report is in [benchmark-results/20261105T011648Z](../benchmark-results/20261105T011648Z/summary.md). A later attempt to re-run the two final profiles is recorded separately below.

No video, source filename, local path, transcript, OCR/model output, raw API response, process/server log, or private diagnostic CSV is included in this note.

## Completed six-profile matrix

The runner completed the complete sequential plan and exited with code 1. It reported 3 profiles completed without runtime errors and 3 failed.

| Profile | Final status | Wall time | VCE time | Scenes | Whisper words | VLM | Whisper | Apple OCR | FFmpeg OCR | Text LLM | CPU/GPU overlap | Errors |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | completed | 398.6 s | 396.87 s | 14 | 139 | 285.4 s | 167.4 s | 4.6 s | 9.6 s | 93.0 s | 164.7 s | 0 |
| `adaptive_keyframes` | completed | 410.5 s | 408.92 s | 14 | 139 | 310.5 s | 178.2 s | 6.0 s | 9.5 s | 78.7 s | 176.1 s | 0 |
| `ocr_cache` | completed | 395.9 s | 395.48 s | 14 | 139 | 283.3 s | 168.4 s | 4.5 s | 9.4 s | 94.0 s | 166.2 s | 0 |
| `mlx_whisper_flag` | failed | 1792.2 s | 1789.97 s | 14 | 139 | 1744.0 s | 445.7 s | 3.2 s | 9.2 s | 0.0 s | 407.7 s | 4 |
| `fast_scene_detection` | failed | 194.4 s | unavailable | unavailable | unavailable | — | — | — | — | — | — | 5 |
| `all_experiments` | failed | 193.3 s | unavailable | unavailable | unavailable | — | — | — | — | — | — | 5 |

For the first four profiles, the API returned HTTP 200. The v4 classifier correctly overrode the fourth profile to `failed` because it contained four runtime errors. The last two profiles ended with API HTTP errors and did not return usable VCE process-time or scene-count metadata.

### Interpretation of the three clean profiles

All three completed profiles produced the same scene count (14) and Whisper word count (139).

- `adaptive_keyframes` was 11.9 s (~3.0%) slower in wall time than baseline.
- `ocr_cache` was 2.7 s (~0.7%) faster in wall time than baseline.

There is only one run per profile. The OCR-cache difference is small and does not establish a real speed-up; the adaptive-keyframe difference is likewise only a single-run observation. Do not declare a winner or make a performance claim without repeated, controlled runs.

The reported stage durations can overlap. In particular, the CPU/GPU overlap field is not additive with the individual stage times, and the VCE process time should not be treated as the sum of exclusive CPU/GPU execution durations.

## Fourth profile: long unchanged progress, then completion with errors

The earlier terminal capture ended while the fourth profile repeatedly reported 15.3%. The complete capture shows what happened afterward:

- Profile start: 2026-11-04 21:40:26 local.
- Whisper CPU reported completion with 19 speech chunks at elapsed 172.9 s.
- Progress remained at 15.3% through elapsed 1785.8 s; the next recorded update advanced to 20.7% / scene 7 of 14 at 1787.9 s.
- The profile then completed at elapsed 1792.2 s, with 14 scenes and four runtime errors.
- Final classifier status: `failed` despite API HTTP 200.

The prolonged flat progress is confirmed, but it was not an indefinitely stuck process in this capture. The final timing report attributes 1744.0 s to VLM and 445.7 s to Whisper, with 407.7 s of reported CPU/GPU overlap. The result remains **not accepted** because the runner reported four runtime errors.

The `enable_mlx_whisper` flag is a validation/preflight switch in the current runtime; it does not switch Whisper inference to GPU. Whisper telemetry remains `mlx-whisper` on CPU stream `mx.cpu`. This profile is not a GPU Whisper benchmark and is not a valid clean A/B comparison against baseline.

## Fifth and sixth profiles: Metal/API failure

Both `fast_scene_detection` and `all_experiments` failed shortly after starting:

- `fast_scene_detection`: wall 194.4 s, API HTTP error, five errors, no usable VCE time or scene metadata.
- `all_experiments`: wall 193.3 s, API HTTP error, five errors, no usable VCE time or scene metadata.

The progress output repeatedly showed Metal command-buffer submission failures in the `kIOGPUCommandBufferCallbackErrorSubmissionsIgnored` family. This confirms recurrence of the previously observed Metal failure signature. It does **not** establish the underlying cause; do not attribute it to RAM pressure, a specific driver defect, or a particular queue without further evidence.

## Follow-on selective-launcher attempt

After the six-profile matrix ended, the supplied `run_remaining_vce_profiles.command` was invoked to attempt only the last two profiles. It downloaded the pinned runner and patch helper, applied patch v4, then aborted with:

`FATAL: Required remaining profiles not found: all_experimental`

The selector expected a plan entry named `all_experimental`, but the actual combined-profile key recorded in the matrix report is `all_experiments` (plural). This naming mismatch caused the selector to abort. The script exited with code 2 before invoking the benchmark runner. Therefore, this second attempt did not submit another VCE profile request and produced no new inference measurement. Its sanitized launcher-status record is under [benchmark-results/20261105T011752Z](../benchmark-results/20261105T011752Z/launcher_status.txt).

This selector defect is in the selective-launcher script, not in the recorded six-profile matrix. The matrix had already attempted both `fast_scene_detection` and the combined profile and had reported both as failed. Do not interpret the selector failure as an additional VCE/Metal runtime failure.

## Telemetry and configuration caveats

- The launcher reported that local `settings.json` differed from live API settings; the benchmark requests used active API settings. Do not assume the local settings file describes the effective request.
- `powermetrics` was unavailable (`permission_required_or_sudo_unavailable`); the monitoring export contains no usable GPU samples.
- CPU/RSS values in the published monitoring table are zero-valued/empty observations and should not be interpreted as proof of zero resource use.
- MLX GPU device/stream smoke tests demonstrate configuration and stream availability, not per-operator GPU execution or process-level GPU utilization.
- Whisper uses the CPU `mx.cpu` path. Apple Vision's CPU/GPU/ANE assignment is not exposed by the current telemetry.
- Scene and word counts are coverage indicators, not accuracy measures. No WER/CER conclusion is possible without a labelled reference transcript.

## Acceptance and next steps

1. Keep baseline, adaptive keyframes and OCR cache as preliminary completed runs with zero reported errors; repeat them under controlled conditions before drawing performance conclusions.
2. Keep the `mlx_whisper_flag`, `fast_scene_detection` and `all_experiments` profiles failed / not accepted.
3. Do not rerun the last two profiles merely to work around the selective-launcher selector defect: the full matrix already attempted them and reproduced the Metal/API failure signature. First investigate and isolate the runtime failure.
4. Keep raw logs, error rows, API payloads and all video-derived data local. Publish only sanitized aggregate status and stage-timing data.
5. Keep PR #34 in Draft. This update does not establish a successful six-profile matrix, GPU Whisper inference, per-process GPU attribution, or a known root cause for the Metal failure.
