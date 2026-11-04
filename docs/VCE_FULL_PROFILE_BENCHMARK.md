# Полный локальный benchmark VideoContext Engine для Apple Silicon

## Запуск одной командой

После установки проекта в `~/VideoContext-Engine` и авторизации GitHub CLI (`gh auth login`) укажите локальный путь к входному видео через `VCE_VIDEO_PATH`, затем запустите команду в Terminal. Приватный путь и имя видео намеренно не хранятся в репозитории. Launcher загружает с GitHub сжатый Python runner, распаковывает его во временный файл и удаляет после выполнения:

```bash
curl -fsSL https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/experimental-benchmark-launcher/scripts/run_vce_full_profile_benchmark.command | /bin/bash
```

Перед запуском задайте `export VCE_VIDEO_PATH="/path/to/private-input.mp4"`. Каталог проекта задаётся через `VCE_PROJECT_DIR` (по умолчанию `~/VideoContext-Engine`). Для автоматической публикации безопасной агрегированной сводки нужен установленный и авторизованный GitHub CLI: `brew install gh && gh auth login` (один раз).

Скрипт сам пытается открыть `/Applications/VideoContext Engine.app`, если API `127.0.0.1:7555` ещё не запущен. При нестандартном каталоге установки можно задать `VCE_PROJECT_DIR`; дополнительные аргументы передаются runner, например `--dry-run` или `--no-combined`.

## Порядок прогонов

1. **Baseline:** Qwen3-VL-8B 4-bit, Whisper large, полный режим, `scene_threshold=0.15`, `min_scene_duration=1.5`, `max_scene_duration=20`, один кадр/сцену, Apple Vision OCR, аудио-признаки, word timestamps и Qwen3.5 post-processing. FFmpeg/Tesseract OCR включается только при подтверждённой поддержке в текущем локальном API; интервал 5 секунд, три CPU workers.
2. **Адаптивные keyframes:** включается чекбокс и задаётся верхний предел 2 кадра, иначе feature при baseline `keyframes_per_scene=1` не имел бы пространства для изменения.
3. **OCR cache:** включается кэш Apple Vision OCR.
4. **MLX Whisper flag:** включается одноимённый флаг, отдельно сохраняется диагностическая оговорка о реализации текущего runtime.
5. **Fast scene detection:** включается выборка для scene detection с частотой один кадр/сек.
6. **All experimental:** все четыре экспериментальные функции включаются вместе для проверки их совокупного эффекта.

Каждый прогон выполняется последовательно на одном и том же файле и с теми же базовыми параметрами. `enable_stage_timing` включён во всех случаях только как инструмент измерения. TXT/JSON autosave отключены в отправляемой форме, чтобы тест не перезаписывал отчёты рядом с оригинальным видео; полный API JSON-ответ сохраняется в отдельной приватной папке benchmark.

## Что измеряется

Скрипт читает `/openapi.json` и `GET /api/v1/ui-settings`, отправляет локальный видеофайл через multipart API, опрашивает progress endpoint и собирает:

- общее wall time и VCE `process_time`;
- scene detection, загрузку/инференс Whisper и VLM, Apple Vision OCR, FFmpeg/Tesseract OCR, Qwen3.5 post-processing;
- CPU Whisper ↔ MLX/Metal VLM overlap, keyframe prefetch worker/wait;
- PID дерева процесса VCE, `%CPU`, RSS и memory pressure;
- доступные GPU power/residency samples через `powermetrics`.

GPU samples от `powermetrics` относятся ко всей системе, не к PID VCE. Поддержка `powermetrics` зависит от прав macOS; при отсутствии разрешений скрипт фиксирует, что sampler недоступен. Никакие GPU данные не будут выдаваться как per-kernel attribution.

## Важная особенность текущего кода v3.19

Несмотря на старую подпись поля `whisper_acceleration` в UI и описание экспериментального переключателя, текущий runtime явно возвращает CPU в `resolve_whisper_device()` и создаёт `_VCEWhisperMLXCPU` в `load_whisper_model()`. Вызов Whisper закрепляется на `mx.cpu`. `enable_mlx_whisper` выполняет preflight-валидацию совместимости модели, но не выбирает между MPS и MLX: текущий путь уже MLX CPU и при выключенном флаге. Поэтому это измерение проверяет фактическое влияние validation flag, но не сравнивает PyTorch MPS с MLX Whisper.

Qwen3-VL и Qwen3.5 запускаются через MLX/Metal GPU worker. `configured_device=Device(gpu)`, GPU stream smoke-test и `powermetrics` — свидетельства конфигурации и системной активности, но не доказательство выполнения каждого оператора модели на GPU. Apple Vision не раскрывает в текущем API аппаратный путь CPU/GPU/ANE; отчёт честно помечает его как неизвестный.

VLM resolution выбирается по объёму unified memory: 1280 px при 32 GiB и выше, 768 px на системах с меньшим объёмом. Это сочетает опубликованный профиль 1280 px для чтения экранного текста с текущим консервативным safe default 768 px для меньшей unified memory. Для пользовательских слайдов OCR Apple Vision продолжает получать исходный кадр независимо от уменьшения изображения для VLM.

## Локальные результаты и приватность

Результаты сохраняются в `~/Downloads/VCE_Full_Profile_Benchmark/<run_id>/`:

- `summary.md`, `summary.json`, `run_summary.csv`;
- `stage_comparison.csv` — времена и классификация CPU/GPU для каждого этапа;
- `device_monitoring.csv` — CPU/RSS и доступная системная GPU телеметрия;
- `errors.csv` и per-run `errors.log`;
- per-run `response.json`, `stage_timings.json/csv`, `progress_trace.jsonl`, `process_samples.jsonl`, `powermetrics_gpu_power.jsonl`, `server_log_delta.log`.

Полные ответы могут содержать исходный транскрипт, OCR и содержимое видео. Скрипт не меняет `videocontext_settings.json` и не перезаписывает пользовательские TXT/JSON. После завершения он автоматически создаёт commit в ветке `feat/experimental-benchmark-launcher` и отправляет только `summary.md`, `stage_comparison.csv` и `launcher_status.txt` в `benchmark-results/<UTC timestamp>/`. Путь и basename видео удаляются из сводки. `run_summary.csv`, `errors.csv`, per-run `errors.log`, filtered error excerpts, полный API response, транскрипт, OCR-текст, модельные ответы и все сырые process/server logs остаются локально и не публикуются. Локальная полная диагностика хранится в `$HOME/Downloads/VCE_Full_Profile_Benchmark/<run_id>/`.

Без размеченного эталонного транскрипта нельзя вычислить WER/CER. Количество распознанных слов и сцен показываются только как coverage proxies, а не оценка точности.


## Launcher compatibility guard (2026-11-04)

The one-command launcher now downloads a small patch helper alongside the compressed runner. It normalizes the argument passed to `jsonl_append()` with `pathlib.Path` before the JSONL writer calls `.open()`. This addresses the observed runner-side `AttributeError: 'str' object has no attribute 'open'` and restores progress/error telemetry collection. The patch is applied only to the temporary unpacked runner; it does not edit the installed VCE runtime or user settings.

This is deliberately separate from engine-native crashes. A Python `SIGSEGV` in Apple `libBNNS.dylib` is outside Python exception handling and cannot be repaired by the benchmark launcher. Treat a runner error and a VCE process crash as distinct events; use the macOS crash report and server log to isolate the native backend.


### Isolated crash-diagnostic switches

The launcher supports two opt-in environment variables for a controlled one-profile diagnostic:

- `VCE_BENCH_ONLY_BASELINE=1` limits the plan to the baseline profile.
- `VCE_BENCH_SKIP_AUDIO=1` sets `skip_audio=true`, disables audio features, and disables word timestamps for that request. This bypasses Whisper inference while leaving the visual/VLM/OCR path enabled.

These switches are applied only to the in-memory request fields. They do not persist settings or rewrite local configuration. This diagnostic is not a performance or accuracy benchmark. Comparing the result with a normal run can help isolate whether a native crash is associated with the audio/Whisper path; a successful run alone does not prove causation.


### Whisper-preserving crash isolation

Use `VCE_BENCH_ONLY_BASELINE=1 VCE_BENCH_WHISPER_ONLY=1` for a single baseline request that keeps Whisper enabled, preserves word timestamps and audio features, and selects API mode `whisper_ocr`. The diagnostic disables Qwen3-VL, Apple Vision OCR, FFmpeg/Tesseract OCR, and Qwen3.5 post-processing for that request only. It does not change persisted settings. This is an isolation run, not a quality/performance comparison.


### Immutable source pin

The one-command launcher pins the runner and patch-helper downloads to commit `650f3a1437ee0cdcab1c3ea41005a599a373d578` rather than the mutable `main` branch. This prevents a cached `raw.githubusercontent.com/.../main` response from silently mixing an old helper with a new launcher. It also prints the pinned source revision and helper URL before applying the patch.


### Progress-trace routing fix (2026-11-04)

The second diagnostic run proved that Whisper completed successfully: four MLX Whisper chunks were transcribed, the detected language was Russian, total Whisper inference was 185.99 seconds, and the VCE analyze endpoint returned HTTP 200. The benchmark runner then failed when it passed the API endpoint string `/api/v1/progress/<job_id>` into the local JSONL writer.

Patch v2 now detects that API-route value at the JSONL writer boundary and writes the snapshot to the per-profile local file `<run_root>/<index>_<profile>/progress_trace.jsonl`. It uses the active `run_root`, profile key, and index from the caller frame. This does not modify the endpoint used for network polling. Regression tests cover the API-route collision, ordinary string paths, baseline-only mode, Whisper-preserving mode, and patch idempotence.


### Automatic report publication

The launcher writes the full diagnostic bundle locally and, after the run (including a failed run), publishes only `summary.md`, `stage_comparison.csv`, and `launcher_status.txt` to the benchmark feature branch. It does not publish `run_summary.csv`, `errors.csv`, any error excerpts, full API responses, video-derived text, or raw logs. The input video path and basename are redacted from the published summary. It requires an authenticated GitHub CLI session with push access to the repository. A non-zero runner exit code is preserved after publication so failures remain visible to the terminal caller. If GitHub publication is unavailable, the local report is retained and the launcher explains the missing prerequisite.


### Reading the CPU/GPU execution table

The stage table distinguishes:
- `CPU`: stage execution is evidenced on CPU;
- `GPU`: GPU/Metal execution is evidenced for the stage;
- `CPU+GPU`: the same stage has evidenced CPU and GPU work;
- `CPU||GPU`: separate CPU and GPU work overlaps in time;
- `unknown`: available telemetry cannot attribute the hardware path safely.

MLX and Metal are reported where the VCE runtime exposes them. MPS is not inferred from Apple Silicon presence: it is reported only if the actual execution path exposes evidence for it. System-wide `powermetrics` is corroborating telemetry, not per-process or per-kernel attribution. Apple Vision's internal CPU/GPU/ANE choice remains unknown unless the OS/runtime exposes direct evidence.

### Stage-aware profile acceptance (2026-11-04)

The pinned runner's original status rule treated HTTP 200 plus a non-empty `meta` object as `completed`, even when its own error collection contained MLX/Metal failures or Text LLM fallback. The launcher now keeps the runner pinned to its immutable source revision and downloads the stage-classification patch helper from a separate immutable commit.

The patched runner records `http_status` / `api_status` separately from `status` and emits a local `stage_status.json` for API, Metal, VLM, Text LLM and Whisper:

- `completed`: every stage requested by the profile has positive completion evidence; Text LLM additionally requires the runtime's explicit `success` status and returned enhanced analysis.
- `degraded`: HTTP completed, but a requested optional/fallback-capable stage failed or lacks completion evidence. A Text LLM fallback is not a successful full profile.
- `failed`: HTTP/transport failure, a Metal command-buffer submission error, a VLM memory-guard shutdown, or an explicitly skipped/failed requested VLM stage.

A profile is not comparable unless `status=completed`. A baseline that is failed or degraded stops the sequential matrix. The HTTP 200 value remains visible but cannot promote a profile to success.

### Controlled single-profile isolation switches

First set `VCE_VIDEO_PATH` locally to the private input file. These isolation variables are applied to the in-memory request only; they do not change persisted VCE settings:

- VLM-only, with audio/Whisper, both OCR paths and Text LLM disabled:
  ```bash
  export VCE_BENCH_ONLY_BASELINE=1 VCE_BENCH_VLM_ONLY=1
  curl -fsSL "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/experimental-benchmark-launcher/scripts/run_vce_full_profile_benchmark.command" | /bin/bash
  ```
- Whisper-only, retaining CPU Whisper/audio features and disabling VLM, OCR and Text LLM:
  ```bash
  export VCE_BENCH_ONLY_BASELINE=1 VCE_BENCH_WHISPER_ONLY=1
  curl -fsSL "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/experimental-benchmark-launcher/scripts/run_vce_full_profile_benchmark.command" | /bin/bash
  ```
- Concurrent baseline (CPU Whisper + VLM + Text LLM):
  ```bash
  export VCE_BENCH_ONLY_BASELINE=1
  curl -fsSL "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/experimental-benchmark-launcher/scripts/run_vce_full_profile_benchmark.command" | /bin/bash
  ```

Run each isolation profile only after confirming the previous launcher/request has ended and restarting the VCE process cleanly. Do not run the concurrent profile until VLM-only and Whisper-only have been independently checked. These are diagnostic isolation runs, not a performance comparison.

