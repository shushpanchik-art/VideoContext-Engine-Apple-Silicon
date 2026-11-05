# Fast MPS launcher patch mismatch — next-agent handoff (2026-11-05)

## User's latest target-Mac attempt

The user ran the published launcher from PR #34 on their MacBook Pro:

```bash
export VCE_PROJECT_DIR="$HOME/VideoContext-Engine-Apple-Silicon"
curl -fsSL \
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/experimental-benchmark-launcher/scripts/launch_vce_fast_profile_mps.command" \
  -o /tmp/launch_vce_fast_profile_mps.command
bash /tmp/launch_vce_fast_profile_mps.command
```

Preflight succeeded:

- PyTorch 2.15.0; openai-whisper 20250625
- PyTorch MPS built/available
- MLX/Metal available
- Apple Vision importable
- MLX Whisper not imported
- FFmpeg + Tesseract, Russian and English language data available

The launcher then downloaded the pinned patcher and stopped during patch application:

```text
ERROR: ValueError: Sequential Whisper-before-VLM scheduling:
expected 1 exact match(es), found 0; no files were written.
```

This is a patch/source compatibility failure, **not** an MPS, MLX, OCR, or VCE runtime failure. The patcher's fail-closed behavior worked as intended. The launcher did not proceed to server startup.

## Exact local repository state reported by user

The user's terminal inspection reported:

- Local branch: `main`
- Local HEAD: `3d9ee78`
- Engine source: modified (uncommitted local changes exist)
- Project path: `~/VideoContext-Engine-Apple-Silicon`

The user searched `VideoContextEngine_v3.19.py` and supplied this scheduling excerpt (line numbers from their local file):

```python
if not skip_audio:
    _vce_whisper_task = asyncio.create_task(_vce_run_whisper_cpu_stage())

if run_vlm and _vce_metal_runtime_failure() is not None:
    run_vlm = False
    print("[VCE MLX] Skipping VLM for this request because this process has a latched Metal failure; restart VCE to restore GPU inference.", flush=True)
if run_vlm:
    release_accelerator_memory('before VLM load')
    set_job_progress(job_id, "load_vlm", None, "Загружаем визуальную модель Qwen3-VL", indeterminate=True, stage_index=3, stage_count=6)
    t0 = time.time()
    await _vce_mlx_run(vlm_engine.load_model, target_vlm)
    t1 = time.time()
    vlm_load_time = vlm_engine.last_load_time or (t1 - t0)
else:
    vlm_load_time = 0.0
```

Immediately before this excerpt, the Whisper CPU stage completes and returns `result`; its finally block records `_vce_whisper_finished_at`. The task is created at line 5333 in the user's excerpt. The VLM Metal-latch guard between task creation and `if run_vlm:` is absent from the patcher's expected exact template.

## Why the patch failed

The patcher in this PR currently searches for this exact two-part block:

```python
if not skip_audio:
    _vce_whisper_task = asyncio.create_task(_vce_run_whisper_mps_stage())
    # Await ASR before loading Qwen: MPS Whisper and MLX/Metal VLM
    # are intentionally serialized on the shared Apple GPU.
    whisper_segments = await _vce_whisper_task

if run_vlm:
```

The user's local source still names the task function `_vce_run_whisper_cpu_stage`, and it has an intervening Metal-runtime-failure guard. Therefore the expected exact match count is zero. Do **not** respond by weakening `_replace_exact`, removing the Metal guard, or accepting an ambiguous/zero-match patch.

## Required next-agent work

1. **Inspect the actual local source version.** The user has uncommitted local engine changes on `main` at `3d9ee78`; the complete local file is not present in this handoff. Do not assume the PR branch source and user's local source are identical. Ask the user for a sanitized relevant source excerpt or a local diff if additional context is required; never ask them to discard local changes.
2. Read the full Whisper stage function and the full VLM/Metal guard section, not just the excerpt. Confirm task result/error semantics, whether any later code awaits `_vce_whisper_task`, and whether the Metal latch guard must remain active.
3. Adapt the patcher for the real source shape with narrow, explicit anchors. The intended transformation is:
   - preserve the Metal runtime failure guard unchanged;
   - create the PyTorch Whisper MPS task;
   - await the task and propagate its result/failure before VLM model loading;
   - only then, if `run_vlm` remains true, release accelerator memory and load/run the MLX/Metal VLM;
   - ensure no un-awaited Whisper task or overlapping Whisper-MPS/VLM-Metal work remains on the success path.
4. Handle both relevant source variants only if each is independently understood and covered by tests. Prefer a small dedicated transformation helper over broad text replacement. Keep exact expected counts, explicit postconditions, AST parsing, idempotence and rollback semantics.
5. Add regression fixtures using the exact local excerpt shape, including the intervening Metal-latch guard. Test the resulting order and preservation of the guard. Test fail-closed behavior for unknown variants and duplicate anchors.
6. Update the launcher's immutable patcher commit pin only after the new patcher is committed. Do not use a mutable branch URL for the patcher payload.
7. Run patcher unit tests, `py_compile`, launcher `bash -n`, distribution validation and full current-head CI. Re-check the diff for unrelated edits.
8. Only after static checks pass, ask the user to rerun the launcher. Verify a new timestamped backup exists and inspect the resulting profile before starting VCE. Then validate the runtime Whisper log explicitly reports `backend=openai-whisper device=mps model=large`. Do not run a full-length video benchmark until isolated Whisper and short VLM checks pass.

## Safety and state constraints

- Do not rerun the current failing launcher unchanged.
- Do not manually edit the user's local engine file or ask them to reset/checkout over uncommitted changes.
- Do not kill processes, overwrite settings, or start VCE without the user's explicit next-step authorization.
- Do not merge PR #34, mark it ready, or modify `main`.
- Keep PR #34 Draft and open.
- No claim that the profile was applied: the patcher reported no files were written.
- The earlier isolated PyTorch Whisper MPS smoke test is only backend smoke evidence; it does not validate VCE integration.
- Preserve OCR cache and FFmpeg/Tesseract OCR in the target profile. The only allowed experimental optimization is OCR cache.
- Keep raw logs, video-derived text, private paths and media out of the public repository.

## Current PR

Existing Draft PR #34:
https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34

This handoff supplements, and does not replace, the earlier fast-profile/MPS handoff and the broader Metal investigation handoff.


---

## Follow-up — guarded scheduler patcher update

A feature-branch patcher update was committed after reviewing the exact target-Mac scheduling shape.

- Patcher commit: `c8368888225f463a7b4aa76fe2eb66dbaad58ae5`
- Launcher pin update: `f93cadac83de0a2a1c65250fd250fe18652e44f0`
- Regression test: `tests/test_vce_fast_profile_mps_scheduler.py`
- PR #34 remains open and Draft; `main` is unchanged.

The scheduler transformation no longer replaces the complete task/guard/VLM block. It finds exactly one PyTorch Whisper task-creation line and inserts `await _vce_whisper_task` immediately after it. This preserves any intervening local Metal-failure guard byte-for-byte and ensures the VLM branch occurs after Whisper completion. Missing or duplicate task anchors fail closed. The regression fixture models the reported CPU-stage source name after the patcher's existing explicit CPU-to-MPS rename and includes the intervening Metal guard.

**Validation status:** repository files and the new test were committed, but this handoff does not claim that target-Mac patch application, Python test execution, or current-head CI has passed. No local engine, settings, profile, VCE process, or video was touched by this repository update. Do not run the full launcher until the patcher tests and CI pass; then rerun the launcher only after reviewing a fresh local backup and the resulting diff.
