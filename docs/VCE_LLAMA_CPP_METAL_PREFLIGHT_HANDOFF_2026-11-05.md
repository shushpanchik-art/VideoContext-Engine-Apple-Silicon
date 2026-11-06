# llama.cpp Metal preflight false-negative — next-agent handoff (2026-11-05)

## Current state

PR #34 remains **open / Draft**. Do not merge it, mark it ready, or change `main`.

- Repository: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`
- Branch: `feat/experimental-benchmark-launcher`
- The user's local project is `~/VideoContext-Engine-Apple-Silicon`.
- The launcher is a configuration/startup tool only. It does not submit a video.
- The user stopped the previously running VCE PID 86223. A subsequent `ps -p 86223` showed no process. Do not assume a VCE listener is still active.
- No local Mac files, settings, backups, model cache, or Homebrew packages were modified by this GitHub-side work.

## Target-Mac evidence received

The user ran:

```text
/opt/homebrew/bin/llama-server --version
version: 0.7.0 (build 11669, commit 620d2225c)
built with AppleClang 21.0.0.21000334 for Darwin arm64

/opt/homebrew/bin/llama-server --list-devices --verbose
Available devices:
  BLAS: Accelerate (0 MiB, 0 MiB free)
  MTL0: Apple M1 Max (25559 MiB, 25558 MiB free)
exit_code=0

brew list --versions llama.cpp ggml
ggml 0.27.0
llama.cpp 0.7.0

file /opt/homebrew/bin/llama-server
Mach-O 64-bit executable arm64
```

This is positive evidence that the installed native Apple-Silicon llama.cpp build advertises an MTL Metal device. The earlier launcher failure was a **false negative**: it checked only for the literal substring `metal`, while llama.cpp 0.7.0 labels the Apple device `MTL0`.

## Changes made remotely

The feature branch has been updated:

1. `patches/patch_vce_fast_profile_mps.py`
   - Runtime preflight now accepts either an explicit `Metal` label or a device identifier matching `MTL<number>:`.
   - CPU fallback remains forbidden. A non-zero device-probe exit or absence of both forms still fails closed.
2. `scripts/launch_vce_fast_profile_mps.command`
   - The initial preflight uses the same MTL-aware detection.
   - Its immutable `PATCHER_REF` is pinned to the updated patcher commit.
3. `tests/test_vce_fast_profile_mps.py`
   - Regression test accepts `MTL0: Apple M1 Max` and `Metal: Apple GPU`.
   - It rejects the unrelated `BLAS: Accelerate` line.
   - It verifies that both launcher and runtime patcher use the MTL-aware matcher.

The regular expression is case-insensitive and recognizes `\\bMTL\\d+\\s*:` as well as the word `Metal`.

## Next exact action on the target Mac

Do not reinstall llama.cpp. Do not delete MLX packages. Do not start a video request yet.

Run the current branch launcher:

```bash
export VCE_PROJECT_DIR="$HOME/VideoContext-Engine-Apple-Silicon"

curl -fsSL \
"https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/experimental-benchmark-launcher/scripts/launch_vce_fast_profile_mps.command" \
-o /tmp/launch_vce_fast_profile_mps.command

bash /tmp/launch_vce_fast_profile_mps.command
```

Expected preflight: it should accept `MTL0: Apple M1 Max` and report llama.cpp/Metal available. It will then apply or verify the profile, start VCE, and check the live API settings.

If it fails:
- Do not start VCE manually.
- Share only the failing step and sanitized error text.
- If a patcher fails, inspect timestamped backups before retrying; never overwrite/restore user files blindly.
- Keep raw logs, local paths, API responses, video-derived content and model output private.

If it succeeds, verify from the live API:
- `vlm_model=Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M`
- `text_llm_model=gguf/Qwen3.5-9B-GGUF:Q4_K_M`
- `whisper_model=large`
- `whisper_acceleration=True`
- Apple Vision OCR, OCR cache and FFmpeg/Tesseract OCR remain enabled.
- The UI shows the two distinct GGUF model IDs and preserves the OCR/Whisper controls.

Then inspect the VCE server log for the first VLM invocation and confirm that the managed `llama-server` announces `backend=llama.cpp; device=Metal`. The first GGUF downloads/model load may take time. The VLM and Text LLM are intentionally distinct models loaded sequentially in one managed server process; do not assume both are resident at once.

Only after successful profile/API/UI verification should the next agent plan a single short diagnostic request. Do not run the full input video, a benchmark matrix, or concurrent heavy workloads yet. Preserve the previously agreed staged validation and Metal-failure caution.

## Validation still required

- Run current-head GitHub Actions after these changes.
- Confirm patcher compile and focused tests, including the MTL0 regression.
- Confirm launcher shell syntax and installer/distribution validation.
- The user's target-Mac launcher has **not yet been rerun** with this fix.
- No target-Mac VLM/Text LLM GGUF inference has been validated by this change.
- The previous positive llama-server device listing validates Metal device enumeration, not model loading, inference correctness, throughput, or stability.

## Safety / scope

- Keep PR #34 Draft.
- Do not merge or modify `main`.
- Do not change the user's local `main`, local working tree, settings or backups.
- Do not remove the user's installed MLX packages; MLX runtime use is removed by the profile, package deletion is unnecessary.
- Do not claim performance improvement until controlled target-Mac measurements exist.
