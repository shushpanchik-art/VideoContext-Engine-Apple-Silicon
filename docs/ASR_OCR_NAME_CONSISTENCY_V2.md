# ASR/OCR name consistency guard v2

## Why

The supplied 108.23-second sample has a clear OCR reading `СП 1.13130.2020`, while ASR-derived text still leaves the variant `СП 1.13.132.020` in `topics`. A prompt-only final sweep was not sufficient for this known variant.

## What the patch does

Run `patch_vce_name_consistency_v2.py` on an extended local VCE v3.19 project that contains `run_text_llm_postprocess`, `reconcile_text_llm_result`, and the `TEXT_LLM_POSTPROCESS_STAGE` anchor.

- Adds a final instruction to align canonical names in generated summaries, topics, key points, entities, and scene descriptions.
- Adds deterministic correction for the known СП identifier aliases only when OCR contains the canonical `СП 1.13130.2020`.
- Applies the same guarded correction to `global_summary_text`.
- Leaves `audio_transcript`, `ocr_text`, `visual_description`, timestamps, and `original_summary` untouched.
- Creates timestamped backups and is safe to re-run.

## Limits

This is an allow-listed fix for a confirmed error, not fuzzy matching for arbitrary standards or brand spellings. Other proper names are resolved through the prompt evidence hierarchy; add new aliases only after a real example confirms them.

## Run

```bash
python3 patch_vce_name_consistency_v2.py --project "$HOME/VideoContext-Engine" --dry-run
python3 patch_vce_name_consistency_v2.py --project "$HOME/VideoContext-Engine"
```

Restart the app before the next analysis. The runtime canonicalizer logs `[ASR/OCR]` if it changes an alias.
