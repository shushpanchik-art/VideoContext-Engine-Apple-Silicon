# Agent handoff: VLM-only smoke runner API compatibility

## Current state
- Repository: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`
- Existing PR: #34 — keep it as the single PR; do not open a duplicate.
- Head branch: `feat/experimental-benchmark-launcher`
- Latest known edits in this handoff sequence:
  - adapter fix: `2a8869450ad5224cf13a34f41282379385f3f5d4`
  - baseline-bypass regression test: `bd37754a1b8a1264c66d0f04df3a8162e21b8469`
  - smoke runner patcher pin: `6e2de85a12244ad08c5a1170569f944a2b44008d`
  - fast-profile test pin: `bf771d5558704160ccb0b228bf89b5a1f7ac4267`
- PR remains Draft; do not merge or mark ready without user authorization.
- Last known CI before this newest fix had benchmark tests, distribution, OCR worker, audio resilience, Metal latch, and optimization patch checks passing. The newest commits must be revalidated.

## Confirmed Mac-side diagnosis
User ran the VLM-only smoke twice on a MacBook Pro M1 Max, 32 GB:
- First run at `20261106T064127Z`; second run at `20261106T071929Z`.
- VCE server starts and API settings respond. On second run server PID was `53188`, listener on port 7555.
- Diagnostic flags are correct: `analysis_mode=full`, `skip_audio=true`, `skip_visual=false`, Apple/FFmpeg OCR false, Text LLM false, summary false.
- No POST analysis request is sent. Server log delta contains only GET `/openapi.json` and `/api/v1/ui-settings`.
- Runner console on both attempts: `FATAL: Локальный API не подтверждает поля ['enable_adaptive_keyframes']; не буду имитировать эксперимент.`
- Therefore no VLM inference occurred; `http_status=None`, `response_found=false`, `vlm_inference_seconds=0.0` are pre-request abort evidence, not a Metal/model failure.

## Root cause and correction
The legacy compressed benchmark runner's `build_plan()` validates every experiment's API fields before returning its plan. Even with `VCE_BENCH_ONLY_BASELINE=1`, the previous adapter called the original planner first and only sliced the resulting plan afterward. Thus the original planner raised on the intentionally removed `enable_adaptive_keyframes` before baseline selection.

The prior adapter also filtered removed fields from `build_base_fields()`, but that alone could not fix the earlier `build_plan()` validation.

Latest adapter change in `scripts/patch_vce_full_profile_benchmark.py` now returns a single baseline plan immediately when `VCE_BENCH_ONLY_BASELINE=1`, without invoking the legacy experiment planner. Default benchmark behavior remains unchanged when that environment variable is not set.

Regression test added to `tests/test_vce_full_profile_benchmark_patch.py`: replace the original planner with a function that raises the exact legacy missing-field error, set baseline-only mode, and assert that the adapter returns the single baseline plan without calling it.

Smoke runner and static pin test now point to adapter commit `2a8869450ad5224cf13a34f41282379385f3f5d4`.

## Next actions for receiving agent
1. Fetch PR #34 current head and inspect the final diff. Confirm the runner pin and test pin both reference `2a8869450ad5224cf13a34f41282379385f3f5d4`.
2. Run the focused Python test module `tests/test_vce_full_profile_benchmark_patch.py` and `tests/test_vce_fast_profile_mps.py` (or their CI equivalents). Ensure the regression test proves original `build_plan` is bypassed in baseline-only mode.
3. Wait for all relevant CI on the latest PR head. Note the cancelled speed-profile/menu-bar workflow separately; do not misreport it as success.
4. Only after CI passes, prepare a fresh immutable smoke-runner URL pinned to the final validated commit, if the current runner is not already pinned to the final adapter.
5. Before asking user to rerun on Mac, remember the last known server PID was `53188` on port `7555`. Ask them to inspect/stop that specific server gracefully (never kill an unknown listener). The runner refuses a pre-existing listener.
6. Ask for the next smoke output. Acceptance requires an actual HTTP 200 response, completed VLM stage, positive VLM inference timing and visual description evidence. No full-video analysis; retain 20-second VLM-only scope.
7. Do not reinstall dependencies, change the user's original MP4, delete timestamped backups, modify persisted profile settings, upload local diagnostic artifacts, merge PR, or alter main.

## Important distinction
The current issue is still a runner preflight/planning bug. Do not diagnose llama.cpp/Metal, Qwen model loading, GPU memory or video decoding until a real POST request is observed and its local response/logs show a runtime failure.


## 2026-11-06 follow-up: VLM diagnostic runner pin consistency

A follow-up inspection found that the dedicated VLM diagnostic launcher still pinned the adapter to `10f8c706e9aae494f6d6a092db7ba7d34a21897a`, which predates the baseline planner bypass. The fast-profile smoke launcher already used the corrected adapter, but that did not update the separate VLM diagnostic launcher.

Corrected in the existing PR #34:
- `scripts/run_vce_vlm_smoke_diagnostic.command`: `PATCHER_REF` now points to `2a8869450ad5224cf13a34f41282379385f3f5d4` (commit `f55e01d508b9bfabb9ad2b728a7026abc832296e`).
- `tests/test_vce_full_profile_benchmark_patch.py`: added `test_vlm_smoke_launcher_pins_baseline_planner_compat_adapter`, which asserts that exact immutable adapter pin (commit `2a5036489cdd5a380d554a6408bd956d25aed428`).

The PR head is now `2a5036489cdd5a380d554a6408bd956d25aed428`. CI for this head was triggered: OCR workers, Audio resilience, Quality benchmark, Optimization patch and Metal runtime latch were in progress; Distribution was queued; Speed profile/menu bar was cancelled. Re-check all runs against the latest PR head before a Mac smoke.

No Mac smoke was run by this follow-up agent. Before running, inspect the actual current listener on port 7555 and confirm no active request; PID 53188 is only the last recorded PID and must not be killed blindly. The launcher itself requires exactly one listener and refuses an established connection. It also verifies the listener started after the expected patched engine source.

For a video longer than 30 seconds, the launcher creates a separate 20-second video-only excerpt at the beginning of the source and leaves the original unchanged. Keep the local source path in the shell environment only; do not write it into this public handoff or commit local diagnostic output.


## 2026-11-06 target-Mac preflight follow-up: engine checksum mismatch

The next VLM diagnostic attempt stopped before starting a request. User-provided read-only terminal output:

- No process was listening on TCP port 7555 at the time of the attempt.
- Installed `VideoContextEngine_v3.19.py` exists and compiles successfully in memory.
- Installed source SHA256: `7cf4f32cf6e63685ae1ea470f6a63f9b357105966c9b2de28a3156b552e99e23`.
- Timestamped backup `.backup-20261105T000114` exists and has SHA256 `eb0708be06944503a9a2ad3429b8f27938356d1d16816634855fc93b7cda4f2c`.
- The local project directory did not contain `patches/patch_vce_metal_runtime_latch.py`, so the audit could not run patch idempotence or reconstruct the patched output from the backup.
- No API request or VLM inference was started.

Checksum provenance is inconsistent in the current materials:
- The earlier Metal runtime-latch handoff records the applied source SHA as `eacd4eecfda2cc8a2c71203bf5ae3589611f5fd8d0405ded747146a78f147055`.
- The VLM diagnostic launcher currently requires `6a47735e6cff4ea72e66f4052c7eeb770fb32c790504986f07967da4cd242ca6`.
- The target Mac currently reports `7cf4f32cf6e63685ae1ea470f6a63f9b357105966c9b2de28a3156b552e99e23`.

Do not update the launcher's expected hash to the observed value and do not reapply the patch yet. The launcher also checks for `VCE_METAL_RUNTIME_LATCH_RAM_MINUS_TEARDOWN_V1`; that marker is not present in the repository's currently pinned `patch_vce_metal_runtime_latch.py` helper (blob `926ddcb9d29c54f22f8d7487239f933b5b760f94`). This suggests the expected source may include an additional local safeguard not reproduced by the published helper, but the cause is not yet established.

Next safe step: download the exact helper from this PR's immutable commit into a temporary directory (not into the installed project), then run it in memory against both the backup and current source. Record the reconstructed SHA, whether the current source is idempotently accepted by the helper, and whether the RAM teardown marker exists. Do not write either file. Only after inspecting that comparison should the launcher checksum/preflight and public patch provenance be corrected.

The failed smoke attempt did not alter the source, backup, VCE settings, or GitHub artifacts. Keep PR #34 Draft and do not run the VCE service until source provenance is resolved.


## 2026-11-06 target-Mac source reconstruction result

User ran the read-only comparison using the repository's immutable Metal-latch patcher from commit `5dced7045c63f4de492c598e082cd9a60bb49b93`. Results supplied from the target Mac:

- Current installed source SHA256: `7cf4f32cf6e63685ae1ea470f6a63f9b357105966c9b2de28a3156b552e99e23`.
- Timestamped backup exists; SHA256: `eb0708be06944503a9a2ad3429b8f27938356d1d16816634855fc93b7cda4f2c`.
- The current source compiles successfully in memory.
- `VCE_METAL_RUNTIME_LATCH_RAM_MINUS_TEARDOWN_V1` is present in the installed source.
- Applying the official repo patcher in memory to the backup deterministically reconstructs SHA256 `eacd4eecfda2cc8a2c71203bf5ae3589611f5fd8d0405ded747146a78f147055`, matching the earlier handoff's recorded patched SHA.
- The reconstructed file is not byte-identical to the installed current source.
- Applying the official patcher to the current source fails closed with `ValueError: Found a partial Metal-latch patch`, reporting missing marker calls:
  - `_vce_mark_metal_runtime_failed("text_llm_stream_fallback", fallback_stream_error)`
  - `_vce_mark_metal_runtime_failed("text_llm_stream_generate", stream_error)`
- The user's diff script reported 70 SequenceMatcher opcodes between reconstructed and installed files. This is a large structural difference; the opcode count alone does not identify the semantic changes and should not be interpreted as 70 isolated defects.

Interpretation: the backup is intact and the canonical repo patcher reproduces the earlier `eacd4eec...` hash exactly. The installed `7cf4f32c...` source is syntactically valid but contains additional/different local modifications (including the RAM teardown marker) and does not satisfy the canonical patcher's complete-latch idempotence checks. Whether this represents an intentional later runtime revision or an incomplete/mixed patch is not yet known.

Do not restore the backup, reapply the canonical patch, weaken the launcher SHA check, or run VCE yet. Next perform a narrow, read-only structural comparison of top-level function ASTs between the installed source and reconstructed canonical source. Report names/line ranges only for changed Metal/MLX/Text-LLM functions, plus a short excerpt around the RAM teardown marker and Text LLM fallback exception handlers. This should identify whether the two missing latch hooks were replaced by equivalent newer control flow or removed. No raw source or private logs need to be uploaded.


## 2026-11-06 target-Mac AST comparison follow-up

User ran the read-only top-level AST comparison between the official patcher reconstruction (`eacd4eec...`) and installed runtime (`7cf4f32c...`).

Observed:
- Both files compile/parse; the installed file is syntactically valid.
- Installed source contains the latch marker, `_vce_mark_metal_runtime_failed("vlm_generation", exc)`, `_vce_mark_metal_runtime_failed("text_llm_generation", _vce_text_llm_active_error)`, and the extra `VCE_METAL_RUNTIME_LATCH_RAM_MINUS_TEARDOWN_V1` marker.
- The installed source does not contain the canonical patcher's two explicit streaming-fallback hook calls.
- Top-level AST comparison shows significant structural differences in `analyze_video`, `run_text_llm_postprocess`, `_vce_clear_metal_memory_caches`, `format_text_llm_report`, and `get_vlm_engine`. Several canonical MLX helper definitions are absent as top-level functions in the installed file (`_vce_mlx_run`, `_vce_mlx_invoke_traced`, `_vce_mlx_trace_runtime`, `_vce_mlx_trace_stream`, `_vce_configure_mlx_metal`, `_vce_prepare_mlx_vlm_runtime`, among others). The cause may be a different runtime architecture/revision or local patch set; not established yet.
- The user's fallback-handler grep found no lines containing `fallback_stream_error` or an `except ... stream_error` form. This does not establish that there are no equivalent handlers; exception variable names/control flow may differ.
- The current and canonical SHA values remain unchanged: installed `7cf4f32c...`, reconstructed `eacd4eec...`.

Interpretation is deliberately limited: the installed runtime is a different structural variant with a visible latch subset and an additional teardown safeguard. Do not call it corrupt, equivalent, or safe until the actual exception-handler control flow and teardown branch have been inspected.

Next read-only inspection should use AST to list every `except` handler inside installed `run_text_llm_postprocess`, its exception type/name/line range, and all calls to `_vce_mark_metal_runtime_failed`, `_vce_metal_runtime_failure`, and `_vce_assert_metal_runtime_healthy` within `run_text_llm_postprocess`, `analyze_video`, and `_vce_clear_metal_memory_caches`. Also print only a small numbered window around the RAM teardown marker. No file writes, service restart, VCE run, or checksum override.


## 2026-11-06 target-Mac exception-handler and RAM teardown audit

User ran the requested read-only AST audit against installed source `7cf4f32c...`.

Findings:
- `run_text_llm_postprocess` contains JSON parse/repair exception handlers and calls `_vce_assert_metal_runtime_healthy('text_llm')`, `_vce_mark_metal_runtime_failed('text_llm_generation', _vce_text_llm_active_error)`, and `_vce_metal_runtime_failure()`. It does not show explicit streaming fallback handlers in the inspected function.
- `analyze_video` has a broad Text LLM postprocess exception handler (lines 5363-5368 in this installed revision), which marks the Text LLM status failed and continues with the original result. It also has ordinary request/download/cleanup exception handlers.
- `_vce_clear_metal_memory_caches` has four broad `except Exception: pass` cleanup handlers (lines 4087, 4093, 4096, 4100). Cleanup exceptions are swallowed.
- RAM-minus teardown guard at lines 5228-5240 checks `_vce_metal_runtime_failure()`; on a latched fatal Metal timeout it skips VLM unload and accelerator-memory cleanup, logging that a process restart is required. Otherwise it unloads VLM and releases accelerator memory.
- Within the three inspected functions, the audit found no `_vce_mark_metal_runtime_failed("vlm_generation", ...)` call. It did find failure-state getter calls in `analyze_video` and the Text LLM mark call in `run_text_llm_postprocess`. This differs from an earlier source excerpt/line map; re-check the exact current installed file before inferring a VLM failure hook exists.
- The audit confirms a protective RAM teardown branch exists, but does not establish that VLM inference failures are latched equivalently to Metal timeouts in every inference path.

Operational decision:
- Do not run the old diagnostic launcher: its immutable engine SHA gate correctly rejects installed SHA `7cf4f32c...` (it expects `6a47735e...`).
- Do not bypass or edit the SHA check, and do not start an inference request through a hand-crafted API call yet.
- A real VLM-only test can proceed only after identifying the installed app's exact VLM inference call path and verifying the relevant Metal failure latch around that path, or after preparing a reviewed diagnostic adapter that explicitly targets this installed source hash and architecture.
- Next safe action is a read-only inspection of the exact VLM call site and its surrounding `try/except` and failure-latch calls, then a normal app launch/health check without submitting a video request. Only after those checks should the single 20-second VLM-only request be issued.
