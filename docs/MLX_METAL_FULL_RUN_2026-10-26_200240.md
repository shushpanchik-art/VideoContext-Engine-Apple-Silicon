# Full runtime measurement — 2026-10-26 20:02:40

## Scope and evidence boundary

This report records the latest full end-to-end run observed on the target Apple Silicon Mac. The measurements below were read from the local watchdog artifacts and runtime verdict; the source video, transcript, full analysis response, and raw local logs are not included in this repository.

This is a runtime measurement record, **not a source snapshot of `/Applications/VideoContext Engine.app`**. The installed application bundle has not been made available to the repository tooling for inspection or commit.

## Environment and workload

| Item | Recorded value |
|---|---|
| Run ID | `watchdog_20261026_200240` |
| Host | MacBook Pro, Apple M1 Max |
| Unified memory | 32 GB |
| macOS / architecture | macOS 27.0.1 / arm64 |
| Python | 3.14.4 |
| MLX | 0.32.4 |
| mlx-vlm | 0.7.10 |
| mlx-whisper | 0.4.3 |
| Input duration | 108.23 s |
| Scenes processed | 14 |
| API result | HTTP 200 |
| VCE processing time | 271.66 s |
| Client wall time | 272.318 s |
| Maximum sampled process RSS | 7,868.3 MiB (~7.68 GiB) |

Process RSS is not a direct measure of total unified-memory usage or Metal/GPU allocations.

## Stage timings

| Stage | Time | Interpretation |
|---|---:|---|
| Qwen3-VL 8B load | 2.117 s | Visual-language model setup |
| VLM scene inference | 158.682 s | Scene-description calls |
| mlx-whisper inference | 33.788 s | Whisper on the configured MLX CPU stream |
| Whisper/VLM scene-pass overlap | 31.982 s | ASR overlapped with visual scene processing |
| Qwen3.5 9B Text LLM | 90.056 s | Final summary generation |

Stage durations overlap and must not be summed to reconstruct total wall time. The observed design is Whisper on the CPU stream in parallel with VLM scene inference; the final Text LLM stage follows the scene pass.

## VLM device/stream tracing

The runtime verdict was:

`VLM_SCENE_GPU_WIRING_AND_SYSTEM_GPU_ACTIVITY_CORROBORATED_EXECUTION_NOT_PER_OPERATOR_PROVEN`

Recorded trace evidence around the scene-description path:

- 60 VLM scene-call trace snapshots.
- 60 snapshots reported `Device(gpu, 0)` as MLX's default device; 0 reported `Device(cpu, 0)`.
- The MLX-VLM generation stream observations included `Device(gpu, 0)`.
- A load-time process-global default-device snapshot did report CPU. Because Whisper runs concurrently inside an `mx.stream(mx.cpu)` context, this snapshot alone is not evidence that scene inference used CPU; the scene-call snapshots are more directly relevant.

These observations support GPU-target/stream wiring during VLM scene calls. They do not individually trace each MLX operator or prove that every operation executed on the GPU.

## System GPU power samples

The watchdog power log contained 20 entries: one initial sudo/noninteractive record without a successful sample and 19 successful `powermetrics` samples. A GPU activity label was found.

Observed samples included:

| Elapsed time | GPU HW active residency | GPU frequency | GPU power |
|---:|---:|---:|---:|
| 0.000 s | 18.71% | 426 MHz | 90 mW |
| 15.023 s | 36.66% | 1,247 MHz | 13,703 mW |
| 29.589 s | 100% | 1,296 MHz | 42,411 mW |
| 45.243 s | 88.75% | 1,296 MHz | 34,195 mW |
| 59.502 s | 100% | 1,296 MHz | 51,648 mW |
| 73.251 s | 100% | 1,296 MHz | 52,677 mW |

Across the main workload, successful samples generally showed 88.75%–100% GPU HW active residency, 1,296 MHz, and approximately 34–53 W GPU power. Later samples continued to show high GPU activity during the run.

**Important limitation:** `powermetrics` is system-wide telemetry. It corroborates that the Mac's GPU was active during the workload, but it does not attribute each sample to the VideoContext Engine PID, VLM, or Text LLM individually.

## Per-component conclusion

| Component | Best supported conclusion | What remains unproven |
|---|---|---|
| Whisper | Runtime configured to use `mlx-whisper` on an MLX CPU stream; 33.788 s measured | No need to use GPU for this intended design |
| VLM | MLX backend, Metal/GPU target and generation stream configured; scene-call snapshots show GPU; system GPU activity corroborates the run | Per-operator GPU execution / process-attributed GPU kernels |
| Text LLM | Runtime reports MLX, Metal and `Device(gpu, 0)`; 90.056 s measured | Per-operator GPU execution specifically attributed to Text LLM |

The data do not support the broad claim that “MLX is not working.” They provide strong evidence of GPU-target/stream use for VLM and GPU-reported runtime configuration for Text LLM, while keeping the system-wide versus per-process evidence distinction explicit.

## Reproducibility artifacts

Raw measurement files remain on the target Mac under the run folder `VCE_watchdog_runs/watchdog_20261026_200240`, including `powermetrics_gpu_power.jsonl`, runtime logs and stage timings. The folder path shown here is relative by design; no video, transcript, or full analysis output is committed.

For exact source provenance, a separate source snapshot is still required from the installed `/Applications/VideoContext Engine.app` bundle. That should be inventoried and scanned for tokens, credentials, personal data, generated user data, and bundled third-party/model assets before any upload. The existing repository is public, so adding the whole app bundle would publish its contents to anyone.
