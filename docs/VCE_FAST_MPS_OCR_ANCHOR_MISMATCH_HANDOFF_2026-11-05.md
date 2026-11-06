# Next-agent handoff — Fast MPS profile OCR anchor mismatch (2026-11-05)

## Status

PR #34 remains **Draft / open**:
https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/34

Feature branch: `feat/experimental-benchmark-launcher`.

The target-Mac launcher was executed by the user on 2026-11-05. Preflight passed:
- PyTorch 2.15.0; MPS available.
- MLX/Metal available.
- Apple Vision importable.
- FFmpeg and Tesseract available, with Russian (`rus`) and English (`eng`) traineddata installed.

The profile patcher then failed closed before writing files:
`ValueError: Settings API OCR defaults: expected 1 exact match(es), found 0`.

The launcher explicitly warned not to use a partial profile. No VCE launch or video submission followed this failure.

## Target Mac: observed active settings

Project directory:
`~/VideoContext-Engine-Apple-Silicon`

Active `videocontext_settings.json` values:
- `enable_apple_ocr = true`
- `apple_ocr_languages = ["ru-RU", "en-US"]`
- `enable_ffmpeg_ocr = false`
- `ffmpeg_ocr_workers = 2`
- `recognition_languages = ["ru", "en"]`

Tesseract binary: `/opt/homebrew/bin/tesseract`.
Installed language list includes both `rus` and `eng`.

The UI settings API in the local engine (around lines 3821–3838) currently has:
```python
# VCE_FFMPEG_OCR_WORKERS_SETTING_V1
defaults = {
    "analysis_mode": "full", "recognition_languages": _vce_legacy_apple_languages(persisted.get("apple_ocr_languages", ["ru-RU", "en-US"])), "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,
    "ffmpeg_ocr_workers": 2,
    "enable_stage_timing": False,
    ...
}
```

The PR patcher currently expects this exact compact anchor:
```python
'"analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 2,'
```

That expected text does **not** occur because the target engine's defaults dict has the additional `recognition_languages` item between `analysis_mode` and `enable_ffmpeg_ocr`. This is the identified reason for the exact-match failure. Do not weaken exact-match checks or use a broad replacement.

## Local checkout safety

The user's local checkout is on `main` and has pre-existing modifications and untracked backups. Previously observed:
- Modified `VideoContextEngine_v3.19.py`, settings example, diagnostics docs, app launcher, patch scripts, tests, web UI.
- Untracked timestamped engine backups.

Do not checkout/switch branches, reset, clean, pull, restore, overwrite, delete, or run any patcher against that checkout. Do not inspect or alter unrelated `~/VideoContext-Engine` or snapshot directories. The local file `patches/patch_vce_fast_profile_mps.py` is absent from this main checkout; it exists on the PR feature branch. A grep attempted from the local checkout therefore returned “No such file”; this is not evidence the remote patcher is missing.

## Next-agent task

1. Work only on `feat/experimental-benchmark-launcher` / PR #34, not the user's local `main`.
2. Inspect the current remote patcher and exact target-engine code. Add a narrowly scoped anchor that includes the observed `recognition_languages` field while preserving exact-count validation and the intended default changes:
   - FFmpeg OCR enabled by default in the profile.
   - interval 5 seconds.
   - workers 3.
   Keep Apple Vision enabled and preserve language selection.
3. Add regression fixtures for both supported defaults-dict layouts if compatibility is intended; otherwise document the single supported layout. Test zero/multiple matches fail closed.
4. Run Python unit tests, patcher tests, `py_compile`, shell syntax checks, and current-head CI. Do not claim validation until the commands/check runs actually pass.
5. Before asking the user to retry, provide a reviewable diff and an explicit statement that the patcher remains fail-closed and creates backups. Do not retry against the local project without the user's explicit approval.
6. After an approved retry only: verify backups and syntax; do not start VCE or submit the private video until profile application is fully confirmed.

## Constraints

- Never change `main` or merge PR #34.
- Do not bypass exact-match protections.
- Do not alter the user's local project or settings during this handoff.
- Do not publish private video paths, filenames, OCR output, transcripts, model output, or raw logs.
- Existing successful short VLM smoke tests do not validate full-video processing, Whisper, OCR inference, Text LLM, or benchmark performance.
