# NSR Specification A/B: MLX finalist and Qwen3.5 JSON follow-up

**Evidence date:** 2026-11-07  
**Hardware:** Apple MacBook Pro, Apple M1 Max  
**Video:** `NSR_Specification.mp4`, duration 108.23 seconds  
**A/B profile:** `baseline_mps_sequential`

## Decision

**Promote pass 1 (MLX/Metal) as the current hardware-validated finalist/reference configuration.** It is the only pass in this A/B run that completed the fixed video with Text LLM status `success`.

This is a successful end-to-end result for this specific video and M1 Max run. It is not a universal claim that MLX is faster or better than llama.cpp. The comparison pass did not complete successfully, so the A/B experiment is **not a completed backend comparison**.

Status labels:

- **FINALIST / REFERENCE:** MLX/Metal completed the entire run successfully.
- **JSON FOLLOW-UP OPEN:** llama.cpp Qwen3.5 failed parsing after its repair pass.
- **A/B NOT COMPLETE:** the comparison backend has not yet completed an end-to-end successful result.

## Finalist configuration (pass 1)

| Component | Fixed configuration |
|---|---|
| Inference backend | `mlx_metal` |
| Runtime profile | `baseline_mps_sequential` |
| Whisper | `openai-whisper large`, PyTorch MPS |
| MLX Whisper | Disabled (`enable_mlx_whisper=false`) |
| VLM | `mlx-community/Qwen3-VL-8B-Instruct-4bit` |
| Text LLM | `mlx-community/Qwen3.5-9B-MLX-4bit` |
| VLM resolution | 1280 |
| Apple Vision OCR | Enabled |
| FFmpeg/Tesseract OCR | Enabled; 5-second interval; 3 workers |
| OCR cache | Enabled |
| Audio features / word timestamps | Enabled |
| Analysis mode | Full |
| Scene count | 14 |

### Measured result

| Metric | Pass 1 result |
|---|---:|
| HTTP status | 200 |
| Runner exit code | 0 |
| Runtime verdict | `PASS` |
| Text LLM status | `success` |
| Video duration | 108.23 s |
| Scene count | 14 |
| VCE process time | 321.959 s |
| Client wall time | 323.008 s |
| Whisper inference | 24.717 s |
| VLM inference | 174.698 s |
| Text LLM inference | 91.377 s |
| Text LLM model cache before run | Cached |
| MLX version | 0.32.4 |

The observed run records the configured MLX device as GPU/Metal and a successful explicit GPU stream smoke test. These are runtime-wiring/probe signals, not proof that every model operator executed on the GPU.

### Finalist acceptance boundary

This reference combination is accepted as the **working end-to-end baseline for the tested NSR Specification video**. Keep its model selectors and runtime profile unchanged while investigating the llama.cpp JSON failure. Do not switch the finalist to GGUF as a result of the failed comparison pass.

The exact pass-1 settings are preserved in the private forensic run:
`run1_mlx_metal/observed_runs/run_20261107_155713/request_settings.json` and `active_server_settings_snapshot.json`.

## Comparison pass 2: where it failed

The llama.cpp pass also used the same `baseline_mps_sequential` profile and the same 14-scene input video.

| Check | Pass 2 result |
|---|---|
| HTTP status | 200 |
| Runner exit code | 3 |
| VLM Qwen3-VL | Loaded and processed scenes |
| MTMD context | Initialized |
| Text LLM Qwen3.5 | Failed |
| Final verdict | `TEXT_LLM_FAILED` |
| VCE process time | 514.404 s |
| Text LLM inference | 262.985 s |
| JSON repair attempt | Failed |

The exact parser exception was:

```text
Expecting property name enclosed in double quotes: line 1 column 3 (char 2)
```

The primary response failed, then the repair-pass response failed as well. The old implementation did not retain either raw model response, so the original characters at the failure point cannot be reconstructed from this run. The specific shape of the malformed output therefore remains unproven; a prose prefix or malformed object is plausible but not established.

The MTMD/Qwen3-VL path was not the failing stage. The real-model preflight reported `MTMD_CONTEXT_READY`, the handler used `mmproj_path`, the MTMD context initialized, and both vision and video capabilities were true. Runtime logs recorded Metal offload of 37/37 VLM layers and 34/34 Text LLM layers. The watchdog did not independently attribute per-operator GPU execution, so this should be reported as successful model-layer offload evidence, not a profiler-level execution proof.

## JSON follow-up changes in this branch

The pinned runtime dependency is `llama-cpp-python 0.4.6` at fork commit `1dd04befbaf1277853eb188cc4513d6089a931dd`. The follow-up uses API controls verified in that exact source revision; it does not assume an arbitrary per-call `chat_template_kwargs` parameter reaches the model's Jinja template.

1. **Constrained generation.** llama.cpp Text LLM requests now use `response_format={"type":"json_object"}`, which activates the fork's JSON grammar support.
2. **Bounded reasoning.** The pinned fork exposes `reasoning_budget`, `reasoning_start`, `reasoning_end`, and `reasoning_start_in_prompt`. Since the model's GGUF template already inserts `<think>` by default when `enable_thinking` is undefined, llama.cpp calls now use `reasoning_budget=0` with `reasoning_start_in_prompt=True` to close the first reasoning block immediately.
3. **More defensive parsing.** The parser removes complete `<think>...</think>` blocks, refuses to parse content inside an unterminated reasoning block, and scans for a complete JSON object containing a non-empty `executive_summary` rather than blindly parsing from the first `{` through the last `}`.
4. **Failure evidence.** If both generation and repair fail, the exception carries bounded primary and repair excerpts (maximum 2,000 characters each) plus both parse errors. These fields are included in the API response and `stage_timings.json`; the observer writes `text_llm_raw.txt` into the local forensic run directory.

Raw excerpts can include user video-derived text. They are diagnostic artifacts for the user's local/private forensic run and must not be added to public issues, pull requests, or public repositories. The A/B launcher may include them only in the private forensic archive.

## Validation still required

The code and regression tests in this follow-up branch have not yet been rerun on the target M1 Max after the changes above.

Before declaring the JSON issue fixed:

- [ ] Focused regression tests pass in CI.
- [ ] The updated one-command A/B run executes on the M1 Max.
- [ ] The MLX finalist again completes the same video with exit code 0.
- [ ] llama.cpp Qwen3.5 returns valid structured JSON without relying on a successful repair-pass.
- [ ] If JSON still fails, `text_llm_raw.txt`, `stage_timings.json`, and `server_events.jsonl` preserve the bounded primary/repair excerpts and errors.
- [ ] Original API settings are restored and verified.
- [ ] The new private forensic archive is uploaded and its remote byte count is verified.

Until these items are checked, keep the MLX combination as the finalist/reference and keep the llama.cpp JSON follow-up open. Do not merge this follow-up as hardware-validated merely because static tests pass.
