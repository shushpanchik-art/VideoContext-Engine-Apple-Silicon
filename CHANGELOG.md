# Changelog

## Unreleased — ASR/OCR reconciliation

- Added an optional patcher that exposes the name-reconciliation prompt in the settings UI for extended builds with Qwen3.5 text post-processing.
- The prompt is persisted as text_llm_name_reconciliation_prompt and appended to the actual text post-processing instruction, while original transcript/OCR/timestamps remain protected by the engine's fixed rules.
- Added a qualitative audit documenting remaining summary inconsistency for standard identifiers. A single different-video sample is not treated as a controlled quality comparison.
- Strengthened the editable default prompt with a final cross-field consistency check.
- Added optional OCR-gated v2 runtime patch: normalize a known СП-number alias across derived fields and the base summary while preserving raw evidence.


## 0.1.0 — Apple Silicon patch set

- Aspect-ratio-safe VLM preprocessing.
- Apple Vision OCR at original source-frame resolution; OCR text passed as a grounding hint to Qwen and included in TXT/JSON.
- Persistent JSON settings and a Web UI settings panel.
- Whisper acceleration toggle and MPS/CUDA/CPU device selection.
- Word-level timestamps, per-scene speech grouping/audio features, and Qwen audio context.
- Whisper DTW MPS fix (`x.cpu().double()` before NumPy conversion).
- Batch video selection with per-file status, sequential processing, cooperative cancel.
- TXT/JSON autosave beside the source video through native macOS file selection.
- macOS `.app` launcher, `/Applications` installer, desktop alias.
- Benchmarked a quality/speed profile for Apple Silicon on lecture/presentation footage.
