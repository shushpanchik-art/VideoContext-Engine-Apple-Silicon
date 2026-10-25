# VideoContext Engine: parallel CPU Whisper + GPU VLM patch

## Goal

The current VCE `analyze_video()` flow waits for full Whisper transcription, then loads VLM and starts scene analysis. This patch schedules Whisper inference in a worker thread and allows it to overlap with VLM loading and OCR/VLM scene analysis.

At the end of scene analysis, it awaits Whisper and backfills `audio_transcript` and optional `audio_features` into each scene before global summary generation. The summary therefore still has audio + visual context.

## Important trade-off

Per-scene VLM prompts may not contain Whisper speech text, because the full transcript may not yet be available while VLM is analyzing scenes. The prompt still includes the base visual instructions and OCR. The final scene records and global summary include the transcript after backfill. This is intentional to avoid serializing ASR before VLM; please compare report quality as well as speed in the first test.

## Safety / memory

- CPU mode is enforced for this parallel run: `whisper_device="cpu"`, MPS acceleration disabled, MLX Whisper disabled for this request. This avoids Whisper competing with VLM for Metal GPU capacity.
- A background Whisper task is awaited before the endpoint removes its temporary video, including error-cleanup paths.
- The global-summary VLM inference is routed through the already-installed Metal-memory fallback too.
- Existing settings are preserved except `whisper_acceleration=false`, `enable_mlx_whisper=false`, and `enable_stage_timing=true`.
- The patcher is fail-closed: it refuses to write if expected code anchors or the temporary-file cleanup `finally` cannot be matched.
- It creates timestamped backups before writing.

## Install

First, let any active VCE job finish and stop the local server. Copy `patch_vce_parallel_cpu_asr_vlm.py` to `~/VideoContext-Engine/patches/`.

Dry-run first:

```bash
PROJECT="$HOME/VideoContext-Engine"
PATCH="$PROJECT/patches/patch_vce_parallel_cpu_asr_vlm.py"

"$PROJECT/.venv/bin/python" -m py_compile "$PATCH"
"$PROJECT/.venv/bin/python" "$PATCH" --project "$PROJECT" --dry-run
```

Review the diff. If dry-run reports an integration mismatch, do not apply; share the error output so the patch can be adapted to the current source.

Apply only after the diff looks correct:

```bash
PROJECT="$HOME/VideoContext-Engine"
PATCH="$PROJECT/patches/patch_vce_parallel_cpu_asr_vlm.py"

"$PROJECT/.venv/bin/python" "$PATCH" --project "$PROJECT"
"$PROJECT/.venv/bin/python" -m py_compile "$PROJECT/VideoContextEngine_v3.19.py"
"$PROJECT/.venv/bin/python" -m json.tool "$PROJECT/videocontext_settings.json" >/dev/null
```

Restart VideoContext Engine before testing.

## Validation status

The patcher passed Python syntax checks and synthetic source-fixture tests covering source transformation, idempotence, summary fallback routing, CPU Whisper enforcement, and cleanup waiting. It has **not** yet been run on the user's actual Mac runtime or benchmarked end-to-end on a real video. First compare a short clip's total runtime and report quality; inspect `stage_diagnostics` in its JSON output. The performance gain depends on overlap between ASR inference and VLM/OCR work.