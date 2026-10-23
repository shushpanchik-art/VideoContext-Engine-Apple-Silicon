# Changelog

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
