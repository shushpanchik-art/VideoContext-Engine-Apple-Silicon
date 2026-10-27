# Text LLM quality review bundle — 20261027T005522Z_2cb3f5

This PR bundles the six historical benchmark variants and all 18 repeats, generated reports, corrected quality auditing, an evidence-gated final-scene guard, and a focused rerun mode.

- `benchmark/metrics.json`: measured runs, aggregates and speed deltas.
- `benchmark/runs.csv`: tabular metrics for all 18 historical runs.
- `benchmark/report.md`: original benchmark timing report.
- `benchmark/llm_outputs.json`: generated output for every historical repetition.
- `quality_review.md` / `quality_review.json`: quality findings and recommendations.
- `tools/quality_check_text_llm_variants.py`: local quality audit, including scene completeness and cross-field grounding checks.
- `patches/patch_vce_text_llm_quality_guard.py`: local engine patcher with `--dry-run`; preserves supported spoken claims with explicit audio attribution.
- `tools/bench_text_llm_summary.py`: benchmark harness with `--focused-quality`, guard telemetry and the same v4 final-scene grounding logic.
- `tests/test_vce_text_llm_quality_audit.py` / `tests/test_vce_text_llm_quality_guard.py`: regression tests.

Historical run bundle: [20261027T005522Z_2cb3f5](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5).

## Latest focused rerun and next step

The latest focused rerun is already published at [20261027T031718Z_607e6e](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T031718Z_607e6e). Timing medians: baseline 81.713 s, B/9B Fast Summary 59.999 s, D/4B Fast Summary 42.244 s. All 9 runs had valid JSON and all 14 scene-enrichment entries. The v3 guard repaired final-scene content in all 9 outputs. D is the speed leader, but this run remains quality-HOLD because some summaries do not attribute the claimed collision result to speech, and D repeat 3 has the suspicious topic `АИ ИИ ПОМОЩНИК`.

**Do not repeat the model run yet.** The next step is a cheap local audit of the existing outputs against the private source JSON:

```bash
curl -fL --retry 3 \
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/review/text-llm-quality-bundle-20261027/tools/quality_check_text_llm_variants.py" \
  -o /tmp/quality_check_text_llm_variants.py

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/quality_check_text_llm_variants.py \
  --results-dir "$HOME/Downloads/vce_text_llm_benchmarks/20261027T031718Z_607e6e" \
  --source-json "$HOME/Downloads/NSR Specification/Новые модули NSR Specification автоматизация проектирования _context.json"
```

The audit writes `quality_audit.md` and `quality_audit.json` into the local result directory. It now blocks cross-field contradictions between a closing title and un-attributed result claims, and flags the observed OCR-like topic. Keep the source JSON on the Mac.

The next model experiment, after reviewing that audit, should use the latest harness v4. It compares baseline, B/9B and D/4B only, reuses the existing local VCE context JSON and avoids rerunning Qwen3-VL video analysis:

```bash
curl -fL --retry 3 \
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/review/text-llm-quality-bundle-20261027/tools/bench_text_llm_summary.py" \
  -o /tmp/bench_text_llm_summary.py

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/bench_text_llm_summary.py --version
```

By default, benchmark metrics and generated model outputs upload to the repository's `main` branch. Add `--no-upload` for a local-only run.

## Historical findings

- Baseline remains the control; it is not automatically certified as semantically correct.
- B/Fast Summary is 26.6% faster than the latest baseline in the focused rerun.
- D/4B is 48.3% faster than baseline and 29.6% faster than B, but factual attribution still needs work.
- All three candidates had a guard repair in each run. The current `critical_atom_coverage` proxy was only 5.33% for every variant and must not be read as a direct quality score; it can count OCR debris, identifiers and time values.
- The raw source video and original source-scene/transcript/OCR payload are excluded. Generated Text LLM outputs are included and should be treated as potentially sensitive derived content.
