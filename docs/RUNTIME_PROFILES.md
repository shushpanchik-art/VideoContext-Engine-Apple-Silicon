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

## Independent inference backend selector

The `inference_backend` setting is orthogonal to `runtime_profile`:

- `mlx_metal` is the default and preserves the MLX VLM + MLX Text LLM implementation.
- `llama_cpp_metal` selects llama.cpp for both VLM and Text LLM. It requires local GGUF paths for `vlm_model`, `vlm_mmproj_model`, and `text_llm_model`, plus a llama-cpp-python build with Metal/GPU offload support.

Whisper scheduling remains controlled only by `runtime_profile`. To compare architectures, keep `runtime_profile=baseline_mps_sequential` in both runs and change only `inference_backend` plus the corresponding model files. Apple Vision OCR and FFmpeg/Tesseract OCR settings are independent and unchanged.

Changing `inference_backend` requires restarting the native VideoContext Engine app because the VLM provider is created during server startup. The API rejects a request whose declared backend differs from the backend loaded by the running server. There is no automatic fallback from llama.cpp/Metal to MLX or CPU.

The llama.cpp path requests all model layers for GPU offload and checks that the installed llama-cpp-python build advertises GPU offload support. This is a runtime configuration check, not per-operator Metal profiling; benchmark archives must retain llama.cpp logs/build information and memory measurements. Use a Qwen3-VL-compatible mmproj and chat handler; generic LLaVA handlers are not interchangeable.

## Measurement requirements

Each result must include `meta.runtime_profile`, `timings.runtime_profile`, Whisper backend/device, VLM backend/device diagnostics, Whisper/VLM overlap seconds, and stage timings. The sequential profile must report zero Whisper/VLM overlap because its scheduling guarantees Whisper completion before VLM loading. The parallel profile reports the measured overlap; it must not infer overlap from backend selection alone.

For a valid A/B test, run the exact same input file with the same scene boundaries, keyframe count/resolution, prompts, model identifiers, OCR switches, token limits, and output options. Record whether model caches were cold or warm. Compare both runtime and output quality; runtime alone is not sufficient.

## Separate architecture track

A future `llama.cpp`/GGUF VLM or Text LLM backend is a distinct architecture experiment, not a runtime-profile variant and not a reproduction of the 25 October baseline. Its model-size/quantization and output-quality trade-offs require a separate benchmark and must not silently replace either canonical profile.
