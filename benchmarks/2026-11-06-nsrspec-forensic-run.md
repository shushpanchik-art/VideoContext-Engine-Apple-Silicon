# NSR Specification forensic run — 2026-11-06

## Result

- Input: same NSR Specification sample used for the local comparison; 108.233 s, 17.412 MiB
- Scenes: 14
- API result: HTTP 200
- Client wall time: **345.324 s**
- VCE processing time: **344.331 s**
- Runtime: macOS 27.0.1, arm64, Python 3.11.18

## Measured stages

| Stage | Time |
|---|---:|
| Scene detection | 3.230 s |
| Whisper load | 15.579 s |
| Whisper inference | 25.136 s |
| VLM load | 5.224 s |
| VLM inference | 200.622 s |
| Apple Vision OCR | 4.669 s |
| FFmpeg/Tesseract OCR | 10.365 s |
| Text LLM postprocess | 88.591 s |

Whisper was `openai-whisper large` on MPS; the runtime diagnostic marked its device as verified. VLM was `Qwen3-VL-8B-Instruct-GGUF:Q4_K_M` through `llama.cpp`/Metal. Text LLM was `Qwen3.5-9B-GGUF:Q4_K_M` through `llama.cpp`/Metal. Whisper/VLM overlap was 0.0 s.

## Comparisons

- Previous full run: 855.378 s. This run was 510.054 s shorter, approximately **2.48× faster** (59.6% less wall time).
- The earlier Text LLM stage was 598.351 s versus 88.591 s now: approximately **6.76× faster**.
- VLM inference was effectively unchanged: 199.664 s previously versus 200.622 s now.
- Documented 25 October reference: 278.970 s. This run is 66.354 s (23.8%) slower in raw time, but it is **not an apples-to-apples comparison** because this run used llama.cpp/GGUF for VLM and Text LLM rather than the documented MLX model path.

## Interpretation limits

This run confirms sequential PyTorch Whisper/MPS and llama.cpp/Metal stages. It does **not** verify the intended `baseline_mps_sequential` profile with MLX Qwen3-VL and MLX Qwen3.5 Text LLM: the local API at run time did not expose the new `runtime_profile` form field, and the structured runtime verdict was `NO_DIRECT_MLX_RUNTIME_EVIDENCE`.

The watchdog's maximum sampled RSS was 13,887.3 MiB for the VLM `llama-server` process and 8,530.8 MiB for the Python server. RSS is not a measurement of total unified-memory or Metal allocations.

The response passed the server's JSON integrity check and contained 14 scene IDs. No ground-truth quality benchmark was performed, so these timing results make no claim about relative output quality.

No source video, transcript, prompts, local paths, or raw response payload are included in this public summary. Full forensic artifacts belong in the user's private Dropbox archive.
