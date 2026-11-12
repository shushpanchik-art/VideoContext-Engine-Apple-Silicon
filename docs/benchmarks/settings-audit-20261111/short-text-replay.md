# Short Text replay: 54 completed calls, no universal quality winner

Snapshot: 2026-11-11 01:57:51 UTC. This is a completed Text-only comparison stage, not 54 full-video benchmarks and not completion of the settings audit. Long-context Text and fixed-frame VLM comparisons remain separate ongoing work and are excluded from the completed results here.

## Method and conditions

Three fixed saved inputs (Video A, Video B, Video C), six conditions and three repetitions per condition produced 54 outputs. The saved raw input is identical across conditions for each video; the condition that removes the existing summary intentionally changes that portion of Text input. This does not rerun scene detection, ASR, OCR or VLM. Source and saved-settings checks remained unchanged for every call.

Conditions are baseline; an opt-in domain profile; an added name-conflict instruction; a 4096 output-token limit instead of the baseline 8192; a request for a 100–160-word summary instead of 50–90; and removal of the existing summary from Text input. The domain condition is anonymized here. No private prompt content, source labels, transcripts, OCR, frames or generated analysis is published.

Read-only semantic review covered all 54 saved results in 18 groups, reusing existing pixel-grounded checks. The principal derived sections were reviewed rather than treating unchanged raw fields or an original summary as newly recovered facts. A number appearing in text is not sufficient: its object, role, unit, relation and comparison operator must also be correct. Missing facts that were never present in the fixed input were not required to be invented.

## Formatting and instruction compliance

All 54 final outputs were formally successful. The metrics observed no repair phase for these calls; that observation is not a claim that the raw generation contained only JSON. Non-JSON prefix text was recorded. Formatting success does not establish factual correctness, complete coverage or compliance with every instruction.

The 4096 limit produced nine of nine successful short outputs. That does not establish quality equivalence to 8192, safety for long outputs, or a causal speed benefit.

The requested 100–160-word summaries contained only 61–77 whitespace-separated words across all nine responses. Zero of nine met the requested range. This deterministic count is operational, not a language-aware count or a model-token count. Requesting longer summaries did not reliably restore missing quantitative evidence.

## Semantic findings

No condition is a universal quality winner across the three inputs and repetitions.

- Video A: the domain profile consistently improved one classification-system entity type in all three repetitions. Complete table relationships were still not restored. Name-conflict instructions showed isolated first-repeat gains that did not reproduce across all repetitions. Name attribution, semantic roles and object/value binding remained unreliable.
- Video B: placeholders were repeatedly described as entered content, and open menus as selected actions. Quantities could be attached to the wrong dimension, operators reversed, or existing source conditions narrated as newly edited values. Longer summaries and reduced output limits did not resolve these UI-state or relational errors.
- Video C: removing the existing summary consistently improved an entity's type in the entity list across three repetitions, but narrative classification remained inconsistent. Names, source attribution and a linked quantitative condition remained problematic. Generic mention of a relation does not count as preserving its complete bound requirement.

An uncertainty note does not repair a contradictory confident assertion elsewhere. Omitting an entity is not automatically a correct taxonomy fix. Removing the summary from this Text input does not establish that global-summary generation should be disabled for every purpose.

## Descriptive replay timings

Seconds below are the recorded Text replay-call elapsed measurements, not full-video client wall time, server request time or exact model-token throughput. Each row has three repetitions. Summary values are taken from the completed metrics; parentheses show minimum–maximum.

### Video A

- Baseline: median 92.646 s (92.089–92.917).
- Domain profile: median 94.479 s (91.003–97.415).
- Name conflict instruction: median 93.678 s (90.432–96.552).
- Output limit 4096: median 92.423 s (87.946–95.435).
- Summary request 100–160 words: median 93.842 s (92.244–99.366).
- Without existing summary: median 92.544 s (84.719–96.294).

### Video B

- Baseline: median 72.560 s (70.545–74.311).
- Domain profile: median 70.750 s (68.053–73.713).
- Name conflict instruction: median 74.840 s (71.110–84.691).
- Output limit 4096: median 70.786 s (70.559–77.190).
- Summary request 100–160 words: median 74.330 s (72.432–76.775).
- Without existing summary: median 74.601 s (71.125–76.361).

### Video C

- Baseline: median 73.877 s (72.594–73.995).
- Domain profile: median 80.472 s (77.185–82.117).
- Name conflict instruction: median 77.722 s (74.138–82.673).
- Output limit 4096: median 75.877 s (73.433–81.122).
- Summary request 100–160 words: median 79.237 s (77.158–82.747).
- Without existing summary: median 82.857 s (70.866–89.355).

Overall system load varied substantially during the chronological experiment. Fixed inputs and alternating order do not isolate small elapsed-time differences as effects of a prompt or setting. Three repetitions per input/condition are not a controlled performance-equivalence study. Sparse progress token counts are not exact final token totals and are not used to claim generation throughput.

## Publication boundary and next work

The companion JSON contains allowlisted aggregate counts and timings only. The installed runtime provenance is recorded in [the earlier stage report](README.md). The completed full-video baseline is a separate [draft PR #54](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/54); it is not assumed merged or included in this branch.

The practical next targets are UI-state grounding, exact relational/numeric coverage and source provenance, followed by review of the separate long-context and VLM comparisons. No experimental setting is applied to saved preferences, no broad optimality claim is made, and no merge or branch deletion is part of this publication.
