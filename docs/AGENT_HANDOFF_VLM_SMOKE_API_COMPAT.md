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
