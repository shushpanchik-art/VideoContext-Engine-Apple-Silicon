# VCE Runtime Watchdog — полный отчёт по наблюдаемому прогону (2026-10-26)

## Резюме

Watchdog завершил полный анализ исходного ролика через локальный VCE API. Запрос успешен, но наблюдатель не смог построить собственную сводку RSS из-за ошибки разбора локализованного вывода macOS ps. При этом обычный runner сохранил RSS в своих сэмплах, а консольные строки дают полезную, но не исчерпывающую картину.

Главный технический вывод: **VCE действительно сообщает об использовании MLX, конфигурации Metal и успешном Metal smoke test; Whisper работает через mlx-whisper на потоке mx.cpu; Text LLM сообщает Device(gpu, 0); VLM Qwen3-VL при этом сообщает Device(cpu, 0)**. Это означает, что конфигурация VLM и его поле device не согласованы. Доказательств того, что каждый оператор VLM фактически исполнялся на GPU, этот прогон не даёт.

Сырые видео, полный ответ анализа и транскрипция не включались в этот отчёт и не коммитились.

## 1. Параметры запуска

| Параметр | Значение |
|---|---|
| Дата | 26 октября 2026 |
| HTTP endpoint | POST /api/v1/analyze через локальный VCE API |
| HTTP результат | 200 OK |
| Длительность видео | 108,233 с |
| Размер видео | 17,412 MiB |
| Количество сцен | 14 |
| Обработанные ветви | Все запрошенные; skipped = {} |
| Время обработки VCE | 269,630 с |
| Wall time клиента | 271,183 с |
| Среднее отношение обработки к длительности видео | 2,49× от длительности ролика |

Проверка предусловий прошла: runner сравнил локальные обычные настройки с активными настройками через GET /api/v1/ui-settings; видео было отправлено один раз, запрос завершился штатно. Файл настроек на диске watchdog не менял. Никакие результаты не загружались на GitHub автоматически.

## 2. Тайминги этапов

| Этап | Измерение | Интерпретация |
|---|---:|---|
| Детекция сцен | 10,863 с | Начальная обработка видео |
| Whisper inference | 32,649 с | Первая загрузка модели включена в первый inference |
| VLM load | 2,343 с | Qwen3-VL-8B-Instruct-4bit |
| VLM inference | 152,910 с | Проход по 14 сценам |
| Text LLM stage | 94,443 с | Qwen3.5-9B-MLX-4bit; измеренное поле inference_time данного VCE этапа |
| Apple Vision OCR | 0,065 с | Включён |
| FFmpeg/Tesseract OCR | 9,608 с | Backend tesseract-cli; 22 OCR samples |
| CPU frame-prefetch worker | 1,961 с | Подготовка кадров |
| Ожидание prefetch | 0,119 с | Ожидание подготовленных кадров |
| Prefetch fallback | 0 | Не потребовался |
| Перекрытие Whisper / VLM scene pass | 30,541 с | Перекрытие ASR с визуальным проходом по сценам |

Времена стадий перекрываются и **не должны складываться**, чтобы получить wall time. Whisper завершился примерно через 44,9 с по таймлайну, выдав 18 сегментов речи.

## 3. Наблюдаемый таймлайн

- 0–10,9 с: обнаружение границ сцен.
- Примерно 12,3 с: старт Whisper на CPU stream; примерно 14,3 с: визуальный анализ/OCR.
- Примерно 22,5 с: сообщение об анализе сцены 1/14; затем анализ продолжался последовательно до сцены 14.
- Примерно 44,9 с: Whisper завершён, обнаружено 18 сегментов речи.
- Примерно 148,6–154,7 с: заканчивается визуальный проход по последним сценам; начинается формирование общей сводки.
- Примерно 175,1 с: сообщение об освобождении памяти и подготовке результата.
- Примерно 177,4 с: Qwen3.5 загружает/подготавливает модель.
- Примерно 179,5 с: начинается генерация структурированной сводки; сначала счётчик равен 0 токенов.
- К 197,8 с: отображается 11 токенов; дальше счётчик растёт непрерывно.
- К 269,1 с: наблюдались 3 737 токенов и скорость около 40,9 ток/с.
- Примерно к 271,2 с: HTTP-запрос завершился.

Индикатор прогресса некоторое время оставался около 96,6%, но токен-счётчик продолжал расти. Поэтому этот лог **не свидетельствует о зависании**: финальная генерация продолжалась, хотя проценты прогресса больше не менялись.

## 4. MLX, Metal и другие инструменты

| Компонент | Что сообщает runtime | Что можно заключить |
|---|---|---|
| Whisper | backend mlx-whisper; device cpu; stream mx.cpu; модель mlx-community/whisper-large-v3-mlx | Используется MLX Whisper на CPU stream, это не отсутствие ускорения в целом и не обычный PyTorch Whisper |
| VLM | Qwen3-VL-8B-Instruct-4bit; backend MLX; accelerator Metal; MLX 0.32.4; device Device(cpu, 0); metal_smoke_test passed | MLX и Metal сконфигурированы и малый Metal probe прошёл, но runtime-поле VLM сообщает CPU |
| Text LLM | Qwen3.5-9B-MLX-4bit; backend MLX; accelerator Metal; device Device(gpu, 0); status success | Итоговая текстовая модель сообщает GPU как устройство и закончила сводку |
| Apple OCR | enabled=true; 0,065 с | Apple OCR ветка была включена и отработала |
| FFmpeg/Tesseract OCR | requested=true; backend tesseract-cli; 22 samples; 9,608 с | OCR через Tesseract CLI действительно отмечен в итоговом профиле |
| Детекция сцен / кадры | 10,863 с scene detection; prefetch fallback 0 | Видеопроход и предварительная подготовка кадров завершились |

### Что означает вердикт watchdog

В консоли записано: **MLX_METAL_CONFIGURED_BUT_VLM_DEVICE_TELEMETRY_CONFLICTS**. Это полезная подсказка, но при статическом аудите кода watchdog обнаружен нюанс: старый классификатор находил Device(gpu, 0) в строке переназначения GPU generation stream и мог засчитать её как GPU device маркер VLM. Это не то же самое, что отдельное поле device в JSON runtime-диагностики модели.

Факты нужно разделять:

1. Runtime-JSON VLM сообщает Device(cpu, 0).
2. Helper сообщает, что выбрал GPU и переназначил generation stream на Device(gpu, 0).
3. Runtime-JSON Text LLM сообщает Device(gpu, 0).
4. Metal smoke test завершился успешно.

Это **расхождение между сообщением о конфигурации VLM и его runtime-полем device**, но оно пока не доказывает, что все VLM вычисления выполнялись CPU-only или GPU-only. Для установления точной причины нужно найти код формирования поля vlm.runtime.device в локальном VideoContextEngine_v3.19.py, логировать mx.default_device() и generation_stream непосредственно внутри executor перед/после генерации и проверить путь передачи stream в mlx-vlm.

## 5. RSS и состояние памяти

В видимых runner-сэмплах процесса сервера PID 2825 наблюдалась следующая динамика:

| Время от старта | RSS | Контекст |
|---:|---:|---|
| 8,2 с | 1 287,9 MiB | Ранний этап обработки |
| 18,4 с | 2 857,9 MiB | Рост в начале визуального/аудио прохода |
| 28,6 с | 2 675,6 MiB | Анализ сцен |
| 48,9 с | 1 369,2 MiB | После завершения Whisper RSS снижается |
| 150,7–171,0 с | примерно 1 337–1 357 MiB | Конец визуального прохода и подготовка сводки |
| 181,6 с | 7 126,9 MiB | Подготовка/загрузка Text LLM |
| 212,1–252,8 с | до 7 128,6 MiB | Генерация итоговой сводки |
| 269,1 с | 7 128,6 MiB | Поздний видимый сэмпл |

Максимум **среди показанных консольных сэмплов** — 7 128,6 MiB, то есть около 6,96 GiB RSS. Основной скачок RSS происходит при подготовке финальной Text LLM; после завершения Whisper RSS был около 1,3 GiB, а при загрузке Qwen3.5 поднялся примерно до 7,0 GiB.

Ограничения:
- RSS — резидентная память процесса, а не прямой счётчик всей unified memory или Metal/GPU allocations.
- Нельзя выводить из RSS в одиночку отсутствие memory pressure.
- Максимум по консольным сэмплам не заменяет вычисление максимума по полному system_samples.jsonl.

### Дефект наблюдателя, обнаруженный этим прогоном

Watchdog напечатал «Максимальный RSS среди наблюдавшихся PID: None». Причина найдена в его parser: macOS ps в используемой локали выводил CPU в форме с десятичной запятой, например 528,2, а старое регулярное выражение принимало только цифры и точку. В результате строки процессов отбрасывались и process_resource_summary.json был пустым, хотя runner отдельно корректно записал целые значения RSS в system_samples.jsonl и показал их в терминале.

Это также означает, что запрошенные --vmmap и --sample-stacks, вероятно, не получили target PID: watchdog выполнял эти команды только когда PID присутствовал в успешно разобранной таблице процессов. Флаг --powermetrics собирался независимо, но в предоставленном фрагменте терминала нет статуса успешного power-сэмплирования. Поэтому **нельзя заявлять, что этот запуск доказал системную GPU-активность, собрал стеки или библиотечные mapping-снимки**. Для окончательной проверки нужны watchdog_summary.json, watchdog_verdict.json, process_resource_summary.json, powermetrics_gpu_power.jsonl, runtime_mappings.jsonl, stack_samples.jsonl и memory_samples.jsonl из локальной папки прогона.

## 6. Сравнение с предыдущим прогоном того же ролика

| Метрика | Предыдущий прогон | Прогон с watchdog | Δ |
|---|---:|---:|---:|
| VCE process time | 259,640 с | 269,630 с | +9,990 с (+3,85%) |
| Wall time клиента | 260,869 с | 271,183 с | +10,314 с (+3,95%) |
| Детекция сцен | 10,681 с | 10,863 с | +0,182 с |
| Whisper inference | 32,960 с | 32,649 с | −0,311 с |
| VLM load | 2,483 с | 2,343 с | −0,140 с |
| VLM inference | 150,642 с | 152,910 с | +2,268 с (+1,51%) |
| Text LLM stage | 86,892 с | 94,443 с | +7,551 с (+8,69%) |
| FFmpeg/Tesseract OCR | 9,622 с | 9,608 с | −0,014 с |

Оба запроса вернули HTTP 200 и обработали 14 сцен. Эта таблица — описательное сравнение двух прогонов, **не строгий performance benchmark**: второй запуск выполнялся под диагностической нагрузкой watchdog; точный вклад измерительного overhead, кэширования моделей, системного фонового состояния и вариации длины генерации не изолирован. Большая часть дельты общего времени связана с измеренным увеличением времени Text LLM stage; нельзя приписывать всю дельту watchdog без контролируемого повторения.


## 8. Исправления наблюдателя и runtime трассировка для следующего запуска

В PR добавлены два изменения для следующего сбора доказательств:

1. `scripts/vce_runtime_watchdog.py` — новый watchdog v2. Он принимает значения CPU с десятичной запятой и точкой в выводе macOS `ps`, сохраняет `ps_parser_diagnostics.jsonl` и рассчитывает максимум RSS по наблюдаемым PID. Классификация VLM device берёт значение из структурированного `stage_timings.json` и не считает сообщение о создании GPU stream самостоятельным доказательством `Device(gpu, 0)`.
2. `scripts/apply_vce_mlx_metal_patch.py` — патчер добавляет `[VCE MLX TRACE]` snapshots до/после `load_model`, каждого `describe_scene` и `run_text_llm_postprocess`. Снимок формируется именно внутри выделенного `vce-mlx-metal` executor и включает PID, имя/ID потока, `mx.default_device()`, доступность GPU/Metal и состояние `generation_stream` из `mlx_vlm.generate.common` / `mlx_vlm.generate.dispatch`. Эти сообщения не включают промпты, кадры или текст анализа.

Это позволяет проверить, выбирает ли реальный worker-поток GPU и одинаковы ли common/dispatch generation stream в момент входа/выхода визуальной генерации. Но и такой снимок — доказательство конфигурации и wiring; для утверждения о фактическом исполнении каждого оператора нужны инструментирование/профайлер ниже уровня вызова модели.

Добавлены регрессионные тесты на оба варианта локали `ps`, расчёт максимума RSS, раздельную интерпретацию VLM CPU и Text LLM GPU, а также чтение trace snapshot из серверных логов.

### Следующий запуск на целевом Mac

После получения коммитов ветки:

```bash
git -C "$HOME/Downloads/VCE-watchdog-repo" pull --ff-only
python3 "$HOME/Downloads/VCE-watchdog-repo/scripts/apply_vce_mlx_metal_patch.py" \
  --engine "$HOME/VideoContext-Engine/VideoContextEngine_v3.19.py" \
  --settings "$HOME/VideoContext-Engine/videocontext_settings.json" \
  --apply
```

Патчер сперва выполняет preflight и компиляцию, затем создаёт резервные копии исходника и настроек перед записью. После применения нужно полностью перезапустить VideoContext Engine, чтобы процесс загрузил новый модуль, и убедиться, что локальный `/openapi.json` возвращает HTTP 200.

Затем запустить:

```bash
python3 "$HOME/Downloads/VCE-watchdog-repo/scripts/vce_runtime_watchdog.py" \
  --runner "$HOME/Downloads/VCE-watchdog-repo/scripts/vce_observed_run.py" \
  --video '/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4' \
  --settings "$HOME/VideoContext-Engine/videocontext_settings.json" \
  --powermetrics --sample-stacks --vmmap
```

В `watchdog_verdict.md` нужно проверить `VLM device` из API-таймингов и отдельно `runtime_trace_snapshots`. Ключевые поля: `mlx_default_device`, `common_generation_stream.device`, `dispatch_generation_stream.device`, `thread_name` и `thread_ident`. В `process_resource_summary.json` ожидается числовой `max_observed_rss_mib`; при отсутствии выборок сначала читать `ps_parser_diagnostics.jsonl`.

## CI-проверка изменений

На ветке PR все три соответствующие проверки завершились успешно после фиксов парсера и runtime trace:

- [VCE Runtime Watchdog tests — run 4](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/40406717935): компиляция watchdog/patcher, тесты парсинга RSS и классификации runtime evidence, тесты upgrade и генерации патчера.
- [MLX Metal helper tests — run 114](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/40406718054): аппаратно-независимые runtime-тесты и тесты патчера.
- [VCE quality benchmark tests — run 80](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/40406718050): компиляция всех изменённых скриптов и набор test_vce_*.py.

Это подтверждает статический код и тестовые случаи, но не заменяет повторный запуск на Mac с установленным VideoContext Engine.

## 9. Итог и план дальнейшей проверки

1. Полный end-to-end pipeline работает: API 200, 14 сцен, Whisper, VLM, OCR и итоговая Text LLM сводка завершились.
2. Runtime сообщает MLX 0.32.4 и Metal; Whisper явно направлен на mx.cpu, Text LLM сообщает Device(gpu, 0), а VLM сообщает Device(cpu, 0).
3. Зависания на 96,6% не видно: генерация продолжалась и число токенов росло.
4. Наибольший RSS среди показанных старых runner-сэмплов — около 6,96 GiB. В первом watchdog v1 собственный RSS-summary был None из-за decimal-comma parser bug; watchdog v2 включает исправленный парсер и диагностику ps. Точный максимум по всему новому прогону и системное memory pressure ещё нужно измерить повторно.
5. В старом запуске нет достаточных данных, чтобы подтвердить успешные GPU power-сэмплы, vmmap или stack snapshots.
6. Исправления парсера/классификатора и runtime-трассировка уже закоммичены и прошли CI; локальное фактическое исполнение с новыми trace markers ещё не запускалось.
7. Следующий шаг — обновить локальный checkout, применить патчер с созданием бэкапа, перезапустить приложение и запустить watchdog v2 без параллельных задач. Разбор trace snapshots должен показать фактические mx.default_device() и generation-stream поля внутри VLM executor. Это проверяет wiring; для доказательства исполнения каждого оператора всё ещё нужен низкоуровневый GPU profiler или инструментирование mlx-vlm.

Этот документ фиксирует только агрегированные тайминги и диагностические выводы. Видео, полный ответ API и транскрипция не включены.



## 10. Повторный прогон после применения патчера на диск (18:46, 2026-10-26)

Второй запуск watchdog v2 завершился HTTP 200 на исходном ролике (108,233 с, 14 сцен).

| Метрика | Значение |
|---|---:|
| VCE process time | 269,360 с |
| Клиентский wall time | 271,341 с |
| Детекция сцен | 12,229 с |
| Whisper inference | 46,152 с |
| VLM load | 2,310 с |
| VLM inference | 155,493 с |
| Text LLM inference | 90,331 с |
| Apple OCR | 0,095 с |
| FFmpeg/Tesseract OCR | 9,602 с |
| Whisper/VLM scene-pass overlap | 44,192 с |
| Максимальный sampled RSS по watchdog v2 | 7 206,1 MiB (~7,04 GiB) |

Исправленная сводка RSS теперь выдаёт численное значение, так что ошибка локализованного CPU-процента в ps устранена. Максимум оказался в финальной фазе Text LLM, около 7,04 GiB RSS. Это максимум дискретных сэмплов процесса, не вся unified memory и не оценка Metal allocations.

**Важное ограничение этого прогона:** патчер действительно изменил VideoContextEngine_v3.19.py на диске, но после него в терминале была вызвана только команда открытия приложения. До применения патча listener PID уже был 2825, и в новом логе первые сэмплы показывают process elapsed около 01:21:59. Это указывает, что уже работавший Python-сервер не был полностью перезапущен после изменения файла. Следовательно, нельзя считать, что этот прогон проверил новые [VCE MLX TRACE] hooks: активный Python-процесс мог продолжать выполнять код, импортированный до патча.

Runtime-JSON всё ещё сообщает vlm.runtime.device=Device(cpu, 0), а Text LLM — Device(gpu, 0). Этот результат подтверждает сохранение конфликта в телеметрии активного процесса, но **не разрешает вопрос о фактическом VLM device placement**, пока не будет сделан перезапуск с проверкой свежего PID и собраны новые trace snapshots.

### Действие перед следующим прогоном

1. Полностью завершить VideoContext Engine и дождаться, чтобы порт 7555 перестал слушаться.
2. При необходимости послать TERM только старому listener PID, убедиться, что listener исчез.
3. Открыть приложение заново и проверить новый PID и малое время uptime (секунды/минуты, а не больше часа).
4. Проверить, что в локальном VideoContextEngine_v3.19.py присутствует маркер VCE_MLX_VLM_RUNTIME_TRACE_V1.
5. Запустить watchdog v2 и после окончания читать watchdog_verdict.json / watchdog_verdict.md, включая runtime_trace_snapshot_count и runtime_trace_snapshots.

Если новый trace по-прежнему отсутствует после подтверждённого перезапуска, надо проверить путь логирования или не прошёл ли trace wrapper через конкретный call path. Видео и полный API response по-прежнему не загружаются в GitHub.


## 11. Reproduced MLX process-global default-device snapshot (2026-10-26)

A short local reproducer was run on the target Mac after the instrumented end-to-end run:

- Main thread called `mx.set_default_device(mx.gpu)`.
- A second thread entered `with mx.stream(mx.new_thread_local_stream(mx.cpu))` and blocked inside that context.
- While the CPU context was active, the main thread observed `mx.default_device() == Device(cpu, 0)`.
- After the CPU context exited, the main thread observed `Device(gpu, 0)` again.

This reproduces the exact class of mismatch seen in the VCE log. MLX documents that operations use an explicitly supplied `stream` when present, otherwise they use the default stream of the default device; its API exposes `set_default_device`, `default_device`, and `stream` as separate controls ([Using Streams](https://ml-explore.github.io/mlx/build/html/usage/using_streams.html), [Devices and Streams](https://ml-explore.github.io/mlx/build/html/python/devices_and_streams.html)). The test demonstrates the shared default-device observation in the installed MLX 0.32.4 runtime; it does not by itself prove where the full VLM model's operators execute.

### Diagnostic correction in this change

- The Metal smoke test now uses an explicit `mx.default_stream(mx.gpu)` and passes that stream to both `mx.ones` and `mx.multiply`; it no longer relies on an array created from the mutable default device.
- Runtime JSON keeps `device` as the explicitly configured GPU target for compatibility, and adds `configured_device`, `device_source`, `default_device_observed`, `gpu_probe_stream`, and `gpu_stream_smoke_test`. The default-device snapshot is retained instead of being silently treated as the target-device truth.
- The setup no longer fails solely because a cross-thread snapshot of `mx.default_device()` differs from the intended GPU target. The VLM compatibility helper reports the selected GPU and separately logs the process-global default snapshot.
- The watchdog now differentiates an explicit GPU target plus GPU generation-stream configuration from a CPU value returned by the global default-device snapshot. Its verdict explicitly retains the caveat that stream wiring does not prove per-operator GPU execution.
- Regression tests cover explicit-stream probe generation and this device-target/default-snapshot discrepancy.

**Scope limitation:** this corrects and improves diagnostics and the small GPU probe; it does not make MLX's process-global default device thread-local, nor does it prove that every operation during concurrent Whisper/VLM execution is routed to GPU. A separate runtime change—such as isolating the CPU Whisper workload in another process or temporarily serializing MLX CPU/GPU contexts—would be needed to eliminate that shared-state interaction rather than merely report it accurately. Keep PR #13 in Draft until a new target-Mac run confirms the revised fields and the actual inference placement is validated.

## 12. Успешный прогон с runtime-трассировкой VLM (19:50, 2026-10-26)

После исправления входного вектора GPU smoke test полный запуск watchdog v2 завершился HTTP 200: 108,233 с, 14 сцен, wall time клиента 281,353 с. VLM inference занял 158,086 с; Text LLM завершился успешно за 91,311 с. Whisper выполнялся на CPU 34,163 с, с измеренным перекрытием VLM 26,987 с. Максимальный RSS процесса в дискретных сэмплах — 7 077 MiB (~6,91 GiB); это не total unified-memory и не измерение Metal allocation.

Runtime для VLM сообщает MLX 0.32.4, Metal available, explicit GPU stream smoke test passed, configured_device=DeviceType.gpu, gpu_probe_stream=Stream(Device(gpu, 0), 0). При этом default_device_observed=Device(cpu, 0) — это снимок на этапе конфигурации/загрузки модели.

Трассировка вокруг actual scene calls дала 60 snapshots (vlm_scene_trace_snapshot_count=60), в которых mlx_default_device наблюдался как Device(gpu, 0); в показанных before/after snapshots обе ссылки mlx_vlm.generate.common.generation_stream и mlx_vlm.generate.dispatch.generation_stream указывали на ThreadLocalStream(Device(gpu, 0), 3). Text LLM before/after также показывал GPU и GPU generation streams. Следовательно, данные подтверждают GPU default-device observation и GPU generation-stream wiring во время вызовов анализа сцен, несмотря на одиночный CPU snapshot при загрузке.

Watchdog v2 первоначально выдавал VLM_GPU_TARGET_CONFIGURED_BUT_DEFAULT_DEVICE_SNAPSHOT_CPU, потому что ставил load-time default_device_observed выше более релевантных snapshots вокруг describe_scene. Классификатор теперь сначала проверяет snapshots вокруг scene calls:
- VLM_SCENE_TRACE_DEFAULT_AND_GENERATION_STREAM_GPU_EXECUTION_UNPROVEN — scene snapshots показывают GPU default device, и generation streams GPU, но это всё ещё не per-operator proof.
- VLM_GPU_TARGET_CONFIGURED_BUT_DEFAULT_DEVICE_SNAPSHOT_CPU сохраняется, если хоть один доступный scene snapshot показывает CPU при GPU generation-stream wiring.

powermetrics не получил успешных сэмплов (0), поэтому независимого системного подтверждения GPU activity в этом прогоне нет. Важно: before/after snapshots окружают вызов describe_scene, а не каждый отдельный MLX оператор. Они не исключают CPU-выполнение отдельных операций, fallback внутри библиотеки или различия между generation и пред-/постобработкой. Для подтверждения размещения отдельных операторов нужен профиль GPU/Metal, который связывает GPU execution с процессом, или более детальная инструментализация модели.

Результат подтверждает, что исправленный smoke test и полный pipeline работают; runtime snapshots сильнее указывают на GPU-bound VLM scene path. Для строгого доказательства выполнения каждого VLM оператора на Metal имеющихся данных недостаточно.

## 13. Повторный прогон с успешными GPU power samples (20:02, 2026-10-26)

Третий watchdog v2 run завершился HTTP 200: 108,23-секундный ролик, 14 сцен, VCE processing time 271,66 с. VLM inference — 158,682 с; Whisper CPU inference — 33,788 с; измеренное перекрытие Whisper/VLM — 31,982 с; финальный Text LLM — 90,056 с. Максимальный sampled RSS — 7 868,3 MiB (~7,68 GiB).

После sudo -v все 19 вызовов powermetrics во время прогона завершились успешно. Начиная с elapsed 29,589 с, samples через интервалы примерно 14–16 с фиксировали GPU HW active residency 88,75–100%, обычно 98–100%, при частоте 1296 MHz. GPU Power в тех samples был примерно 34–53 W. Эти наблюдения покрывают значительную часть VLM scene inference и продолжаются во время финального Text LLM. Первые два samples (elapsed 0 и 15 с) показывали более низкую активность, что согласуется с переходом pipeline в тяжёлую модельную работу.

Вместе с runtime trace из предыдущего полного прогона (60 из 60 scene-call snapshots показали Device(gpu, 0) и GPU generation streams, без scene-call CPU snapshots), это представляет сильную независимую корреляцию между GPU-настроенным scene path и реальной системной GPU activity. Однако powermetrics измеряет GPU на уровне всей системы, не может приписать активность непосредственно PID VCE, и отдельный GPU sample не определяет операторы MLX, породившие эту активность. Строгое per-operator attribution всё ещё требует profiler/instrumentation.

Watchdog classification updated: when scene-call traces consistently observe GPU default device and GPU generation streams, Metal is reported, and successful power samples contain GPU activity labels, verdict is VLM_SCENE_GPU_WIRING_AND_SYSTEM_GPU_ACTIVITY_CORROBORATED_EXECUTION_NOT_PER_OPERATOR_PROVEN. If those power samples are absent, the less conclusive scene-trace verdict remains. Regression test covers both paths.

Initial powermetrics failures were not GPU/sampler incompatibility: sudo had no cached authorization (sudo: a password is required). After interactive sudo -v, samples succeeded. The verification command/output and video response were not uploaded; this report contains aggregate resource numbers only.

