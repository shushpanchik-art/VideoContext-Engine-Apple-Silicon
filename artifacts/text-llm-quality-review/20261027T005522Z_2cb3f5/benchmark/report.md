# VCE Text LLM benchmark

- Run: 20261027T005522Z_2cb3f5
- Scenes: 14
- Repeats: 3
- Selected 9B candidate for D/E: B_fast_summary
- Timing deltas within ±5% count as test noise.

> Raw video and raw scene/transcript/OCR input remain local. Generated Text LLM outputs are uploaded separately.

| Variant | Successful | Total median (s) | Load median (s) | Input tokens | TTFT (s) | Decode (s) | Output tokens | tok/s | Critical atom coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 3/3 | 84.15 | 1.47 | 7183.00 | 18.51 | 62.96 | 3462.00 | 54.98 | 0.05 |
| A_thinking_off | 3/3 | 73.46 | 1.36 | 7185.00 | 18.44 | 53.20 | 2942.00 | 55.30 | 0.05 |
| B_fast_summary | 3/3 | 59.01 | 1.60 | 7034.00 | 18.08 | 38.76 | 2138.00 | 55.59 | 0.05 |
| C_compact_context | 3/3 | 81.31 | 1.50 | 7183.00 | 18.44 | 60.88 | 3369.00 | 55.23 | 0.05 |
| D_Qwen3_5_4B_vs_B_fast_summary | 3/3 | 42.40 | 1.30 | 7034.00 | 10.04 | 29.52 | 2495.00 | 84.44 | 0.06 |
| E_speculative_vs_B_fast_summary | 3/3 | 132.56 | 1.77 | 4194.00 | 18.83 | 109.20 | 5182.00 | 48.12 | 0.05 |

## Interpretation notes

- TTFT/prefill is stream start to first non-empty text chunk, a proxy rather than an internal MLX kernel timer.
- Token counts use the model tokenizer when available; otherwise metrics mark a lexical estimate.
- Decode tok/s uses output tokens / time after the first chunk when streaming metrics are available.
- Fast Summary is accepted only when prompt/schema rewriting applied and raw model JSON has an empty scene_enrichment list. VCE downstream reconciliation may repopulate that field deterministically.
- Speculative decoding runs only if installed runtime accepts draft_model/draft_kind and the Qwen3.5-9B MTP drafter loads. Otherwise the reason is recorded.
- Quality audits cover JSON, scene IDs, source preservation, technical/numeric coverage proxies, and suspicious numeric claims. They cannot prove semantic correctness; manually review reports before adopting a variant.
