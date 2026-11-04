# Next-agent handoff — VCE Apple Silicon benchmark (2026-11-04)

## Repository state
- Repository: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`
- Working branch: `feat/experimental-benchmark-launcher`
- Draft PR: https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34
- PR base: `main`; do not merge, close or convert from Draft.
- At the time of the new VLM run, PR head was `d3070b19681d6f15824c8eeb7547504fcaaf3d90`.
- Runner source pin: `650f3a1437ee0cdcab1c3ea41005a599a373d578`.
- Patch helper used by this run (v3): `f3d9592f704881819f416d3e1d0c18348888c16f`.
- Six GitHub Actions workflows passed on this head. Re-check CI after any new commit.

## New target-Mac result: isolated VLM-only (2026-11-04)
The supplied RTF confirms a single-profile run using:
`VCE_BENCH_ONLY_BASELINE=1 VCE_BENCH_VLM_ONLY=1`.

- The launcher reported patch v3 and ran exactly one profile (baseline-only).
- HTTP/API returned 200 and the runner exited 0.
- VLM model load: 5.91 s; VLM inference: 104.805 s.
- VCE process time: 114.76 s; client wall time: 117.0 s; 14 scenes reached 100%.
- Runtime/device telemetry: Apple M1 Max, MLX 0.32.4, explicit GPU device target and GPU-stream/Metal smoke tests passed. This is configured-device/stream evidence, not per-operator attribution.
- Whisper, OCR and Text LLM were disabled by the isolation mode; their zero timings are expected and do not validate them.
- `powermetrics` was unavailable (`permission_required_or_sudo_unavailable`); no GPU samples were collected.
- The runner reported `errors=1`, even though patch v3 printed `CLASSIFIER OVERRIDE: completed`. The local error detail was not in the RTF and was not uploaded.

**Acceptance decision:** treat this VLM-only run as *provisionally DEGRADED / needs local error review*, not as an unqualified successful benchmark. Positive VLM inference and 14 processed scenes are real evidence that the isolated visual path executed. The single unclassified runtime error prevents clean acceptance until the owner reviews the local `errors.csv` and confirms it is unrelated/non-fatal. Do not publish that CSV or raw logs.

## Important follow-up defect to fix
Patch v3 classifies VLM/Whisper/Text LLM based on stage telemetry and Metal log patterns, but does not use the runner's `error_count`. The new run exposes this gap: a profile with `error_count=1` can still be marked `completed`.

Implement patch v4:
1. Parse `record["error_count"]` conservatively (invalid count => at least one error).
2. Add `stage_status.runtime_errors` with count and a reason to inspect local diagnostics.
3. If count > 0, overall profile must be `degraded` unless a stronger condition already makes it `failed`.
4. Add regression coverage for HTTP 200 + all requested ML stages complete + `error_count=1` => overall `degraded`.
5. Keep actual error text local; never add it to CI logs or the PR.
6. Pin the new helper commit in the launcher; update patch/version labels and docs. Run all CI workflows again.

## Published artifacts / privacy
The VLM run created only these three aggregate files under `benchmark-results/20261104T234747Z/`:
- `launcher_status.txt`
- `stage_comparison.csv`
- `summary.md`

They contain aggregate timing/device metadata and a redacted input identifier; no transcript, raw response, error CSV or raw log was uploaded. Review these artifacts before further publication. Detailed `errors.csv`, `run_summary.csv`, `response.json`, server log deltas and device-monitoring data remain local. Never commit local diagnostics or video-derived text.

## Target Mac state / next sequence
- Before the VLM run, API readiness was confirmed at `http://127.0.0.1:7555/openapi.json`; listener PID then observed was 8742. Do not assume that PID is still current.
- The previously observed Claude `atomno-mcp-zakupki` process chain (PIDs 1328/1329/1392) is unrelated to VCE; do not terminate it.
- No post-run process/listener snapshot was supplied. Verify current VCE process and port state read-only before launching another request.
- Ask the owner to inspect the *local* single error entry; share only a redacted category/message without paths, video content, transcript or model output.
- After patch v4 and error review, run Whisper-only with `VCE_BENCH_ONLY_BASELINE=1 VCE_BENCH_WHISPER_ONLY=1`. Require an available audio track and positive Whisper inference timing. Current VCE runtime pins Whisper to CPU; this is not GPU Whisper.
- Restart VCE cleanly between tests. Run the concurrent baseline (`VCE_BENCH_ONLY_BASELINE=1`) only after separate VLM-only and Whisper-only validation. Require every requested stage to complete and zero runtime errors.
- Keep PR #34 Draft, do not change `main`, and do not rank profiles until valid tests complete.

## Prior known Metal failure
Earlier full-profile runs showed `kIOGPUCommandBufferCallbackErrorSubmissionsIgnored`, VLM guard shutdown and Text LLM Metal failure. System memory was not exhausted in the supplied snapshot. Root cause remains unproven; do not claim a fix for the underlying Metal problem from this one successful-looking isolated VLM execution.
