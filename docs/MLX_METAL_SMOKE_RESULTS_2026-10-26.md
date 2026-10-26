# MLX/Metal + Whisper: интеграционные smoke-замеры (2026-10-26)

## Назначение и границы результата

Замеры собраны на пользовательском MacBook Pro M1 Max с 32 ГБ unified memory, macOS 27.0.1 arm64, Python 3.14.4, MLX 0.32.4, mlx-vlm 0.7.10 и mlx-whisper 0.4.3. Это короткие синтетические проверки интеграции после исправлений thread affinity / `wired_limit` / контракта результата Whisper, а не полноценный бенчмарк длинного производственного ролика.

Входные synthetic-видео и их результаты должны оставаться локальными; этот документ содержит только короткие технические описания и числа таймингов.

## Тест A — только VLM / Metal

- Endpoint: `POST /api/v1/analyze`
- HTTP: `200 OK`
- Видео: 3,0 с, синтетический кадр 320×180, аудиодорожки нет.
- Профиль: Qwen3-VL-8B-Instruct-4bit, разрешение VLM 128 px, один кадр на сцену, 16 выходных токенов.
- Whisper пропущен; Apple Vision OCR, FFmpeg OCR, глобальная сводка и Text LLM отключены.
- Сцен: 1.
- Визуальный вывод описал тёмно-синий фон, зелёный контур прямоугольника и белый внутренний прямоугольник.

### Тайминги

| Этап | Время | Примечание |
|---|---:|---|
| Детекция сцен | 0,002 с | По `stage_diagnostics.scene_detection_seconds` |
| Загрузка VLM | 4,915 с | Первый загрузочный вызов в этом прогоне |
| Генерация VLM | 2,603 с | Вызов MLX/Metal завершился |
| CPU prefetch кадров — worker | 0,003 с | `frame_prefetch_worker_seconds` |
| Ожидание CPU prefetch | 0,006 с | `frame_prefetch_wait_seconds` |
| Fallback prefetch | 0 | `frame_prefetch_fallback_count` |
| Whisper load / inference | 0 / 0 с | Преднамеренно пропущен |
| Apple Vision OCR | 0 с | Отключён |
| FFmpeg/Tesseract OCR | 0 с | Не запрашивался |
| Text LLM / итоговая сводка | отключены | Не запускались |
| **Общее время обработки** | **8,327 с** | `meta.process_time` |
| **Полное время запроса** | **8,399 с** | Включая HTTP I/O |

Runtime-диагностика указала `backend=MLX`, `accelerator=Metal`, `Device(gpu, 0)`, модель Apple M1 Max и успешный Metal smoke test. Значение `max_recommended_working_set_size` было доступно в GPU device info; ранее проблемный KeyError в этом запросе не повторился.

## Тест B — Whisper + VLM / Metal

- Endpoint: `POST /api/v1/analyze`
- HTTP: `200 OK`
- Видео: синтетическое, 5 с, с короткой речью и статичным геометрическим кадром.
- Whisper: `mlx-community/whisper-large-v3-mlx`, поток MLX CPU (`mx.cpu`).
- VLM: Qwen3-VL-8B-Instruct-4bit на MLX/Metal.
- Сцен: 1.
- Распознанная речь: «Видеоконтекст аудиотест. 1, 2, 3.»
- Whisper inference: **2,60 с** по строке `[VCE Whisper] CPU inference: 2.60s`.
- Визуальное описание получено успешно.

Этот прогон подтвердил работу аудио- и визуальных ветвей в одном запросе и отсутствие ошибки `too many values to unpack`. При проверке ответа подробные тайминги не были выведены в Terminal, поскольку диагностический вывод обратился к `data.timings`, тогда как сериализованный VCE-ответ содержит их внутри `data.meta.timings`. Поэтому остальные тайминги теста B здесь намеренно не приводятся; их можно извлечь из локального файла `/tmp/vce_whisper_vlm_smoke_response.json` без повторного инференса.

Для smoke-теста лимит визуальной генерации был намеренно небольшим (32 токена), поэтому тест не оценивает полноту структурированного описания сцены.

## Вывод

1. На синтетическом silent-видео подтверждены этапные тайминги scene detection, загрузки VLM, VLM inference, CPU keyframe prefetch и total request/process.
2. На синтетическом видео с речью подтверждено успешное совместное выполнение Whisper CPU и VLM/Metal; Whisper inference занял 2,60 с.
3. Данные не заменяют полного бенчмарка исходного 17,4-МБ ролика со штатными OCR, сводкой и реальными лимитами токенов. Не следует сравнивать эти короткие smoke-тесты напрямую с прежними многоминутными замерами из `APPLE_SILICON_BENCHMARKS.md`.
