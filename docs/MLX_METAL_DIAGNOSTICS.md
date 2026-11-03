# MLX / Metal diagnostics for VideoContext Engine

These tools collect local evidence for a single VCE analysis. They do not patch or stop a running server, alter settings, or upload videos/results to GitHub.

## Requirements

- macOS on Apple Silicon.
- A configured VideoContext Engine virtual environment and a running local API at `http://127.0.0.1:7555`.
- The local settings file and an input video.
- Optional Xcode Command Line Tools for stack/process sampling. GPU power sampling may require an interactive administrator authorization.

## Basic runtime check

From the repository root:

```bash
source .venv/bin/activate
python scripts/verify_mlx_metal_runtime.py
```

This checks the installed MLX/Metal runtime and prints diagnostics. It does not load the VCE models or prove that every model operator executes on the GPU.

## Observe one complete analysis

```bash
python scripts/vce_observed_run.py \
  --video "$HOME/path/to/video.mp4" \
  --settings "$HOME/VideoContext-Engine/videocontext_settings.json"
```

The observer checks the local API/settings before sending the video, polls job progress, records timing and process samples, and saves the complete response and transcript under a local output folder. It does not upload the input or response to GitHub and does not write to the settings file.

## Runtime watchdog

```bash
python scripts/vce_runtime_watchdog.py \
  --video "$HOME/path/to/video.mp4" \
  --settings "$HOME/VideoContext-Engine/videocontext_settings.json" \
  --runner scripts/vce_observed_run.py
```

Optional flags include `--powermetrics`, `--sample-stacks`, and `--vmmap`. These may require additional macOS permissions and collect system/process telemetry. Use them only when needed.

## How to interpret device fields

- `configured_device` / `device_source` describe the explicit target selected by the VCE runtime helper.
- `generation_stream` records the MLX generation stream observed by the worker.
- `default_device_observed` is a snapshot of MLX's process/thread context and must not be treated as the configured target on its own.
- GPU availability, a successful Metal smoke test, or system-wide GPU power samples do **not** prove that every VLM operator ran on Metal.
- If `vlm.runtime.device` disagrees with the configured GPU target or generation-stream snapshot, preserve the raw trace and compare a fresh run after fully restarting the VCE server. Do not rewrite telemetry or treat the discrepancy as resolved based on one field.

The historical PR #13 experiment documented such a telemetry mismatch on one M1 Max run. The published runtime contains the thread-affinity and `wired_limit` compatibility code, but this diagnostic note does not claim that all VLM operators have been independently proven to run on Metal.

## Privacy

The scripts require an explicit video path and keep reports under the local output directory. Those reports can contain a full transcript, scene payloads, local process names and memory samples. Do not commit them or upload them to a public repository. The repository deliberately excludes raw watchdog runs and installed-app snapshots.

## Command-buffer timeout recovery

The runtime recognizes both `kIOGPUCommandBufferCallbackErrorTimeout` and `kIOGPUCommandBufferCallbackErrorSubmissionsIgnored`. The MLX engine's image-resize retry is reserved for allocation OOM errors; command-buffer timeouts are raised immediately to the outer VLM guard and are never retried with smaller frames. The latter commonly appears after a prior command-buffer failure. On this error chain VCE unloads the VLM and skips further VLM requests for the current video, while preserving Whisper transcription, Apple Vision OCR, optional FFmpeg/Tesseract OCR, and TXT/JSON report generation. This is a recovery path, not a guarantee that a timed-out Metal device can be reset in-process.

New Apple Silicon installations default to a 768 px VLM input cap and one keyframe per scene. OCR continues to inspect its original-resolution frame independently of the VLM resize.
