# Text LLM quality review bundle — 20261027T005522Z_2cb3f5

This self-contained bundle brings the previously committed benchmark evidence together with the quality-first review in one PR.

- `benchmark/metrics.json`: every measured run plus aggregates and deltas.
- `benchmark/runs.csv`: tabular metrics for all 18 model runs.
- `benchmark/report.md`: original benchmark timing report.
- `benchmark/llm_outputs.json`: generated outputs for each repetition, allowing review of the actual text.
- `quality_review.md` and `quality_review.json`: quality findings and recommendation.

The original benchmark folder remains at [artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5). This bundle is intentionally versioned for code review; it does not replace the original artifacts.

**Privacy:** the raw video and original source-scene/transcript/OCR payload are not included. Generated model outputs are included because they are needed to review report quality.

**Decision:** no accelerated variant has passed the quality gate in this run. Baseline remains the control; D/4B is the most promising speed candidate to repair and re-test; B/Fast Summary also needs its scene-14 truncation/completeness issue fixed.
