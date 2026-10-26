# MLX/Metal VCE runtime with mlx-whisper CPU-stream overlap

## Target architecture

This overlay prepares a backup-safe patch for the customized local engine in `~/VideoContext-Engine`. The custom engine is not checked into this overlay repository; do not replace it with upstream VCE v3.19.

| Component | Required runtime | Enforcement |
|---|---|---|
| Whisper ASR | `mlx-whisper` on a thread-local MLX CPU stream | `mx.new_thread_local_stream(mx.cpu)` scoped by `mx.stream`; synchronization targets that stream only |
| Scene VLM | `mlx-vlm` + MLX GPU / Metal | configure and smoke-test MLX GPU before VLM load |
| Final text analysis | MLX-backed text generation through the customized source's MLX-VLM path | configure and smoke-test MLX GPU in the text-analysis call path |
| Parallelism | CPU-stream ASR while VLM scene analysis executes on Metal | asynchronous ASR worker plus measured overlap interval |
| CPU assistance | CPU decode, frame preparation, OCR, prompts, I/O and orchestration remain available | independent CPU work should overlap GPU inference where the source pipeline permits it |

## Implementation files

- `scripts/apply_vce_mlx_metal_patch.py`: source-aware patcher, dry-run default, compiles the candidate source before writing, creates timestamped backups on apply. Current v2 marker uses `mlx-whisper` CPU stream rather than PyTorch/OpenAI Whisper CPU.
- `scripts/mlx_metal_runtime.py`: reusable MLX/Metal diagnostics, CPU-stream `mlx-whisper` helper, and generic CPU-ASR/GPU-stage overlap helper.
- `scripts/verify_mlx_metal_runtime.py`: checks packages and runs the Metal smoke test.
- `tests/test_vce_mlx_metal_patcher.py` and `tests/test_mlx_metal_runtime.py`: hardware-independent source patcher and stream-placement tests.

## Model selection and loading

The patch maps VCE's `large` preset to `mlx-community/whisper-large-v3-mlx` and `large-v3-turbo` / `turbo` to `mlx-community/whisper-large-v3-turbo`. Other common preset names map to corresponding MLX Community checkpoints. A local model path or explicit Hugging Face repo ID can be supplied with `VIDEOCONTEXT_MLX_WHISPER_MODEL`.

`mlx_whisper.transcribe()` uses a model-holder cache and loads weights lazily on first transcription. Therefore, the first-call inference timing includes the checkpoint's first load. RAM-minus mode explicitly attempts to clear the MLX Whisper model holder after transcription. Concurrent ASR calls are serialized around that process-wide cache to avoid concurrent cache switches.

Install `mlx-whisper` in the exact Python environment running the API. The source snapshot imports OpenAI Whisper elsewhere at module import time, so keep that dependency until the target source can be safely refactored to remove that import. Do not upgrade every live dependency blindly; resolve to a compatible lockfile/environment first.

## CPU/GPU stream semantics

A CPU stream is not Metal execution. It is MLX compute on the CPU, deliberately separate from the GPU/Metal stream for VLM/LLM. MLX documentation confirms independent operations can be assigned to CPU and GPU streams and executed concurrently: [devices and streams](https://ml-explore.github.io/mlx/build/html/python/devices_and_streams.html), [unified memory](https://github.com/ml-explore/mlx/blob/main/docs/src/usage/unified_memory.rst).

GPU selection is only strict for the VLM/LLM neural inference path; it does not prevent CPU preprocessing, OCR, Whisper or orchestration from running. MLX does not autonomously redirect arbitrary VLM transformer layers to idle CPU cores. Additional producer/consumer prefetch of scene decode/OCR should only be added after checking the exact local loop and profiling memory/speed; the patch preserves that loop rather than guessing at invasive refactors.

## Local verification

The customized engine/settings exist only on the target Mac, so the source apply and live runtime benchmark cannot be performed by GitHub Actions. After a clean dry-run, apply the patch on the Mac, restart the service, run `python scripts/verify_mlx_metal_runtime.py` in the API's actual environment and process one benchmark video.

Acceptance requires: VLM and final text analysis report MLX/Metal; Whisper reports `mlx-whisper` / `mx.cpu`; the timing JSON records positive Whisper/VLM overlap; transcripts and scene summaries remain correct; total elapsed time and memory pressure are compared against the baseline. Do not merge/release as runtime-verified based only on compile/unit tests.
