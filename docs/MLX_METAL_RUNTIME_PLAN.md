# MLX + Metal runtime mode with CPU Whisper overlap

## Target architecture

This branch adds integration helpers for the customized local VCE engine under ~/VideoContext-Engine. The customized engine and its final-summary worker are not checked into this overlay repository, so the helpers are not yet wired into the production application. Do not replace that customized source with upstream v3.19.

| Component | Required runtime | Enforcement |
|---|---|---|
| Whisper ASR | OpenAI Whisper / PyTorch CPU | load_whisper_cpu passes device="cpu" and checks model placement |
| VLM | mlx-vlm + MLX GPU / Metal | configure_mlx_gpu before mlx_vlm.load |
| Final-summary LLM | mlx-lm + MLX GPU / Metal | configure_mlx_gpu in the summary worker before mlx_lm.load |
| Overlap | CPU Whisper while GPU VLM scene pass runs | run_whisper_cpu_parallel_with_gpu(asr_fn, vlm_fn) |

On supported Apple Silicon builds, MLX GPU execution uses Metal. This overlay selects the GPU explicitly and fails closed instead of silently accepting CPU fallback. The smoke operation evaluates and synchronizes a tiny array; it proves that the MLX GPU path is callable, not that every application operation is GPU-bound. Per-stage telemetry is still required.

## Files

- scripts/mlx_metal_runtime.py: strict GPU setup, CPU-only Whisper loader, and CPU/GPU overlap primitive.
- scripts/verify_mlx_metal_runtime.py: reports installed runtimes and runs the GPU smoke test.
- tests/test_mlx_metal_runtime.py: hardware-independent unit tests with mocked MLX plus Whisper placement and overlap tests.

## Setup on the M1 Max

Use native arm64 Python in every environment that actually imports models. Installing a package into a different environment does not change the runtime of an already-running API process.

~~~bash
cd ~/VideoContext-Engine
source .venv/bin/activate
python -c 'import platform,sys; print(platform.machine(), sys.executable)'
python -m pip install -U mlx mlx-vlm mlx-lm openai-whisper
~~~

The customized engine path shown in the source snapshot uses `mlx-vlm` both for scene VLM and the final text-analysis call, so its engine environment needs `mlx` and `mlx-vlm`. If the newer local branch has replaced the final summary with a standalone `mlx-lm` worker, that separate worker must call the same GPU setup before `mlx_lm.load()`; do not assume setting the device in another process configures it. Resolve dependencies against the project's current pinned versions before upgrading the live service.

## Wire into the customized engine

### VLM process

Call the helper before loading the model:

~~~python
from mlx_metal_runtime import configure_mlx_gpu
runtime_info = configure_mlx_gpu()
from mlx_vlm import load, generate
model, processor = load(model_path, trust_remote_code=True)
print("[VCE runtime]", runtime_info)
~~~

The upstream source uses mlx_vlm in its MLX VLM engine. The customized local source must call the helper in that same process before the load call.

### Summary LLM worker

At the start of the worker process, before mlx_lm.load(model_id):

~~~python
from mlx_metal_runtime import configure_mlx_gpu
runtime_info = configure_mlx_gpu()
from mlx_lm import load, generate
model, tokenizer = load(model_id)
print("[VCE summary runtime]", runtime_info)
~~~

Default-device selection must happen in the worker process before model load. Keep the generation calls and sampling logic already used by the local worker; this helper only selects the device and does not rewrite prompts or model generation.

### Whisper CPU

Replace every OpenAI Whisper model load in the customized engine with:

~~~python
import whisper
from mlx_metal_runtime import load_whisper_cpu
whisper_model = load_whisper_cpu(whisper, target_whisper)
~~~

The loader forces device="cpu" and checks the model's reported placement. Keep Whisper out of the MLX GPU worker.

### CPU/GPU overlap

After the audio path is available and scene/keyframe preparation is complete, start ASR in the CPU executor while the calling thread runs the MLX VLM scene pass:

~~~python
from mlx_metal_runtime import run_whisper_cpu_parallel_with_gpu

audio_result, visual_result = run_whisper_cpu_parallel_with_gpu(
    whisper_cpu_fn=lambda: whisper_model.transcribe(
        temp_path,
        condition_on_previous_text=False,
        temperature=0.0,
        compression_ratio_threshold=1.8,
    ),
    gpu_fn=lambda: analyze_scenes_with_mlx_vlm(scenes_raw),
)
~~~

Merge transcripts into scenes after both results return, then run the final-summary worker. This overlaps CPU ASR with the GPU VLM stage; it does not prove that VLM and final LLM should run simultaneously. On a 32 GB M1 Max, avoid loading multiple large GPU models at once unless benchmarked evidence confirms adequate headroom.

This is an integration layer, not a complete patch to VideoContextEngine_v3.19.py: the customized engine file and summary worker are local-only. They must be patched and tested against their exact current contents before the application can be described as fully migrated.

## Verify

~~~bash
python scripts/verify_mlx_metal_runtime.py
python -m unittest discover -s tests -p 'test_mlx_metal_runtime.py' -v
~~~

The verifier fails when MLX GPU is unavailable and reports package presence. Runtime integration is complete only after live logs show MLX/Metal for VLM and LLM, CPU for Whisper, and benchmark timestamps demonstrate overlapping intervals.

## Evidence baseline

- Latest corrected pipeline benchmark: results/vce-benchmark/20261026_085101/.
- Structural-index A/B: results/vce-summary-structural-ab/20261026_105858/. That experiment increased token count by 77.82% and generation time by about 41%; do not combine it with the MLX/Metal runtime change.


## Backup-safe source integration

The implementation script `scripts/apply_vce_mlx_metal_patch.py` targets the customized engine in `~/VideoContext-Engine`. It defaults to a dry run, verifies the expected code anchors, compiles the candidate source in memory, and refuses to write when the source version differs. Applying it creates timestamped backups of both the engine and settings.

The patch forces Whisper to CPU, starts its asynchronous transcription while VLM scene processing runs, restores transcript/audio-feature fields to each scene before the final summary, explicitly configures MLX GPU/Metal for VLM and text analysis, and records runtime diagnostics and measured overlap in the timing JSON. Settings are adjusted to CPU Whisper and Metal for MLX.

GitHub Actions verifies the helper and patcher with hardware-independent tests. The actual source apply, service restart, and benchmark must run on the target Apple Silicon Mac, where the local project and model cache exist.
