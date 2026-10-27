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


## Follow-up fix included in this PR

This PR now includes an **evidence-gated final-title guard** in \`patches/patch_vce_text_llm_quality_guard.py\` and in the benchmark's pinned-source instrumentation. When the last source scene's OCR or visual description explicitly confirms \`THE END\` or \`КОНЕЦ\`, it replaces only clearly corrupted final-scene fragments such as \`AND JU\`, \`D_\` or \`NC END\` in \`key_points\` and \`scene_enrichment\`. It does not change the executive summary, source scenes, timestamps, technical facts or any non-final scene. If the source does not confirm a closing title, it makes no change.

The prior results in this bundle are **unpatched historical outputs**. They are not relabeled as repaired. The next benchmark should re-run baseline/A/B/C/D with the updated harness (\`--skip-speculative\`) and then run the local quality audit over the new run folder.

### Apply to the installed engine

First stop VideoContext Engine only when no analysis is active, then download and validate the patcher:

\`\`\`bash
curl -fL --retry 3 \\
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/review/text-llm-quality-bundle-20261027/patches/patch_vce_text_llm_quality_guard.py" \\
  -o "$HOME/Downloads/patch_vce_text_llm_quality_guard.py"

"$HOME/VideoContext-Engine/.venv/bin/python" \\
  "$HOME/Downloads/patch_vce_text_llm_quality_guard.py" \\
  --project "$HOME/VideoContext-Engine" --dry-run
\`\`\`

Only if the dry run prints \`DRY RUN OK\`, apply it by repeating the command without \`--dry-run\`. The patch creates a timestamped backup before writing; restart VCE before using the modified engine. The benchmark harness applies the same guard to its pinned source snapshot so a new benchmark can measure the model outputs plus this deterministic post-parse guard without re-running the VLM video stage.

The guard and audit tests pass in GitHub Actions. See the [quality benchmark test run](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/40442476959).
