# Runtime profiles and benchmark contract

## Canonical profiles

### `baseline_mps_sequential` (default)

1. OpenAI Whisper / PyTorch runs on `mps` when macOS MPS is available and Whisper acceleration is enabled. Otherwise it explicitly falls back to CPU.
2. Whisper completes before the VLM model is loaded or visual inference begins.
3. Qwen3-VL continues to use MLX/Metal.
4. Qwen3.5 Text LLM continues to use MLX/Metal after the scene analysis and OCR stages.

This is the reproducible sequential baseline selected to represent the documented 25 October behavior.

### `mlx_parallel` (experimental optimization)

1. MLX Whisper is used on its CPU thread-local MLX stream.
2. Whisper can overlap with Qwen3-VL on MLX/Metal.
3. Qwen3.5 Text LLM remains unchanged and runs on MLX/Metal after the scene pass.

This profile is explicitly an optimization experiment. It must not be used as the baseline label.

## Measurement requirements

Each result must include `meta.runtime_profile`, `timings.runtime_profile`, Whisper backend/device, VLM backend/device diagnostics, Whisper/VLM overlap seconds, and stage timings. The sequential profile must report zero Whisper/VLM overlap because its scheduling guarantees Whisper completion before VLM loading. The parallel profile reports the measured overlap; it must not infer overlap from backend selection alone.

For a valid A/B test, run the exact same input file with the same scene boundaries, keyframe count/resolution, prompts, model identifiers, OCR switches, token limits, and output options. Record whether model caches were cold or warm. Compare both runtime and output quality; runtime alone is not sufficient.

## Separate architecture track

A future `llama.cpp`/GGUF VLM or Text LLM backend is a distinct architecture experiment, not a runtime-profile variant and not a reproduction of the 25 October baseline. Its model-size/quantization and output-quality trade-offs require a separate benchmark and must not silently replace either canonical profile.

## Environment overrides and internal markers

Saved settings are the normal configuration surface. The following environment values are
explicit process-level overrides and take precedence over saved settings:

- `VIDEOCONTEXT_MLX_WHISPER_MODEL` overrides the selected Whisper model for the MLX Whisper adapter.
- `VIDEOCONTEXT_MLX_DEVICE` overrides `mlx_device`; only `auto` and `metal` are accepted for the VLM.
- `VCE_MLX_WHISPER_REPO` is consulted only when the selected Whisper model is not one of the built-in presets.
- `VCE_DEV_DOCS` enables Swagger/ReDoc only for `1`, `true`, `yes`, or `on` (case-insensitive).

`VCE_CANCELLED_BY_USER` is an internal downloader error sentinel used to distinguish a user
cancellation from a download failure. It is not a user setting and should not be set manually.
