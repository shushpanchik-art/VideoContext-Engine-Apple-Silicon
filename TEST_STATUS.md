# Test status (user-provided logs, 2026-10-23)

## Confirmed by logs

- `python patch_vce_save_next_to_video.py` completed with `Patch installed successfully.` and timestamped Engine/UI backups.
- `patch_vce_stop_and_mps.py` completed with `Patch installed successfully.`; it reported the exact target `.venv/.../whisper/timing.py` and the corrected conversion order.
- macOS installer completed with `ГОТОВО`; it reported `/Applications/VideoContext Engine.app` and the desktop alias.
- `GET /ui`, `GET /api/v1/ui-settings`, `POST /api/v1/ui-settings`, and `POST /api/v1/select-local-videos` returned HTTP 200.
- A video analysis completed with `POST /api/v1/analyze` HTTP 200, Whisper MPS inference 46.13 s, VLM inference 140.31 s, Apple OCR 5.65 s, total 210.44 s.

## Not yet evidenced by the supplied log excerpt

- Successful completion of all items in a multi-video queue; the excerpt ends while Whisper for the next video is loading.
- The actual filesystem paths of the TXT and JSON output files. The output path should be verified on disk; HTTP 200 proves successful response, not by itself that outputs landed in the intended source folder.
- User-triggered cancellation during Whisper and during a Qwen inference.

## Static checks performed while preparing the patch set

- Python patchers compile with `py_compile`.
- The macOS orchestration script passes `bash -n`.
- Package file list is checked; the patched upstream engine is not copied into this repository.
