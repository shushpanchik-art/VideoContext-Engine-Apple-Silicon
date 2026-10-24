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


## Качественная проверка ASR/OCR (пользовательский прогон, 2026-10-24)

- Один ролик длиной 108.23 s, 14 сцен: /ui и /api/v1/analyze завершились успешно; Whisper large на MPS — 30.49 s, Qwen3-VL-8B — 209.02 s, Apple OCR — 8.48 s, Qwen3.5 text post-processing — 93.49 s, общий process_time — 368.56 s.
- Улучшенные описания сцен 13–14 осторожно разделяют аудиосодержание и видимый интерфейс. Это качественное наблюдение по одному ролику, не объективная оценка WER/CER и не A/B-сравнение.
- В `meta.global_summary` и `enhanced_analysis.executive_summary` используется СП 1.13130.2020, но `enhanced_analysis.topics` всё ещё содержит ошибочную форму СП 1.13.132.020. `enhanced_analysis.original_summary` оставлен исходной сводкой до постобработки; `audio_transcript` и `ocr_text` должны сохраняться как исходные данные. Нормализацию производных полей нельзя считать полностью исправленной.
- Факт применения редактируемой инструкции ASR/OCR не подтверждается одним логом анализа; ручная проверка поля в UI и сохранения настройки нужна отдельно.


## ASR/OCR consistency guard v2 (prepared patch, 2026-10-24)

- New optional patch: `patch_vce_name_consistency_v2.py`.
- It targets extended VCE builds that already have Qwen3.5 text post-processing. It adds a final cross-field instruction and deterministic correction for the known СП 1.13130.2020 misread, gated by exact OCR confirmation.
- Raw transcript, OCR, visual description, timestamps and original_summary remain unchanged.
- The patch has been syntactically and synthetically smoke-tested; it has not been applied to the user's Mac from this chat.
