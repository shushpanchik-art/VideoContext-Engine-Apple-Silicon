# VCE Text LLM quality-first review

- Run: `20261027T005522Z_2cb3f5`
- Video: `🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4`
- Input: existing VCE JSON with 14 scenes; the video was not reprocessed for this benchmark.
- Repeats: 3 per variant.
- Policy: **quality and completeness gates first; speed second**.
- Statuses below reproduce the quality-audit output provided for `quality_check_text_llm_variants.py 2026.10.27.2-quality-anchors`.
- The heuristic score and atom-coverage values are not proofs of semantic correctness. Every candidate still requires human review against the original speech/OCR/scene evidence.

## Results across all repeats

| Variant | Quality status | Heuristic score | Median seconds | Speed comparison | Hard blockers |
|---|---|---:|---:|---:|---|
| baseline | REVIEW | 79.8/100 | 84.154 | reference | none detected by automatic hard checks; manual review still required |
| A — thinking off | HOLD | 72.1/100 | 73.459 | 12.7% faster than baseline | `AND JU` fragment in scene 14 |
| B — Fast Summary | HOLD | 72.1/100 | 59.011 | 29.9% faster than baseline | `AND JU` and missing scene 14 |
| C — compact context | HOLD | 71.8/100 | 81.307 | 3.4% faster than baseline; within noise | `AND JU`; measured chars removed = 0 |
| D — Qwen3.5-4B with B prompt | HOLD | 80.1/100 | 42.403 | 28.1% faster than B | scene 14 missing in one repeat |
| E — speculative decoding with B prompt | HOLD | 69.1/100 | 132.558 | 124.6% slower than B | missing scenes, truncated/garbled output, token cap, JSON repair |

## Repeat-level integrity checks

### baseline

- Status: **REVIEW**
- Median: 84.154 s
- Automatic blockers: none detected
- Repeat details:

| Iteration | Seconds | Output tokens | Key points | Scene 14 text | JSON repairs | Parse failures |
|---:|---:|---:|---|---|---:|---:|
| 1 | 84.154 | 3462 | 14 | Видео завершается заставкой с текстом THE END. | 0 | 0 |
| 2 | 89.653 | 3827 | 14 | Видео завершается заставкой с текстом THE END. | 0 | 0 |
| 3 | 82.817 | 3420 | 14 | Финальная заставка THE END. | 0 | 0 |

### A_thinking_off

- Status: **HOLD**
- Median: 73.459 s
- Automatic blockers: garbled_or_truncated_key_point (AND JU in 2/3 repeats)
- Repeat details:

| Iteration | Seconds | Output tokens | Key points | Scene 14 text | JSON repairs | Parse failures |
|---:|---:|---:|---|---|---:|---:|
| 1 | 73.459 | 2942 | 14 | Финальная заставка с надписью THE END. | 0 | 0 |
| 2 | 76.291 | 3082 | 14 | AND JU | 0 | 0 |
| 3 | 70.824 | 2805 | 14 | AND JU | 0 | 0 |

### B_fast_summary

- Status: **HOLD**
- Median: 59.011 s
- Automatic blockers: garbled_or_truncated_key_point (AND JU); missing_scene_points:14
- Repeat details:

| Iteration | Seconds | Output tokens | Key points | Scene 14 text | JSON repairs | Parse failures |
|---:|---:|---:|---|---|---:|---:|
| 1 | 59.011 | 2138 | 14 | AND JU | 0 | 0 |
| 2 | 58.115 | 2121 | 13 | MISSING | 0 | 0 |
| 3 | 60.844 | 2258 | 14 | AND JU | 0 | 0 |

### C_compact_context

- Status: **HOLD**
- Median: 81.307 s
- Automatic blockers: garbled_or_truncated_key_point (AND JU)
- Repeat details:

| Iteration | Seconds | Output tokens | Key points | Scene 14 text | JSON repairs | Parse failures |
|---:|---:|---:|---|---|---:|---:|
| 1 | 80.561 | 3321 | 14 | Видео завершается заставкой с текстом THE END. | 0 | 0 |
| 2 | 83.684 | 3466 | 14 | AND JU | 0 | 0 |
| 3 | 81.307 | 3369 | 14 | AND JU | 0 | 0 |

### D_Qwen3_5_4B_vs_B_fast_summary

- Status: **HOLD**
- Median: 42.403 s
- Automatic blockers: missing_scene_points:14 (one repeat)
- Repeat details:

| Iteration | Seconds | Output tokens | Key points | Scene 14 text | JSON repairs | Parse failures |
|---:|---:|---:|---|---|---:|---:|
| 1 | 139.111 | 2425 | 14 | Финальная заставка THE END. | 0 | 0 |
| 2 | 41.239 | 2495 | 14 | Финальная заставка THE END. | 0 | 0 |
| 3 | 42.403 | 2571 | 13 | MISSING | 0 | 0 |

### E_speculative_vs_B_fast_summary

- Status: **HOLD**
- Median: 132.558 s
- Automatic blockers: missing_scene_points in aggregate: 1,3,4,5,6,14; garbled_or_truncated_key_point; json_repair_or_parse_failure; primary_output_near_token_limit
- Repeat details:

| Iteration | Seconds | Output tokens | Key points | Scene 14 text | JSON repairs | Parse failures |
|---:|---:|---:|---|---|---:|---:|
| 1 | 139.422 | 5182 | 8 | MISSING | 1 | 1 |
| 2 | 130.682 | 5079 | 8 | MISSING | 1 | 1 |
| 3 | 132.558 | 5506 | 14 | AND JU | 1 | 1 |


## Recommendation

1. Keep baseline as the comparison control; `REVIEW` is not a blanket approval.
2. Do not deploy A, B, C, D, or E from this run as-is because the audit found integrity/completeness blockers.
3. Re-run D after fixing missing scene-14 output and do a manual side-by-side factual review. It is the best speed candidate, not yet the quality winner.
4. Fix the output truncation / scene completeness issue before re-evaluating B. Fast Summary's speed gain is real in this run, but it does not meet the quality gate.
5. Exclude E in the current setup: it is both slower than B and unstable at the 4096-token output limit.
6. Rework C before repeating it: no evidence characters were removed, so the claimed compaction did not actually take effect.

## Included benchmark evidence

The sibling `benchmark/` directory contains the complete `metrics.json`, `runs.csv`, `report.md`, and `llm_outputs.json` for all six variants and all 18 runs. The raw input video and original source-scene payload are not included.
