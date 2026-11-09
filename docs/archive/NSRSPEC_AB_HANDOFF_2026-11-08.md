# NSR A/B handoff — 2026-11-08

## Цель

Передать следующему агенту проверяемую картину аварии llama.cpp/Qwen3.5 на Apple M1 Max и не выдавать гипотезу о JSON grammar за установленную первопричину.

Задача: [Issue #46 — verify llama.cpp/Qwen3.5 native JSON-grammar abort](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/issues/46).

Этот handoff stacked поверх PR #45. PR #45 отвечает за отключение OCR cache при сравнении; текущий PR добавляет отказоустойчивую упаковку артефактов и handoff. Исправление самой native crash ещё не заявляется.

## Обязательный тестовый файл

Для всех аппаратных воспроизведений, regression/hardware runs и итогового A/B агент обязан использовать **ровно этот локальный путь**. Не задавать пользователю вопрос о другом видео и не подменять его fixture:

/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4

Путь уже жёстко задан в A/B launcher; regression test проверяет это. Сам исходный MP4 в Dropbox не загружать — загружать только результаты и логи.

## Проверенные факты прогона 2026-11-08

| Проверка | Наблюдение | Значение |
| --- | --- | --- |
| Машина | Apple M1 Max, 32 GB, macOS arm64, Python 3.11.18 | Аппаратный контекст |
| OCR cache | enable_ocr_cache=false в pre-run settings и backend override обоих прогонов | Нет преимущества от warm OCR cache |
| MTMD installer | status=passed | Проверка resolver/native library bundle прошла |
| Реальный Qwen3-VL MTMD preflight | status=passed; MTMD context initialized; vision/video capabilities true | Противоречит гипотезе, что последующая ошибка — это MTMD init failure |
| MLX | HTTP 200, text LLM success, 14 scenes, 313.43 s | Завершённый эталонный прогон |
| llama.cpp | соединение закрыто без HTTP response через 263.82 s | Неуспешный/незавершённый прогон |
| GPU offload | VLM 37/37 слоёв, Text LLM 34/34 слоёв | Оба model load сообщают полный GPU offload |
| Native failure | std::runtime_error: Unexpected empty grammar stack after accepting piece: @ (31) | Аварийное завершение native runtime; первопричина пока не доказана |
| llama.cpp timings | отсутствует валидный stage_timings.json | Сравнивать 263.82 s с 313.43 s как успешные времена нельзя |

В runtime_verdict.json для llama.cpp ошибочно указан код MTMD_CONTEXT_FAILED. Однако отдельный реальный MTMD preflight прошёл, а лог сервера заканчивается native grammar-stack exception. Эту метку необходимо исследовать и исправить; не считать её доказательством сбоя MTMD.

Возможное взаимодействие JSON grammar/sampler — пока гипотеза. Следующий агент должен воспроизвести сбой и изолировать причину.

## Dropbox: полный архив и ключевые доказательства

Полная папка с run trees, watchdog samples, snapshots настроек, preflight, launcher log и диагностикой:

[Открыть полный Dropbox forensic folder](https://www.dropbox.com/scl/fo/u5zaqf0rnhq7lfljp08vq/AKnZjgkGqpywJif7g_PfFGA?rlkey=385cm9ds5l74eg8z76fmffauy&st=49sihehg&dl=0)

Ключевые файлы:
- [Pre-run active settings](https://www.dropbox.com/scl/fi/eld63a0ls2vz92knxc4db)
- [MTMD context preflight](https://www.dropbox.com/scl/fi/8ejsws1qnn17bukdelrzt)
- [MTMD installer report](https://www.dropbox.com/scl/fi/9ga351d5isgqbqwinid97)
- [MLX response summary](https://www.dropbox.com/scl/fi/32b2zfj8qdm0cdczma576)
- [MLX stage timings](https://www.dropbox.com/scl/fi/aclsasiglx14fivplanze)
- [MLX server key lines](https://www.dropbox.com/scl/fi/ctseloc23bxe5jprbjv51)
- [llama.cpp watchdog verdict](https://www.dropbox.com/scl/fi/pgqawfng6jse9iivu6auf)
- [llama.cpp runtime verdict JSON](https://www.dropbox.com/scl/fi/ah5u13sir7kb3se9u55g4)
- [llama.cpp response summary](https://www.dropbox.com/scl/fi/muc13adaw4ldkevosg7m5)
- [llama.cpp server key lines / native exception](https://www.dropbox.com/scl/fi/wmsneack26zt53eqe0qqn)
- [llama.cpp request settings](https://www.dropbox.com/scl/fi/cqasocz6mucid9kxcyy1f)
- [launcher.log](https://www.dropbox.com/scl/fi/hbjemn5bbz0xbzwkl9xjw)

### Почему исправляется uploader

В launcher.log от 8 Nov после нативного падения llama.cpp видно Connection refused при попытке восстановить API settings. Старый код завершался через fail до старого ZIP/upload блока. То есть каталог forensic-данных сохранился в Dropbox через отдельную выгрузку файлов, но штатный ZIP upload launcher не выполнил.

В этом handoff launcher финализация запускается из EXIT trap: сначала попытка восстановления настроек, затем копирование доступного server.log, checksum manifest, ZIP и попытка Dropbox upload. Сбой восстановления больше не должен обрывать сбор и упаковку логов.

## Задача следующему агенту

1. Воспроизвести прогон на целевом Apple Silicon Mac, используя точный MP4 path выше.
2. Сделать минимальный Qwen3.5 reproducer. Независимо проверить response_format={"type":"json_object"}, reasoning_budget=0 и stream/non-stream — не менять все параметры одновременно.
3. Проверить совместимость tokenizer/grammar и состояние native grammar stack вокруг piece "@". Сохранить stderr, abort context, exit status и нативные диагностические строки.
4. Исправить классификацию verdict: native process abort / transport disconnect не должен становиться MTMD_CONTEXT_FAILED при успешном preflight.
5. Сохранить строгий JSON output contract. Не выключать JSON mode без валидированной замены и тестов эквивалентной структурной гарантии.
6. Не менять MLX baseline при диагностике llama.cpp. Не трактовать незавершённые 263.82 s как успешный результат для сравнения с завершёнными 313.43 s MLX.
7. После изоляции minimal reproducer добавить regression tests и повторить полный A/B.

## Обязательный Dropbox upload для каждого теста

Каждый запуск должен автоматически:
- собрать весь каталог результатов, включая все generated run files, launcher output и watchdog logs;
- включить полный файл ~/Library/Logs/VideoContext Engine/server.log, если он существует;
- создать SHA256SUMS.txt;
- создать VCE_NSRSPEC_AB_<timestamp>.zip;
- выгрузить архив в тот же Dropbox forensic folder через rclone;
- проверить размер удалённого объекта перед записью DROPBOX_UPLOAD=SUCCESS;
- выполнять сбор/упаковку/upload через EXIT trap при нормальном и ненулевом завершении, включая неуспешное восстановление API settings.

Если Dropbox remote не настроен или upload не удался, архив должен остаться локально, а команда должна завершиться ненулевым кодом и явно сообщить локальный путь. Нельзя загружать сам исходный MP4.

## Связанные PR

- [PR #43 — MTMD fix](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/43)
- [PR #44 — Qwen3.5 JSON / llama.cpp fixes](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/44)
- [PR #45 — fair A/B without OCR cache](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/45)

PR остаётся Draft, пока CI и новый аппаратный прогон на M1 Max не подтвердят финализацию и автоматическую выгрузку при аварии.