# Text LLM quality review bundle — 20261027T005522Z_2cb3f5

This PR bundles all six benchmark variants and all 18 repetitions with a corrected quality-first review of the generated reports.

- \`benchmark/metrics.json\`: every measured run, aggregate and speed delta.
- \`benchmark/runs.csv\`: tabular metrics for all 18 model runs.
- \`benchmark/report.md\`: original benchmark timing report.
- \`benchmark/llm_outputs.json\`: generated outputs for each repetition, so reviewers can inspect the actual prose.
- \`quality_review.md\` and \`quality_review.json\`: quality findings and next-step recommendations.
- \`tools/quality_check_text_llm_variants.py\`: local standard-library-only audit.
- \`tests/test_vce_text_llm_quality_audit.py\`: regression tests for the quality gate.

The original benchmark run is also available at [artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5).

**Quality semantics:** \`key_points\` contains selected summary theses; it is not required to reference every source scene. The per-scene completeness contract is \`scene_enrichment\`. The updated checker gates on that field, but flags corrupted or suspicious text anywhere in the report.

**Findings:** Baseline is the control. A and C contain \`AND JU\` in some repeats. B has \`AND JU\` plus suspicious \`D_\` / \`NC END\` fragments in every final-scene enrichment. D/4B is the strongest latency candidate but has the same suspicious final-scene enrichment artifacts in all repeats. E is slower than B and repeatedly hits the output token cap and requires JSON repair. No accelerated candidate is approved for production by this run.

**Privacy:** the raw video and original source-scene/transcript/OCR payload are not included. Generated Text LLM outputs are included for review; users should treat them as potentially sensitive derived content.
