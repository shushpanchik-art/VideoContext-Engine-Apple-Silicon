# Four-video baseline: execution complete, quality not accepted

This report supersedes only the interim baseline completion count in [the previous stage report](README.md). The detector, OCR and deterministic context-contract results remain separately scoped. This is completion of a baseline execution stage, not completion of the whole settings audit or acceptance of all output quality.

## Evidence accounting

All 12 planned runs completed: four fixed private videos, three repetitions each. Ten runs have retained responses and execution records; two have server-log-only evidence. Among retained responses, Text processing reports nine successes and one failure. Both log-only completions report Text success, but their missing response bodies prevent equivalent factual-quality review. No missing response is reconstructed or silently treated as reviewed.

Video A and Video B each have three retained repetitions. Video C has two retained responses and one log-only completion. Video D has two retained responses and one log-only completion. Video D repeat 2 returned HTTP 200 while Text JSON failed after repair. The final repeat returned successful Text JSON after repair. Successful parsing and complete execution are not equivalent to verified facts or comprehensive coverage.

The source remains the installed runtime SHA-256 `8aa39daa7815ccdd9e6656cb05aa1fe0d5a642fe101b56787a3c2c3d3a52eac7`, with the provenance limitations explained in the previous report. This publication changes no application code or saved settings. The companion JSON contains aggregate measurements only.

## Final long-input repeat

Video D repeat 3 retained 90 scenes and a response plus execution record:

- Client wall time: 2447.354925 seconds.
- Server processing time: 2446.702 seconds.
- Server request time: 2446.966 seconds.
- Text stage: success after repair, 416.770 seconds reported for inference; this is not an independent successful first-pass generation.
- Runtime telemetry verifies Whisper on MPS and VLM and Text on Metal.
- Apple OCR: 0.432 seconds with cache enabled, versus about 97 seconds in the earlier long repeat.

These timing definitions remain distinct. The chronological runs include cache and load differences; they are not controlled cold-to-cold or warm-to-warm A/B measurements. A lower elapsed time does not establish a setting improvement. The earlier short-input medians and ranges retain the same caveat. Log-only server request times must not be pooled into client wall-time medians.

## Quality review of the final long-input response

The review inspected the saved response, reconstructed delivered context using the unchanged source, and used existing pixel-grounded checks. It did not perform new inference. The retained result has only 17 scene enrichments out of 90 scenes. Its 18 top-level key points end at 435 seconds, approximately 30 percent of the 1434.93-second input. This describes where those points stop, not a measured recall percentage or proof that all early content is covered.

Later checked technical facts are absent from the derived analysis. Observed failure categories include missing numeric outcomes and qualifying conditions, incomplete search-condition descriptions, incorrect expansion of terminology, and noisy OCR strings promoted to software identities. The broad executive summary is not a substitute for quantified technical results. Apparent absence of an item in a counted group does not by itself establish a regulatory violation.

The reconstructed context contains 56,176 characters, uses the existing 47,000-character soft budget and 80-scene cap, and omits ten scenes. Per-field clipping remains 140/160/160 characters. Successful JSON repair does not restore evidence omitted or clipped before generation.

The final payload also includes deterministic postprocessing. For more than 80 scenes, missing enrichment is filled only at the first/last boundary; entity links may be expanded using full raw segments. Consequently, an entity linked to a late or omitted scene is not proof that the model received or analyzed that scene. The final payload alone cannot establish model-versus-fallback provenance for every enrichment. Do not describe 17 enrichments as 17 independently verified model outputs or claim whole-video model coverage from those links.

## Limits and next stage

The baseline is now complete by execution count, while quality acceptance remains unmet. The inputs are private; raw media, screenshots, transcripts, OCR strings, generated analysis, personal or customer names and local paths are intentionally excluded. Published aggregates do not enable independent full reproduction without equivalent private inputs and the exact runtime.

Text comparisons and the fixed-frame VLM comparison are a separate ongoing stage. Partial formatting success is not an overall winner, and no unfinished comparison is included among these completed results. No global preset or universal optimum is recommended. Further decisions require semantic review of those comparisons and reconciliation of the remaining parameter-coverage gaps.
