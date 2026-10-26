# CPU + MLX/Metal execution model for VCE

## Required mode

The desired pipeline is:

1. Whisper neural inference runs on CPU.
2. VLM neural inference runs through MLX on the Apple GPU / Metal.
3. While one VLM inference is running, CPU work can continue independently: Whisper ASR, decoding the next frames, resize/keyframe selection, OCR, tokenization/prompt formatting, input/output and orchestration.
4. The final text-analysis model runs through MLX/Metal after ASR and scene analysis are joined.

## Can CPU Whisper use MLX/Metal at the same time?

Not for the same CPU-only inference operations. If OpenAI Whisper is loaded with PyTorch on `device="cpu"`, that inference does not use MLX or Metal. The CPU can run Whisper while the VLM uses Metal concurrently.

The separate `mlx-whisper` implementation runs the Whisper model through MLX, normally using the MLX default device (GPU/Metal on Apple Silicon). That is a different inference backend and makes Whisper a GPU workload, not the requested CPU Whisper workload. MLX can also run individual operations on a CPU or GPU stream, but mixed per-operation placement requires support/explicit placement in the implementation; installing MLX does not automatically split a regular PyTorch Whisper model across CPU and Metal.

## What "CPU assists the VLM" means

Explicitly selecting MLX GPU as the default device does not turn off CPU use by Python or other libraries. It selects where new MLX operations run by default and helps make the model's GPU path deterministic. CPU work remains available for:

- OpenCV video decoding and frame sampling/resizing
- Apple Vision OCR and text extraction
- preparing prompts and image inputs
- Whisper CPU transcription in parallel with VLM generation
- Python orchestration, reading/writing files, JSON handling and response processing

On Apple Silicon, CPU and GPU share unified memory. MLX also supports explicitly choosing CPU or GPU streams for individual MLX operations. Independent operations can be scheduled on different devices, but the VLM implementation does not automatically distribute arbitrary model layers between CPU and GPU simply because both resources exist. See the official MLX docs on [unified memory](https://github.com/ml-explore/mlx/blob/main/docs/src/usage/unified_memory.rst) and [streams](https://ml-explore.github.io/mlx/build/html/usage/using_streams.html), and the official [MLX Whisper example](https://github.com/ml-explore/mlx-examples/tree/main/whisper).

## Why check Metal availability?

A startup check for Metal is a guard on the *required VLM backend*, not a ban on CPU work. The check must not reject ordinary CPU preprocessing or OCR. It should reject the run only if the requested MLX/Metal inference backend is unavailable, so a benchmark cannot silently report a failed GPU setup as a successful Metal run.

## Best way to improve CPU/GPU overlap

Start with the high-value independent work: CPU Whisper concurrently with VLM inference. For additional VLM-stage overlap, use a bounded producer/consumer pipeline: CPU decodes and prepares the next frame batch/OCR/prompt while the GPU processes the current batch. Keep the queue small to control unified-memory pressure on the 32 GB M1 Max. Do not try to offload arbitrary transformer layers to CPU without profiling; that can slow inference due to synchronization and contention.

Benchmark at least:
- total wall time;
- Whisper CPU interval;
- VLM Metal interval;
- their measured overlap;
- scene-analysis quality and any changes from missing ASR context during early scene prompts;
- peak memory pressure.

A run is accepted only when telemetry confirms Whisper's CPU placement, MLX/Metal availability and actual overlap. A Metal smoke test confirms the backend can execute; by itself it does not prove that every VLM operation ran on GPU.
