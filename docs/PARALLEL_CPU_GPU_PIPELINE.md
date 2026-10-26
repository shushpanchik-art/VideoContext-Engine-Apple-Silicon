# Параллельная CPU/GPU-обработка: результат и следующий план

## Первый результат: CPU Whisper параллельно с GPU VLM

Дата записи: 25 октября 2026.

На MacBook Pro M1 Max (32 ГБ unified memory) пользователь сообщил об ускорении обработки примерно на **25%** после включения режима, в котором Whisper выполняется на CPU в фоне, а VLM продолжает анализировать сцены на GPU/Metal.

Изменение установлено через `patches/patch_vce_parallel_cpu_asr_vlm.py`; установщик: `scripts/install_vce_parallel_cpu_asr_vlm.command`.

### Как работает текущий эксперимент

- Whisper принудительно использует CPU; MLX Whisper и MPS для Whisper выключены в этой задаче.
- Инференс Whisper запускается в рабочем потоке, пока основной pipeline загружает VLM и анализирует сцены.
- После завершения анализа сцен транскрипция распределяется по сценам, и аудиополя добавляются перед формированием глобальной сводки.
- Очистка ожидает фонового Whisper, чтобы не удалить файл, пока worker его читает.
- Промпты отдельных VLM-сцен могут не содержать Whisper-транскрипцию: ASR может завершиться позже VLM-анализа. Это известный компромисс текущей версии.

### Как трактовать цифру 25%

Это предварительный практический результат, сообщённый пользователем, а не независимый benchmark. В записи не зафиксированы абсолютные времена до/после, число повторов и контроль одинаковости кеша/фоновой нагрузки. Следующий этап — повторить тест на одном и том же ролике 2–3 раза, сравнить медиану wall time и проверить полноту транскрипции, число сцен и содержимое сводки.

## Следующие возможности параллелизма

Главная идея: перекрывать CPU-работу с GPU-инференсом, не запускать несколько тяжёлых MLX/Metal-инференсов одновременно без измерений и держать очередь кадров ограниченной по памяти.

### Приоритет 1 — pipeline декодирования и подготовки кадров

**CPU:** чтение/декодирование следующего кадра, извлечение миниатюр, resize, конвертация цветового пространства, подготовка image payload.

**GPU:** VLM анализирует текущую сцену.

Реализовать producer/consumer с очередью на 1–2 сцены, чтобы CPU мог готовить следующий вход, пока Metal занят текущим. Ограниченная очередь важна для unified memory. Сначала измерить время decode/preprocess и время VLM отдельно: если подготовка занимает мало времени, ожидаемый выигрыш будет небольшим.

### Приоритет 2 — CPU OCR в ограниченном pipeline

Если выбранный OCR backend действительно использует CPU (например, Tesseract/FFmpeg), можно запускать OCR следующего кадра одновременно с VLM-инференсом предыдущей сцены. Перед отправкой очередной сцены в VLM её OCR-текст должен быть готов, если он нужен в промпте. Это требует стадийной очереди и аккуратной синхронизации; не следует без проверки считать Apple Vision исключительно CPU-задачей.

### Приоритет 3 — аудио-выравнивание и лёгкие аудиопризнаки

Распределение готовых Whisper-сегментов по уже обнаруженным границам сцен, дедупликация соседних фрагментов, вычисление простых признаков и подготовка полей JSON — CPU-работа. Её можно выполнять для уже завершённых сцен, пока GPU анализирует следующую. Это, вероятно, даст меньший выигрыш, чем перекрытие Whisper, поэтому сначала замерить длительность данных шагов.

### Приоритет 4 — постобработка результатов сцен на CPU

Проверка структуры полей, сортировка временных меток, объединение результатов OCR/ASR, детерминированное удаление дублей и подготовка JSON могут выполняться по мере готовности каждой сцены, пока VLM работает над следующей. Не переносить на CPU генеративную смысловую сводку без отдельного сравнения качества: это уже смена модели/алгоритма, а не только конвейеризация.

### Что не делать первым

- Не запускать параллельно несколько крупных MLX VLM-задач на одном Metal-устройстве: дополнительное давление на unified memory может снизить скорость или вызвать OOM.
- Не запускать много потоков CPU без ограничений: декодирование, OCR и Whisper могут конкурировать за performance cores и память.
- Не использовать неограниченную очередь кадров/изображений.
- Не менять одновременно разрешение VLM, число кадров на сцену, модель Whisper и параллелизм: иначе нельзя понять, что именно дало выигрыш.

## План измерений

Для каждого изменения записывать: wall time целиком, decode/preprocess, OCR, Whisper, VLM, текстовую постобработку, число сцен, объём транскрипции и состояние памяти/ошибки. Сравнивать одинаковый ролик и настройки, желательно по медиане 3 повторов. Сначала менять только один этап.

Порядок экспериментов:
1. Повторить baseline и текущий CPU Whisper + GPU VLM на одном входном ролике.
2. Добавить bounded prefetch для декодирования/подготовки кадров.
3. При наличии достаточного CPU OCR времени — конвейеризировать OCR следующей сцены с VLM текущей.
4. Только после профилирования перекрывать аудио-выравнивание и CPU-постобработку результатов.


## Реализация bounded frame prefetch

Добавлен отдельный патчер `patches/patch_vce_bounded_frame_prefetch.py` и установщик `scripts/install_vce_bounded_frame_prefetch.command`.

- Один worker thread использует собственный `cv2.VideoCapture`, чтобы не делить курсор декодирования с основным потоком.
- Очередь ограничена максимум двумя подготовленными сценами.
- Очередь потребляется в исходном порядке; ошибка получения изображения включает синхронный fallback для этой сцены.
- Worker останавливается и join-ится до удаления временного видео.
- Патчер проверяет наличие предыдущего parallel ASR/VLM-патча, показывает diff в dry-run, создаёт backup и валидирует Python AST. Настройки пользователя не переписываются.

**Статус:** код и установщик опубликованы в экспериментальной ветке; установка и benchmark bounded prefetch на Mac ещё не подтверждены. Следующий тест должен проверить корректность сцен и сравнить wall time без изменения прочих параметров.

Обновлённый порядок экспериментов:
1. Прогнать один и тот же ролик с текущим параллельным ASR/VLM без prefetch, зафиксировать времена стадий.
2. Установить bounded prefetch и повторить тот же ролик при тех же настройках.
3. Сравнить wall time, число сцен, изображения/описания, OCR и транскрипцию.
4. Если эффект повторяется, проверить 2–3 повтора и только затем переходить к конвейеризации CPU OCR.


## Bounded keyframe prefetch

A companion patch, `patches/patch_vce_bounded_frame_prefetch.py`, uses a dedicated OpenCV capture worker to prepare sampled keyframes while the VLM analyzes the current scene. The queue holds at most one waiting scene, with at most one additional scene being prepared by the worker. VLM inference itself remains serial.

The producer uses the existing keyframe sampler and the same adaptive per-scene frame limit as the synchronous path. If prefetch fails for a scene, that scene falls back to synchronous sampling; a background worker is stopped and joined before temporary-file cleanup. When `enable_stage_timing` is enabled, JSON `timings.stage_diagnostics` includes `frame_prefetch_worker_seconds`, `frame_prefetch_wait_seconds`, and `frame_prefetch_fallback_count`.

This is an optimization hypothesis, not a guaranteed speedup. Compare at least three runs on the same video and settings. A low prefetch wait time means CPU preparation was mostly hidden; a high wait time means the VLM pipeline still had to wait for the producer. Also compare scene count, transcript/OCR completeness, and output quality.


## Recovery for broken/unsupported MP4 streams

The bundled installer applies MP4 recovery before bounded keyframe prefetch. For fragmented-container diagnostics such as `trun track id unknown` / `no tfhd was found`, recovery now attempts a stream-copy remux first, followed by H.264/AAC transcoding. For errors more likely caused by an unsupported decoder/codec, it keeps transcoding first and remux second. Both paths write to a temporary file and never overwrite the source.

If a valid video stream cannot be recovered but audio is readable, VCE falls back to audio-only analysis: Whisper and the summary remain enabled; VLM, visual scene analysis and OCR are disabled for that file. If neither video nor audio is readable, the API returns a file-scoped diagnostic rather than pretending that analysis succeeded. Missing MP4 fragment metadata cannot always be reconstructed by a transcoder; in that case, re-download or re-export the source.

The prefetch worker remains bounded to one queued scene plus at most one scene being prepared, consumes scenes in order, and is stopped before temporary media cleanup. VLM inference remains serial.

The strategy-order/idempotency fixture is covered by `tests/test_patch_vce_mp4_recovery_retry.py`. Hardware validation still requires the original problematic file and a readable control video on macOS; no performance or recovery result is claimed without that run.
