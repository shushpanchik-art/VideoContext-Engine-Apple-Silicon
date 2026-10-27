# Text LLM quality audit: universal core

## Design requirement

VCE processes arbitrary videos: demonstrations, meetings, lectures, interviews, tutorials, presentations, gameplay and other material. The default quality gate must not depend on subject matter, product names, ending-card text, language-specific keywords or assumed outcomes.

The tool has two distinct responsibilities:

1. **Structural integrity** — deterministic checks that apply to every VCE output.
2. **Semantic grounding** — whether claims in the generated report are actually supported by source audio, OCR and visual evidence. This is content-dependent and cannot be proven by matching a few hard-coded words.

Never present a structural score as a semantic-quality score.

## Default profile: generic

The generic profile validates:

- The decoded output is a JSON object and required VCE fields are present.
- Each per-scene enrichment references an existing source scene_id.
- The scene_enrichment collection covers every source scene exactly once, unless the VCE schema contract is intentionally changed.
- Selected key_points use valid scene IDs, but they are not required to mention every scene.
- Timestamps are parseable and, when present, are compared against source start times.
- Output is not empty and does not contain Unicode replacement characters or forbidden control characters.
- Benchmark JSON repair, parse failure, token-cap and source-mutation diagnostics.

These are schema/integrity invariants, not content rules. Generic mode does not automatically assert that a report is true, complete in every semantic detail, or safe to use without review. It explicitly reports that semantic/domain grounding is not evaluated.

There are no implicit source paths. The caller must pass --results-dir and --source-json, so the tool does not silently analyze one developer's sample.

## Optional profile diagnostics

The CLI keeps nsr-case-study as an opt-in profile for reproducing diagnostic observations from one historical benchmark. It includes lexical and number/identifier heuristics tailored to that dataset. These checks must never be applied to other videos by default or described as universal product rules.

If a future domain requires specialized validation, it should be:
- a separately named and explicitly selected profile;
- documented with supported content types and limitations;
- tested on positive and negative examples from that domain;
- kept out of generic status decisions unless the caller requests it.

## Content must not be rewritten by keyword rules

The post-parse engine guard and benchmark guard are non-mutating. They flag schema/integrity issues, but do not replace text because it contains a particular word, acronym or title. This avoids deleting a legitimate spoken claim, inventing a conclusion, or breaking an unrelated video.

A deterministic structural check is not a universal semantic evaluator. For semantic grounding, use a separate source-aware evaluation stage that:
1. extracts claims from the generated summary and scene descriptions;
2. links each claim to the relevant source scene and modality (audio/transcript, OCR or visual description);
3. evaluates support, contradiction and uncertainty without assuming what the video is about;
4. distinguishes “not supported by the visible frame” from “not stated in the audio”;
5. fails closed or asks for manual review when the evaluator is uncertain;
6. is itself tested across multiple unrelated videos, languages and domains.

A future semantic evaluator may be model-based, but its output should be an evidence-linked set of findings, not an opaque numerical score alone. It must not be conflated with the current generic structural pass.

## Status meanings

- HOLD: structural or text-integrity blocker; do not adopt that run.
- REVIEW: no structural blocker was found, but warnings remain or semantic grounding was not evaluated.
- MANUAL_REVIEW_REQUIRED: no blocker was detected by the selected profile; this is not proof of semantic correctness.

Runtime is only a tie-breaker after the required quality checks pass. The fastest candidate is not selected if a generic integrity flag exists.

## Current CLI

    python tools/quality_check_text_llm_variants.py --results-dir /path/to/benchmark-run --source-json /path/to/context.json

To reproduce the old NSR-specific diagnostics, opt in explicitly:

    python tools/quality_check_text_llm_variants.py --results-dir /path/to/benchmark-run --source-json /path/to/context.json --profile nsr-case-study

The source JSON is read locally. The audit writes its JSON/Markdown result into the selected run folder by default and does not upload the source payload.

## Source-aware grounding screen: lexical evidence pointers, not semantic scoring

The separate `tools/evaluate_text_llm_grounding.py` tool is an additional diagnostic step after the structural audit. It is universal by default and uses the source context JSON supplied by the caller; it contains no NSR/product/topic vocabulary.

It extracts report-facing text from the executive summary, selected key points, topics, entity names and per-scene enrichment. Each output phrase is compared with text evidence in the source context:
- Summary-level claims are compared with text evidence across the video.
- Scene-level claims are compared first with evidence from their referenced scene. Whole-video overlap is reported separately for context, but it is not alone enough to flag a possible scene mismatch. That cue requires meaningful lexical overlap with at least one individual other scene (at least two matched claim tokens and a minimum 0.30 token-recall floor, or the stricter configured threshold). This avoids combining unrelated words scattered across multiple scenes into a false mismatch cue.
- When a possible scene mismatch is detected, the report points to the best-matching other scene as well as retaining the whole-video diagnostic. Evidence pointers still suggest what a human should inspect; they do not prove entailment.
- Evidence matches retain pointers to source scene ID, source field path and a coarse modality group (audio/transcript, OCR, or visual description/tags).
- The evidence allowlist is limited to source-modality fields such as `audio_transcript`, OCR fields, `visual_description` and `visual_tags`. It explicitly excludes `segments[].text_llm.*`, model-generated topic/key-point/enrichment fields, `meta.global_summary`, and other derived summaries. This prevents the evaluator from using a generated Text LLM claim as its own evidence.
- Variant summaries show both the number of low-overlap review cues and their percentage of extracted claims, so the cue count is not compared without accounting for differing output lengths.
- The Markdown/console report is compact by default and lists claims with diagnostic findings only; the JSON preserves all extracted claims and evidence pointers. Use `--all-claims` to include every claim in the Markdown/console table.
- The table distinguishes low-overlap cues, possible scene-scope mismatch, missing textual source evidence, and ordinary lexical overlap. A row omitted from the compact view is not approved; it only had no lexical review cue.
- When overlap is low, the tool emits a manual-review cue and shows the closest textual evidence candidates. It does not label the claim false and it never edits output text.

This is deliberately a **lexical overlap screen**, not a semantic entailment evaluator. It cannot reliably recognize paraphrase, translation, implication, contradiction, sarcasm, speaker attribution, visual reasoning, or whether a copied phrase is interpreted correctly. Low overlap can be a valid paraphrase or a source-context omission; high overlap can still be a false or misattributed claim. It does not inspect raw video frames or audio waveforms. The report's `semantic_quality_score` is always `null`, and every claim remains marked for manual review.

Run both stages locally against the same historical benchmark folder:

```bash
python tools/quality_check_text_llm_variants.py \
  --results-dir /path/to/benchmark-run \
  --source-json /path/to/context.json \
  --profile generic

python tools/evaluate_text_llm_grounding.py \
  --results-dir /path/to/benchmark-run \
  --source-json /path/to/context.json
```

The second tool writes `quality_grounding_audit/quality_grounding_audit.md` and `quality_grounding_audit.json` in the run folder by default. `--min-token-recall` changes only the sensitivity of the low-overlap review cue; it is not a truth threshold. Both reports stay local and neither tool uploads source JSON or changes generated output.

A proper semantic evaluator remains future work. It should use an evidence-linked verifier that can assess support, contradiction, modality and uncertainty on multiple unrelated videos and languages. Its assertions must be calibrated against human-labeled positive and negative examples before being used as an acceptance gate. Do not promote any candidate based on structural or lexical overlap scores alone.

