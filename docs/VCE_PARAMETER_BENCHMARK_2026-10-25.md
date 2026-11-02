# VCE parameter benchmark — anonymized summary

## Test envelope

- Video duration: 108.23 s; file size: about 18.3 MB (source identity and checksum intentionally omitted).
- Hardware: Apple Silicon MacBook Pro M1 Max, 32 GB unified memory.
- Engine: VideoContext Engine v3.19; Qwen3-VL-8B MLX, Whisper large/MPS, Apple Vision OCR, FFmpeg/Tesseract OCR, Qwen3.5-9B.
- 47 one-factor-at-a-time cases: 44 completed, 3 were rejected by API validation because the test generator supplied non-integer FFmpeg OCR intervals.
- Baseline runtime: 278.97 s; 14 scenes.
- This was one run per variant on one sample, not a controlled multi-video benchmark. Small deltas are not conclusive.

## Baseline stage timing

| Stage | Time |
|---|---:|
| Qwen3-VL visual inference | 139.63 s |
| Qwen3.5 text post-processing | 84.31 s |
| Whisper | 29.41 s |
| FFmpeg/Tesseract OCR | 9.32 s |
| Apple Vision OCR | 4.67 s |
| Whisper / VLM load | 9.50 / 4.96 s |
| Total runner time | 278.97 s |

## Fastest observed variant per factor

| Parameter | Fastest completed candidate | Time | Delta to baseline | Coverage note |
|---|---|---:|---:|---|
| VLM resolution | 384 | 260.82 s | −6.5% | 14 scenes; small text requires visual review |
| Scene threshold | 0.27 | 242.67 s | −13.0% | 11 scenes vs. 14 at baseline |
| Minimum scene duration | 2.7 s | 260.51 s | −6.6% | 12 scenes vs. 14 |
| Maximum scene duration | 20 s | 277.53 s | −0.5% | No meaningful speed improvement |
| Keyframes per scene | 1 | 283.56 s | +1.6% | 2–5 frames increased runtime up to about +54% |
| VLM max tokens / scene | 1024 | 274.44 s | −1.6% | Difference close to run noise |
| VLM max tokens / summary | 128 | 279.39 s | +0.2% | No useful speed gain |
| FFmpeg OCR interval | 9 s | 264.73 s | −5.1% | Fewer OCR samples and less OCR text |
| FFmpeg OCR workers | 6 | 272.02 s | −2.5% | OCR stage 6.58 s vs. 9.32 s at 3 workers |
| FFmpeg OCR disabled | false | 250.46 s | −10.2% | Less extracted screen text |

## Practical interpretation

- Resolution 384 was a promising speed candidate in this sample, but it should be checked against small labels and technical notation.
- Scene threshold 0.27 and minimum scene duration 2.7 s shortened the analysis partly by reducing scene count. The speed gain is not free: short visual states may be merged or omitted.
- Increasing keyframes per scene substantially increased runtime in this experiment. Keep one frame as the fast default for static presentations; use more frames only when dynamic transitions matter.
- A 9 s OCR interval reduced samples and OCR output. That is a speed/coverage trade-off, not an unconditional improvement.
- Disabling FFmpeg OCR reduced elapsed time but also reduced extracted screen text. Keep it enabled when detailed screen-text coverage matters.
- The three failed OCR-interval cases were HTTP 422 validation errors from non-integer candidate values; they were not VCE inference failures.

## Limitations

- One video and one run per variant; combined parameter effects were not tested.
- No independently labelled reference transcript or WER/CER was available.
- Prompt fallback values were used where persisted prompts exceeded API limits.
- The fastest factor candidate is not a validated combined profile.
- Full per-video API responses and their manifests were removed from the public repository. This document preserves only aggregate timing and parameter observations.
