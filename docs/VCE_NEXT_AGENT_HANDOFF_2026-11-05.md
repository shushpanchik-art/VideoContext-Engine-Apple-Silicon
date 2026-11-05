# Next-agent handoff — VCE benchmark and Metal investigation (2026-11-05)

## Current repository / PR state

- Repository: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`
- Work only on: `feat/experimental-benchmark-launcher`
- Existing PR: [#34 — Apple Silicon benchmark launcher](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34)
- Metal investigation task: [#35 — Investigate recurring MLX/Metal command-buffer submission failures](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/issues/35)
- PR #34 must remain open and Draft. Do not merge, close, mark ready, or modify `main`.
- The live PR head is authoritative; fetch it before making changes.
- Runner source pin: `650f3a1437ee0cdcab1c3ea41005a599a373d578`.
- Patch helper v4 pin: `db54501d459569eaa3e75a8e22ef1385ecee68f9`.

## Executive decision: recommended operating settings

**Use the baseline configuration with OCR cache enabled, and all other experimental switches disabled.**

| Setting / profile | Recommendation | Evidence / reason |
|---|---|---|
| Baseline | Keep as the stable reference | Completed in 398.6 s wall / 396.87 s VCE time, 14 scenes, 139 Whisper words, 0 runtime errors |
| `enable_ocr_cache` | **ON — recommended** | The OCR-cache profile completed in 395.9 s wall / 395.48 s VCE time, 14 scenes, 139 Whisper words, 0 errors. It was the fastest clean profile in this sample |
| `enable_adaptive_keyframes` | **OFF — not recommended** | Its one run took 410.5 s wall, about 11.9 s / 3.0% slower than baseline, with no additional scene or word coverage |
| `enable_mlx_whisper` | **DO NOT ENABLE** | Its run took 1792.2 s, spent a very long interval at 15.3%, and finished with 4 runtime errors; classifier marked it failed. Current VCE still runs Whisper on CPU stream `mx.cpu`; this flag does not enable GPU Whisper |
| `enable_fast_scene_detection` | **OFF pending Metal investigation** | Its run failed after 194.4 s with API HTTP error and 5 errors; no usable VCE timing or scene metadata |
| All experimental features together | **DO NOT ENABLE** | Failed after 193.3 s with API HTTP error and 5 errors; repeated Metal submission failure signature |

The measured OCR-cache wall-time difference versus baseline is only 2.7 s (~0.7%). It supports choosing OCR cache as the preferred clean measured configuration, but does **not** prove a statistically significant speed-up. There is only one measured run per profile. Do not claim a benchmark win or promise a 0.7% improvement.

The operational profile for continued normal work is therefore:

```text
baseline settings
enable_ocr_cache = true
enable_adaptive_keyframes = false
enable_mlx_whisper = false
enable_fast_scene_detection = false
```

Keep the remaining settings at the known baseline / current stable configuration; do not introduce unrelated changes while investigating Metal. `enable_stage_timing` is measurement instrumentation, not a performance optimization.

## Latest full six-profile measurement

Source: sanitized aggregate report under [benchmark-results/20261105T011648Z](../benchmark-results/20261105T011648Z/summary.md). Target: Apple Silicon MacBook Pro M1 Max, 32 GiB unified memory; MLX 0.32.4 is reported in the run evidence. The full runner used stage-aware patch helper v4 and exited with code 1.

| Profile | Final status | Wall | VCE | Scenes | Whisper words | Runtime errors |
|---|---|---:|---:|---:|---:|---:|
| Baseline | completed | 398.6 s | 396.87 s | 14 | 139 | 0 |
| Adaptive keyframes | completed | 410.5 s | 408.92 s | 14 | 139 | 0 |
| OCR cache | completed | 395.9 s | 395.48 s | 14 | 139 | 0 |
| MLX Whisper flag | failed | 1792.2 s | 1789.97 s | 14 | 139 | 4 |
| Fast scene detection | failed | 194.4 s | unavailable | unavailable | unavailable | 5 |
| All experiments | failed | 193.3 s | unavailable | unavailable | unavailable | 5 |

All three clean profiles had the same 14 scenes and 139 Whisper words. They are the only profiles in this matrix eligible for preliminary comparison. The other three must not be included in rankings.

### MLX Whisper run correction

An earlier partial capture ended during a long flat 15.3% progress period. The complete capture shows the process later resumed, advanced to 20.7% / scene 7 of 14 and finished at 1792.2 s. It was **not an indefinitely hung process in this capture**, but it is still a failed, non-accepted profile because it recorded four runtime errors. Do not re-run or enable this flag.

### Fast-scene and combined profile failures

Both profiles showed recurring Metal command-buffer submission failures in the `kIOGPUCommandBufferCallbackErrorSubmissionsIgnored` family, followed by API HTTP errors. Their missing VCE timing/scene metadata means no performance conclusion is possible.

## Assigned task: investigate MLX / Metal

Work is tracked in [GitHub issue #35](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/issues/35). This is the priority for the next agent.

### Questions to answer

1. What is the first exception / GPU failure before the repeated `SubmissionsIgnored` messages?
2. Which exact VCE stage and MLX model/operation are active at that moment?
3. Are subsequent Metal errors only a cascade after an earlier GPU failure, and where does VCE's VLM memory guard intervene?
4. Does the failure reproduce in a minimal VLM-only request after a clean VCE restart?
5. Is Text LLM post-processing independently affected, or does it fail only after the earlier VLM/Metal failure?
6. Does any proposed code change actually address the first failure, and can that be shown with a regression test and repeated clean validation?

### Required investigation order

**A. Static analysis before more heavy runs**

- Inspect VCE MLX initialization, device/stream selection, model loading, model probes, generation paths, synchronization/evaluation points, VLM guard and Text LLM lifecycle.
- Identify shared/global MLX device or stream state only from code/runtime evidence; do not assume the cause is a concurrency or queue issue.
- Confirm MLX, macOS, VCE and model versions from the actual serving process/interpreter.
- Compare the first error and its stack trace with later `SubmissionsIgnored` messages.

**B. Capture the first failure**

- If reproducing, record wall-clock and monotonic timestamps, request identifier, exact stage, first exception/stack, and a short preceding event window.
- Inspect OS unified logs around that timestamp if available.
- Keep raw logs, API responses, prompts, video-derived text, model output, error CSVs and local paths private. Commit only sanitized conclusions and minimal non-sensitive metadata.
- Do not interpret `powermetrics` as VCE process telemetry. The prior run had no usable GPU samples; system-wide GPU activity is not process attribution.

**C. Minimal controlled reproduction only after A/B**

- Confirm no previous launcher or server-side analysis request is still active. Inspect process/listener state read-only and restart VCE cleanly before another request.
- Start with one minimal VLM-only request, with Whisper/audio and Text LLM disabled.
- Do **not** enable `enable_mlx_whisper` under any circumstance during this work.
- If VLM-only is clean, isolate Text LLM separately using a safe minimal path if the current runtime permits it; only then test the combined stages.
- Change one variable at a time. Do not run the full six-profile benchmark matrix while the Metal issue remains unexplained.
- Use a short, non-sensitive local test input if possible. Never upload media or raw diagnostics.

**D. Fix and validation**

- Do not silence, downgrade, swallow or reclassify a real Metal failure as success.
- If code changes are justified, add focused regression coverage for the actual failure and guard behavior.
- Verify non-zero runtime errors always prevent a profile from being accepted.
- Demonstrate the first failure before the fix and clean behavior after the fix. Prefer at least two repeated controlled runs with affirmative completion for every requested stage and zero runtime errors.
- If root cause cannot be proven, state that explicitly and leave the issue open with the strongest evidence and next discriminating experiment.

## Important script caveat

The supplied local helper `run_remaining_vce_profiles.command` was attempted after the six-profile matrix had already completed. It aborted **before invoking the benchmark runner** because it searched for the non-existent key `all_experimental`; the actual combined-profile key is `all_experiments`. That attempt did not submit a VCE request and produced no inference data. Fix and syntax-check the selector before any future use; do not mistake this selector error for a VCE/Metal runtime failure.

## Acceptance and privacy guardrails

- A profile is not successful solely because API returned HTTP 200.
- Any required-stage failure, Metal submission error, missing required completion evidence, or `error_count > 0` means failed/degraded and non-comparable.
- Whisper telemetry `backend=mlx-whisper device=cpu stream=mx.cpu` is CPU inference, not GPU inference.
- MLX GPU target / stream smoke tests are configuration evidence, not per-operator GPU execution proof.
- No process-level GPU residency or GPU-memory conclusions may be drawn from system-wide `powermetrics`.
- Do not publish video, source identity, transcripts, OCR text, prompts, model output, full API responses, `errors.csv`, raw logs or local paths.
- Work only on `feat/experimental-benchmark-launcher`. Never modify `main`.
- Keep PR #34 open and Draft. No merge, no close, no Ready-for-review transition.


## New target-Mac server.log evidence supplied after 22:58 (2026-11-05)

A later terminal capture showed the current `~/Library/Logs/VideoContext Engine/server.log` is 18 MiB and begins with Uvicorn PID `41359`. The prior failing benchmark process PID `8742` is not present in this current log search. The current server process is therefore distinct from the process in the 22:13 / 22:45 traceback evidence; do not conflate their process lifetimes.

The user's search found these earlier log entries in the current file:

- Lines 16,720, 41,998, 81,176 and 81,200: explicit `[METAL] Command buffer execution failed: Insufficient Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)`.
- Line 81,222: VCE reports its Metal-memory fallback/error after resolution reduction.
- Lines 159,309 onward, 193,818 onward and 205,403 onward: VLM memory guard attempts cache clearing and lower resolutions (384/320/256 px), then skips visual descriptions.
- Lines 215,487 onward: runtime telemetry includes passing `metal_smoke_test` and, later, passing `gpu_stream_smoke_test`; MLX version 0.32.4, Apple M1 Max, architecture `applegpu_g13s`, reported unified memory 32 GiB and recommended working-set size about 26.8 GB.
- Lines 217,774 onward: explicit GPU target and GPU probe stream are logged, but `default_device_observed` alternates between `Device(cpu, 0)` and `Device(gpu, 0)`. The log itself explains that `mx.default_device()` is process-global and may temporarily reflect a concurrent `mx.stream(mx.cpu)` context. This observation alone does not prove incorrect GPU routing or that each operator executed on GPU.
- The search output did not include reliable timestamps around these line numbers and was capped at 60 matches. The final eight lines were routine `GET /ui` requests only.

### Interpretation / next discriminator

This is additional evidence that the VCE runtime has encountered genuine Metal out-of-memory errors in its history, followed by repeated VLM memory-guard fallback behavior. It does **not** establish that the first error in the 22:13 / 22:45 `SubmissionsIgnored` traceback was caused by OOM: those earlier tracebacks failed in `_vce_configure_mlx_metal("vlm") -> mx.eval(probe)` with `SubmissionsIgnored`, and the current log excerpt does not tie the OOM entries to those request IDs/timestamps. Nor do later passing smoke tests rule out a failure during real model allocation or inference.

Do not implement cache, stream, model-size or concurrency changes from this excerpt alone. First recover exact timestamps and request/job context for the OOM and memory-guard entries (small, redacted windows around lines 16,720; 41,998; 81,176–81,222; 159,309; 193,818; 205,403; 215,487; and 217,774). Then correlate the earliest relevant Metal event with macOS unified logs. If those timestamps cannot be recovered, explicitly record that chronology is unresolved. Current server PID 41359 should be used for any future process attribution, after read-only confirmation that it is still the serving process.
