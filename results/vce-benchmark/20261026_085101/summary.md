# VideoContext Engine benchmark report

- Script: scripts/benchmark_vce_macos.py
- Timestamp UTC: 2026-10-26T11:51:01+00:00
- VCE API: http://127.0.0.1:7555
- Privacy: no video path, transcript text, scene descriptions, global summary or prompt text stored.

## Machine and runtime

| Property | Value |
|---|---|
| macOS | 27.0.1 |
| architecture | arm64 |
| cpu | Apple M1 Max |
| logical_cpu_count | 10 |
| physical_memory_gb | 32.0 |

## Installed code markers

| Feature | Detected in local engine file |
|---|---|
| parallel_cpu_asr_gpu_vlm | yes |
| bounded_frame_prefetch | yes |
| mp4_recovery | yes |

## Video metadata (no path/name)

{
  "extension": ".mp4",
  "size_mb": 17.41,
  "duration_s": 108.233,
  "streams": [
    {
      "type": "video",
      "codec": "h264",
      "width": 1920,
      "height": 1080,
      "avg_frame_rate": "30/1"
    },
    {
      "type": "audio",
      "codec": "aac"
    },
    {
      "type": "data",
      "codec": "timed_id3"
    }
  ],
  "ffprobe_available": true
}

## Request-only compatibility adjustments

These fields were shortened to API limits for this benchmark request only; local settings were not changed.
- visual_user_prompt: 1420 to 998 characters (API maxLength 1000).
- summary_user_prompt: 2138 to 1997 characters (API maxLength 2000).

## Runs

| Run | HTTP wall (s) | Server processing (s) | Whisper (s) | VLM (s) | Scenes | Mean CPU % | Peak RSS (MB) | Prefetch wait (s) | Fallbacks |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 264.384 | 264.04 | 107.82 | 143.301 | 14 | 136.6 | 13310.7 | 0.131 | 0 |

## Interpretation notes

- client_wall_s includes file upload and API response; server_process_time_s is the VCE's own timer.
- CPU/RSS samples cover the process listening on port 7555 and its descendants, not the whole machine.
- frame_prefetch_wait_seconds and frame_prefetch_worker_seconds are only available if the patched API returns timings.stage_diagnostics.
- GPU utilization is not inferred from CPU metrics. MLX model selection is evidence of the intended backend, not a direct GPU-utilization measurement.
- Compare at least 3 runs with identical video and settings; the first run may include cold-start/model loading costs.
