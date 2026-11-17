# Native Text JSON failure and isolated mitigation

The PR61 RAM matrix stopped on analysis 7 (first input of repeat 1, queue size 3, RAM+), after six successful analyses and four completed queues. It was not the third item in the queue. HTTP 200 carried Text LLM status failed; primary and repair lacked a complete JSON object with nonempty executive_summary. No source content was discarded.

Whisper large/MPS, Qwen3-VL-8B and Qwen3.5-9B/Metal were used, full original budgets (1024/2048/8192). The controller stopped without restarting. Free memory minimum was 17%, above the 15% floor. This is not evidence of a RAM-mode causal failure. The same video previously succeeded. Transcript/OCR hashes match, but VLM summaries differ, so those prompts were not identical.

The base and PR61 Text postprocess function ASTs are identical except final cleanup. Primary explicitly enabled thinking despite the nearby comment requiring constrained extraction without long reasoning. Repair already disabled it. The failure showed output budget consumed without valid structured output. The patch disables thinking consistently at primary chat template, stream and fallback; removes the irrelevant thinking budget. It preserves token budgets, validation, repair and source fallback. This is a bounded mitigation, not proof that every possible invalid model response is fixed.

Regression suite: 295 unittest cases passed. New AST contract tests cover primary/repair thinking flags and absence of contradictory thinking-budget requests. Native replay and new matrix results are recorded separately, with different engine versions never pooled. Installed app and persistent settings are unchanged.
