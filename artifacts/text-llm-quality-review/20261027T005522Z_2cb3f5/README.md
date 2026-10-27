# VCE Text LLM optimization review bundle

This PR preserves the original benchmark run, the focused baseline/B/D rerun, and all generated outputs, while refactoring the default quality tooling to be video-agnostic.

## Universal core versus optional diagnostics

The default audit profile is **generic** and contains no knowledge of a particular video's subject, brand, title card, domain terminology or claim vocabulary.

The generic checks validate:
- output JSON/required VCE output fields,
- scene IDs, duplicates, unexpected IDs and complete \`scene_enrichment\` coverage,
- timestamp shape/deviation where timestamps are present,
- empty required text, Unicode replacement characters and control-character corruption,
- source scene data preservation, JSON repair/token-cap measurements and benchmark timing.

\`key_points\` remains a selective list of report theses. A scene does not need to be referenced by a key point when it has a valid \`scene_enrichment\` entry. This follows the VCE output schema and is not tied to a particular video.

Generic mode deliberately does **not** claim to understand semantic correctness. It reports that domain/semantic grounding was not evaluated, so a clean schema is not an automatic quality approval. A real semantic judge should compare the generated claims against each scene's transcript/OCR/visual evidence, rather than deciding by matching words such as “collision” or “THE END”.

Any dataset-specific lexical diagnostics are opt-in via \`--profile nsr-case-study\`; they are used only to reproduce the earlier NSR example and must not be treated as rules for other videos. The production guard and benchmark validator are now non-mutating: they report schema/text-integrity flags and do not rewrite generated prose based on a handful of hard-coded keywords.

## Files in this review

- \`benchmark/metrics.json\`, \`benchmark/runs.csv\`, \`benchmark/report.md\`, \`benchmark/llm_outputs.json\`: original benchmark data for all 18 A–E runs.
- \`quality_review.md\` and \`quality_review.json\`: historical quality analysis and correction log.
- \`focused_run_review.md\` and \`focused_run_review.json\`: the focused baseline/B/D run and manual observations.
- \`tools/quality_check_text_llm_variants.py\`: generic-by-default local audit. It requires explicit \`--results-dir\` and \`--source-json\`; it writes audit output locally.
- \`patches/patch_vce_text_llm_quality_guard.py\`: safe patcher for the non-mutating generic VCE integrity validator, including \`--dry-run\`.
- \`tools/bench_text_llm_summary.py\`: benchmark harness; \`--focused-quality\` compares baseline 9B, Fast Summary 9B and Fast Summary 4B without repeating video analysis.
- \`tests/test_vce_text_llm_quality_audit.py\` and \`tests/test_vce_text_llm_quality_guard.py\`: regression tests for generic behavior and optional diagnostics.

## Run the generic audit on any completed benchmark

Provide the paths for the video being audited. Example using the current local run:

\`\`\`bash
curl -fL --retry 3 \\
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/review/text-llm-quality-bundle-20261027/tools/quality_check_text_llm_variants.py" \\
  -o /tmp/quality_check_text_llm_variants.py

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/quality_check_text_llm_variants.py \\
  --results-dir "$HOME/Downloads/vce_text_llm_benchmarks/20261027T031718Z_607e6e" \\
  --source-json "$HOME/Downloads/NSR Specification/Новые модули NSR Specification автоматизация проектирования _context.json"
\`\`\`

These paths are only arguments for this example. The checker has no hard-coded default video or source JSON. For the optional sample-specific diagnostics, explicitly add \`--profile nsr-case-study\`.

## Historical timing results

The focused run [20261027T031718Z_607e6e](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T031718Z_607e6e) recorded median times of 81.713 s for baseline 9B, 59.999 s for Fast Summary 9B, and 42.244 s for Fast Summary 4B. All 9 runs succeeded with valid JSON and all 14 scene-enrichment entries.

Those timings remain useful, but the run used an earlier content-rewriting guard and is not evidence that a generic non-mutating guard or any candidate is already production-approved. The 5.33% old atom-coverage metric is a domain-specific heuristic, not an actual quality percentage.

## Privacy

The original video and source scene/transcript/OCR payload are not included. Generated report outputs are included as derived artifacts; treat them as potentially sensitive. The local checker never uploads the source JSON.
