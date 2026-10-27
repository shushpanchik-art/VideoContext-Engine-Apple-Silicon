# VCE Text LLM benchmark

- Run: 20261027T031718Z_607e6e
- Scenes: 14
- Repeats: 3
- Focused quality run: True
- Evidence-gated final-scene guard: enabled
- Selected 9B candidate for D/E: B_fast_summary
- Timing deltas within ±5% count as test noise.

> Raw video and raw scene/transcript/OCR input remain local. Generated Text LLM outputs are uploaded separately.

| Variant | Successful | Total median (s) | Load median (s) | Input tokens | TTFT (s) | Decode (s) | Output tokens | tok/s | Critical atom coverage | Guard-repaired runs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 3/3 | 81.71 | 1.59 | 7183.00 | 18.45 | 61.16 | 3348.00 | 55.11 | 0.05 | 3 |
| B_fast_summary | 3/3 | 60.00 | 1.58 | 7034.00 | 18.07 | 39.90 | 2222.00 | 55.63 | 0.05 | 3 |
| D_Qwen3_5_4B_vs_B_fast_summary | 3/3 | 42.24 | 1.88 | 7034.00 | 10.03 | 29.32 | 2483.00 | 84.66 | 0.05 | 3 |

## Interpretation notes

- TTFT/prefill is stream start to first non-empty text chunk, a proxy rather than an internal MLX kernel timer.
- Token counts use the model tokenizer when available; otherwise metrics mark a lexical estimate.
- Decode tok/s uses output tokens / time after the first chunk when streaming metrics are available.
- Fast Summary is accepted only when prompt/schema rewriting applied and raw model JSON has an empty scene_enrichment list. VCE downstream reconciliation may repopulate that field deterministically.
- Speculative decoding runs only if installed runtime accepts draft_model/draft_kind and the Qwen3.5-9B MTP drafter loads. Otherwise the reason is recorded.
- Quality audits cover JSON, scene IDs, source preservation, technical/numeric coverage proxies, and suspicious numeric claims. They cannot prove semantic correctness; manually review reports before adopting a variant.
