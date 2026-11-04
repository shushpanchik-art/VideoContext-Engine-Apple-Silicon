# Полный локальный benchmark VideoContext Engine для Apple Silicon

## Запуск одной командой

После установки проекта в `~/VideoContext-Engine` и первого открытия приложения укажите путь к видео через `VCE_VIDEO_PATH` и скопируйте команду в Terminal. Launcher загружает с GitHub сжатый Python runner, распаковывает его во временный файл и удаляет после выполнения:

```bash
curl -fsSL https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/main/scripts/run_vce_full_profile_benchmark.command | VCE_VIDEO_PATH="$HOME/Downloads/video.mp4" /bin/bash
```

Путь к исходному файлу можно изменить, не редактируя скрипт. Если `VCE_VIDEO_PATH` не задан, используется `~/Downloads/video.mp4`.

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

Полные ответы могут содержать исходный транскрипт, OCR и содержимое видео. Скрипт не меняет `videocontext_settings.json`, не перезаписывает пользовательские TXT/JSON и не загружает видео, ответы, логи или результаты в GitHub. Не добавляйте папку результатов в Git.

Без размеченного эталонного транскрипта нельзя вычислить WER/CER. Количество распознанных слов и сцен показываются только как coverage proxies, а не оценка точности.
