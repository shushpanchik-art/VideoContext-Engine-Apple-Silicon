# VCE Metal failure investigation — runtime latch patch (2026-11-05)

## Executive finding

The supplied read-only diagnostic archive contains evidence of two distinct Metal failure classes:

1. **Allocation / Metal-memory failures** in earlier requests. The \`server.log\` excerpt contains three \`[VLM memory guard] Metal OOM\` starts (original log lines 8,899, 43,416 and 55,009), each followed by bounded retries at lower image resolution (384 / 320 / 256 px). These records support genuine Metal allocation pressure in those requests.
2. **Command-buffer timeout / queue-rejection failures** in separate requests. For PID 12,863, the first captured fatal event is \`kIOGPUCommandBufferCallbackErrorTimeout\` during \`mlx_vlm.generate.ar.generate_step\` (trace at original log line 78,403; traceback ends at line 78,442). The preceding log reports successful \`MLXEngine.load_model\` and passing explicit GPU-stream/Metal smoke tests. The same PID subsequently receives \`kIOGPUCommandBufferCallbackErrorSubmissionsIgnored\` (lines 78,685 / 78,724).

For PID 8,742, the excerpt shows the VLM memory guard disabling VLM after a timeout/submission rejection (line 114,110). The same process then attempts \`run_text_llm_postprocess\`, which receives \`SubmissionsIgnored\` (line 114,168), followed by another \`MLXEngine.load_model\` attempt that is also rejected (line 114,628 and later tracebacks). This is a recoverable-control-flow defect: after a fatal GPU error the process still enters another GPU-backed stage.

The archive does **not** establish the initiating low-level GPU fault. It also does not prove that concurrent CPU Whisper caused the GPU timeout. Whisper is explicitly assigned to \`mx.cpu\`, but it runs concurrently with GPU VLM and could still add unified-memory / scheduling load. That remains a variable for a controlled VLM-only versus concurrent run.

## What the runtime code shows

- \`_vce_configure_mlx_metal()\` sets the default device to GPU and probes an explicit GPU stream with \`mx.eval()\` / \`mx.synchronize()\`.
- \`_vce_prepare_mlx_vlm_runtime()\` rebinds mlx-vlm's generation stream to a thread-local GPU stream. The log shows this configuration and stream binding succeeding in affected processes.
- \`_vce_describe_scene_with_memory_fallback()\` distinguishes command-buffer timeout / \`SubmissionsIgnored\` from ordinary allocation OOM. For timeout it disables VLM for the current video and returns a fallback sentinel. Ordinary allocation OOM still uses bounded smaller-image retries.
- \`analyze_video()\` starts CPU Whisper asynchronously before VLM model loading. Whisper runs on the CPU stream while VLM work uses the GPU stream.
- The timeout fallback currently disables VLM only for that request. It does not record a process-wide failed Metal state, so later Text LLM / VLM calls can still reach MLX/Metal.
- \`run_text_llm_postprocess()\` independently configures Metal and loads an mlx-vlm model. Therefore it can submit more work after the earlier VLM timeout.
- The timeout branch previously called \`vlm_engine.unload_model()\` and the shared cache cleanup helper; the latter calls \`mx.synchronize()\` before \`mx.clear_cache()\`. Because the unload implementation was not available in the diagnostic excerpt and could itself synchronize or release MLX arrays, the patch now skips both unload and cache operations after a fatal timeout. GPU resources remain resident until the required process restart. The ordinary allocation-OOM recovery branch is unchanged.

## Proposed defensive change

\`patches/patch_vce_metal_runtime_latch.py\` applies an additive, idempotent patch to the locally installed \`VideoContextEngine_v3.19.py\`:

1. Adds a process-local, thread-safe \`FAILED\` latch that records only the first fatal Metal timeout / \`SubmissionsIgnored\` event (stage, PID, thread, timestamp and bounded exception text).
2. The central \`_vce_mlx_run()\` executor refuses new GPU work once latched and latches uncaught timeout errors, including those raised from Text LLM.
3. The VLM timeout fallback latches the process before returning its existing fallback sentinel.
4. \`_vce_configure_mlx_metal()\`, \`_vce_prepare_mlx_vlm_runtime()\` and \`run_text_llm_postprocess()\` fail closed before starting GPU operations if the process is already latched.
5. Later analyze requests skip VLM model loading and continue on the normal audio/OCR/report path when possible.
6. Model unload and cache/synchronization calls are skipped in the fatal-timeout branch and during application shutdown because their implementation may touch the failed Metal queue. GPU memory remains resident until the process exits; the OS then reclaims it. The separate ordinary allocation-OOM recovery path remains unchanged.
7. The latch resets only when the VCE process is restarted. This is intentional: the patch does not try to repair a failed Metal command queue in place.

This patch is **recovery hardening, not a fix for the initiating GPU timeout**. It does not suppress or reclassify Metal errors as success, alter settings, change model sizes, disable CPU Whisper, or touch \`main\`.

## Applying safely on the target Mac

The latest traceback for the listener PID 8742 points to `~/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py`; older log entries also refer to a separate `~/VideoContext-Engine` copy. Use the active PID 8742 path below. Do not patch both copies unless their active server ownership is independently confirmed. The patcher writes a separate candidate file by default and does not overwrite the installed runtime:

\`\`\`bash
python3 patches/patch_vce_metal_runtime_latch.py \\
  "$HOME/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py"
\`\`\`

It prints a unified diff and writes \`VideoContextEngine_v3.19.py.metal-latch.py\` next to the input file. Review that diff first. To apply in place only after review:

\`\`\`bash
python3 patches/patch_vce_metal_runtime_latch.py \\
  "$HOME/VideoContext-Engine-Apple-Silicon/VideoContextEngine_v3.19.py" --apply
\`\`\`

\`--apply\` creates a timestamped backup before writing. If the actual source has drifted from the known anchors, the patcher exits without writing. It is idempotent.

After applying, restart VideoContext Engine once so the patched module is loaded and the process-local Metal state is clean. Then validate in this order: (a) one minimal VLM-only request, (b) one Whisper-only request, (c) a combined request only if both isolated paths complete. Do not run the full benchmark matrix during diagnosis. A required VLM/Text LLM stage must be marked failed/degraded if it is skipped by the latch; HTTP 200 alone is not acceptance evidence.

## Validation completed here

- At commit `f9a02cb9d35d18801eca821707a0c2e9a1531670`, the benchmark-tests GitHub Actions job discovered and passed the initial four fixture tests (patch generation/parse, idempotence, first-failure latch semantics, and fail-closed behavior for missing anchors).
- Subsequent revisions added an executor-level timeout/latch test, shutdown-teardown protection, and assertions that the fatal-timeout path does not call model unload or MLX cache/synchronization helpers. No CI result has been retrieved for the latest branch head, so those later changes remain unverified by GitHub Actions.
- All tests use a representative source fixture. They are not a substitute for running the patcher against the complete local engine file or testing on the M1 Max Metal runtime.

## Remaining unknowns

- The macOS unified-log collection timed out, so no native IOGPU event window was included in the archive.
- The diagnostic script queried the Python 3.11 executable in a clean child process and reported MLX packages as not installed. That does not match the serving process's effective import environment; VCE's own runtime trace reports MLX 0.32.4. Do not use the child-process package result to infer that the live server lacks MLX.
- The log excerpt does not contain reliable timestamps for each physical line; PID separation is important and must be retained when correlating events.
- The low-level initiating Metal workload / OS driver event remains unknown. A controlled VLM-only reproduction with a narrow unified-log capture is still needed to investigate the root cause.
