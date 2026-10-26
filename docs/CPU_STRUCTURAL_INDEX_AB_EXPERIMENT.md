# VCE deterministic structural-index A/B experiment

## Purpose

This experiment replaces the failed CPU text-generation prefilter with a deterministic, extractive structure pass. It reuses a saved phase-1 evidence checkpoint, so it does not re-run Whisper, scene detection, OCR, VLM analysis, or the VCE API.

The experiment compares the same large MLX-LM model on:
- **Candidate A:** the current full transcript/visual/OCR evidence context.
- **Candidate B:** a source-linked structural index containing a scene map, unique source strings, exact duplicate references, and literal numeric/date/standard/acronym anchors.

No small generative model runs on CPU. Structural processing uses ordinary Python string handling and regular expressions. It does not paraphrase or semantically summarize evidence. It only groups evidence fields whose contents are identical after whitespace normalization; case, punctuation, and technical symbols are preserved. Every grouped item retains all source scene IDs and timestamps.

## What it measures

- Time required to build the deterministic index.
- Character and tokenizer-count differences between A and B.
- Time for the large model to load once and generate both candidate summaries.
- Scene coverage, number of unique source fields, exact duplicates grouped, and literal anchors extracted.
- Manual quality comparison against the existing VCE summary.

A successful shorter context does not automatically mean a better result. Verify that facts, chronology, uncertainty, attribution and technical details survive. If Candidate B grows the prompt or omits details, do not integrate it.

## Run with the saved checkpoint

Prerequisite: these files should be next to one another on Desktop:
- benchmark_vce_summary_prefilter.py
- benchmark_vce_summary_structural_ab.py

Download the new script:

    curl -fL "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/vce-parallel-cpu-asr-vlm-20261025/scripts/benchmark_vce_summary_structural_ab.py" -o "$HOME/Desktop/benchmark_vce_summary_structural_ab.py" &&
    python3 -m py_compile "$HOME/Desktop/benchmark_vce_summary_structural_ab.py"

Then run it against the existing checkpoint (this is the path from the phase-1 run):

    python3 "$HOME/Desktop/benchmark_vce_summary_structural_ab.py" \
      --resume-evidence "$HOME/Desktop/VCE-Summary-Prefilter-20261026_101906/evidence_checkpoint.json" \
      --worker-python "$HOME/.venvs/vce-summary-prefilter/bin/python"

This skips VCE analysis and CPU generative extraction. It uses the model ID saved in the checkpoint by default and loads the configured VCE summary user prompt if the local project/settings can be located. If needed, pass --project "$HOME/VideoContext-Engine". The --large-model, --max-input-tokens, and --summary-max-tokens options allow overrides.

The script first writes the deterministic index and its statistics, then runs one GPU worker for both candidate summaries. It prints progress lines when available and a heartbeat every 15 seconds, so silence is less ambiguous. The output directory is VCE-Summary-Structural-AB-<timestamp> on Desktop.

## Outputs and privacy

Outputs include:
- structural_index.md: the source-linked index, including transcript, scene descriptions, OCR, and visual tags.
- index_stats.json: counts, source coverage and context size.
- report.json: model timings, prompt token counts and both candidate summaries.
- summary.md: a readable A/B report and manual review checklist.

These files contain video-derived text and candidate summaries. The output directory uses owner-only permissions and files are owner-readable/writable. Review before sharing and delete the folder when no longer needed. The script does not upload files or publish results to GitHub.

## Interpretation and next step

1. Compare full-context and indexed prompt token counts. Context reduction is meaningful only when token counts fall, not merely character counts.
2. Compare Candidate A and B against the existing VCE summary and structural_index.md.
3. Verify exact standards, numbers, dates, units, names and source scene references. Also inspect chronology, causality, contradictions and uncertainty.
4. If the index preserves quality and reduces input/inference time enough to matter, the next experiment can be an opt-in VCE engine integration. That integration would start deterministic structuring after Whisper completes while GPU scene analysis continues, followed by the final summary. This offline A/B test does not itself measure CPU/GPU overlap or end-to-end concurrency speedup.
