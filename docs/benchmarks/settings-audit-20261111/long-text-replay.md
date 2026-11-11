# Long Text replay: 12 attempts complete, no accepted quality preset

This is the completed long-input Text comparison: four conditions with three repetitions each, using one fixed retained 90-scene input, anonymized as Video D. It is not 12 full-video runs. Scene detection, ASR, OCR and VLM were not rerun for this comparison. Source and saved settings were not changed.

## Method and conditions

All conditions use the same saved raw result. The context builder changes the delivered input according to character and scene limits. The earlier [context-contract report](README.md) describes the deterministic preparation separately; this report adds actual inference outcomes and review of all 12 saved results. Failed payloads are not reconstructed.

- Default: character setting 47,000; scene cap 80; field limits 140/160/160; delivered context 55,938 characters.
- Characters only: character setting 140,000; scene cap 80; field limits 600/650/650; delivered context 121,091 characters.
- Scenes only: character setting 47,000; scene cap disabled (0); field limits 140/160/160; delivered context 62,803 characters.
- Characters and scenes: character setting 140,000; scene cap disabled (0); field limits 600/650/650; delivered context 135,887 characters.

The character setting is a soft budget and also changes per-field clipping. It therefore does not isolate a pure aggregate-length effect. Removing the scene cap admits all 90 source scenes but does not ensure complete derived coverage. The widest condition still clips fields and is not full raw-evidence input.

## Completed reliability and elapsed measurements

All-attempt medians and ranges below include failed attempts and repair costs. These are Text replay elapsed measurements, not full-video client wall time or isolated token-generation throughput.

- Default: 3/3 format successes; median 260.074 seconds; range 190.066–434.555 seconds.
- Chars only: 1/3 format successes; median 549.389 seconds; range 533.999–565.896 seconds.
- Scenes only: 3/3 format successes; median 202.928 seconds; range 196.332–206.147 seconds.
- Chars and scenes: 1/3 format successes; median 561.845 seconds; range 559.861–576.062 seconds.

There are eight formally successful outputs and four failed attempts in total. Each expanded-character group has only one success out of three. Default and scenes-only each have three format successes, but neither provides complete semantic coverage. The sample is too small, and load/order/repair effects too variable, for a general causal reliability or speed claim. Success-only timing must not conceal failures; small differences between medians are not demonstrated setting effects.

## Coverage and semantic review

Default produces 19, 25 and 17 scene enrichments out of 90. Scenes-only produces 16, 20 and 19. In these six successful runs, the last top-level key point falls between source scenes 31 and 41, despite later checked evidence. Admitting all source scenes does not produce a complete long-video technical synthesis.

Each successful expanded-character output has just two enrichments, at the first and last scene. Their style and source alignment are compatible with the deterministic fallback path. The final JSON alone does not establish exactly which repaired items were absent before postprocessing, so these are described as fallback-compatible, not definitively model-generated coverage or conclusively reconstructed fallback provenance.

None of the eight successful outputs preserves the complete checked late-stage count and conditional-constraint rubrics in the principal derived sections. Wider input delivers some relevant count evidence that is then omitted by the output. Other qualifying detail remains excluded by fixed field clipping even at the largest character setting. Generic descriptions, isolated numbers or entity references do not establish complete factual retention.

Additional observed categories include corrupted names promoted to software identities, confusion between source types and source-binding errors where a derived scene description belongs to other content. A possible additional cross-scene drift observation is not asserted as verified here. Numerical source values and identifiable material are deliberately excluded from this report.

## Provenance and limitations

For a 90-scene source, postprocessing fills missing boundary enrichments rather than all missing scenes. Entity references and added key points may use full raw source fields even when the model context omitted scenes. Their presence does not prove the model saw or synthesized the associated evidence.

Semantic review used the saved results, existing source reconstruction and prior evidence checks. Some target evidence was previously checked against frames; another target relied on raw OCR/VLM agreement rather than a new pixel review. This is not fresh independent verification of every source fact. Failed outputs retain unavailable coverage as null in the aggregate JSON, not as zero verified coverage or fabricated semantic results.

The completed baseline stage is documented in [the baseline report](baseline-complete.md). The separate short-input replay is [PR #55](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/55), which remains a separate draft. The fixed-frame VLM calls have finished execution, but their semantic review is ongoing and no VLM quality result is included here.

## Decision

No tested condition yields a reliably complete long-input technical result. Do not adopt a larger character budget or disabled scene cap as a quality improvement on this evidence. The default is more often parseable in this sample, not semantically complete or globally optimal. No new preset is applied or recommended.

This publication contains only anonymized aggregates, settings, methods and error categories. It excludes raw generated text, transcripts, OCR, frames, private names, local paths and source-specific numeric facts. It does not change application code or saved settings, merge another PR, or delete a branch.
