# Handoff — latest target-Mac VLM smoke diagnostic (2026-11-05)

## Repository state and guardrails

- Repository: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`
- Working branch: `feat/experimental-benchmark-launcher`
- Existing PR: [#34 — Apple Silicon benchmark launcher: diagnostics and safe result classification](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34)
- Metal investigation: [Issue #35](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/issues/35)
- PR #34 remains open and Draft. Do not merge, close, mark ready, retarget, or modify `main`.
- Fetch the live PR head before any edits. Keep all work on `feat/experimental-benchmark-launcher`.
- No full benchmark, long-video test, Text LLM test, Whisper test, concurrent test, or benchmark matrix until the issues below are resolved.
- Never publish raw logs, `errors.csv`, API responses, transcripts, OCR/video-derived text, model output, source video, private paths, or local diagnostic archives.

## Target-Mac state confirmed before the latest run

- Local project/source: `~/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py`.
- Current source SHA256: `6a47735e6cff4ea72e66f4052c7eeb770fb32c790504986f07967da4cd242ca6`.
- Timestamped pre-patch backup exists: `VideoContextEngine_v3.19.py.backup-20261105T000114`, SHA256 `eb0708be06944503a9a2ad3429b8f27938356d1d16816634855fc93b7cda4f2c`.
- Reconstructing the expected source from that backup with the pinned Metal patcher produced SHA256 `eacd4eecfda2cc8a2c71203bf5ae3589611f5fd8d0405ded747146a78f147055` (the known base latch patch); the installed file differs only by the reviewed additional `VCE_METAL_RUNTIME_LATCH_RAM_MINUS_TEARDOWN_V1` block. The installed file contains both latch markers and passed in-memory Python syntax compilation in the launcher.
- The launcher was updated to pin the actual installed source SHA and require the additional RAM-minus teardown marker.
- VCE listener: PID `47366`, Python 3.11.18, serving the same project/source path. It remained stable through the latest test.
- macOS uses a Russian locale. The launcher process-start check was corrected to use `LC_ALL=C`; it now passes.
- The test clip was generated as a separate 20-second video-only excerpt. The source video was not modified.

## Latest run — 2026-11-05 12:35 local / 15:35 UTC

Launcher archive:
`~/Downloads/VCE_VLM_Diagnostic/20261105T153518Z/`

Runner's own output directory, reported in its console:
`~/Downloads/VCE_Full_Profile_Benchmark/20261105_123520_477586/01_baseline`

### Facts from the user's Terminal output

- Exactly one baseline request was launched by the smoke launcher.
- Runner source pin: `650f3a1437ee0cdcab1c3ea41005a599a373d578`.
- Patch helper: v4, pin `db54501d459569eaa3e75a8e22ef1385ecee68f9`.
- VCE listener PID before/after: `47366`; stable.
- New server.log byte window: stable; 64 new lines, 6 explicitly containing PID 47366.
- No Metal timeout/submissions-ignored/OOM signature was found in this new log window.
- Generated test clip duration: 20.000 seconds.
- Runner progress: VLM/Qwen3-VL stage reached scene 2/2; runner printed `completed; wall=19.1s; VCE=18.68s; scenes=2/2; errors=1`.
- Runner classifier explicitly overrode the result to `degraded (API http_200)`.
- Runner exit code: 1. The baseline did not pass acceptance because the runtime reported one error. The runner cancelled any remaining experiments (only baseline was selected).
- No video, transcript, full response, or raw diagnostic archive was uploaded.

### Actual request flags recorded by the smoke launcher's guard

```json
{
  "analysis_mode": "full",
  "skip_audio": true,
  "skip_visual": false,
  "enable_apple_ocr": false,
  "enable_ffmpeg_ocr": false,
  "text_llm_enabled": false,
  "generate_summary": false
}
```

Interpretation must be careful:

- Audio/Whisper, visual skip, Apple OCR, FFmpeg OCR, Text LLM, and summary flags above are the actual fields the guard observed immediately before the request.
- `analysis_mode` remained `full`, not a dedicated visual-only enum. The isolation wrapper selects stages through the flags; do not infer more than the recorded fields.
- Runner progress displayed “OCR + Qwen3-VL: анализ сцены 2/2”, and its startup banner said FFmpeg OCR was available. The request manifest says both explicit OCR flags were false. It is not yet known whether that progress label is generic, another OCR path is enabled by a different field, or the request was not fully isolated. Verify this from the exact request payload and server-side effective settings before calling the run strictly VLM-only.
- The output demonstrates that the visual/Qwen3-VL path was active and reached 2/2 scenes. It does not establish a clean VLM result because `errors=1`.

## Important smoke-launcher reporting bug

The smoke launcher printed:

- `response_found=false`
- `http_status=null`
- `runner_stage_status=null`
- `vlm_inference_seconds=0.0`
- `visual_description_count=0`

These are **not valid conclusions about the VCE request**. The smoke collector searched for `response.json` and `run_summary.json` under its own `$RUN_DIR/runner-output`, but the runner's console explicitly reported its actual output at:

`~/Downloads/VCE_Full_Profile_Benchmark/20261105_123520_477586/01_baseline`

The collector did not discover/read that directory. Therefore the missing response/metadata fields in `diagnostic_summary.json` are a collector path mismatch, not proof that the API returned no response. The launcher correctly failed closed, but its report parser must be fixed before another smoke test.

The smoke launcher's local archive still contains useful evidence:
- `request_flags.json`
- `launcher_console.log`
- `server_log_delta.log`
- `server_log_pid_47366.log`
- `preflight.txt`
- `static_source_index.txt`
- `diagnostic_summary.json` (with the parser limitation above)

## Required next-agent actions — in this order

1. **Do not launch another request yet.** Confirm the current listener/process and that no VCE client request is active, read-only.
2. Inspect only the single local error entry for this run in the runner output directory:
   `~/Downloads/VCE_Full_Profile_Benchmark/20261105_123520_477586/01_baseline/errors.csv`
   Share back only a sanitized category/subsystem and a short redacted message. Do not upload the CSV or raw logs.
3. Inspect the local `response.json`, `run_summary.json`, `stage_status.json`, and relevant request metadata in that same `01_baseline` directory. Determine:
   - actual HTTP status and API response presence;
   - `meta.params.run_vlm`;
   - VLM load/inference timing and 2/2 scene description evidence;
   - exact runtime error count/category;
   - all effective request flags, including any OCR flags not currently guarded.
   Keep all raw values/private output local.
4. Fix the smoke launcher collector to use the runner-reported result directory (parse the exact `local:` output line or add a reliable explicit output-dir contract). Do not assume `VCE_BENCH_RESULT_ROOT` is honored until verified. Add tests for result-directory discovery and missing output behavior. Preserve fail-closed behavior.
5. Expand the request guard to assert all relevant isolation fields and, if the API supports it, assert the intended analysis mode. Investigate why progress says “OCR + Qwen3-VL” while the two guarded OCR flags are false. Do not dismiss it as cosmetic without code evidence.
6. Reconcile the earlier `run_vlm=False` vs positive VLM timing/descriptions anomaly against this run's actual response metadata. Do not infer it from the smoke collector's empty fields.
7. Run current-head CI after code changes. Only then, if safe, run one short isolated visual/VLM request. No full video, Text LLM, Whisper, concurrency or profile matrix.
8. Update this handoff and PR #34 with sanitized results. Keep PR Draft.

## Acceptance status

**Latest run: DEGRADED / NOT ACCEPTED.**

Positive evidence: visual/Qwen3-VL progress reached 2/2 scenes; HTTP 200 was reported by the runner classifier; no Metal failure signature appeared in the new PID-scoped server-log window.

Blocking evidence: runner reported `errors=1`, classified the baseline as degraded, and exited 1. The smoke collector also failed to find the runner's actual result directory, so response metadata and VLM timing were not captured by the launcher. OCR-isolation semantics remain unresolved.

Do not claim a successful clean VLM-only run, a fixed Metal root cause, or a performance result. The prior fatal Metal timeout remains unexplained; the runtime latch is recovery hardening, not a root-cause fix.
