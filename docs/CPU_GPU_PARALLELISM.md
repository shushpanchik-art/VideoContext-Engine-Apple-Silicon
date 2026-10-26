# CPU + MLX/Metal execution model for VCE

## Intended execution plan

- **Whisper:** use the `mlx-whisper` inference implementation, but execute its MLX operations on a dedicated `mx.cpu` thread-local stream.
- **Scene VLM:** use `mlx-vlm` with MLX GPU / Metal.
- **Final text analysis (LLM):** use the MLX backend in the customized VCE source and require MLX GPU / Metal there too.
- **CPU assistance:** while the VLM is generating, continue independent CPU work such as Whisper, video decode, scene/frame selection, resizing, OCR, prompt assembly, JSON and I/O. Keep queued frame work bounded to reduce unified-memory pressure.

## Does mlx-whisper use Metal in this mode?

No: this patch intentionally assigns Whisper's MLX operations to a CPU stream so they can overlap with the VLM's GPU stream. MLX is the same framework/API family, but stream placement decides the device used for each operation. The official MLX docs describe using CPU and GPU streams for independent operations and explain Apple's unified memory model: [streams](https://ml-explore.github.io/mlx/build/html/usage/using_streams.html) and [unified memory](https://github.com/ml-explore/mlx/blob/main/docs/src/usage/unified_memory.rst).

The CPU stream is created with `mx.new_thread_local_stream(mx.cpu)` and scoped with `mx.stream(cpu_stream)`. Synchronization targets that CPU stream, not the whole MLX runtime, so the Whisper worker should not wait for unrelated Metal work. The target Mac must verify that the installed MLX version and `mlx-whisper` build behave this way end-to-end.

The standard `mlx_whisper.transcribe()` API loads a converted MLX checkpoint. The VCE patch maps named presets such as `large` to `mlx-community/whisper-large-v3-mlx`, and `large-v3-turbo` to `mlx-community/whisper-large-v3-turbo`. Model loading is lazy on first transcription, so its initial load time is included in that first inference measurement. A local checkpoint or full Hugging Face repo ID can be supplied via `VIDEOCONTEXT_MLX_WHISPER_MODEL`.

## Why configure GPU explicitly?

Selecting `mx.gpu` is a guard for the VLM and final text-model inference, not a global ban on CPU tasks. CPU stream placement for Whisper is explicit and local to its worker. The patch validates a real MLX/Metal GPU operation before the VLM/LLM stage and fails if Metal is unavailable rather than silently presenting CPU fallback as a GPU run.

MLX does **not** automatically send idle CPU cores to accelerate arbitrary transformer layers currently running on the GPU. The CPU can help by performing independent pre-processing concurrently; assigning a portion of model operations to CPU requires explicit support and profiling because synchronization and unified-memory contention may make it slower.

## Safety and acceptance criteria

- Use native arm64 Python and install `mlx`, `mlx-vlm`, `mlx-whisper` and the dependencies required by the customized engine in the same runtime environment.
- VLM and final text LLM must log MLX/Metal runtime diagnostics.
- Whisper logs `backend=mlx-whisper`, `device=cpu`, `stream=mx.cpu`; first inference time includes lazy model loading.
- Compare timestamps and require a positive measured Whisper/VLM overlap.
- Check transcript quality against the existing Whisper configuration and compare end-to-end latency; MLX Whisper on CPU may be slower as a standalone ASR backend even if overlap reduces total elapsed time.
- Check memory pressure with the VLM and ASR loaded concurrently. MLX uses unified memory, so CPU placement does not imply a separate physical RAM pool.

The source patch is backup-safe and dry-run by default. A passing GitHub unit test cannot prove device placement or speed on the target M1 Max; the live Mac benchmark remains the final acceptance test.
