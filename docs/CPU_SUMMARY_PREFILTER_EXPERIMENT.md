# CPU fact prefilter: phase-1 experiment

> **Status (2026-10-26): superseded for performance work.** The small CPU model completed a one-scene smoke test but took 106.117 seconds to generate at most 64 tokens on the test machine. The 3-chunk run was stopped by the user after more than 11 minutes. Do not rerun the generative CPU prefilter for this optimization experiment. Use [CPU_STRUCTURAL_INDEX_AB_EXPERIMENT.md](CPU_STRUCTURAL_INDEX_AB_EXPERIMENT.md) and the saved `evidence_checkpoint.json` instead.

## Goal

Test the hypothesis that a small CPU model can turn long scene evidence into compact, source-linked fact cards, allowing the large GPU text model to prepare a better structured final summary from less input.

This is a **local feasibility A/B test**, not yet a live-concurrency patch. It runs one normal analysis against the local VCE API and holds the response only in memory. Then it runs:
1. A small MLX-LM model forced to the CPU in a separate Python process, on groups of scene records.
2. The same large MLX-LM model on the full scene evidence and on the generated fact cards.

Keeping CPU inference in a separate process prevents the CPU device selection from mutating the default MLX device inside the running VCE process. The script does not edit the installed engine or the local settings file.

## What this test measures

- CPU model load time and fact-card inference time.
- Large model load time and summary generation time for both context variants.
- Prompt character/token counts and context reduction.
- The existing VCE summary, two candidate summaries and fact cards for manual quality comparison.
- Scene IDs cited by the preprocessor, and whether its output is parseable JSON.

A good result is not just a shorter prompt. Check that names, numbers, standards, chronology, contradictions and uncertainty remain supported by the source scenes. The report is local-only because generated cards and summaries still contain video-derived information.

## Important limitation

The current endpoint returns only after the existing full VCE pipeline has completed. Therefore this first experiment does **not** measure CPU/GPU overlap or end-to-end acceleration from concurrency. It tests whether the proposed small-model preprocessing is fast enough, reduces the final context, and preserves summary quality. If the quality and timing results are promising, the next step is an opt-in engine patch that starts transcript-card preparation as soon as Whisper finishes while VLM scene analysis is still running.

## Requirements

- macOS on Apple Silicon.
- Running local VCE at http://127.0.0.1:7555.
- The benchmark helper and this experiment script in the same directory.
- MLX and MLX-LM importable in the worker Python. The script checks this before it starts the expensive VCE analysis. By default it uses the discovered project’s .venv/bin/python, but `--worker-python` can select a separate environment.
- Access to the selected MLX models in the local Hugging Face cache or internet access for the first model download.

Defaults:
- CPU prefilter: mlx-community/Qwen2.5-1.5B-Instruct-4bit
- Large GPU summary: model ID reported by the current VCE text_llm timing, falling back to mlx-community/Qwen3.5-9B-MLX-4bit

Model IDs may be overridden with --small-model and --large-model.

## Run from Terminal

Download both scripts into the same directory, then run the experiment and select a representative long video:

    curl -fL "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/vce-parallel-cpu-asr-vlm-20261025/scripts/benchmark_vce_macos.py" -o "$HOME/Desktop/benchmark_vce_macos.py" &&
    curl -fL "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/vce-parallel-cpu-asr-vlm-20261025/scripts/benchmark_vce_summary_prefilter.py" -o "$HOME/Desktop/benchmark_vce_summary_prefilter.py" &&
    VIDEO="$(osascript -e 'POSIX path of (choose file with prompt "Выберите длинное тестовое видео")')" &&
    python3 "$HOME/Desktop/benchmark_vce_summary_prefilter.py" --video "$VIDEO" --chunk-scenes 6

At the confirmation prompt, enter RUN. Do not run other VCE analyses concurrently.

Optional overrides:

    python3 "$HOME/Desktop/benchmark_vce_summary_prefilter.py" \
      --video "$VIDEO" \
      --small-model "mlx-community/Qwen2.5-1.5B-Instruct-4bit" \
      --large-model "mlx-community/Qwen3.5-9B-MLX-4bit" \
      --chunk-scenes 6 \
      --max-input-tokens 48000

If the preflight reports that MLX-LM is unavailable, do not install packages into the VCE environment blindly. Create a separate worker environment so its MLX / Transformers dependency versions cannot alter the working VCE stack. Use the project virtualenv's Python as the base interpreter (adjust the project path if your checkout is in Downloads):

    "$HOME/VideoContext-Engine/.venv/bin/python" -m venv "$HOME/.venvs/vce-summary-prefilter"
    "$HOME/.venvs/vce-summary-prefilter/bin/python" -m pip install -U pip mlx-lm
    "$HOME/.venvs/vce-summary-prefilter/bin/python" -c "import mlx.core; import mlx_lm; from mlx_lm import load, generate; print('MLX-LM OK')"

Then rerun the command with:

    --worker-python "$HOME/.venvs/vce-summary-prefilter/bin/python"

This environment is used only for the small CPU prefilter and the standalone large-model A/B calls. The currently running VCE service continues to use its own Python environment. The script checks the imports before running VCE again, so a missing dependency does not waste another full analysis.

Before running another long video, it is possible to test model loading and one short inference without contacting VCE:

    printf '%s' '{"chunks":[[{"scene_id":1,"start":0,"end":1,"audio":"Проверка: температура 20 градусов.","visual":"На экране таблица.","ocr":"20 °C","visual_tags":{}}]]}' | "$HOME/.venvs/vce-summary-prefilter/bin/python" "$HOME/Desktop/benchmark_vce_summary_prefilter.py" --_worker-task prefilter --_worker-device cpu --_worker-model "mlx-community/Qwen2.5-1.5B-Instruct-4bit" --_worker-max-tokens 64

This smoke test loads the small model and extracts one synthetic fact card, but it does not run a VCE analysis. Worker errors now include the exception message to help diagnose incompatible model/runtime APIs. The Python API uses `mlx_lm.sample_utils.make_sampler(temp=0.0)` and passes `sampler=` to `generate()`; do not pass `temp=` directly to the Python `generate()` call.

To resume after a model-stage failure without repeating VCE, pass --save-evidence-checkpoint on the initial run. After the baseline completes, the script saves evidence_checkpoint.json in its output directory. To resume, use:

    python3 "$HOME/Desktop/benchmark_vce_summary_prefilter.py" --resume-evidence "$HOME/Desktop/VCE-Summary-Prefilter-YYYYMMDD_HHMMSS/evidence_checkpoint.json" --worker-python "$HOME/.venvs/vce-summary-prefilter/bin/python"

Resume mode skips the VCE API call and does not require --video. The checkpoint contains raw transcript, scene descriptions and OCR, so it is sensitive local data; it is created with owner-only permissions. Delete the checkpoint when it is no longer needed.

## Output and privacy

Creates VCE-Summary-Prefilter-<timestamp> under Desktop (or --report-dir) containing:
- summary.md: timings and both candidate summaries for manual review.
- report.json: timing metadata, fact cards and both generated candidate summaries.

By default, raw transcripts, raw OCR, raw scene descriptions, the source path, source filename and prompts are not written to disk. If --save-evidence-checkpoint is used, the checkpoint intentionally stores raw scene transcript/visual/OCR evidence to make the experiment resumable; it does not store the source path or filename. The checkpoint is created with owner-only permissions and should be deleted when no longer needed. Generated fact cards and candidate summaries are written locally and may reveal video content; do not upload the folder to GitHub or share it unless you have reviewed it.

The experiment script has no report-publication option and sends the video only to the local VCE API. MLX may download model weights from the configured Hugging Face model source if they are not already cached.

## Interpreting the result

1. Compare the candidate summaries against the existing VCE summary. Check technical terms, numbers, dates, source attribution and omissions.
2. Compare the full-evidence input token count with the fact-card input token count.
3. Compare large-model generation times, and include CPU preprocessing time when assessing total added work.
4. If the baseline prompt exceeds --max-input-tokens, that candidate is explicitly marked skipped_input_over_limit; the prompt is not silently truncated.
5. Do not infer concurrency speedup from this first-stage result. End-to-end concurrency requires a later engine integration and a same-video A/B run with identical settings.
