# NSR Specification A/B — MTMD preflight fix and Mac handoff (2026-11-07)

## Назначение handoff

Довести воспроизводимый A/B-прогон на целевом Mac до завершения обеих архитектур, не запускать llama.cpp поверх неработающего baseline, сохранить полную forensic-диагностику и передать единый архив в приватный Dropbox.

Порядок строго фиксирован:

1. Исправить ложный отказ проверки конструктора `Qwen3VLChatHandler`.
2. Проверить Python-модуль `llama_cpp.mtmd_cpp`, его native bindings и реальную инициализацию MTMD context с загруженными Qwen3-VL Q4 + F16 mmproj.
3. Только после успешного реального MTMD preflight запустить тем же launcher полный MLX/Metal baseline на фиксированном видео.
4. Только после успешного завершения MLX baseline запустить llama.cpp/Metal на том же видео и с теми же настройками остальных стадий.
5. Восстановить сохранённые настройки, упаковать обе диагностики и загрузить единый ZIP в приватный Dropbox с проверкой размера удалённого файла.

Это handoff для выполнения аппаратного теста на Mac M1 Max. Успех статических тестов или установочного ABI smoke test не считается успешным запуском реальной модели и видео.

---

## 1. Фиксированное входное видео — не менять

Видео задаётся прямо в launcher, не через диалог, аргумент командной строки, file picker или переменную окружения:

```bash
VIDEO="/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"
```

Это единственный input для обоих проходов. Оба backend должны получить один и тот же абсолютный путь и один и тот же файл. Перед началом launcher проверяет `[[ -f "$VIDEO" ]]`; при отсутствии файла выполнение прекращается до старта API/анализа.

Не переименовывать MP4, не заменять его сокращённым клипом и не копировать его в репозиторий или Dropbox. В forensic archive допустимы run metadata, API-ответ, тайминги, логи и диагностические снимки, но исходное видео в Dropbox не загружается.

Регрессионный тест проверяет точную строку `VIDEO=...` с Unicode-именем файла.

---

## 2. Зафиксированные версии и пути

| Компонент | Зафиксированное значение |
|---|---|
| Текущая feature base | `feature/llama-cpp-on-runtime-profile-20261107` |
| Feature base HEAD на момент handoff | `00b3a90247c62085d6cca8b2eabea0849c98769e` |
| llama-cpp-python fork | `JamePeng/llama-cpp-python` |
| llama-cpp-python commit | `1dd04befbaf1277853eb188cc4513d6089a931dd` |
| llama-cpp-python version | `0.4.6` |
| HF model repository | `Qwen/Qwen3-VL-8B-Instruct-GGUF` |
| HF revision | `f982a07559d4a2f6c8744d840bf6fccab30eea96` |
| VLM GGUF | `Qwen3VL-8B-Instruct-Q4_K_M.gguf` |
| VLM projector | `mmproj-Qwen3VL-8B-Instruct-F16.gguf` |
| Runtime profile обоих проходов | `baseline_mps_sequential` |
| MLX pass backend | `mlx_metal` |
| llama.cpp pass backend | `llama_cpp_metal` |
| Обязательная аппаратная цель | macOS / Apple Silicon, целевая машина Apple M1 Max |

Патчевые инструменты launcher скачивает из одного неизменяемого commit `5d41d71599c424f8601434111df9be6cce7dfe23`. Проверяются git blob SHA-1 watchdog, observer и MTMD installer; значения SHA закреплены в launcher. Не переключать URL на `main` или другую движущуюся ветку.

Сборка llama-cpp-python должна быть Metal-enabled и находиться в проектном `.venv`. Системные Homebrew dylib не должны смешиваться с `llama_cpp/lib` из venv.

---

## 3. Инцидент 7 ноября: факты, а не предположения

### 3.1 Что прошло

На машине обнаружено и подтверждено:

- Python проекта: `/Users/artemijvisnevskij/VideoContext-Engine-Apple-Silicon/.venv/bin/python`.
- Установлен venv-scoped resolver hook `vce_mtmd_loader_fix.pth`; Homebrew libraries не изменялись.
- `libggml-base.dylib`, `libggml.dylib`, `libllama.dylib` и `libmtmd.dylib` были загружены из одного каталога `.venv/lib/python3.11/site-packages/llama_cpp/lib`.
- Native ABI smoke check обнаружил `mtmd_get_video_config`.
- Установленная версия `llama-cpp-python` — `0.4.6`, commit совпал с закреплённым `1dd04befbaf1277853eb188cc4513d6089a931dd`.
- Metal GPU offload был включён; приложение определило Apple M1 Max.
- API подтвердил `runtime_profile` и схему MLX/llama.cpp settings.
- Whisper MPS DTW fix прошёл проверку.
- Восстановление settings завершилось со статусом `passed`, `mismatches={}`; исходные настройки не остались переключёнными.
- Весь forensic ZIP был загружен в Dropbox, размер удалённого файла совпал с локальным.

### 3.2 Что выглядело как зависание

Процесс Python preflight (PID `34322`) имел открытые HTTPS-соединения к CloudFront/AWS и держал открытый Hugging Face `.incomplete` blob. Xet log показывал передачу более 1.1 GB, 1,440 completed transmissions и затем:

```text
File reconstruction completed successfully
file_hash=a944dcdbd4f70a20a48a5117501aeb50ca269dba70670ccb0194f97f304b93a0
total_bytes_scheduled=1159029824
```

После завершения runner процесс PID `34322` уже отсутствовал. Следовательно, первоначальная пауза была сетевой загрузкой/реконструкцией HF/Xet, а не доказанным deadlock Python. Предупреждение об unauthenticated HF Hub было предупреждением о rate limits; оно не было причиной фактического preflight отказа.

В failure report уже записаны локальные пути модели и projector:

- model: `/Users/artemijvisnevskij/.cache/huggingface/hub/blobs/06/06aec93901abc5d5438a62d2787beb0cbd3d89123d3bcd73016e1eaee68f2dcf`
- mmproj: `/Users/artemijvisnevskij/.cache/huggingface/hub/blobs/a9/a944dcdbd4f70a20a48a5117501aeb50ca269dba70670ccb0194f97f304b93a0`

Не очищать Hugging Face cache и не скачивать заново уже имеющиеся корректные файлы без отдельного доказательства повреждения.

### 3.3 Фактический отказ

Real mmproj preflight завершился:

```text
REAL_MMPROJ_PREFLIGHT_FAILED
TypeError:
Unsupported Qwen3VLChatHandler constructor:
(self, force_reasoning: bool = False, add_vision_id: bool = True, **kwargs)
```

Отказ произошёл до создания handler, до загрузки Llama и до вызова `handler._init_mtmd_context(llm)`. Поэтому это **не** доказательство повреждённых GGUF/mmproj или несовместимости native MTMD.

Причина — баг в нашем preflight: код проверял только наличие явного имени `mmproj_path` в `inspect.signature(Qwen3VLChatHandler.__init__)`. У закреплённого fork конструктор Qwen3-VL явно объявляет `force_reasoning`, `add_vision_id` и `**kwargs`, а затем вызывает `super().__init__(**kwargs)`. Общий `MTMDBaseHandler` принимает `mmproj_path` явно. Следовательно, канонический `mmproj_path` корректно передаётся через `**kwargs`; старый guard давал ложный отрицательный результат.

Исходный код pinned fork для сверки:

- [Qwen3VLChatHandler constructor at pinned fork](https://github.com/JamePeng/llama-cpp-python/blob/1dd04befbaf1277853eb188cc4513d6089a931dd/llama_cpp/llama_multimodal.py#L4763-L4779)
- [MTMDBaseHandler constructor at pinned fork](https://github.com/JamePeng/llama-cpp-python/blob/1dd04befbaf1277853eb188cc4513d6089a931dd/llama_cpp/llama_multimodal.py#L82-L135)

Исправление должно трактовать `inspect.Parameter.VAR_KEYWORD` как допустимый путь передачи `mmproj_path`, а не как отсутствие поддержки.

### 3.4 Исход текущего прогона

- `mtmd_preflight_exit_code = 42`.
- MLX pass: `125` — пропущен до отправки видео.
- llama.cpp pass: `125` — пропущен до отправки видео.
- Ни один POST с исходным MP4 в API не выполнялся.
- Settings были восстановлены; mismatch не обнаружен.
- Локальный ZIP: `~/Downloads/VCE_forensic_runs_NSRSPEC_AB/VCE_NSRSPEC_AB_20261107_104113.zip`, 7,793 bytes.
- Dropbox: `vce-dropbox:20261104_224554_1f098d/VCE_Forensic_Runs/VCE_NSRSPEC_AB_20261107_104113.zip`, 7,793 bytes; `DROPBOX_UPLOAD=SUCCESS`.

Это архив неуспешного preflight, а не результаты A/B benchmark. Код возврата `125` в обоих run entries не означает, что обе архитектуры были реально запущены.

---

## 4. Исправление в этом handoff

### 4.1 Qwen3-VL constructor contract

Preflight должен:

1. Импортировать `Qwen3VLChatHandler` из pinned fork.
2. Сохранить `inspect.signature(Qwen3VLChatHandler.__init__)` в JSON report.
3. Проверить параметры:
   - если явный `mmproj_path` есть — передать `mmproj_path`;
   - если явного параметра нет, но есть `inspect.Parameter.VAR_KEYWORD` — передать канонический `mmproj_path` через `**kwargs`;
   - только для старого интерфейса без `**kwargs` допускается explicit legacy alias `clip_model_path`;
   - если ни один контракт не поддерживается — остановиться fail-closed с понятной ошибкой.
4. Создать handler с `mmproj_path=<resolved mmproj>`, `verbose=True`, `use_gpu=True`.
5. Не считать успешное создание handler подтверждением работоспособности projector: это лишь конструкторный этап.

В отчёте отдельно фиксировать `qwen3vl_handler_accepts_kwargs`, `handler_init_keyword` и `handler_constructor`.

### 4.2 Проверка `mtmd_cpp` до загрузки модели и после неё

До больших загрузок проверить:

- `import llama_cpp.mtmd_cpp as mtmd_cpp`;
- `mtmd_cpp.__file__` и `mtmd_cpp._libmtmd._name` указывают на venv package bundle;
- `mtmd_cpp.mtmd_init_from_file` и `mtmd_cpp.mtmd_get_video_config` доступны как Python bindings;
- native `libmtmd` экспортирует соответствующие symbols.

После скачивания model/mmproj и загрузки Llama обязательно вызвать:

```python
handler._init_mtmd_context(llm)
```

И записать в `mtmd_context_preflight.json`:

- локальные пути и размеры GGUF/mmproj;
- llama-cpp-python version и VCS commit;
- путь к `mtmd_cpp.py` и native `libmtmd`;
- Python bindings/native symbols status;
- handler signature и выбранный init keyword;
- `model_load`, `mtmd_context`;
- `vision_capability` и `video_capability`.

Для отправки именно видео префлайт должен fail-closed, если реальный контекст не инициализировался или не сообщил необходимые vision/video capabilities. Наличие символа `mtmd_get_video_config` само по себе не является достаточным доказательством: нужна реальная инициализация контекста и проверка возможностей модели.

Если любой этап не проходит: **ни MLX, ни llama.cpp анализ видео не запускаются**. Отчёт и логи сохраняются, настройки восстанавливаются, архив по возможности загружается в Dropbox, launcher завершает работу с ошибкой.

### 4.3 Порядок A/B: MLX baseline является обязательным gate

Предыдущая версия пропускала llama.cpp только если MLX был прерван кодом 130; прочие non-zero exit codes не блокировали второй проход. Это позволяло начать сравнение при недоказанном baseline.

Новое правило:

- Сначала всегда выполняется pass 1 `mlx_metal`.
- Pass 2 `llama_cpp_metal` запускается только когда `RUN1_EXIT == 0`.
- При любом другом exit code pass 2 получает sentinel `125` и явный `skipped_reason` в `ab_summary.json`.
- При провале MTMD preflight оба анализа получают sentinel `125`; видео не загружается.
- После любого исхода восстанавливается snapshot настроек и формируется forensic bundle.

MLX — эталон именно этого запуска: он одновременно проверяет, что текущая baseline-конфигурация и весь pipeline способны обработать заданное видео. Если MLX baseline не работает, сравнивать время/стадии llama.cpp некорректно.

---

## 5. Точный профиль двух проходов

### Pass 1 — эталон MLX

- `inference_backend=mlx_metal`
- `runtime_profile=baseline_mps_sequential`
- VLM: MLX Qwen3-VL 8B 4-bit
- Text LLM: MLX Qwen3.5 9B 4-bit
- Whisper: OpenAI Whisper large через PyTorch/MPS; не включать MLX Whisper
- Full audio + visual analysis
- `skip_audio=false`, `skip_visual=false`, summary enabled
- OCR cache enabled; FFmpeg OCR interval 5 seconds, 3 workers; Apple OCR enabled
- `vlm_resolution=1280`
- Stage timings enabled
- Output TXT/JSON side effects выключены для запроса; полный API JSON сохраняется в forensic run directory.

Выполнить полный ролик, не тестовый фрагмент. Зафиксировать exit code, HTTP status/body, stage timings, progress trace, server log delta, process/RSS/memory samples и runtime/backend provenance.

### Pass 2 — сравнение llama.cpp

Запускается только после Pass 1 exit code 0:

- `inference_backend=llama_cpp_metal`
- `runtime_profile=baseline_mps_sequential`
- VLM: pinned Qwen3-VL-8B Q4_K_M GGUF + соответствующий pinned F16 mmproj
- Text LLM: Qwen3.5-9B Q4_K_M GGUF
- Whisper остаётся точно тем же OpenAI Whisper large через PyTorch/MPS
- OCR, resolution, prompts, full analysis, output policy и instrumentation — те же, что на pass 1.

Различаться должен только backend двух LLM стадий. Не переключать Whisper, runtime profile, видео, OCR настройки или промпты между проходами.

Оба прохода должны использовать тот же watchdog/observer и одинаковые временные метрики. Не объявлять speedup по одному наблюдению без повторов: этот запуск прежде всего acceptance/smoke benchmark и диагностический baseline.

---

## 6. One-command запуск и скачивание patch tools

Launcher уже реализует единый сценарий: скачать watchdog, observer и MTMD installer из pinned tools commit, проверить SHA, установить venv-scoped `.pth` resolver, открыть нативное приложение, дождаться локального API, проверить schema и native libraries, скачать при необходимости модель/mmproj, выполнить реальный MTMD preflight, а затем запустить gated A/B и Dropbox upload.

Перед запуском:

- Закрыть VideoContext Engine через `VideoContext Engine → Quit` / `⌘Q`; launcher сам остановится, если приложение или API на 7555 уже активны, чтобы не инжектировать `.pth` в работающий Python.
- Не убивать вручную несвязанные процессы скачивания/модели.
- Убедиться, что исходное видео доступно ровно по hard-coded пути.
- Проверить, что `ffprobe` и `rclone` установлены.
- Убедиться, что `rclone` remote `vce-dropbox` или `dropbox` настроен как Dropbox. При необходимости экспортировать `VCE_DROPBOX_REMOTE` и `VCE_DROPBOX_BASE`.
- Для Hugging Face authentication token необязателен для публичного repo; при наличии валидного `HF_TOKEN` можно экспортировать его для более высоких rate limits. Не записывать token в logs или repo.

Команда проверки PR-ветки (скачивает launcher из этой handoff-ветки и выполняет его):

```bash
/bin/bash -o pipefail -c 'curl -fsSL "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/fix/qwen3vl-kwargs-mtmd-preflight-handoff-20261107/scripts/vce_run_nsrspec_ab.command" | /bin/bash'
```

После merge handoff-изменений в feature base использовать raw URL целевой feature-ветки или commit SHA. Не заменять pinned tooling commit на движущуюся ветку.

---

## 7. Dropbox output и privacy boundary

Remote по умолчанию:

```text
vce-dropbox:20261104_224554_1f098d/VCE_Forensic_Runs/
```

На каждый run launcher формирует отдельную временную папку:

```text
~/Downloads/VCE_forensic_runs_NSRSPEC_AB/YYYYMMDD_HHMMSS/
```

Один ZIP должен содержать как минимум:

- `launcher.log`;
- `mtmd_installer_report.json`;
- `mtmd_context_preflight.json`;
- `active_settings_before.json`;
- `settings_restore_report.json`;
- `run1_mlx_metal/` (если pass запускался);
- `run2_llama_cpp_metal/` (если pass запускался);
- `ab_summary.json`.

После `rclone copyto` проверить удалённый размер через `rclone size --json`; `DROPBOX_UPLOAD=SUCCESS` разрешено печатать только после совпадения remote и local bytes. При ошибке Dropbox оставить локальный архив и сообщить точную ошибку. Исходное видео не копировать и не загружать.

Не публиковать видео, transcript, полный API response или локальные settings в публичном GitHub. В GitHub PR хранятся только код, регрессионные тесты и этот handoff.

---

## 8. Результат, который должен приложить следующий исполнитель

В PR conversation / handoff receipt приложить:

1. commit SHA протестированной ветки;
2. вывод focused tests и CI;
3. `mtmd_context_preflight.json` с `status=passed`, правильными pinned commits, venv libmtmd path, symbols и обеими capabilities;
4. `ab_summary.json` с точным `video_path`, обоими backend и exit codes;
5. подтверждение `settings_restore_report.json`: `status=passed`, пустой `mismatches`;
6. локальный ZIP путь + bytes и Dropbox remote path + bytes;
7. краткую сводку обеих длительностей и stage timings, без утверждений о превосходстве backend, если данных недостаточно.

Неприемлемые состояния:

- handler signature отвергнут только потому, что у него есть `**kwargs`;
- проверен только symbol import, но не вызван `handler._init_mtmd_context(llm)`;
- отсутствует реальная проверка video capability;
- llama.cpp pass запустился, хотя MLX baseline завершился non-zero;
- между проходами изменились video, runtime profile, Whisper, OCR или prompts;
- settings остались переключёнными;
- в Dropbox отправлен исходный видеофайл или не проверен размер ZIP.

### Definition of Done

- [ ] Regression suite подтверждает `**kwargs -> mmproj_path` contract.
- [ ] Regression suite подтверждает baseline-first gate.
- [ ] Hard-coded video path совпадает посимвольно с указанным выше.
- [ ] `bash -n` и focused Python tests проходят.
- [ ] Native `mtmd_cpp` bindings и symbols проходят проверку из venv.
- [ ] Реальный Qwen3-VL GGUF + F16 mmproj контекст успешно инициализируется; vision и video capabilities подтверждены.
- [ ] MLX baseline завершает полный ролик с exit code 0.
- [ ] Только после этого llama.cpp pass завершает тот же ролик с exit code 0.
- [ ] Исходные API settings восстановлены и проверены.
- [ ] Один ZIP содержит обе диагностики, remote Dropbox size совпадает с локальным.
- [ ] Исходное видео не попало в ZIP/Dropbox и не публиковалось в GitHub.
