# CPU fact prefilter: phase-1 experiment

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
- MLX-LM available in the VCE virtual environment. The script prefers the discovered project’s .venv/bin/python, falling back to the current Python if there is no such virtual environment.
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
      --max-input-tokens 24000

If the CPU worker reports that MLX-LM is unavailable, activate the VCE virtual environment and check python -m pip show mlx-lm before installing anything.

## Output and privacy

Creates VCE-Summary-Prefilter-<timestamp> under Desktop (or --report-dir) containing:
- summary.md: timings and both candidate summaries for manual review.
- report.json: timing metadata, fact cards and both generated candidate summaries.

Raw transcripts, raw OCR, raw scene descriptions, the source path, source filename and prompts are not written to disk. Generated fact cards and candidate summaries are written locally and may reveal video content; do not upload the folder to GitHub or share it unless you have reviewed it.

The experiment script has no report-publication option and sends the video only to the local VCE API. MLX may download model weights from the configured Hugging Face model source if they are not already cached.

## Interpreting the result

1. Compare the candidate summaries against the existing VCE summary. Check technical terms, numbers, dates, source attribution and omissions.
2. Compare the full-evidence input token count with the fact-card input token count.
3. Compare large-model generation times, and include CPU preprocessing time when assessing total added work.
4. If the baseline prompt exceeds --max-input-tokens, that candidate is explicitly marked skipped_input_over_limit; the prompt is not silently truncated.
5. Do not infer concurrency speedup from this first-stage result. End-to-end concurrency requires a later engine integration and a same-video A/B run with identical settings.
