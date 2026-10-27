# Text LLM benchmark review — universal core, sample findings isolated

## Product requirement

VideoContext Engine must work with arbitrary video content. The default quality gate must not assume a language, topic, brand, title card, domain vocabulary or expected outcome. This document preserves the measurements from one NSR Specification example, but those observations are not global acceptance rules.

The latest code in this PR separates three layers:

1. **Generic structural checks** — schema, valid and complete scene references, timestamps where present, JSON/token-limit signals, source-data preservation and basic text encoding integrity. These checks are content-agnostic.
2. **Semantic grounding** — whether any generated claim is supported by the relevant source audio/transcript, OCR or visual description. Generic structural checks do not prove this. It requires a source-aware semantic judge or manual review.
3. **Optional sample diagnostics** — the old NSR-specific lexical checks are available only with explicit \`--profile nsr-case-study\`. They do not run in generic mode.

The post-parse guard is now non-mutating. It flags structural or text-encoding issues and never rewrites generated text based on a few hard-coded words. The generic checker requires explicit \`--results-dir\` and \`--source-json\`; there are no defaults pointing to this video's files.

## Historical benchmark 1 — original A–E run

Run: \`20261027T005522Z_2cb3f5\`. It re-used an existing VCE JSON with 14 scenes rather than reprocessing the video.

| Variant | Median seconds | Historical speed result | Sample-specific observations |
|---|---:|---:|---|
| Baseline — Qwen3.5-9B | 84.154 | Reference | Reasonable control; still needs factual review |
| A — thinking off | 73.459 | 12.7% faster than baseline | Some repetitions contained an incomplete final-scene string |
| B — Fast Summary, 9B | 59.011 | 29.9% faster than baseline | Faster but some outputs contained an incomplete final-scene string and inconsistent result claims |
| C — compact context, 9B | 81.307 | 3.4% faster; within the ±5% noise band | Context compaction removed zero evidence characters |
| D — Qwen3.5-4B with B prompt | 42.403 | 28.1% faster than B; 49.6% faster than baseline | Best historical latency, but final-scene text and report consistency need review |
| E — speculative decoding | 132.558 | 124.6% slower than B | Reached the 4096-token cap and needed JSON repair in every repetition |

The original metrics and outputs are preserved in [the historical benchmark bundle](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/tree/main/artifacts/text-llm-benchmarks/20261027T005522Z_2cb3f5). These are measurements on one video and must not be interpreted as evidence about other content.

## Historical benchmark 2 — focused baseline/B/D run

Run: \`20261027T031718Z_607e6e\`. It reused the same 14-scene JSON and did not re-run video analysis.

| Variant | Median seconds | Improvement vs baseline |
|---|---:|---:|
| Baseline 9B | 81.713 | Reference |
| B — Fast Summary 9B | 59.999 | 26.6% faster |
| D — Fast Summary 4B | 42.244 | 48.3% faster; 29.6% faster than B |

All 9 runs succeeded. Their outputs had valid JSON, all 14 scene-enrichment entries, no JSON repairs, and no output-token-cap issues.

This run used an earlier sample-specific final-title rewriting guard. Its outputs and timing metrics are retained unchanged as historical evidence. It does **not** validate the current non-mutating generic guard or certify the semantic correctness of B or D.

The previous 5.33% critical-atom coverage metric is a domain-specific heuristic, not a universal quality score. The extractor may count incidental OCR strings, dates, values and identifiers that are not all required in a summary.

## Generic quality gate

The default audit profile is \`generic\` and checks:

- JSON object and required field types;
- valid, unique scene IDs and no references to unknown scenes;
- complete \`scene_enrichment\` coverage under the current VCE schema;
- selective \`key_points\` without imposing one key point per scene;
- timestamps when provided;
- token-cap/repair evidence, source-scene preservation and basic encoding/control-character integrity.

Empty \`topics\`, \`entities\` and \`uncertainties\` arrays are allowed when those fields exist with the correct type; their semantic usefulness is a separate question. Domain-specific number extraction and NSR lexicon rules are not run by default. The report explicitly marks semantic grounding as not evaluated by the generic structural pass.

### Re-run the generic audit on the current local output

This is a quick local audit of existing outputs; it does not re-run the models and does not upload the private source JSON.

\`\`\`bash
curl -fL --retry 3 \\
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/review/text-llm-quality-bundle-20261027/tools/quality_check_text_llm_variants.py" \\
  -o /tmp/quality_check_text_llm_variants.py

"$HOME/VideoContext-Engine/.venv/bin/python" /tmp/quality_check_text_llm_variants.py \\
  --results-dir "$HOME/Downloads/vce_text_llm_benchmarks/20261027T031718Z_607e6e" \\
  --source-json "$HOME/Downloads/NSR Specification/Новые модули NSR Specification автоматизация проектирования _context.json"
\`\`\`

For reproducing the historical NSR-only heuristics, explicitly add \`--profile nsr-case-study\`. Do not use that profile to evaluate arbitrary videos.

## What is still needed for universal semantic quality

To automatically judge semantic quality across unrelated videos, a separate evaluator must compare generated claims against source evidence. It should operate claim-by-claim, retain an evidence pointer and modality, distinguish audio claims from visual claims, record uncertainty, and be validated on videos across topics and languages. A handful of regular expressions is not a substitute for this component.

## Privacy

The raw source video and original source scene/transcript/OCR payload are not included in the repository. Generated Text LLM outputs are included as derived artifacts and should be treated as potentially sensitive. The local audit reads the source JSON and writes its report locally; it does not upload source evidence.
