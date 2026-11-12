# Text output caps: generation outcomes and causal postprocessing replay

This completed stage contains nine new long-input Text calls and 24 model-free postprocessing replays on six accepted repaired outputs. These are different experiments: generation timing uses a historical comparator, while replay holds each accepted raw output identical to isolate deterministic parser/postprocessor effects. No setting was applied to the application.

## Generation method and outcome

The fixed private input is Video D with 90 source scenes. Delivered context remains 55,938 characters under the 47,000-character soft setting and 80-scene cap, with transcript/OCR/visual field limits of 140/160/160. The output-token limit remains 8192. Generation temperature remains 0.1 and repair temperature 0.0. Three repetitions test each new key-point/topic cap combination:

- 36 key points / 15 topics: 3/3 format successes after repair; median all-attempt time 431.648 seconds; range 430.802–432.483.
- 18 key points / 30 topics: 3/3 format successes after repair; median 435.525 seconds; range 429.014–442.398.
- 36 key points / 30 topics: 0/3 successes, including repair; median 469.055 seconds; range 468.943–470.760.

All nine primary generations were invalid. The six successful final outputs became parseable only after model repair. This must not be described as six successful first-pass JSON generations. Failed structured content is not invented or reconstructed.

The historical 18/15 comparator had 3/3 formal successes and a median of 260.074 seconds. It was not concurrent or randomized with these new runs. Order and system-state differences confound that comparison; timings include repair costs and are descriptive, not causal speed estimates or evidence that a specific cap alone caused the difference.

## Same-output replay isolates the truncation mechanism

Each of the six accepted repaired raw outputs was replayed through four cap combinations, producing 24 pure parser/postprocessor evaluations with no new inference. Original parser results and original final key-point/topic arrays were reproduced. This evidence isolates cap behavior on the same generated content; it does not measure model variability or prove semantic quality.

The accepted repaired payloads contained more key points than requested: 26/59/36 for the 18-point condition and 64/78/64 for the 36-point condition. A prompt maximum is therefore not an enforced generation boundary.

The observed sequence is:

1. The parser keeps the first configured number of entries in generated order.
2. Reconciliation adds candidates derived from the raw source and deduplicates them.
3. The resulting points are ordered chronologically and capped again. Added early points can displace later generated points.

On identical accepted outputs, the 36-point cap extends the last retained source scene in all six cases: the 18-point cap ends at scene 22–31, while the 36-point cap ends at scene 42–63. Raising the topic cap changes topic counts, not key-point arrays on the same raw output. This separates the deterministic cap effect from differences between independently generated answers.

Some additional middle-input content is supported by the retained narrative, but it was not newly pixel-validated. More entries or later timestamps do not establish correct, complete technical synthesis.

## Provenance and quality boundary

Five of the six accepted repaired payloads contain no scene enrichment before postprocessing. Their final two boundary enrichments are demonstrably created by postprocessing, not by the model. The remaining accepted payload contains 18 model-provided enrichments and ends with 19 after postprocessing. These attributions are directly supported by captured stage boundaries and are stronger than earlier fallback-compatible observations without such boundaries.

The clipped context does not supply several complete late numerical and conditional facts. Increasing output caps cannot recover missing input evidence without invention. Chronological trimming also discards later available points. Final-third coverage, names and source attribution remain inadequate; structural extension is not full quality acceptance.

The public aggregate retains counts at accepted raw, parser and final stages, not private generated text or source-specific values. Empty counts for failed attempts mean unavailable structured output, not a semantic pass or a measured zero-coverage result.

## Integrity and decision

Source and saved settings remained unchanged in all runs. The recorded 138 resource snapshots showed AC power and no thermal/performance warning. No new swap or system-load measurements support a controlled timing claim.

Do not adopt 36/30 or promote the 30-topic cap as a quality optimization. The 36/15 condition demonstrates reproducible structural extension, but still requires repair in all three runs and remains incomplete. No new preset or universal optimum is established. Further changes to chronological selection or context clipping require a separate design/code task and validation; this report does not implement them or start more inference.

This publication excludes private primary/repair text, source names, paths, frames and source-specific numerical facts. Other audit stages retain their own limitations. No merge or branch deletion is part of publication.
