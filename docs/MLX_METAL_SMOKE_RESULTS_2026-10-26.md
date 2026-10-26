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
| Перекрытие Whisper / VLM scene pass | **0,000 с** | `whisper_vlm_overlap_seconds` измеряет пересечение ASR-интервала именно с визуальным проходом по сценам; не включает VLM model load |
| **Общее время обработки** | **10,954 с** | `total_process_time=10.954` |
| **Полное время запроса** | **11,014 с** | `total_request_time=11.014`, включая HTTP I/O |

### Диагностическая оговорка

В тесте B runtime записал `backend=MLX`, `accelerator=Metal` и `metal_smoke_test=passed`, но поле `vlm.runtime.device` было равно `Device(cpu, 0)`. При этом startup/log line от compatibility helper сообщал о переназначении generation stream на `Device(gpu, 0)`. Это несогласованность телеметрии, поэтому одного поля `device` недостаточно, чтобы утверждать, что весь VLM inference в тесте B точно исполнялся на GPU. Тест подтверждает успешную генерацию и отсутствие исходного KeyError, но диагностику device binding нужно проверить отдельно.

Whisper-задача запускается до загрузки VLM, поэтому ASR может исполняться параллельно с этапом загрузки модели. Метрика `whisper_vlm_overlap_seconds` в данной реализации считает пересечение ASR только с визуальным проходом по сценам, не с загрузкой VLM; значение 0,0 с значит, что пересечение с этим узким интервалом не измерено, а не обязательно отсутствие любого перекрытия. По агрегированным длительностям нельзя надёжно вычислить overlap без синхронных wall-clock маркеров начала/окончания Whisper, VLM load и VLM scene pass. Whisper inference 2,599 с содержит первый load, согласно `whisper_runtime.model_load_included_in_first_inference=true`.

## Тест C — end-to-end на исходном видео (2026-10-26)

Это первый завершившийся полный прогон исходного видео; в отличие от тестов A/B выше, это не синтетический smoke-тест.

- Видео: исходный ролик, 17,412 MiB, 108,233 с.
- Запрос: `POST /api/v1/analyze`; HTTP `200 OK`; 14 сцен; все запрошенные ветви завершились, `meta.params.skipped = {}`.
- Общее время VCE: **259,64 с**; полное wall time клиента: **260,869 с**.
- Отношение времени обработки к длительности видео: **2,40×** (около 2,4 секунды обработки на секунду входного видео).
- Активны Whisper, VLM, Text LLM, Apple Vision OCR и FFmpeg/Tesseract OCR; итоговая сводка включена. Скрипт-наблюдатель не менял файл настроек и не загружал видео или результаты в GitHub.

### Тайминги этапов

| Этап | Время |
|---|---:|
| Детекция сцен | 10,681 с |
| Whisper inference (включая первую загрузку модели) | 32,960 с |
| VLM load | 2,483 с |
| VLM inference | 150,642 с |
| Text LLM / итоговая сводка | 86,892 с |
| Apple Vision OCR | 0,094 с |
| FFmpeg/Tesseract OCR | 9,622 с |
| CPU frame-prefetch worker | 1,986 с |
| Ожидание prefetch | 0,129 с |
| Prefetch fallback | 0 |

Времена этапов частично перекрываются и **не должны суммироваться** для восстановления общего wall time. Измеренное перекрытие Whisper с визуальным проходом составило **30,83 с**. Whisper использовал `mlx-whisper` на MLX CPU stream (`mx.cpu`); VLM и Text LLM показывали runtime backend MLX / accelerator Metal. Первый VLM использовал `mlx-community/Qwen3-VL-8B-Instruct-4bit`, финальная текстовая модель — `mlx-community/Qwen3.5-9B-MLX-4bit`.

### Наблюдаемый таймлайн

- Около 0–10 с: детекция сцен.
- Примерно с 12 с: старт Whisper; около 14 с: старт визуальной обработки.
- Около 151 с: закончена обработка 14 сцен; примерно с 153 с формируется итоговая сводка.
- Около 173 с: подготовка/загрузка Text LLM; около 177 с: начало генерации сводки.
- В финальной генерации наблюдалась скорость порядка 42 токенов/с около 258,8 с. Индикатор прогресса оставался около 96,6% на фазе итоговой генерации; это отражает семантику прогресса, а не доказанное зависание.

### Память процесса — предварительная оценка

По ресурсным значениям, видимым в консольном выводе, максимальный из показанных сэмплов RSS составил примерно **7 223 MiB (7,05 GiB)** во время финальной загрузки/генерации Text LLM (примерно с 181 с). Это RSS процесса, не полная нагрузка unified memory и не отдельное измерение GPU/Metal allocation. Значение пока следует считать **максимумом видимых консольных сэмплов, а не окончательным максимумом всего файла**: для точного пика RSS, динамики всех сэмплов и классификации macOS memory pressure необходимо разобрать локальный `system_samples.jsonl`.

### Неустранённая оговорка о VLM device

В этом полном прогоне поле `vlm.runtime.device` сообщило `Device(cpu, 0)`, хотя runtime также указал `backend=MLX`, `accelerator=Metal`, `metal_smoke_test=passed`, а Text LLM сообщил `Device(gpu, 0)`. Реализация патчера в этом репозитории выставляет `mx.set_default_device(mx.gpu)`, создаёт GPU generation stream и переназначает aliases `generation_stream` и `wired_limit` внутри выделенного однопоточного executor для загрузки и генерации VLM. Следовательно, helper настройки VLM выбрал GPU, но поле `vlm.runtime.device` сообщает другое значение. Документация MLX 0.32.4 различает default device и thread-local streams ([Devices and Streams](https://ml-explore.github.io/mlx/build/html/python/devices_and_streams.html)); поэтому недостаточно объяснить CPU-поле тем, что диагностика читалась в другом потоке. Самое надёжное заключение сейчас: поле `vlm.runtime.device` не согласуется с логом GPU setup helper и, вероятно, формируется из другого runtime/source field либо перезаписывается позже. Точный producer этого поля не находится в overlay: для подтверждения нужен локальный исходник `VideoContextEngine_v3.19.py` и сырые логи. `metal_smoke_test=passed` сам по себе не доказывает, что каждый тензорный оператор VLM выполнялся на GPU. Следующий диагностический шаг: найти присваивание `vlm.runtime.device`, затем непосредственно перед первой генерацией и после неё залогировать `mx.default_device()`, объекты `generation_stream` из `mlx_vlm.generate.common` и `mlx_vlm.generate.dispatch`, их тип/описание и поток, переданный в generate. Это проверит wiring, но не заменит подтверждение фактического исполнения операторов на Metal.

## Вывод и ограничения

1. На синтетическом silent-видео подтверждены stage timings для scene detection, загрузки и инференции VLM, CPU frame prefetch, а также полного времени обработки/запроса.
2. На синтетическом видео с речью API вернул HTTP 200, распознанную речь и визуальное описание; Whisper inference занял 2,599 с, VLM load — 5,884 с, VLM inference — 3,745 с.
3. Ошибки `max_recommended_working_set_size` и `too many values to unpack` в аудио+визуальном smoke-тесте не возникли.
4. Диагностика device binding во втором тесте противоречива. Метрика overlap равна нулю для интервала VLM scene pass, но не измеряет потенциальное перекрытие ASR с VLM model load.
5. Тесты A/B являются синтетическими smoke-тестами и не заменяют замеры качества. Тест C подтверждает успешное end-to-end выполнение исходного ролика со штатной сводкой и всеми запрошенными ветвями; этот единичный запуск — baseline, а не доказательство стабильной производительности. Сопоставления с прежними замерами делайте только при совпадающих настройках и лимитах.
