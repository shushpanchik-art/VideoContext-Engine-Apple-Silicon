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

---

## Follow-up — 2026-11-05 13:39 local — false-positive runtime error identified

The user inspected the local result bundle for the 12:35 VLM smoke run.

### Local evidence (sanitized)

- errors.json: error_count=1, transport_error=null, http_status=200, text_llm_error=null.
- run_summary.json and stage_status.json: API http_200; VLM completed; Metal no_failure_observed; Text LLM and Whisper skipped; runtime_errors.count=1.
- response.json: run_vlm=true; VLM load 2.456 s; inference 14.941 s; enable_apple_ocr=false; enable_ffmpeg_ocr=false; generate_summary=false; analysis_mode=full.
- The single errors.csv entry was not a runtime failure. It was the positive capability message: FFmpeg and Tesseract CLI are available, and only installed selected languages (rus+eng) are used. errors.log contains the same informational line.

### Root cause confirmed in pinned runner source

The immutable runner source 650f3a1437ee0cdcab1c3ea41005a599a373d578 unconditionally takes meta.timings.ffmpeg_ocr.message, prefixes it with FFmpeg OCR, and appends it to the errors list whenever the message is non-empty. It then sets error_count = len(errors) and copies the list to error_excerpt. There is no severity check. Thus an informational OCR capability banner alone increments the runtime error count and causes patch v4 to classify an otherwise stage-complete HTTP 200 result as DEGRADED.

This explains the single count in this run. It does not establish that OCR inference ran: the recorded request flags had both explicit OCR flags disabled. analysis_mode=full and the “OCR + Qwen3-VL” progress label remain isolation-semantics questions; do not infer actual OCR processing from the capability banner.

### Feature-branch remediation

- Patch helper v5 adds a narrow allowlist for the positive FFmpeg/Tesseract availability banner. It moves that message into local diagnostics.log / informational_diagnostics, removes it from errors.json / errors.log, and updates error_count / error_excerpt before stage classification.
- Filtering is fail-closed: both recorded counters must equal the length of the exact local error list. Other messages remain runtime errors; mixed informational + real-error cases retain the real error.
- Regression tests cover the exact Russian informational banner, a mixed real-error case, and counter mismatch.
- The smoke launcher now passes --out-root "$RUN_OUTPUT" explicitly. VCE_BENCH_RESULT_ROOT was not honored by the pinned runner; the explicit CLI option directs its response.json and run_summary.json below the collector's searched output tree.
- Smoke launcher is pinned to patch helper commit 10f8c706e9aae494f6d6a092db7ba7d34a21897a.

The historical 12:35 run remains recorded as DEGRADED because it was executed with the old runner/patcher and its original artifacts must not be silently rewritten. It is now understood to have a false-positive count from the informational banner. Do not launch a new test until the patcher's CI passes and the updated launcher/collector is reviewed. Any later short test remains limited to a single visual path; no full video, Text LLM, Whisper, concurrent workload, or matrix.

PR #34 remains Draft; no merge and no changes to main.

### CI follow-up for patch helper v5

The first v5 CI attempt caught an idempotency-marker mismatch: the wrapper marker was bumped to v3 but the marker argument remained v2, so a second patch pass attempted to wrap run_one() twice. The marker argument was corrected in commit 10f8c706e9aae494f6d6a092db7ba7d34a21897a, and the smoke launcher was repinned to that immutable helper commit. The corresponding speed-profile workflow run is queued; do not treat v5 as CI-validated until its result is successful.

The smoke launcher now explicitly supplies --out-root "$RUN_OUTPUT"; the collector's recursive search remains rooted at RUN_DIR/runner-output. This supersedes the earlier unused VCE_BENCH_RESULT_ROOT environment variable.


---

## Follow-up — 2026-11-05 16:57 UTC — clean VLM smoke + v5 diagnostic classification verified

A second short smoke request was run locally with the updated launcher and patch helper v5. The run used the same listener PID `47366`; the PID and server-log byte window remained stable.

### Sanitized run evidence

- Run interval: `2026-11-05T16:57:00Z` to `2026-11-05T16:57:21Z`.
- Runner exit code: `0`; HTTP `200`; runner stage status: `completed`.
- `runtime_error_count=0`; `response_found=true`; `vlm_positive_completion=true`.
- `report_params_run_vlm=true`; no run_vlm metadata contradiction.
- VLM inference: `16.225 s`; visual descriptions: `2`.
- Listener PID stable; server log window stable; 65 new server-log lines, 6 explicitly containing PID 47366.
- No Metal signature lines were found in the new server-log delta.
- Raw response, local errors and server logs remained local. No GitHub upload, commit, full-video analysis, Text LLM, Whisper, concurrent request or benchmark matrix was performed.

The request guard recorded:

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

Interpretation: the tested path is the short visual/VLM path with audio, both explicit OCR request flags, Text LLM and summary disabled. The API request still reports `analysis_mode=full`; do not describe this as proof that every possible internal stage or alternate OCR implementation is disabled without checking the request builder/runtime semantics.

### Patch v5 false-positive classification verified locally

The user inspected the local result bundle under the smoke archive. Sanitized findings:

- `01_baseline/errors.json`: `error_count=0`, `errors=[]`, `transport_error=null`, `http_status=200`, `text_llm_error=null`.
- The known positive FFmpeg/Tesseract availability banner is stored in `informational_diagnostics` and `diagnostics.log`, with classification note that it is informational.
- The same banner is present in `summary.json`, `run_summary.json`, and the manifest as an availability reason; this is expected metadata, not a runtime error.
- `stage_rows.json` reports FFmpeg/Tesseract OCR `requested=false`, `sample_count=0`, `inference_time=0.0`. This supports that the OCR worker did not process samples in this request.
- The local `errors.log` contains no FFmpeg OCR/Tesseract error line. The banner remains in the separate diagnostics channel.
- The v5 allowlist therefore removed the exact positive availability banner from the runtime error list while preserving it as informational evidence. The previously added fail-closed counter checks and mixed-real-error regression coverage remain relevant: do not broaden the allowlist or suppress unrelated messages.

### Acceptance

**Accepted as a clean short visual/VLM smoke diagnostic only.** This validates the launcher/collector path, successful Qwen3-VL response metadata and the v5 informational-diagnostic classification for this one 20-second excerpt. It is not a full-video result, performance benchmark, OCR validation, audio/Whisper validation, Text LLM validation, or explanation/fix for the previously observed Metal timeout/submission failures.

### Next-agent actions

1. Confirm latest PR head and all required CI checks are complete on that exact SHA; do not infer green from runs on an earlier commit. At handoff time, all latest-head workflows except `Full distribution installer validation` had completed successfully; that workflow was still queued.
2. Review the v5 classifier and its tests, especially the exact positive-banner allowlist, fail-closed counter matching and mixed informational + real-error preservation. Do not widen the matcher.
3. Review smoke launcher request construction and collector output-directory contract. Preserve the explicit `--out-root "$RUN_OUTPUT"` handoff and immutable patch-helper pin.
4. Keep the PR Draft. Do not merge, alter `main`, or run another test unless a specific remaining question requires it.
5. If further validation is authorized, begin with static inspection and a short isolated test only. No full video, Text LLM, Whisper, concurrent request or profile matrix based solely on this smoke result.

Privacy: the local archive path, source video path/name, raw response, errors CSV, transcripts, model output and full server logs are intentionally omitted from this public handoff.


---

## Follow-up — 2026-11-05 17:08 UTC — second clean VLM smoke

The user ran the pinned VLM smoke launcher against a separate 20-second video-only excerpt generated from the local MP4. The original MP4 was not modified.

### Sanitized evidence

- Run interval: `2026-11-05T17:08:00Z`–`2026-11-05T17:08:22Z`.
- Runner exit code: `0`; HTTP `200`; stage status `completed`.
- Runtime error count: `0`.
- VLM inference: `15.083 s`; two visual descriptions returned.
- `meta.params.run_vlm=true`; no VLM metadata contradiction.
- Listener PID `47366` remained stable; the 65-line server-log delta was stable; six lines explicitly carried the listener PID.
- No Metal timeout, submissions-ignored, or command-buffer failure signature appeared in the captured log delta.
- Request flags: `analysis_mode=full`, `skip_audio=true`, `skip_visual=false`, Apple OCR=false, FFmpeg OCR=false, Text LLM=false, summary=false.
- The request manifest and sanitized summary were supplied by the user. Raw response, errors, transcript, full logs, source video and private paths remain local.

**Acceptance:** accepted as a clean short VLM-only smoke diagnostic. This is the second clean short VLM smoke; it is not a full-video, Whisper, OCR-inference, Text LLM or performance result. It does not establish a fix or root cause for the earlier Metal timeout.

## Runtime source review — `analysis_mode` and OCR paths

Reviewed the v3.19 API handler and OCR call sites in `VideoContextEngine_v3.19.py` at the PR source revision.

### `analysis_mode` semantics

- The API accepts `full`, `whisper_ocr`, or `ocr_only`.
- `full` permits Qwen3-VL when `skip_visual=false`.
- `whisper_ocr` sets `skip_visual=true`; `ocr_only` sets both `skip_visual=true` and `skip_audio=true`.
- The handler comment is explicit: `analysis_mode` controls the visual VLM path; it does not automatically disable the separate Qwen3.5 Text LLM post-processing stage.
- Whisper is governed by the audio skip/effective audio fields. In the standard full mode it runs when `skip_audio=false` and an audio track is present.
- The current `enable_mlx_whisper` checkbox is a validation flag, not a switch that moves Whisper to GPU. The current runtime invokes mlx-whisper on the CPU stream (`mx.cpu`). Keep this experimental flag false.

### Actual OCR execution gates

- Apple Vision OCR is called per selected keyframe only inside `if enable_apple_ocr:`. The normal full profile defaults this to true, but the request can override it.
- FFmpeg/Tesseract OCR is started separately only inside `if enable_ffmpeg_ocr:`; it samples frames and runs either the native FFmpeg OCR filter or standalone Tesseract CLI. This path is optional and defaults false.
- Therefore, the two smoke runs (both OCR flags false) did not execute either OCR engine. The progress text “OCR + Qwen3-VL” is a generic scene-progress label; the actual Apple and FFmpeg OCR call sites remain guarded by their independent boolean fields.
- Text LLM post-processing is a separate `if text_llm_enabled:` stage. It can run in `analysis_mode=full` and depends on its explicit request flag, not on the analysis-mode enum.

This review resolves why a VLM-only smoke could log an “OCR + Qwen3-VL” progress label while recording both OCR flags false: the label itself is not evidence that OCR inference executed.

## One-shot full end-to-end diagnostic launcher

Added an updated `scripts/run_vce_parallel_diagnostic.command` to the feature branch. It now uses the immutable v5 stage-classifier helper (`10f8c706e9aae494f6d6a092db7ba7d34a21897a`) and wraps the single baseline request with a fail-closed in-memory request contract:

- `analysis_mode=full`, VLM enabled, Whisper/audio enabled, word timestamps and audio features enabled.
- Apple Vision OCR enabled.
- FFmpeg/Tesseract OCR intentionally disabled for this first end-to-end run; it is a separate optional CPU-intensive worker path.
- Text LLM and global summary enabled.
- TXT/JSON autosave disabled to avoid overwriting reports beside the source MP4.
- Experimental `enable_mlx_whisper=false`; this does not disable the normal CPU mlx-whisper transcription path.
- One baseline request only; no six-profile matrix. The launcher checks the expected patched runtime SHA/backup/latch markers, source syntax, input audio stream, required Python modules, listener health and absence of another established request before launch.
- It requires authenticated GitHub CLI and publishes only sanitized aggregate results/error categories. Full response, raw error text, source media, transcripts/OCR text and raw logs remain local.

The full-video end-to-end request has **not yet been run**. The earlier 108.23-second run failed after approximately 44m48s with a fatal Metal command-buffer timeout during VLM generation; this remains a material risk. The runtime latch is recovery hardening, not evidence that the original Metal issue has been fixed.

Keep PR #34 Draft. Do not merge or change `main`. Accept a full run only when the stage classifier reports completed and provides positive completion evidence for VLM, Whisper and Text LLM, with zero runtime errors; inspect Apple OCR timing and metadata separately.
