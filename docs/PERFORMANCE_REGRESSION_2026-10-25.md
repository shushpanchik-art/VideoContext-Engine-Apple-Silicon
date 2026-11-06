# Регресс производительности относительно замеров 2026-10-25

Документ фиксирует, как работал движок 25 октября 2026 (дата лучших замеров)
и какие именно участки кода изменили это поведение. **Изменения в коде не
внесены** — это только описание корректировок с номерами строк по коммиту
`650f3a1`.

## 1. Что известно о состоянии на 2026-10-25

Последний коммит 25 октября: `1294cf2` (2026-10-25 17:08, «Test MLX
safe-wrapper upgrade path»).

**Важное ограничение источников.** На `1294cf2` в репозитории ещё **не было**
файла `VideoContextEngine_v3.19.py` — он добавлен только 2026-11-02 в коммите
`549186b`. В репозитории на 25 октября лежали 43 файла: патчеры, тесты и
документация. Сам движок правился локально на Mac и в Git не попадал.

Значит, восстановить поведение 25 октября можно только по двум источникам:

1. `docs/APPLE_SILICON_BENCHMARKS.md` (был в дереве на `1294cf2`).
2. `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md` (добавлен позже, но описывает
   именно прогон 25 октября).

### Конфигурация, на которой получены лучшие цифры

Из `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md`, строка 7 (раздел
«Test envelope»):

> Engine: VideoContext Engine v3.19; Qwen3-VL-8B MLX, **Whisper large/MPS**,
> Apple Vision OCR, FFmpeg/Tesseract OCR, Qwen3.5-9B.

Baseline того прогона (там же, строки 12–22, раздел «Baseline stage timing»):

| Параметр | Значение |
|---|---:|
| `vlm_resolution` | 1280 |
| `whisper_model` | large |
| бэкенд Whisper | **PyTorch / MPS** |
| `keyframes_per_scene` | 1 |
| `max_scene_duration` | 20 |
| `scene_threshold` | 0.15 |
| `min_scene_duration` | 1.5 |
| `enable_apple_ocr` | true |
| `enable_ffmpeg_ocr` | true |

Раскладка baseline по стадиям: VLM 139.63 s, текстовый постпроцессинг 84.31 s,
Whisper 29.41 s, FFmpeg/Tesseract OCR 9.32 s, Apple Vision OCR 4.67 s,
загрузка Whisper/VLM 9.50 / 4.96 s, суммарно 278.97 s.

Рекомендация `docs/APPLE_SILICON_BENCHMARKS.md`, строка 36, подтверждает то же
самое в явном виде: `vlm_resolution=1280`, `keyframes_per_scene=1`,
`max_scene_duration=20`, `scene_threshold=0.15`, `min_scene_duration=1.5`,
Qwen3-VL-8B 4-bit, **Whisper `large` на MPS**, Apple Vision OCR.

### Порядок исполнения стадий на 25 октября

Из шапки `patches/patch_vce_parallel_cpu_asr_vlm.py`, строки 5–7:

> The previous implementation awaited the complete Whisper transcription before
> loading VLM. This patch schedules Whisper inference in a worker thread…

Сам патч добавлен **после** 25 октября (в `58cf8c6`, 2026-10-26). Следовательно,
25 октября Whisper отрабатывал **последовательно**, до загрузки VLM, на
PyTorch/MPS.

## 2. Что изменилось и где это в текущем коде

Номера строк — по `VideoContextEngine_v3.19.py` в коммите `650f3a1`.

### 2.1. Whisper жёстко переведён с MPS на MLX-CPU (главная причина)

Три места, которые вместе полностью убирают путь PyTorch/MPS:

**Строки 416–418** — устройство всегда CPU:
```
def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Whisper is pinned to MLX CPU stream to overlap with MLX/Metal inference."""
    return "cpu"
```
Аргумент `acceleration_enabled` принимается и игнорируется.

**Строки 421–423** — загрузчик всегда возвращает MLX-адаптер:
```
def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Return an mlx-whisper adapter; MLX model weights load inside the CPU stream."""
    return _VCEWhisperMLXCPU(model_name)
```
Аргументы не используются.

**Строки 4067–4078** — функция `_vce_load_whisper_model`, единственная, где
сохранился путь `whisper.load_model(model_name)` (строка 4078), **не вызывается
ниоткуда**. Проверено `grep`: у неё нет ни одного вызова, кроме собственного
`def`. То же относится к `_vce_transcribe_audio` (строка 4081) и
`_vce_transcribe_audio_safe_timed` (строка 4510).

Вызывается всегда только одно: `load_whisper_model` на строках **5021** и
**5028**, причём с `acceleration_enabled=False` в обоих случаях.

**Следствие.** Параметр `whisper_acceleration` (Form, строки 4687–4690) и флаг
`enable_mlx_whisper` (Form, строка 4758) не влияют ни на что. `resolve_whisper_device`
возвращает `"cpu"` независимо от значения. PyTorch-ветка мертва.

**Корректировка.** Вернуть выбор бэкенда в `load_whisper_model` (строки 421–423)
и `resolve_whisper_device` (строки 416–418) по аргументу `acceleration_enabled`,
а на строках 5021 и 5028 передавать фактическое значение
`whisper_acceleration` вместо жёсткого `False`.

### 2.2. Параллельный ASR введён поверх MLX и конкурирует с VLM за память

- Строка **91–92**: `_VCE_MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1)`,
  префикс `vce-mlx-metal`. Все вызовы VLM идут через `_vce_mlx_run` (строка 96)
  в этот единственный поток.
- Строка **262**: `_VCE_MLX_WHISPER_LOCK = _vce_threading.Lock()`.
- Строка **311**: транскрипция выполняется под этим локом внутри
  `mx.stream(cpu_stream)`.
- Строка **5061**: `_vce_whisper_task = asyncio.create_task(_vce_run_whisper_cpu_stage())`.
- Строки **5063–5067**: сразу после создания задачи идёт
  `release_accelerator_memory('before VLM load')` и `await _vce_mlx_run(vlm_engine.load_model, target_vlm)`.

То есть MLX-Whisper на CPU-стриме и MLX-VLM на Metal работают одновременно в
одном процессе и делят unified memory. Это правдоподобный механизм появления
Metal-таймаутов, под которые 2026-11-03 добавлен отдельный guard (`c4f6618`,
`docs/MLX_METAL_DIAGNOSTICS.md`). **Это гипотеза о причине, а не измеренный
факт** — на Apple Silicon я её не проверял.

**Корректировка.** Либо вернуть последовательный порядок «Whisper → VLM» как на
25 октября, либо, если параллельность нужна, поднять `max_workers` в строке 92
и не запускать MLX-Whisper на CPU-стриме одновременно с VLM — второй вариант
требует замеров, а не предположений.

### 2.3. `vlm_resolution` разъехался на три разных значения

| Место | Строка | Значение |
|---|---|---:|
| `config/videocontext_settings.apple_silicon.example.json` | — | 768 |
| `defaults` в `get_ui_settings` | 3519 | 1280 |
| Form-параметр `/api/v1/analyze` (fallback) | 4697 | 1280 |
| `videocontext_web_ui.html` (`cfg-vlm-resolution`) | 75 | 1280 |
| `DEFAULT_RESOLUTION` (не используется) | 466 | 1280 |
| профиль в патчере | `patch_vce_optimization_checkboxes.py:1155` | 384 |

Измеренный baseline 25 октября — 1280. Самый быстрый вариант того прогона —
384 (−6.5 %, с оговоркой про мелкий текст). Патчер предлагает 384, пример
конфига — 768, код по умолчанию — 1280.

**Корректировка.** Зафиксировать одно значение как дефолт и синхронизировать
все шесть мест. Если цель — воспроизвести замеры 25 октября, это 1280.

### 2.4. Form-параметры игнорируют сохранённый конфиг

Эти параметры объявлены с литеральным дефолтом и **не читают**
`videocontext_settings.json`, в отличие от соседних (`skip_audio`, `generate_txt`
и др. на строках 4729–4747, которые вызывают `setting(...)`):

| Строка | Параметр | Дефолт в Form | Дефолт в `defaults` (3519+) |
|---|---|---:|---:|
| 4751 | `analysis_mode` | `"full"` | `"full"` |
| 4752 | `enable_ffmpeg_ocr` | `False` | `False` |
| 4753 | `ffmpeg_ocr_interval` | `5` | `5` |
| 4755 | `enable_stage_timing` | `False` | `False` |
| 4756 | `enable_adaptive_keyframes` | `False` | `False` |
| 4757 | `enable_ocr_cache` | `False` | `False` |
| 4758 | `enable_mlx_whisper` | `False` | `False` |
| 4759 | `enable_fast_scene_detection` | `False` | `False` |

Веб-интерфейс эти поля отправляет явно (`videocontext_web_ui.html`, строки
182–184 и 213), поэтому через UI всё работает. Но прямой вызов API по умолчанию
сохранённый профиль не применяет. Пример: `config/videocontext_settings.local-example.json`
содержит `enable_ffmpeg_ocr: true` и `ffmpeg_ocr_workers: 3`, а Form на строке
4752 отдаст `False`.

**Корректировка.** Обернуть эти параметры в `setting(...)` по образцу соседних,
как это уже сделано для `ffmpeg_ocr_workers` на строке 4754.

### 2.5. Расхождение fallback-значений с baseline

| Параметр | Form fallback | `defaults` | HTML | Baseline 25.10 |
|---|---:|---:|---:|---:|
| `max_scene_duration` (4706) | **10** | 20 | 20 | 20 |
| `keyframes_per_scene` (4710) | **2** | 1 | 1 | 1 |

При отсутствии `videocontext_settings.json` движок возьмёт 10 и 2 — то есть не
baseline. Ровно эти же устаревшие числа зашиты в неиспользуемые константы
`DEFAULT_MAX_DURATION = 10.0` (строка 465) и `DEFAULT_KEYFRAMES_PER_SCENE = 2`
(строка 469).

**Корректировка.** Поставить 20 на строке 4706 и 1 на строке 4710.

### 2.6. Мёртвые константы

Строки 463–469: `DEFAULT_SCENE_THRESHOLD`, `DEFAULT_MIN_DURATION`,
`DEFAULT_MAX_DURATION`, `DEFAULT_RESOLUTION`, `DEFAULT_KEYFRAMES_PER_SCENE`.
Проверено `grep` по каждому имени — совпадение только в строке определения.
Ни одна не используется. Они вводят в заблуждение, потому что содержат старые
значения (10.0 и 2), не совпадающие ни с конфигом, ни с baseline.

**Корректировка.** Либо удалить, либо использовать их в `defaults` и в Form.

### 2.7. Побочное: сервер слушает всю локальную сеть

Строка 5795: `uvicorn.run(app, host="0.0.0.0", port=PORT_SERVEUR)`.
К производительности отношения не имеет, но к отчётам и настройкам есть доступ
из LAN без аутентификации (проверено через TestClient с адресом `192.168.1.50`).

**Корректировка.** `host="127.0.0.1"`.

## 3. Сводка корректировок для возврата к поведению 25 октября

| № | Файл | Строки | Действие |
|---|---|---|---|
| 1 | `VideoContextEngine_v3.19.py` | 416–418, 421–423, 5021, 5028 | Вернуть выбор бэкенда Whisper по `acceleration_enabled`, передавать `whisper_acceleration` вместо `False` |
| 2 | `VideoContextEngine_v3.19.py` | 91–92, 5061–5067 | Решить: последовательный «Whisper → VLM» (как 25.10) или параллель с отдельным контролем памяти |
| 3 | `VideoContextEngine_v3.19.py` | 3519, 4697, 466 | Привести `vlm_resolution` к 1280 |
| 4 | `videocontext_web_ui.html` | 75 | `value="1280"` |
| 5 | `VideoContextEngine_v3.19.py` | 4751–4761 | Читать значения через `setting(...)` |
| 6 | `VideoContextEngine_v3.19.py` | 4706, 4710 | Fallback 20 и 1 |
| 7 | `VideoContextEngine_v3.19.py` | 463–469 | Удалить или задействовать мёртвые константы |
| 8 | `VideoContextEngine_v3.19.py` | 5795 | `host="127.0.0.1"` |

## 4. Что не проверено

- Реальные тайминги на Apple Silicon: MLX, Metal, PyTorch/MPS и Apple Vision в
  среде проверки недоступны. Все выводы раздела 2 получены чтением кода,
  `grep` и статическим анализом, а не запуском.
- Причинно-следственная связь «MLX-Whisper на CPU + VLM на Metal → Metal
  timeout» (раздел 2.2) — гипотеза.
- Замеры 25 октября делались один раз на одном видео (об этом прямо сказано в
  шапке `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md`, раздел Limitations).
  Дельты в пределах нескольких процентов там же признаны недостоверными.
