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
