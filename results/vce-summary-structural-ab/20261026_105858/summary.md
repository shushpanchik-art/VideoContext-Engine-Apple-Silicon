# VCE structural-index A/B — measured result

- Run timestamp: 2026-10-26 10:58:58 local (from output directory name)
- Host: MacBook Pro Apple M1 Max, 32 GB unified memory (same host as benchmark run)
- Scenes: 14
- Large summary model: `mlx-community/Qwen3.5-9B-MLX-4bit`
- Worker Python: `~/.venvs/vce-summary-prefilter/bin/python`
- Input: saved `evidence_checkpoint.json`; video pipeline was not rerun.
- CPU generative prefilter: not used.

## Results

| Metric | Full evidence (A) | Structural index (B) |
|---|---:|---:|
| Context characters | 33,153 | 48,019 |
| Prompt tokens | 12,882 | 22,907 |
| Summary generation | 71.851 s | 101.343 s |

- Character change: **+44.84% larger**, i.e. no character reduction.
- Token change: **+77.82% larger**, i.e. no token reduction.
- Summary generation time: **+41.0% slower** for the index (29.492 s additional).
- Deterministic index construction: 0.009 s.
- Evidence strings: 53 unique strings; 0 exact duplicate occurrences collapsed.

## Interpretation

This implementation of the structural index failed its primary optimization goal: it increases prompt size and summary latency. Do not integrate it into the production summary path as an optimization. Keep the experiment as a reproducible negative result. The likely source of overhead is repeated structural metadata and anchor listings without enough duplicate evidence to offset that overhead.

## Test / validation record

- Script syntax check: `py_compile` passed on the user's Mac.
- A/B run: completed and produced timing/token measurements above.
- The full output directory is local at `~/Desktop/VCE-Summary-Structural-AB-20261026_105858`.
- Full candidate summaries and video-derived evidence were intentionally not published in this result snapshot; inspect the local output if a semantic quality comparison is needed.
- This GitHub record reports the user's pasted local output; it does not claim that this environment reran the Mac-specific benchmark or executed the full test suite.
