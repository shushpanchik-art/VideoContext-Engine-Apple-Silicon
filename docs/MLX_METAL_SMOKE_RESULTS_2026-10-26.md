# MLX/Metal + Whisper: интеграционные smoke-замеры (2026-10-26)

## Назначение и границы результата

Замеры собраны на пользовательском MacBook Pro M1 Max с 32 ГБ unified memory, macOS 27.0.1 arm64, Python 3.14.4, MLX 0.32.4, mlx-vlm 0.7.10 и mlx-whisper 0.4.3. Это короткие синтетические проверки интеграции после исправлений thread affinity / `wired_limit` / контракта результата Whisper, а не полноценный бенчмарк длинного производственного ролика.

Входные synthetic-видео и их результаты должны оставаться локальными; этот документ содержит только короткие технические описания и числа таймингов.

## Тест A — только VLM

- Endpoint: `POST /api/v1/analyze`
- HTTP: `200 OK`
- Видео: 3,0 с, синтетический кадр 320×180, аудиодорожки нет.
- Профиль: Qwen3-VL-8B-Instruct-4bit, разрешение VLM 128 px, один кадр на сцену, 16 выходных токенов.
- Whisper пропущен; Apple Vision OCR, FFmpeg OCR, глобальная сводка и Text LLM отключены.
- Сцен: 1.
- Визуальный вывод описал тёмно-синий фон, зелёный контур прямоугольника и белый внутренний прямоугольник.
- Runtime device: `Device(gpu, 0)`; runtime указал `backend=MLX`, `accelerator=Metal`, `metal_smoke_test=passed`.

### Тайминги теста A

| Этап | Время | Примечание |
|---|---:|---|
| Детекция сцен | 0,002 с | `stage_diagnostics.scene_detection_seconds` |
| Загрузка VLM | 4,915 с | Первый загрузочный вызов в этом прогоне |
| Генерация VLM | 2,603 с | Вызов MLX завершился |
| CPU prefetch кадров — worker | 0,003 с | `frame_prefetch_worker_seconds` |
| Ожидание CPU prefetch | 0,006 с | `frame_prefetch_wait_seconds` |
| Fallback prefetch | 0 | `frame_prefetch_fallback_count` |
| Whisper load / inference | 0 / 0 с | Преднамеренно пропущен |
| Apple Vision OCR | 0 с | Отключён |
| FFmpeg/Tesseract OCR | 0 с | Не запрашивался |
| Text LLM / итоговая сводка | отключены | Не запускались |
| **Общее время обработки** | **8,327 с** | `meta.process_time` / `total_process_time=8.327` |
| **Полное время запроса** | **8,399 с** | `total_request_time=8.399`, включая HTTP I/O |

GPU device info включал `max_recommended_working_set_size`; ошибка KeyError в этом запросе не повторилась.

## Тест B — Whisper + VLM

- Endpoint: `POST /api/v1/analyze`
- HTTP: `200 OK`
- Видео: синтетическое, 5 с, с короткой речью и статичным геометрическим кадром.
- Whisper: `mlx-community/whisper-large-v3-mlx`, поток MLX CPU (`mx.cpu`).
- VLM: Qwen3-VL-8B-Instruct-4bit; API вернул визуальное описание.
- Сцен: 1.
- Распознанная речь: «Видеоконтекст аудиотест. 1, 2, 3.»
- Генерация VLM была ограничена 32 токенами; этот лимит нужен был для smoke-теста, а не для оценки качества.
- Apple Vision OCR, FFmpeg OCR, Text LLM и итоговая сводка отключены.

### Все доступные тайминги теста B

| Этап | Время | Примечание |
|---|---:|---|
| Детекция сцен | 0,013 с | `stage_diagnostics.scene_detection_seconds` |
| Загрузка Whisper | 0,000 с | Модельная загрузка включена в первый вызов транскрипции по runtime-диагностике |
| Whisper inference | 2,599 с | Время до завершения транскрипции, включая первую загрузку модели |
| Загрузка VLM | 5,884 с | `vlm_load_seconds` |
| Инференция VLM | 3,745 с | `vlm_inference_seconds` |
| Apple Vision OCR | 0,000 с | Отключён |
| FFmpeg/Tesseract OCR | 0,000 с | Не запрашивался; доступность backend не означает, что он запускался |
| Text LLM | 0,000 с | Отключён |
| CPU prefetch кадров — worker | 0,006 с | `frame_prefetch_worker_seconds` |
| Ожидание CPU prefetch | 0,009 с | `frame_prefetch_wait_seconds` |
| Fallback prefetch | 0 | `frame_prefetch_fallback_count` |
| Измеренное перекрытие Whisper/VLM | **0,000 с** | `whisper_vlm_overlap_seconds`; на этом коротком прогоне перекрытие не подтверждено |
| **Общее время обработки** | **10,954 с** | `total_process_time=10.954` |
| **Полное время запроса** | **11,014 с** | `total_request_time=11.014`, включая HTTP I/O |

### Диагностическая оговорка

В тесте B runtime записал `backend=MLX`, `accelerator=Metal` и `metal_smoke_test=passed`, но поле `vlm.runtime.device` было равно `Device(cpu, 0)`. При этом startup/log line от compatibility helper сообщал о переназначении generation stream на `Device(gpu, 0)`. Это несогласованность телеметрии, поэтому одного поля `device` недостаточно, чтобы утверждать, что весь VLM inference в тесте B точно исполнялся на GPU. Тест подтверждает успешную генерацию и отсутствие исходного KeyError, но диагностику device binding нужно проверить отдельно.

Также измеренное `whisper_vlm_overlap_seconds=0.0`; поэтому этот короткий запуск не доказывает выигрыш от параллельного исполнения Whisper/VLM. Whisper inference 2,599 с содержит первый load, согласно `whisper_runtime.model_load_included_in_first_inference=true`.

## Вывод и ограничения

1. На синтетическом silent-видео подтверждены stage timings для scene detection, загрузки и инференции VLM, CPU frame prefetch, а также полного времени обработки/запроса.
2. На синтетическом видео с речью API вернул HTTP 200, распознанную речь и визуальное описание; Whisper inference занял 2,599 с, VLM load — 5,884 с, VLM inference — 3,745 с.
3. Ошибки `max_recommended_working_set_size` и `too many values to unpack` в аудио+визуальном smoke-тесте не возникли.
4. Диагностика device binding во втором тесте противоречива; measured overlap равен нулю.
5. Это не полный бенчмарк исходного 17,4-МБ ролика со штатным OCR, полными лимитами токенов и сводкой. Не следует сравнивать smoke-тесты напрямую с прежними многоминутными замерами из `APPLE_SILICON_BENCHMARKS.md`.
