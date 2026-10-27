# Focused quality rerun review — 20261027T031718Z_607e6e

- Source report: [benchmark artifacts on main](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T031718Z_607e6e)
- Machine: Apple M1 Max, macOS arm64, MLX 0.32.4
- Input: existing local VCE JSON, 14 scenes; no video reprocessing
- Repetitions: 3 each for baseline 9B, Fast Summary 9B (B), Fast Summary 4B (D)
- All 9 runs succeeded; every output was valid JSON; no JSON repairs, parse failures or near-token-limit outputs were reported.
- \`quality_guard_repairs=1\` in all 9 runs. The persisted outputs no longer contain the inspected \`AND JU\`, \`D_\` or \`NC END\` fragments.

## Timing

| Variant | Repeats | Median seconds | Improvement vs baseline | Improvement vs B |
|---|---:|---:|---:|---:|
| Baseline, Qwen3.5-9B | 3/3 | 81.7128 | reference | — |
| B, Fast Summary with Qwen3.5-9B | 3/3 | 59.9985 | 26.6% faster | reference |
| D, Fast Summary with Qwen3.5-4B | 3/3 | 42.2438 | 48.3% faster | 29.6% faster |

The speed advantage is material in this focused rerun. D is the leading latency candidate, but the quality gate is not passed yet.

## Integrity checks

- All 9 outputs contain 14 unique \`scene_enrichment\` entries for scene IDs 1–14.
- All 9 outputs contain 14 selected \`key_points\` in this particular run. This is useful to note but is not the completeness contract: selected key points need not be one-per-scene.
- No output required syntax repair, and none approached the configured 4096 primary output-token limit.
- The deterministic guard cleaned final-scene text in every run. This shows the unguarded model was emitting a final-card artifact that the guard considered unsafe to retain.
- The reported \`critical_atom_coverage_median=0.0533\` (5.33%) is the same across all variants and is not a reliable quality rank by itself. The current atom extractor produces 169 candidate atoms from local evidence, many of which may be OCR fragments, time values or identifiers. Run the updated local audit against the private source JSON before interpreting that score.

## Manual findings from the stored outputs

### Baseline
The final key point is a grounded “THE END” title after guard repair in all repeats. However, baseline repeat 3 still has final-scene enrichment saying a collision was found and that the slope violates the standard, despite the final scene being a closing title. The guard fixes obvious corrupted fragments, not all semantic conflicts. Baseline remains the control, not a quality-approved report.

### B — Fast Summary with 9B
All three final-scene enrichments are clean and consistently describe the “THE END” card. Yet the executive summaries in repeats 1 and 3 say that the collision was “shown” at the end; repeat 3 also acknowledges that the screen displays a closing title. This is a cross-field grounding conflict. Repeat 2 describes the collision as the outcome without explicitly stating that it is visually shown, but should still be checked against source audio. B is faster, but remains on hold.

### D — Fast Summary with 4B
All three final-scene key points and enrichments consistently describe the “THE END” card. However, summaries state that a collision was detected in CADLib, while scene 13's enrichment explicitly says the window does not conclusively prove a slope check. The reports need consistent attribution of what is said in speech versus what is visible on screen. Repeat 3 also includes the suspicious topic \`АИ ИИ ПОМОЩНИК\`, a likely OCR/wording artifact requiring source verification. D is the best speed candidate, but it is not approved yet.

## Recommended next action

1. Keep the corrected per-scene completeness contract and the evidence-gated final-title guard.
2. Tighten the guard/checker to identify final-scene enrichment that describes a technical result when the source visual evidence is only a closing title, even if the text is not syntactically garbled. Preserve spoken claims with explicit attribution to audio rather than silently deleting them.
3. Run the updated quality audit locally against the private source JSON; the original source scene/transcript/OCR payload was intentionally not uploaded.
4. Re-run B and D only after the cross-field grounding rule is covered by unit tests. Select D only if it preserves factual attribution as well as its roughly 42.24-second median latency.

No run output in this review has been edited. The timing and generated-output artifacts on \`main\` are preserved as historical evidence.


## Follow-up from the local audit (checker v4)

The audit run on the Mac reported:

- Baseline: HOLD — final-scene enrichment conflict plus a garbled-text flag.
- B/9B: HOLD — closing-title/summary visual conflict, result claim without explicit audio attribution, and a garbled-text flag.
- D/4B: HOLD — result claim without explicit audio attribution, suspicious topic \`АИ ИИ ПОМОЩНИК\`, and a garbled-text flag.

Manual inspection of the checker rule found two false-positive cases: ordinary acronym \`КСИ\` and identifier \`HDB_V5\` were flagged merely because they were short uppercase text. Checker v5 removes the generic uppercase-fragment heuristic while preserving explicit detection of \`AND JU\`, \`NC END\`, \`D_\` and the context-specific suspicious topic. Regression tests cover this behavior and the closing-title/audio-attribution gate.

The many \`important_source_atoms_missing\` warnings should also not be treated as a precise quality percentage. The rule is a heuristic based on contextual number extraction; dates, dimensions, OCR identifiers, and incidental on-screen values may be candidates that are not all required in the report's executive summary. Use it to guide manual review, not as an automatic failure reason.

### Refresh the local audit

Re-run the same command with the latest checker v5 from this PR branch. It consumes existing outputs, does not re-run the models, and does not upload the private source JSON:

\`\`\`bash
curl -fL --retry 3 \\
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/review/text-llm-quality-bundle-20261027/tools/quality_check_text_llm_variants.py" \\
  -o /tmp/quality_check_text_llm_variants.py

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/quality_check_text_llm_variants.py \\
  --results-dir "$HOME/Downloads/vce_text_llm_benchmarks/20261027T031718Z_607e6e" \\
  --source-json "$HOME/Downloads/NSR Specification/Новые модули NSR Specification автоматизация проектирования _context.json"
\`\`\`

The expected correction is that \`КСИ\` and \`HDB_V5\` no longer appear as generic garbled snippets. This does not mean B or D will pass the gate: the separate grounding and result-attribution checks are intentional blockers until the report distinguishes spoken claims from visual evidence.
