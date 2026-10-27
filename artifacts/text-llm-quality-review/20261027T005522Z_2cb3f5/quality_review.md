# Design correction: universal quality core

The previous iterations of this review added lexical checks for the NSR example (for example, \`THE END\`, collision/mismatch wording, specific OCR artifacts) and a guard that rewrote the last scene based on these words. **These were sample-specific experiments, not appropriate universal product rules.** The PR has now been refactored so they cannot affect default behavior.

- \`tools/quality_check_text_llm_variants.py\` defaults to \`--profile generic\`. It does not apply video-specific vocabulary or ending-title logic. The \`nsr-case-study\` profile is opt-in only for reproducing diagnostics on this sample.
- Generic mode audits the VCE schema, expected scene IDs, duplicate/unexpected IDs, complete \`scene_enrichment\`, available timestamps, JSON-repair/token-cap indicators, and basic encoding/control-character integrity. The report says semantic/domain grounding is not evaluated by generic checks.
- Technical atom extraction and the lexical “collision/title/OCR” checks are profile-only heuristics. They are not universal facts, not hard-coded product policy, and their numeric coverage should not be treated as a quality percentage.
- \`patches/patch_vce_text_llm_quality_guard.py\` and the benchmark now share a **non-mutating universal validator**. It flags schema/encoding defects without rewriting generated user-facing prose.
- The benchmark no longer reports a domain-specific critical-atom coverage as if it were universal; grounding is recorded as “not evaluated” unless a specific diagnostic profile is explicitly used.
- The checker now requires explicit \`--results-dir\` and \`--source-json\`, rather than defaulting to the NSR video paths.

The earlier benchmark runs and their generated reports are preserved for historical comparison; they used older sample-specific logic and should not be confused with the current generic checker. Re-run only the cheap local audit first; a new model benchmark is unnecessary for validating the default rule set.

---

# VCE Text LLM quality-first review — corrected scene contract

- Run: \`20261027T005522Z_2cb3f5\`
- Video: \`🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4\`
- Input: existing VCE JSON with 14 scenes; the video was not reprocessed for this benchmark.
- Repeats: 3 per variant; 18 generated reports inspected.
- Policy: **quality and completeness gates first; speed second**.
- This file is a human-readable reassessment of the stored outputs, not a claim that a heuristic score proves semantic correctness.

## Important correction to the first audit

The first audit version treated a scene not referenced in \`key_points\` as a hard completeness failure. That is too strict for this schema: the engine prompt defines \`key_points\` as selected summary theses (maximum 18), not one item per source scene. Scene-by-scene completeness is instead measured from \`scene_enrichment\`, whose prompt explicitly requests an entry for every scene.

Inspection of the saved \`llm_outputs.json\` confirms that **all 18 outputs contain 14 unique \`scene_enrichment\` entries covering scenes 1–14**. Therefore, an omitted \`key_points\` reference by itself is informational, not a hard blocker. It must not be confused with a missing per-scene enrichment.

The updated checker in this PR implements this distinction, flags garbled text anywhere in the user-facing output (including scene enrichment), and includes regression tests for the corrected rule. Re-run the checker locally against the private source JSON to produce fresh numerical scores; the initial heuristic scores below are intentionally not repeated as if they had been recomputed.

## Results across the 18 stored outputs

| Variant | Median seconds | Speed result | Findings from stored outputs | Decision |
|---|---:|---|---|---|
| Baseline — Qwen3.5-9B | 84.154 | Reference | 14/14 scene-enrichment entries in every repeat; no obvious \`AND JU\` / \`NC END\` fragments detected by the repeated-output scan. Still requires factual review. | REVIEW |
| A — thinking off | 73.459 | 12.7% faster than baseline | \`AND JU\` appears in the scene-14 key point in 2/3 repeats. | HOLD |
| B — Fast Summary, 9B | 59.011 | 29.9% faster than baseline | \`AND JU\` appears in 2/3 repeats. All three scene-14 enrichments also contain suspicious \`D_\` / \`NC END\` text artifacts. One repeat has 13 selected key points, but the enrichment still covers all 14 scenes. The summary also overstates visual confirmation in one repeat. | HOLD |
| C — compact context, 9B | 81.307 | 3.4% faster; within ±5% noise | \`AND JU\` appears in 2/3 repeats. Measured evidence characters removed: 0 in all three repeats. | HOLD |
| D — Qwen3.5-4B with B prompt | 42.403 | 28.1% faster than B; 49.6% faster than baseline | All three scene-14 enrichments contain suspicious \`D_\` / \`NC END\` artifacts. One repeat has 13 selected key points, while all 14 scene-enrichment entries are present. It is the strongest speed candidate, but not a quality winner yet. | HOLD |
| E — speculative decoding with B prompt | 132.558 | 124.6% slower than B | All three repeats have JSON parse failure + repair; primary output reaches the 4096-token cap; selected key-point count is 8, 8 and 14; \`AND JU\` appears in one repeat and \`D_\` / \`NC END\` artifacts appear in all three scene enrichments. | HOLD |

## What the next optimization should target

1. **Do not force one key point per scene.** Preserve selective \`key_points\`, and enforce the existing per-scene contract through \`scene_enrichment\`.
2. **Fix scene-14 text integrity.** Investigate the \`AND JU\`, \`D_\` and \`NC END\` artifacts. If source evidence clearly shows only the closing title, prefer the grounded description “Финальная заставка с текстом «THE END».” Do not claim visual proof of a technical result from an ending title.
3. **Re-test D/4B first after the text-integrity guard.** It has the best observed latency, but any new quality guard must run after JSON parsing and before the final result is accepted.
4. **Re-test B/9B Fast Summary only after it stops emitting garbled output and unsupported visual claims.** Its 29.9% speed gain is useful only if all three repeated reports pass the quality gate.
5. **Do not spend more runs on C yet.** The implementation removed zero evidence characters, so the intended optimization was not measured.
6. **Keep E excluded under the current setup.** It is slower and unstable at the output-token ceiling.

## Reproducibility and privacy

The sibling \`benchmark/\` directory contains \`metrics.json\`, \`runs.csv\`, \`report.md\`, and \`llm_outputs.json\` for all 18 runs. The raw input video and original scene/transcript/OCR payload are not included in this PR. The separate quality checker consumes the private source JSON locally and writes its audit locally; it does not upload that data.


## Evidence-gated final-scene guard — v4

The patcher (`patches/patch_vce_text_llm_quality_guard.py`) and benchmark harness now use the same final-scene guard. When OCR/visual evidence for the last source scene confirms `THE END` or `КОНЕЦ`, it repairs malformed strings and also prevents a final-scene enrichment from describing a technical result as visible on that title card. If the source audio in that same scene independently contains a result claim, the guard preserves it as a quotation explicitly attributed to audio and distinguishes the visual closing title. It does not mutate source scenes, other scenes, or silently flatten speech into visual evidence.

The latest focused run (`20261027T031718Z_607e6e`) used guard v3. Its artifacts are historical and remain unchanged; the new v4 guard has not yet been benchmarked on-device.

## Focused rerun review: baseline vs B/9B vs D/4B

New run: [`20261027T031718Z_607e6e`](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T031718Z_607e6e). All 9 runs succeeded with valid JSON, no JSON repairs, no parse failures, no near-token-cap outputs, and all 14 scene-enrichment entries present. The v3 guard repaired one final-scene output in every run; no `AND JU`, `D_` or `NC END` artifacts remain in the persisted reports.

| Variant | Median seconds | Improvement vs baseline | Decision |
|---|---:|---:|---|
| Baseline 9B | 81.713 | reference | Keep as control; manual review required |
| B — Fast Summary 9B | 59.999 | 26.6% faster | HOLD |
| D — Fast Summary 4B | 42.244 | 48.3% faster | HOLD |

D is 29.6% faster than B, making it the speed leader, not yet the quality winner.

### Remaining quality blockers in the v3 run

- Baseline repeat 3's final-scene enrichment still says a collision was detected, although the final frame is a closing title.
- B repeats 1 and 3 imply the result was shown at the end; repeat 3 contradicts itself by also noting that the ending screen displays the title card.
- D asserts a collision was detected in CADLib while scene 13 says the visible window does not conclusively prove the slope check. The report needs to state whether this conclusion comes from speech or from the screen.
- D repeat 3 includes a likely OCR/wording artifact in the topic `АИ ИИ ПОМОЩНИК`.
- The benchmark reports `critical_atom_coverage=5.33%` for every variant with 169 extracted atoms. Do not treat this proxy as a direct quality score: OCR fragments, time values and identifiers may dominate the extraction. Review against the private source evidence.

Full machine-readable review: [`focused_run_review.json`](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/blob/review/text-llm-quality-bundle-20261027/artifacts/text-llm-quality-review/20261027T031718Z_607e6e/focused_run_review.json).

## Next step: run the updated audit locally (no model run needed)

Download the latest checker from the PR branch and run it against the already-generated outputs and the private VCE JSON. The source JSON stays on your Mac:

```bash
curl -fL --retry 3 \
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/review/text-llm-quality-bundle-20261027/tools/quality_check_text_llm_variants.py" \
  -o /tmp/quality_check_text_llm_variants.py

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/quality_check_text_llm_variants.py \
  --results-dir "$HOME/Downloads/vce_text_llm_benchmarks/20261027T031718Z_607e6e" \
  --source-json "$HOME/Downloads/NSR Specification/Новые модули NSR Specification автоматизация проектирования _context.json"
```

The checker writes `quality_audit.md` and `quality_audit.json` locally. It now gates on scene-enrichment completeness, cross-field conflicts between a closing title and an un-attributed result claim, and the observed OCR-like topic. The audit remains a deterministic heuristic and still requires a factual comparison with the source transcript/OCR.

After reviewing that audit, the next on-device experiment should use benchmark version `2026.10.27.4-grounded-final-scene` with `--focused-quality`, which runs baseline, B/9B and D/4B only. It reuses the existing context JSON and does not repeat the video-analysis stage. Use `--no-upload` if the outputs should remain local.

## Reproducibility and privacy

The benchmark files contain the metrics and generated outputs for all repetitions. The raw video and original scene/transcript/OCR payload are not uploaded. The checker itself neither uploads nor rewrites the source JSON.
