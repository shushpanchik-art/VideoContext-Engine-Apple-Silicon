# План двух замеров: MLX baseline и llama.cpp/Metal

Документ фиксирует согласованный порядок работ, спецификацию двух прогонов на
одном и том же видео и требования к переключаемому backend llama.cpp/Metal.
Код движка этим документом не меняется.

Опорный коммит: `650f3a1` (main). Номера строк — по нему.

## 1. Порядок слияний

| № | PR | Действие | Состояние |
|---|---|---|---|
| 1 | #38 | Убрать `install_patches_to_existing_checkout.command` из `tests/test_patchset_sanity.py:86` и слить | 17 коммитов, base `main`, head `71580060` |
| 2 | #40 | Правки A+Б в `tests/test_vce_observed_run.py`, base → `main`, слить после #38 | 8 коммитов, base сейчас на ветке #38 |
| 3 | #39 | Base → `main` (иначе тянет 10 коммитов #38), слить отдельно | 1 коммит, `dae26826` |
| 4 | новый | Переключаемый backend llama.cpp/Metal (§4) | ещё не создан |

### Правки в #40

- **A.** В фикстуру `test_openapi_form_schema_and_request_overrides`
  (`tests/test_vce_observed_run.py`, блок `Body.properties`) добавить
  `"runtime_profile": {"type": "string", "default": ""}`. Без этого
  `get_form_schema` (`scripts/vce_observed_run.py:147`) бросает
  `RuntimeError: Локальный VCE API не поддерживает runtime_profile`.
- **Б.** Строка 100: `assertEqual(fields["enable_mlx_whisper"], True)` →
  `False`. `SAFETY_OVERRIDES` (`scripts/vce_observed_run.py:45`) содержит
  `"enable_mlx_whisper": False` намеренно: канонический профиль
  `baseline_mps_sequential` главный, флаг — устаревший.

Проверено: A+Б дают `5 passed` для файла и `216 passed, 0 failed` для всего
набора. Только A оставляет `AssertionError: False != True`.

### CI

Ни один workflow сейчас не запускает `tests/` целиком. `test_vce_observed_run.py`
не запускается нигде, поэтому зелёный CI в #40 не означал, что тест проходит.
Стоит добавить шаг `python -m pytest -q tests` в один из workflow.

## 2. Прогон 1 — MLX baseline

Цель: воспроизвести архитектуру 25 октября без изменений кода.

| Компонент | Значение |
|---|---|
| Whisper | `large`, PyTorch / MPS |
| VLM | `mlx-community/Qwen3-VL-8B-Instruct-4bit`, MLX/Metal |
| Text LLM | `mlx-community/Qwen3.5-9B-MLX-4bit`, MLX/Metal |
| `vlm_resolution` | 1280 |
| `runtime_profile` | `baseline_mps_sequential` |
| Whisper → VLM | последовательно, overlap 0.0 |

Профиль реализован в PR #38. Замера этой архитектуры **пока не существует**:
единственный фактический прогон (`benchmarks/2026-11-06-nsrspec-forensic-run.md`,
345.324 s) шёл на llama.cpp/GGUF для VLM и Text LLM и сам помечен
`NO_DIRECT_MLX_RUNTIME_EVIDENCE`. Ориентир 280–290 s взят из
`docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md` (278.97 s) и является ожиданием,
а не измеренным результатом.

## 3. Прогон 2 — llama.cpp / Metal

Запускается сразу после прогона 1, на том же файле.

**Видео:** `/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4`

| Компонент | Модель | Backend |
|---|---|---|
| Whisper | Large | PyTorch MPS |
| VLM | Qwen3-VL-8B-Instruct GGUF Q4_K_M | llama.cpp / Metal |
| Text LLM | Qwen3.5-9B GGUF Q4_K_M | llama.cpp / Metal |
| Apple Vision OCR | без изменений | Apple Vision |
| Apple OCR cache | включён | Cache |
| FFmpeg / Tesseract OCR | интервал 5 с, 3 workers | CPU |

Whisper остаётся на PyTorch/MPS в обоих прогонах — это единственная стадия,
которая не меняется, поэтому сравнение по Whisper будет корректным.

## 4. Что требуется для прогона 2: расхождения с кодом

Спецификация выше **не реализуема текущим кодом**. Проверено по `650f3a1`.

### 4.1. Движок llama.cpp недостижим на macOS

`VideoContextEngine_v3.19.py:1808-1809`:

```python
def get_vlm_engine() -> VLMProvider:
    return MLXEngine() if platform.system() == "Darwin" else LlamaCppEngine()
```

Жёсткий платформенный гейт. Класс `LlamaCppEngine` (строка 1704) на Mac не
создаётся никогда. Нужен явный выбор backend, а не `platform.system()`.

### 4.2. Для Text LLM пути через llama.cpp нет вообще

`run_text_llm_postprocess` (строка 2872) на строках 2884–2890 безусловно
импортирует MLX:

```python
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config
```

Альтернативы нет. Кроме того, allow-list на строке 3582 принимает ровно одно
значение — `TEXT_LLM_DEFAULT_MODEL` (`mlx-community/Qwen3.5-9B-MLX-4bit`).
То есть `Qwen3.5-9B GGUF Q4_K_M` сейчас нельзя ни задать, ни загрузить.

**Это самый большой объём работы.** Нужен второй путь для текстовой стадии:
загрузка GGUF через `llama_cpp.Llama`, тот же контракт возврата
(`Dict[str, Any]` с валидированным обогащением), плюс расширение allow-list.

Отдельно: отчёт `benchmarks/2026-11-06-nsrspec-forensic-run.md` сообщает
Text LLM на llama.cpp/Metal за 88.591 s, но в этом репозитории такого кода нет.
Значит тот прогон выполнялся на другой сборке. Расхождение стоит выяснить до
замера, иначе цифры будут несопоставимы.

### 4.3. Зашит не тот размер VLM

Строки 458–460:

```python
DEFAULT_VLM_REPO_GGUF = "bartowski/Qwen3-VL-2B-Instruct-GGUF"
DEFAULT_VLM_MODEL_GGUF = "qwen3-vl-2b-instruct-q4_k_m.gguf"
DEFAULT_MMPROJ_GGUF = "mmproj-model-f16.gguf"
```

Спецификация требует **8B** Q4_K_M, в коде — **2B**. При подмене модели
изменится и качество, и время VLM-стадии. Нужен явный репозиторий и имя файла
для 8B, плюс `mmproj` именно от 8B.

### 4.4. Chat handler не для Qwen3-VL

Строка 1741: `from llama_cpp.llama_chat_format import Llava15ChatHandler`.
Для Qwen3-VL нужен обработчик своего семейства (в llama.cpp-экосистеме это
`Qwen3VLChatHandler` либо прямой вызов с шаблоном). Совместимость
`Llava15ChatHandler` с Qwen3-VL не проверена и требует подтверждения на Mac
до того, как планировать замер.

### 4.5. Зависимость не объявлена

`llama-cpp-python` отсутствует и в `requirements.txt`, и в
`requirements-apple-silicon.txt` (там `mlx-vlm`, `torchvision`, `ocrmac`,
`certifi`, `mlx-whisper`). Есть только комментарий в шапке файла на строке 25.
Для Metal нужна сборка с нужными флагами — это отдельный шаг установки.

### 4.6. Что уже работает и менять не нужно

- **Apple OCR cache.** `_VCE_OCR_CACHE_MAX = 256` (строка 4040), применение —
  строка 5202: `_vce_cached_apple_vision_ocr` при `enable_ocr_cache`.
  Включается через настройку или Form-параметр (строка 4757).
- **FFmpeg/Tesseract OCR.** Интервал по умолчанию 5 (строка 4753), workers из
  настроек (строка 4754). Для спецификации достаточно
  `ffmpeg_ocr_workers = 3` в конфиге.
- **Whisper large / MPS.** Работает после слияния #38.

## 5. Требования к переключаемому backend llama.cpp/Metal

Архитектуры не смешиваются: каждый профиль целиком на своём backend, кроме
Whisper, который в обоих остаётся PyTorch/MPS.

### 5.1. Расширение профилей

Сейчас в PR #38 две константы (строки 416–421 в ветке `7158006`):
`baseline_mps_sequential` и `mlx_parallel`. Предлагается добавить третий
профиль, например `llamacpp_metal`, со значениями по умолчанию:

| Ключ | `baseline_mps_sequential` | `llamacpp_metal` |
|---|---|---|
| Whisper backend | PyTorch/MPS | PyTorch/MPS |
| Whisper порядок | последовательно | последовательно |
| VLM backend | MLX | llama.cpp/Metal |
| VLM модель | `mlx-community/Qwen3-VL-8B-Instruct-4bit` | Qwen3-VL-8B GGUF Q4_K_M |
| Text LLM backend | MLX | llama.cpp/Metal |
| Text LLM модель | `mlx-community/Qwen3.5-9B-MLX-4bit` | Qwen3.5-9B GGUF Q4_K_M |

Профиль должен попадать в `meta.runtime_profile` отчёта, чтобы прогоны нельзя
было перепутать.

### 5.2. Точки изменения

| Файл / строка | Что менять |
|---|---|
| `:1808-1809` | `get_vlm_engine()` — выбор по профилю, а не по `platform.system()` |
| `:458-460` | Отдельные константы для 8B GGUF и его `mmproj` |
| `:1740-1755` | Chat handler под Qwen3-VL; проверка наличия файлов модели |
| `:2872-2890` | Вторая ветка `run_text_llm_postprocess` для llama.cpp |
| `:3582` | Allow-list: разрешить GGUF-модель по профилю |
| `requirements-apple-silicon.txt` | Добавить `llama-cpp-python` с Metal |
| `docs/RUNTIME_PROFILES.md` | Описать третий профиль и границы применимости |

### 5.3. Что не должно измениться в baseline

Профиль `baseline_mps_sequential` обязан остаться побайтово тем же по
поведению: MLX VLM, MLX Text LLM, Whisper/MPS последовательно. Второй прогон
не должен повлиять на первый — ни через общие константы, ни через кэши.

Кэш Whisper уже разделён по профилю (`_vce_whisper_cache_key` =
`f"{profile}|{device}|{model}"`), это сохранить.

## 6. Порядок выполнения

1. Слить #38 (правка 1 → 217/217 тестов).
2. Прогон 1 на указанном видео: `baseline_mps_sequential`. Зафиксировать
   `meta.runtime_profile`, `whisper_backend`, стадийные тайминги.
3. Слить #40 после правок A+Б и смены base.
4. Реализовать профиль `llamacpp_metal` по §5 (новый PR).
5. Прогон 2 тем же файлом и с теми же параметрами сцены, кадров, разрешения,
   промптов и OCR.
6. Сравнивать и время, и качество. Только время — недостаточно
   (`docs/RUNTIME_PROFILES.md`, раздел Measurement requirements).

## 7. Оговорки

- Код llama.cpp-ветки на Apple Silicon не проверялся: в среде проверки нет
  Metal, MLX и `llama-cpp-python`. Все утверждения §4 — чтение исходника,
  а не запуск.
- Совместимость `Llava15ChatHandler` с Qwen3-VL (§4.4) — открытый вопрос.
- Ни один из двух прогонов ещё не выполнен. Единственная известная цифра
  (345.324 s) снята на другой конфигурации и для сравнения не годится.
