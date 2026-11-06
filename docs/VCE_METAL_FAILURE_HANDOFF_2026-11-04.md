# VCE Apple Silicon benchmark — диагностический handoff (2026-11-04)

## Назначение документа

Этот файл передаёт следующему инженеру/ассистенту контекст реального запуска full-profile benchmark на MacBook Pro M1 Max. Он фиксирует наблюдения из пользовательских логов, отделяет подтверждённые факты от гипотез и задаёт безопасный план продолжения.

**Важно:** это диагностический отчёт, а не подтверждение успешного benchmark. HTTP 200 от API означает, что запрос завершился на уровне API; он не означает, что все вычислительные ветви (в частности VLM и Text LLM) успешно отработали. Не использовать эти прогоны для сравнительного ранжирования профилей и не публиковать их как валидные performance results.

## Целевая машина и вход

- Hardware: MacBookPro18,2 / Apple M1 Max.
- Unified memory: 32 GiB.
- OS: macOS 26A434.
- Python process command: `/opt/homebrew/Cellar/python@3.11/3.11.18/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python`.
- Runtime script: `/Users/artemijvisnevskij/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py`.
- VCE local API: `http://127.0.0.1:7555`.
- Server log: `$HOME/Library/Logs/VideoContext Engine/server.log`.
- Input video: 108.233 s, approximately 17.41 MiB. The exact local path is intentionally not repeated here.
- Benchmark launcher branch: `feat/experimental-benchmark-launcher`.
- Draft PR: https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34
- Benchmark runner/patch source is pinned by the launcher to commit `650f3a1437ee0cdcab1c3ea41005a599a373d578`.

## Executive diagnosis

**Primary confirmed failure:** MLX/Metal command-buffer submissions are rejected after prior/excessive GPU errors. VCE's VLM memory guard then disables VLM for the remainder of the current video. A later Text LLM post-processing attempt and a subsequent VLM model-load probe also fail with the same Metal error.

**Not established:** the initiating GPU fault, whether concurrent CPU Whisper materially contributes, whether a particular model/operator triggers the Metal queue failure, and whether H.264 decoder warnings are related.

**Do not label this as an ordinary RAM exhaustion event.** At the supplied system snapshot, macOS reported 87% free system memory, zero throttled pages, and zero swap-ins/outs. This does not rule out a GPU/Metal resource or command-buffer problem; it only argues against system-wide memory pressure at that instant.

## Confirmed evidence from supplied logs

### 1. MLX GPU setup is present, but setup probes are not end-to-end proof

The runtime trace reports:

- `metal_available: true`
- `mlx_gpu_available: true`
- MLX version `0.32.4`
- Apple GPU architecture `applegpu_g13s`, device Apple M1 Max
- `configured_device: DeviceType.gpu`
- `gpu_stream_smoke_test: passed`
- `metal_smoke_test: passed`
- `mlx_vlm.generate.common.generation_stream` and dispatch generation stream are `ThreadLocalStream(Device(gpu, 0), 3)`
- Qwen3-VL-8B-Instruct-4bit model load was reported as successful in approximately 11.97 s in one trace (another run reports 7.45 s).

This confirms that MLX sees the GPU and that the configured VLM generation stream is GPU-targeted. It does **not** prove that every operator in full generation completed on GPU or that the Metal command queue remained healthy.

### 2. Metal command-buffer failure is explicit and repeats

The log contains:

```text
[VLM memory guard] Metal command-buffer timeout/submissions ignored; disabling VLM for the rest of this video.
```

and:

```text
RuntimeError: [METAL] Command buffer execution failed: Ignored (for causing prior/excessive GPU errors)
(00000004:kIOGPUCommandBufferCallbackErrorSubmissionsIgnored).
```

The error appears during `run_text_llm_postprocess`, and later during `MLXEngine.load_model` via `_vce_configure_mlx_metal("vlm")` / `mx.eval(probe)`. This indicates that subsequent GPU work is rejected after the command-buffer failure. It is stronger evidence than a progress bar stall or a transient default-device snapshot.

VCE continues with audio/OCR fallback after disabling VLM. The API subsequently logs HTTP 200 for analyze requests. Treat those requests as degraded/incomplete for VLM-dependent benchmarking.

### 3. Whisper is CPU-backed in these runs

Runtime lines explicitly report:

```text
backend=mlx-whisper device=cpu stream=mx.cpu
```

The run summaries include Whisper inference of 256.16 s and 459.36 s in separate requests. Logs also report CPU Whisper / MLX VLM overlap of 244.37 s and 452.11 s.

The name `mlx-whisper` must not be interpreted as evidence of GPU execution. Whisper is explicitly on the CPU stream in the supplied runtime telemetry.

Concurrent CPU Whisper could affect scheduling/thermal/overall load, but the current evidence does not prove it caused the Metal failure. Test this as a controlled variable; do not state causation in advance.

### 4. System memory snapshot does not show pressure

At approximately 20:20 local time, `memory_pressure` reported:

- System-wide memory free percentage: 87%.
- Pages throttled: 0.
- Swapins: 0.
- Swapouts: 0.

`vm_stat` showed no throttled pages and no swap activity in that snapshot. This is system-wide state, not per-process memory attribution and not GPU allocation telemetry. It cannot rule out a Metal command-buffer timeout or driver/queue fault.

### 5. powermetrics is system-wide GPU activity only

The ten one-second samples showed GPU HW active residency varying roughly from 3.16% to 30.32%, with GPU power roughly 10–414 mW. This indicates some GPU activity during the sample window, but it does not attribute activity to VCE, prove successful model inference, or report per-process VRAM. Do not describe this command output as GPU memory usage.

### 6. Python package check used the wrong interpreter

A previous package check against `/opt/homebrew/bin/python3.11` found no MLX packages. That result is not reliable for the VCE process. The actual server command uses the Python executable under the Homebrew Cellar path shown above, and the VCE logs themselves report MLX 0.32.4 and successful model loading. Re-run package inspection using the exact interpreter from the live process if needed.

### 7. H.264 warnings are not yet causally connected

The log contains FFmpeg decoder messages such as `co located POCs unavailable`. They may indicate damaged or unusual H.264 reference-frame metadata, but no supplied evidence ties them to the Metal command-buffer failure. Keep video decoding as a separate diagnostic branch.

## What the previous run does and does not establish

Established:
- The local VCE API received and completed multiple analyze requests at HTTP level.
- Whisper CPU inference ran and produced language/transcription activity.
- MLX/Metal GPU detection and explicit GPU stream smoke tests passed.
- VLM was disabled by its guard after Metal command-buffer failures.
- Text LLM post-processing failed with the same Metal submissions-ignored error.
- A subsequent VLM load/probe failed with the same error.
- System memory pressure was low at the supplied snapshot.

Not established:
- A successful full VLM analysis for the experimental profiles.
- Valid comparative timings/quality for the profiles.
- That CPU Whisper concurrency is the root cause.
- Per-process GPU utilization or per-process GPU memory.
- That H.264 warnings caused the Metal error.
- That all six profiles completed successfully.
- A clean, isolated benchmark run after a fresh VCE process restart.

## Required benchmark acceptance rules

For this investigation, a profile is **not successful** merely because the analyze endpoint returned HTTP 200.

Mark a profile as failed/degraded/non-comparable if any of the following occurs:
1. VLM memory guard disables VLM for the video.
2. Metal `Command buffer execution failed` / `SubmissionsIgnored` occurs during a required MLX stage.
3. Required Text LLM post-processing fails and silently falls back to the unprocessed result.
4. Required feature branch is skipped or falls back without being represented in the profile status.

Do not calculate a winner from degraded profiles. Keep the raw diagnostic bundle local. Before publishing, verify that reports contain no source video identity, transcript, OCR text, prompts, full API response, model output, secrets, or raw logs.

## Next-session plan (in order)

### Step 1 — establish current process state

Before starting another benchmark, determine whether the previous launcher/request is still active. In Terminal:

```bash
ps -axo pid,ppid,%cpu,%mem,etime,state,command | grep -E 'vce_full_profile_benchmark|runner.py|VideoContextEngine_v3.19.py' | grep -v grep

tail -n 80 "$HOME/Library/Logs/VideoContext Engine/server.log"
```

Do not kill the VCE server blindly. If the benchmark launcher is still running through profiles, stop the launcher from its original Terminal with Ctrl+C if it is continuing to generate invalid degraded comparisons. Preserve the local run directory and logs. Stop/restart the VCE app cleanly before a controlled new run.

### Step 2 — inspect exact runtime interpreter

Use the executable path shown by `ps`, not a guessed `python3` alias. Example:

```bash
PY="/opt/homebrew/Cellar/python@3.11/3.11.18/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python"
"$PY" -c 'import sys, mlx, mlx_vlm; print(sys.executable); print("mlx", mlx.__version__); print("mlx_vlm", getattr(mlx_vlm, "__version__", "unknown"))'
```

If that exact path differs on the next run, use the path from the live process command. Check installed versions of `mlx`, `mlx-vlm`, and `mlx-whisper` with that interpreter. Do not install/upgrade packages until versions and the active environment have been recorded.

### Step 3 — inspect Metal failure context before changing code

Collect a bounded excerpt around the first Metal failure, including timestamps and PID/thread context if available:

```bash
grep -n -B 25 -A 35 \
  'Command buffer execution failed\|VLM memory guard\|submissions ignored' \
  "$HOME/Library/Logs/VideoContext Engine/server.log" | head -n 260
```

Find the earliest failure in a fresh server process. Later `SubmissionsIgnored` errors may be cascading failures rather than the initiating fault.

### Step 4 — controlled isolation, one profile at a time

Do not rerun all six profiles as the first diagnostic. Use the benchmark runner's baseline-only mode, if still supported, and change only one factor per run:

A. VLM-only or visual path with Whisper/audio disabled (if API supports a safe one-request diagnostic).
B. Whisper-only CPU path with VLM disabled.
C. VLM + Whisper concurrency, only after A and B complete independently.
D. Compare same VLM request with reduced scene/keyframe count or lower-resolution input only if A fails.

Record exact active settings, model IDs/quantization, package versions, process restart state, run ID, first Metal error, and whether VLM/Text LLM completed. These are diagnostic isolation runs, not performance comparisons.

### Step 5 — inspect runtime concurrency and recovery design

Review the actual customized local `VideoContextEngine_v3.19.py` and the patcher source before editing. Focus on:
- whether CPU Whisper's MLX CPU stream runs concurrently with GPU VLM generation;
- executor/thread affinity and stream ownership;
- whether VLM and Text LLM share one Metal context/queue or model lifecycle;
- whether the memory guard's timeout classification conflates timeouts with allocation/OOM;
- whether a failed Metal queue is reused after the guard trips;
- whether process restart is required to restore a healthy Metal command queue.

Do not blindly disable concurrency, change wired limits, upgrade MLX, or retry a failed command buffer without a controlled reproduction and regression tests.

### Step 6 — harden benchmark result classification

Inspect `scripts/vce_full_profile_benchmark.py.gz` and its patch helper at the pinned source revision. Ensure the runner can detect and record:
- VLM attempted / completed / disabled-by-guard;
- Metal command-buffer error;
- Text LLM attempted / completed / fallback;
- API HTTP status separately from stage success;
- per-feature requested / applied / skipped / failed status.

A profile with a required stage failure must have an explicit non-success status and must not contribute to a best-profile ranking. Add unit tests with representative sanitized log/response fixtures. Keep this separate from runtime bug fixes where practical.

### Step 7 — only then run the full matrix

After isolated tests pass and the server has been restarted cleanly, run baseline and experimental profiles sequentially. Confirm each required stage completes before accepting the profile. Keep raw logs and all video-derived outputs local. Publish only reviewed, sanitized aggregate tables.

## Useful commands

Current process and memory snapshot:

```bash
ps -axo pid,ppid,%cpu,%mem,etime,state,command | grep -E 'vce_full_profile_benchmark|runner.py|VideoContextEngine_v3.19.py' | grep -v grep
memory_pressure
vm_stat
sysctl vm.swapusage
```

GPU activity (not GPU memory / not per-process):

```bash
sudo powermetrics --samplers gpu_power -i 1000 -n 10
```

Relevant log signatures:

```bash
grep -n -E 'VLM memory guard|Command buffer execution failed|SubmissionsIgnored|VCE runtime|VCE parallelism|PROFIL TEMPS' \
  "$HOME/Library/Logs/VideoContext Engine/server.log" | tail -n 240
```

## Handoff prompt for the next assistant

Continue work on repository `shushpanchik-art/VideoContext-Engine-Apple-Silicon`, branch `feat/experimental-benchmark-launcher`, draft PR #34:

https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34

First read this file and the PR description. Treat the 2026-11-04 MacBook Pro M1 Max run as a failed/degraded diagnostic, not a valid benchmark. Confirm whether the previous launcher is still running, preserve local diagnostics, and inspect the exact VCE Python process/environment. The strongest current finding is repeated MLX Metal command-buffer `SubmissionsIgnored`, followed by VLM guard shutdown and Text LLM fallback; system memory was 87% free at the supplied snapshot. Whisper is explicitly CPU-streamed. GPU detection and smoke tests pass, but they do not prove successful full inference. Do not assume CPU Whisper concurrency or H.264 warnings are the root cause.

Next: inspect the pinned gzip runner and patcher, harden per-profile success/failure classification so HTTP 200 cannot mask VLM/Text LLM failure, add regression tests, then perform controlled VLM-only / Whisper-only / concurrent isolation runs on a freshly restarted VCE process. Do not publish raw logs, prompts, transcripts, OCR, model outputs, or the input video. Do not claim end-to-end success until every required stage has completed and the target-Mac evidence is reviewed.

## Follow-on six-profile run received 2026-11-04

A later terminal capture showed a complete six-profile matrix with runner exit code 1. That capture explicitly reported benchmark patch v2; it predates the stage-aware v3 classifier now pinned by the launcher and must not be used to validate the new classification logic.

- The old runner labelled the baseline `completed` while also recording four errors.
- The five subsequent experimental profiles (adaptive keyframes, OCR cache, MLX Whisper validation flag, fast scene detection, combined) each ended in `error`.
- Repeated MLX/Metal `kIOGPUCommandBufferCallbackErrorSubmissionsIgnored` messages remained present. The non-zero runner exit confirms the matrix was not successful.
- The log capture did not include a `ps`, `pgrep` or `lsof` snapshot. The shell prompt returned after the runner exit, but the current VCE server/process state was not independently measured.
- This matrix did not run the new isolated VLM-only or Whisper-only modes. Enabling the legacy `enable_mlx_whisper` validation flag is not a Whisper-only test and does not change the current CPU Whisper backend.

Do not publish any CSV, error excerpt, terminal log, server log, response or video-derived material from this run. The benchmark-results directory was removed from the PR tree and the feature branch was rewritten to exclude the commit that had added those local artifacts. Git hosting may retain an unreferenced object/cache for some time; current branch history no longer includes that commit.

### Next target-Mac sequence

1. Before a new run, inspect process/listener state locally without printing command-line arguments or video paths:
   ```bash
   ps -axo pid,ppid,stat,etime,%cpu,%mem,comm | egrep -i 'VideoContext|python|uvicorn' | grep -v egrep
   lsof -nP -iTCP:7555 -sTCP:LISTEN
   ```
   Confirm no benchmark launcher or stale analysis request remains. Restart VCE cleanly and verify its API is ready.
2. Set `VCE_VIDEO_PATH` locally in Terminal. Do not add the private path to GitHub, logs or reports.
3. Run VLM-only with `VCE_BENCH_ONLY_BASELINE=1 VCE_BENCH_VLM_ONLY=1`. Require `status=completed`, positive VLM inference evidence and no Metal/guard failure.
4. Restart VCE cleanly, then run Whisper-only with `VCE_BENCH_ONLY_BASELINE=1 VCE_BENCH_WHISPER_ONLY=1`. Require positive Whisper inference evidence and an available audio track. The current Whisper backend is CPU.
5. Only after both isolated tests pass, run the concurrent baseline with `VCE_BENCH_ONLY_BASELINE=1`. Require VLM, Whisper and Text LLM all to show `completed`.
6. If a requested stage returns failed/degraded, or Metal submissions are ignored, stop the matrix and preserve diagnostics locally. Do not rank profiles or call HTTP 200 a successful benchmark.

The launcher now publishes only the redacted aggregate `summary.md`, `stage_comparison.csv` and `launcher_status.txt`. Detailed errors, raw logs, run summaries and full responses remain local.

