# Text LLM quality review bundle — 20261027T005522Z_2cb3f5

This PR bundles all six historical benchmark variants and all 18 repeats, generated reports, corrected quality auditing, an evidence-gated final-scene guard, and a focused rerun mode.

- \`benchmark/metrics.json\`: every historical measured run, aggregate and speed delta.
- \`benchmark/runs.csv\`: tabular metrics for all 18 historical runs.
- \`benchmark/report.md\`: original benchmark timing report.
- \`benchmark/llm_outputs.json\`: generated outputs for each historical repetition.
- \`quality_review.md\` and \`quality_review.json\`: quality findings and next-step recommendations.
- \`tools/quality_check_text_llm_variants.py\`: local quality audit with corrected scene-enrichment completeness.
- \`patches/patch_vce_text_llm_quality_guard.py\`: safe local patcher for final-title artifact cleanup; supports \`--dry-run\`.
- \`tools/bench_text_llm_summary.py\`: updated benchmark harness with the same guard, \`--focused-quality\`, and \`quality_guard_repairs\` metrics.
- \`tests/test_vce_text_llm_quality_audit.py\` and \`tests/test_vce_text_llm_quality_guard.py\`: regression tests.

The original benchmark remains at [artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5).

## Next run: focused quality check of B/9B and D/4B

The new \`--focused-quality\` mode measures only:
1. baseline 9B control,
2. B Fast Summary with 9B,
3. D Fast Summary with 4B.

It automatically skips A/C/E for this targeted run; E was slower and unstable in the earlier run. Existing VCE JSON is reused to avoid re-running the visual Qwen3-VL stage. The benchmark records when the evidence-gated guard repaired a report so the time/quality impact is visible.

\`\`\`bash
curl -fL --retry 3 \\
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/06cb8bb11342043815aa2ba7e1321ddd89fb2d65/tools/bench_text_llm_summary.py" \\
  -o /tmp/bench_text_llm_summary.py

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/bench_text_llm_summary.py --version

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/bench_text_llm_summary.py \\
  --input-json "$HOME/Downloads/NSR Specification/Новые модули NSR Specification автоматизация проектирования _context.json" \\
  --repeats 3 --focused-quality
\`\`\`

This mode publishes benchmark metrics and generated model outputs to the repository's \`main\` branch by default, consistent with the current harness behavior. Use \`--no-upload\` to keep the new run entirely local. After it completes, run the updated quality checker with \`--results-dir\` set to the new run directory shown at the end.

**Quality semantics:** \`key_points\` contains selected summary theses; it is not required to reference every source scene. Per-scene completeness is measured from \`scene_enrichment\`. The checker gates on that field and scans all user-facing text for garbled fragments.

**Guard semantics:** the guard changes only clearly corrupted text in the last scene when source OCR/visual description explicitly confirms \`THE END\` or \`КОНЕЦ\`. It does not change source scenes, non-final scenes, technical claims, timestamps or the executive summary. Historical outputs in this bundle remain unmodified for provenance.

**Findings:** Baseline is the control. A and C contain \`AND JU\` in some repeats. B has \`AND JU\` plus suspicious \`D_\` / \`NC END\` fragments in every final-scene enrichment. D/4B is the strongest latency candidate but has the same suspicious final-scene artifacts in all repeats. E is slower than B and repeatedly hits the output token cap and requires JSON repair. No accelerated candidate is approved for production from the historical run.

**Privacy:** the raw source video and original source-scene/transcript/OCR payload are excluded. Generated Text LLM outputs are included for review and should be treated as potentially sensitive derived content.
