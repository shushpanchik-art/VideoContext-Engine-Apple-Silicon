# Text LLM quality review bundle — 20261027T005522Z_2cb3f5

This PR bundles all six benchmark variants and all 18 repetitions with generated reports, a corrected quality-first checker, regression tests, and an evidence-gated final-scene text guard.

- \`benchmark/metrics.json\`: every measured run, aggregate and speed delta.
- \`benchmark/runs.csv\`: tabular metrics for all 18 model runs.
- \`benchmark/report.md\`: original benchmark timing report.
- \`benchmark/llm_outputs.json\`: generated outputs for each repetition.
- \`quality_review.md\` and \`quality_review.json\`: quality findings and next-step recommendations.
- \`tools/quality_check_text_llm_variants.py\`: local quality audit with the corrected scene-enrichment completeness contract.
- \`patches/patch_vce_text_llm_quality_guard.py\`: safe local patcher for evidence-gated final-title artifact cleanup.
- \`tools/bench_text_llm_summary.py\`: updated benchmark harness; it applies the same guard to its pinned-source instrumentation and records the repair count.
- \`tests/test_vce_text_llm_quality_audit.py\` and \`tests/test_vce_text_llm_quality_guard.py\`: regression tests for the quality gates and guard consistency.

The original benchmark run is also available at [artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5).

**Quality semantics:** \`key_points\` contains selected summary theses; it is not required to reference every source scene. Per-scene completeness is measured from \`scene_enrichment\`. The checker gates on that field and scans all user-facing text for garbled fragments.

**Guard semantics:** the guard changes only clearly corrupted text in the last scene when source OCR/visual description explicitly confirms \`THE END\` or \`КОНЕЦ\`. It never mutates source scenes, non-final scenes or the executive summary. Historical benchmark outputs in this bundle were generated before the guard and remain unmodified for provenance.

**Findings:** Baseline is the control. A and C contain \`AND JU\` in some repeats. B has \`AND JU\` plus suspicious \`D_\` / \`NC END\` fragments in every final-scene enrichment. D/4B is the strongest latency candidate but has the same suspicious final-scene enrichment artifacts in all repeats. E is slower than B and repeatedly hits the output token cap and requires JSON repair. No accelerated candidate is approved for production by this run.

**Next experiment:** install the guard after a successful dry-run, or use the updated benchmark harness to measure guarded outputs; re-run baseline/A/B/C/D with \`--skip-speculative\`, then use the local quality checker on the new result folder.

**Privacy:** the raw video and original source-scene/transcript/OCR payload are not included. Generated Text LLM outputs are included for review; users should treat them as potentially sensitive derived content.
