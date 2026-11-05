# Agent handoff — VCE Metal runtime latch applied on target Mac (2026-11-05)

## Current state

- Repository: shushpanchik-art/VideoContext-Engine-Apple-Silicon
- Working branch: feat/experimental-benchmark-launcher
- Pull request: [#34 — Apple Silicon benchmark launcher: diagnostics and safe result classification](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34)
- PR is open and Draft, base main. Do not merge it or push changes to main.
- The user has now applied the Metal runtime-latch patch directly to the active target-Mac source file. This is the first confirmed write to the local runtime; earlier operations were dry-runs only.

## Target-Mac write confirmation

The user supplied terminal output confirming:

    Source:  ~/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py
    Original SHA256: eb0708be06944503a9a2ad3429b8f27938356d1d16816634855fc93b7cda4f2c
    Patched SHA256:  eacd4eecfda2cc8a2c71203bf5ae3589611f5fd8d0405ded747146a78f147055
    Backup: ~/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py.backup-20261105T000114

The patcher reported "Patched in place" and printed the backup path. The source file hash matched the expected original hash immediately before application.

Important validation gap: the user pasted a suggested py_compile / backup verification command after the apply output, but did not provide its execution result. Do not claim local syntax validation or backup verification has passed until the next agent obtains that output.

## Immediate next actions

1. Ask the user to run this read-only verification in Terminal and share the output:

    SOURCE="$HOME/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py"
    BACKUP="$SOURCE.backup-20261105T000114"
    python3 -m py_compile "$SOURCE" &&
    test -s "$BACKUP" &&
    echo "OK: syntax valid and backup exists."

   If it fails, stop. Do not restart VCE. Capture the exact error and restore from the named backup only with explicit user confirmation and a clear recovery plan.

2. If syntax and backup verification pass, ask the user to restart the VCE service/application once, ensuring the active listener loads the patched source. Do not kill arbitrary Python processes; identify the VCE process and its launch mechanism first.

3. After restart, confirm the listener PID and source path. The earlier diagnostic archive tied listener PID 8742 to this active project path, but that PID may have changed after restart.

4. Run only a minimal VLM-only diagnostic first. Then run Whisper-only independently. A combined VLM + CPU Whisper request is allowed only if both isolated paths complete. Do not run the six-profile benchmark matrix yet.

5. Treat any Metal timeout / SubmissionsIgnored as a failure. Confirm the latch log records the first failing stage/PID and that subsequent VLM/Text LLM GPU work is refused. Confirm independent CPU Whisper/OCR/report work behaves as expected. A successful HTTP response alone is not acceptance evidence.

6. Keep detailed server logs, local reports, video-derived text, source paths containing private data, and raw diagnostic archives out of GitHub. Only add sanitized findings to the PR.

## Patch behavior and limitations

The patcher patches/patch_vce_metal_runtime_latch.py installs a process-local fail-closed latch for fatal Metal command-buffer timeout / submission rejection:

- records the first failure and refuses subsequent MLX GPU submissions;
- catches executor-level failures and prevents Text LLM streaming fallback from retrying another GPU generation after a fatal Metal error;
- skips VLM model unload and MLX cache/synchronization cleanup after fatal timeout, including shutdown, to avoid further operations on a potentially poisoned queue;
- allows later requests to skip VLM and continue through independent audio/OCR/report paths when possible;
- resets only after VCE process restart.

This is recovery hardening, not a fix for the underlying Metal/IOGPU timeout. It does not establish the root cause, guarantee CPU pipeline success, or prove that a restart repairs the GPU runtime.

## Repository / CI state

- The patcher, seven fixture tests, dedicated workflow, and investigation documentation are in the feature branch / PR #34.
- The seven-test focused GitHub Actions run passed at the recorded commit in [run 41958576434](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41958576434).
- Those are representative-fixture tests, not a complete-source integration test and not an M1 Max runtime test.
- The local target-Mac patch is not automatically reflected in GitHub's runtime source; the repository contains the patch helper that generated it.
- Keep PR #34 Draft until the local compile check and controlled target-Mac validation are complete. Do not merge.

## Prior diagnostic context

The diagnostic archive showed two distinct error classes:
1. true Metal allocation OOM events in earlier requests, handled with bounded lower-resolution retries;
2. command-buffer timeout followed by kIOGPUCommandBufferCallbackErrorSubmissionsIgnored during actual VLM generation, with later Text LLM and VLM attempts cascading in the same process.

Qwen3-VL model loading and explicit Metal smoke tests had succeeded before one captured inference timeout. Whisper was configured on mx.cpu; overlap with VLM was observed, but causation was not established. Do not present unified-memory pressure, driver defect, or a specific queue cause as proven.

See [the detailed investigation](VCE_METAL_RUNTIME_LATCH_PATCH_2026-11-05.md) and [PR #34](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34).


---

## 2026-11-05 10:29–10:42 target-Mac follow-up: full-video failure and isolated VLM smoke test

### Evidence supplied from the target Mac

Hardware/runtime context from earlier diagnostics: MacBook Pro M1 Max, 32 GiB unified memory, MLX 0.32.4, VCE v3.19, Qwen3-VL-8B-Instruct-4bit. The user supplied terminal excerpts and parsed report metadata; raw logs and video-derived content remain local.

#### A. Full analysis of the 108.23-second NSR video — FAILED / DEGRADED

Sanitized report metadata:

- Duration: 108.23 s; scenes: 14; analysis mode: `full`.
- Report process time: 2687.53 s (~44 min 48 s).
- VLM timing: load 13.50 s; inference 2659.27 s.
- Whisper timing: inference 330.00 s; telemetry identifies `mlx-whisper` on CPU stream `mx.cpu`.
- OCR and global summary were requested.
- Text LLM post-processing was enabled but failed because the process had already latched a fatal Metal error.
- Runtime emitted `kIOGPUCommandBufferCallbackErrorTimeout` at stage `vlm_generation`, PID 37250, then disabled VLM for the remainder of the video and refused subsequent Text LLM GPU work until process restart.
- The request eventually logged HTTP 200 in one run despite the fatal stage failure; other retries logged HTTP 500. HTTP status is not stage acceptance.
- The report's `params.run_vlm` field was `False` even though VLM timing reports 2659.27 s and runtime trace shows VLM execution. This metadata contradiction must be investigated in report serialization/request normalization. Do not infer that VLM was never run from this flag alone.

A prior run in the same log also showed VLM inference 1743.99 s, total process time 1789.97 s, followed by Metal submissions-ignored errors, Text LLM fallback, and HTTP 500 retries. These are independent failed attempts, not part of the isolated 20-second test below.

#### B. Restarted VLM-only 20-second smoke test — PASS for the narrow test

The user created a separate 20-second clip (946 KB) from the original video; the original was not modified. The test request explicitly disabled audio/Whisper, Apple OCR, FFmpeg OCR, Text LLM and global summary; VLM resolution 768, one keyframe per scene.

Observed result:

- VCE was initially not running; the test opened the app and identified VCE PID 47366.
- API returned HTTP 200.
- Video duration: 20.0 s; 2 scenes.
- VLM remained enabled; both scenes returned visual descriptions (2/2).
- VLM load: 5.615 s; inference: 11.544 s.
- Whisper/audio skipped; OCR and summary disabled; Text LLM disabled.
- The test printed `RESULT: VLM returned visual descriptions.`

**Interpretation:** this is positive evidence that a clean/restarted process can complete a short VLM-only request. It does not prove that the runtime latch handled a fresh fatal timeout, because no new timeout is shown for PID 47366 in the supplied excerpt. The Metal-event grep output included historical events from PIDs 8742 and 37250, including the prior timeout; those lines must not be attributed to PID 47366. The log file is cumulative.

#### C. Current conclusions

1. The long full-video run is not acceptable: 44m48s for a 108s clip, with a fatal Metal timeout and failed Text LLM post-processing.
2. The 20-second VLM-only run is a narrow positive smoke test: 2/2 scenes, HTTP 200, 11.544s inference.
3. Root cause of the Metal timeout remains unknown. Do not attribute it to RAM pressure, CPU Whisper overlap, GPU queue scheduling, a driver defect, or the model without new evidence.
4. The latch is recovery/fail-closed hardening; it is not a performance fix and does not prevent the first timeout.
5. The log is cumulative. Correlate every event by PID and timestamp. Do not use unfiltered `grep | tail` output as evidence that a new process reproduced an old failure.
6. Current server state after the smoke test is not established by the supplied output. The test PID is known, but no final `ps`/listener snapshot was included.

### Next-agent plan (strict order)

1. **Static inspection first:** inspect the runtime-latch patch/helper and the exact VCE source around the Metal probe/configuration, VLM generation wrapper, fatal-error classifier, and report metadata serialization. Specifically resolve why `params.run_vlm=False` coexists with positive VLM timing and scene descriptions.
2. **Verify process identity:** capture current listener PID, process command/source path, and launchd job. Do not kill unrelated Python processes.
3. **Use fresh per-test logs:** record a byte offset or copy/truncate only a dedicated test log after preserving the original; filter by the test PID and request time. Never classify historical PIDs as a new failure.
4. **Repeat the short VLM-only test once after a clean restart**, recording request ID, PID, wall time, per-stage timing, scene descriptions count, runtime errors, and final process/listener state.
5. **Test latch behavior only if a fatal Metal event occurs naturally:** confirm first-failure PID/stage/error is recorded and later GPU operations are refused. Do not deliberately induce a GPU timeout or run a long full-video test just to trigger it.
6. **Do not run the full 108-second video, Text LLM, concurrent Whisper+VLM, or benchmark matrix yet.** The earlier full run consumed ~45 minutes and failed. First establish repeatable short VLM-only stability and resolve report metadata inconsistency.
7. If the isolated VLM test is clean and repeatable, run Whisper-only separately. Only then consider a controlled combined test.
8. Keep all raw logs, report JSON, source paths, video names, transcript/OCR text and generated content local. Commit only sanitized summaries.

### Acceptance status

- [x] Target-Mac patched source syntax check and backup existence were reported successful earlier by the user.
- [x] Short VLM-only 20-second request: HTTP 200, 2/2 scenes with descriptions, 11.544 s inference.
- [ ] Repeat short VLM-only test with fresh PID-scoped diagnostics.
- [ ] Confirm latch behavior on a naturally occurring fatal Metal event (if one occurs).
- [ ] Resolve `run_vlm=False` vs positive VLM timing/report output contradiction.
- [ ] Whisper-only validation after VLM isolation is stable.
- [ ] Current-head CI and full target-Mac integration validation.

**Keep PR #34 Draft. Do not merge.** The new smoke test is not evidence of full-video stability or a fix for the underlying Metal timeout.
