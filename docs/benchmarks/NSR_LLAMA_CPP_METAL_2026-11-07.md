# NSR forensic run — llama.cpp / Metal — 2026-11-07

> **Результат: FAILED на инициализации multimodal VLM.** Тест дошёл до Whisper и попытки загрузить Qwen3-VL, но API вернул HTTP 500. VLM-инференс, OCR и Text LLM не стартовали. Это диагностический прогон, а не завершённый benchmark.

## 1. Идентификаторы прогона

| Поле | Значение |
|---|---|
| Дата прогона | 2026-11-07 |
| Репозиторий | `shushpanchik-art/VideoContext-Engine-Apple-Silicon` |
| Ветка | `feature/llama-cpp-on-runtime-profile-20261107` |
| Код движка | `42531c19d7888404f525104e09397834a2374be4` |
| Forensic runner / watchdog | `vce_observed_llama_cpp_run.py` / VCE Runtime Watchdog v2 |
| Runtime profile | `baseline_mps_sequential` |
| Inference backend | `llama_cpp_metal` |
| Видео | NSR Specification; 108.233333 s; 17.412 MiB |
| Видео передано | Только локальному API `127.0.0.1:7555`; в Dropbox/GitHub видео не отправлялось |
| Job ID | `vceobs_20261107_071931_b5d8268a` |
| HTTP result | `500` |
| Wall time запроса | **190.234 s** |
| Runner exit code | **3** |
| Итог | `API_ERROR`: `None of the shared library symbols were found: mtmd_get_video_config` |

## 2. Конфигурация и проверка среды

| Компонент | Фактическая конфигурация / наблюдение | Статус |
|---|---|---|
| Компьютер | Apple MacBook Pro, Apple M1 Max | Подтверждено логом Metal |
| GPU family | `MTLGPUFamilyApple7`; также объявлена поддержка `MTLGPUFamilyMetal4` | Подтверждено |
| Unified memory | Да | Подтверждено |
| Metal recommended max working set | 26,800.60 MB | Значение из Metal init |
| Metal backend | `libggml-metal.so` загружен; embedded Metal kernels скомпилированы | Подтверждено |
| Metal tensor API | Отключён для pre-M5 / pre-A19 устройств | Информационное сообщение; само по себе не означает отсутствие Metal |
| Whisper | OpenAI Whisper large, PyTorch / MPS, последовательно до VLM | Whisper завершён |
| VLM | Qwen3-VL-8B-Instruct, GGUF Q4_K_M | Инициализация завершилась ошибкой |
| VLM projector | `mmproj-Qwen3VL-8B-Instruct-F16.gguf` | Настроен; успешная загрузка не подтверждена |
| Text LLM | Qwen3.5-9B GGUF Q4_K_M | Не запускалась |
| llama-cpp-python | 0.4.6, JamePeng fork; исходный Git commit `1dd04befbaf1277853eb188cc4513d6089a931dd` | Wheel собран |
| GGML / llama dylibs | В логе найдены `/opt/homebrew/lib/libggml-base.dylib`, `libggml.dylib`, `libllama.dylib` | Важная диагностическая улика |
| GGML Metal dylib | `/opt/homebrew/Cellar/ggml/0.27.0/libexec/libggml-metal.so` | Загружена |
| OCR cache | Включён | Настройка прогона |
| FFmpeg OCR | Включён; интервал 5 s; 3 workers | Не достигнут |
| Whisper MPS DTW fix | Проверка `x.cpu().double().numpy()` пройдена | Подтверждено |
| Runtime profile API | Поле `runtime_profile` присутствует в OpenAPI | Preflight пройден |
| llama.cpp GPU offload API | `llama_supports_gpu_offload()` вернул поддержку GPU | Проверена возможность backend, не инференс модели |
| Qwen3-VL handler | `Qwen3VLChatHandler` импортирован | Импорт пройден; инициализация VLM не пройдена |

## 3. Времена этапов

Времена ниже восстановлены по временным отметкам progress trace, напечатанным runner. Переходы этапов дают приближённую длительность; **190.234 s** — точное wall time HTTP-запроса. Файлы `stage_timings.json/csv` сохранены в приватном forensic ZIP и являются первичным источником для будущей сверки точных внутренних таймеров.

| Этап | Начало от старта запроса | Окончание / последняя отметка | Длительность | Результат |
|---|---:|---:|---:|---|
| Scene detection | ~1.1 s | ~3.1 s | ~2–3 s | Переход к Whisper состоялся |
| Whisper large / PyTorch-MPS | ~4.1 s | 44.5 s | ~40.4 s | **Успех**, 18 речевых фрагментов |
| VLM загрузка / инициализация Qwen3-VL | 45.6 s | 190.234 s | ~144.6 s до ошибки | **Ошибка**, HTTP 500 |
| VLM inference / scene descriptions | — | — | Не измерено | Не стартовал |
| Apple Vision OCR | — | — | Не измерено | Не запускался |
| FFmpeg / Tesseract OCR | — | — | Не измерено | Не запускался |
| Text LLM Qwen3.5-9B | — | — | Не измерено | Не запускалась |
| Финальная сводка / сохранение успешного API response | — | — | Не измерено | Не достигнуты |
| **Весь HTTP-запрос** | 0.0 s | 190.234 s | **190.234 s** | **HTTP 500** |

Прогресс интерфейса оставался на отметке 9.9% в течение большей части инициализации VLM. Это UI progress, а не процент загрузки весов модели.

## 4. Системные выборки процесса

В таблице приведены точки, которые watchdog напечатал в консоль. RSS — resident set size выбранного процесса, а CPU% — значение, выведенное sampler'ом; это не измерение GPU utilization.

| Время от старта | CPU % | RSS, MiB | Контекст |
|---:|---:|---:|---|
| 9.6 s | 1.8 | 3,665.0 | Ранний снимок; заметно выше последующих выборок |
| 20.5 s | 21.2 | 1,022.2 | Whisper |
| 30.8 s | 24.5 | 1,160.0 | Whisper |
| 41.2 s | 24.8 | 1,169.3 | Конец Whisper |
| 51.9 s | 5.2 | 1,282.6 | Загрузка VLM |
| 62.3 s | 16.6 | 1,427.7 | Загрузка VLM |
| 72.7 s | 14.1 | 1,604.2 | Загрузка VLM |
| 83.6 s | 11.8 | 1,163.3 | Загрузка VLM |
| 94.1 s | 13.0 | 1,219.1 | Загрузка VLM |
| 104.5 s | 2.7 | 1,388.1 | Загрузка VLM |
| 115.3 s | 0.9 | 1,389.0 | Загрузка VLM |
| 125.7 s | 2.4 | 1,390.8 | Загрузка VLM |
| 136.1 s | 0.4 | 1,392.3 | Загрузка VLM |
| 147.0 s | 0.4 | 1,392.3 | Загрузка VLM |
| 157.4 s | 0.7 | 1,392.3 | Загрузка VLM |
| 167.8 s | 1.3 | 1,392.4 | Загрузка VLM |
| 178.8 s | 0.5 | 1,392.5 | Загрузка VLM |
| 189.2 s | 0.6 | 1,393.8 | Непосредственно перед HTTP 500 |

Watchdog напечатал итоговый **Max sampled RSS = 5,356.0 MiB**. Это максимум по выбранным watchdog процессам; не следует трактовать его как полную unified-memory allocation или объём памяти Metal. Для точной привязки к PID и времени использовать `process_resource_summary.json` и `process_samples.jsonl` в ZIP.

Powermetrics, vmmap, stack sampling и process sampling были включены. Сам факт запуска GPU sampler или загрузки Metal dylib не доказывает, что тензорные операции Qwen3-VL реально выполнялись на GPU.

## 5. Ошибка и наиболее вероятная причина

**Фактическая ошибка API:**

```text
HTTP 500
None of the shared library symbols were found: mtmd_get_video_config
```

Одновременно в логе динамической загрузки видны следующие факты:

1. Python package собран из JamePeng fork как `llama-cpp-python 0.4.6`.
2. В runtime загружались GGML/llama shared libraries из глобального Homebrew пути `/opt/homebrew/lib`.
3. Дополнительно загружался Metal backend из Homebrew GGML 0.27.0.
4. ctypes сообщал об отсутствии ряда optional symbols.
5. Preflight проверил import handler и общий GPU-offload capability, но не вызвал полноценную инициализацию multimodal runtime, где требуется `mtmd_get_video_config`.

**Наиболее вероятная техническая причина:** несовместимость/неполный набор multimodal ABI symbols между установленным Python binding и фактически загруженными native dylibs (возможный конфликт bundled libraries с глобальными Homebrew dylibs либо отсутствующая/не та версия mtmd-библиотеки). Это сильная гипотеза по логу загрузчика, но ещё не доказанный диагноз конкретного файла dylib: для окончательного вывода нужны `otool -L`, проверка экспортов `nm -gU` и фактического порядка загрузки dylibs.

### Что не является подтверждением успеха

- `Qwen3VLChatHandler` импортировался, но это не доказывает, что multimodal handler можно создать.
- `llama_supports_gpu_offload() == True` подтверждает доступность GPU offload в библиотеке, но не то, что слои Qwen3-VL были загружены на GPU.
- Metal backend и embedded kernels загружались/компилировались, но модель не дошла до inference.
- Сообщение `tensor API disabled for pre-M5 / pre-A19` само по себе не объясняет ошибку `mtmd_get_video_config`.

## 6. Некорректная классификация watchdog

В консоли итоговый watchdog verdict напечатан как:

```text
MLX_REPORTED_METAL_UNCONFIRMED
```

Для этого запуска с `inference_backend=llama_cpp_metal` это **невалидная MLX-классификация**. В версии watchdog `56fc56391202490e07cc398534528e064d8ae333` флаг `mlx_reported` становится true также при простом наличии строки `[VCE runtime]` в логах. Проверка не ограничена фактическим backend `MLX`. Поэтому этот verdict нельзя использовать ни как свидетельство запуска MLX, ни как подтверждение/опровержение Metal execution в llama.cpp.

Для следующей версии watchdog нужно:
- сначала читать `meta.params.inference_backend` / фактическую backend runtime-диагностику;
- разделить классификацию `mlx_metal` и `llama_cpp_metal`;
- возвращать отдельный статус вроде `LLAMA_CPP_METAL_BACKEND_LOADED_MODEL_INIT_FAILED`, когда Metal dylib загружен, но VLM inference не начался;
- не выводить MLX verdict только из общего маркера `[VCE runtime]`.

## 7. Архив и сохранность результатов

| Поле | Значение |
|---|---|
| ZIP | `VCE_NSRSPEC_LLAMA_CPP_20261107_071928.zip` |
| Размер | 984,321 bytes (961.25 KiB) |
| Локальный каталог | `~/Downloads/VCE_forensic_runs_NSRSPEC_llama_cpp/20261107_071928/` |
| Dropbox | Upload **SUCCESS** в частный remote `vce-dropbox`, папка `VCE_Forensic_Runs` |
| Проверка загрузки | rclone завершил передачу 1/1; размер remote совпал: 984,321 bytes |
| Полный response/transcript | Остались в приватном forensic архиве; в публичный GitHub не добавляются |
| Исходное видео | Не включалось в Dropbox ZIP и не публиковалось |

В локальном прогоне перечислены следующие артефакты: `response.json`, `stage_timings.json`, `stage_timings.csv`, `progress_trace.jsonl`, `system_samples.jsonl`, `server_events.jsonl`, `server_log_key_lines.txt`, `run_metadata.json`, watchdog summary, process/resource samples, powermetrics, vmmap и stack samples.

## 8. Вывод и следующий технический шаг

| Вопрос | Вывод |
|---|---|
| Установка точного runtime commit | Успешна: `42531c19d7888404f525104e09397834a2374be4` |
| Нативное приложение пересобрано | Успешно |
| Whisper MPS | Отработал; 18 речевых фрагментов |
| llama.cpp Metal capability | Backend найден; GPU-offload API доступен |
| Qwen3-VL multimodal model init | Неуспешна: отсутствует ожидаемый `mtmd_get_video_config` symbol |
| Фактическое выполнение VLM на Metal | Не подтверждено |
| OCR и Text LLM | Не запускались |
| Сравнение производительности полного анализа | Невозможно по этому прогону |
| Полный forensic архив | Сохранён в частном Dropbox |

**Рекомендуемая последовательность исправления:** проверить `otool -L` для установленного Python extension и всех его llama/GGML/mtmd dylibs; проверить наличие и экспорт `mtmd_get_video_config` в фактически загруженной библиотеке; устранить подхват несовместимых глобальных Homebrew dylibs либо пересобрать и загрузить согласованный комплект native libraries из одного pinned llama.cpp fork/commit. Затем выполнить отдельный минимальный smoke test создания Qwen3-VL multimodal handler и загрузки модели. Только после успешного VLM smoke test повторять полный 108.233 s NSR forensic run.

---

*Публичный отчёт намеренно не содержит исходное видео, transcript, полный API response, персональные абсолютные пути macOS или приватный Dropbox namespace. Полные сырые доказательства сохранены в приватном ZIP.*
