# Отдельный backend llama.cpp / Metal

## Архитектурная граница

Параметр inference_backend — единый выбор для VLM и Text LLM:

- mlx_metal — значение по умолчанию. Сохраняет существующий MLX VLM и MLX Text LLM без изменения их кода.
- llama_cpp_metal — оба этапа используют GGUF через llama-cpp-python с Metal offload.

Whisper не переключается вместе с этим параметром. Его backend и порядок выполнения по-прежнему задаются runtime_profile; в согласованном сравнении используется PyTorch/MPS sequential. Поэтому это не изменение MLX baseline.

Нельзя выбрать llama.cpp только для одной из двух LLM-стадий: VLM и Text LLM переключаются вместе. В JSON-отчёте фиксируется meta.params.inference_backend, а runtime-диагностика каждой стадии содержит backend и accelerator.

## Модели по умолчанию

| Стадия | Репозиторий | Файл |
|---|---|---|
| VLM | Qwen/Qwen3-VL-8B-Instruct-GGUF | Qwen3VL-8B-Instruct-Q4_K_M.gguf |
| VLM projector | тот же репозиторий | mmproj-Qwen3VL-8B-Instruct-F16.gguf |
| Text LLM | gguf/Qwen3.5-9B-GGUF | qwen3.5-9b-Q4_K_M.gguf |

Файлы автоматически загружаются в Hugging Face cache; они не сохраняются в репозитории и не кладутся в рабочую папку проекта. Значения preset-моделей ограничены allow-list.

## Установка llama-cpp-python с Metal

Это опциональная зависимость: не добавляйте её в базовый requirements-apple-silicon.txt — MLX baseline не должен зависеть от llama.cpp. Для Qwen3VLChatHandler requirements-llama-cpp-metal.txt указывает на исходную сборку JamePeng fork, а не на произвольный PyPI wheel. Код использует новый llama_multimodal API с параметром mmproj_path и сохраняет fallback для прежнего clip_model_path.

В терминале на Apple Silicon:

    cd ~/VideoContext-Engine && source .venv/bin/activate && brew install cmake && python -m pip install --upgrade pip setuptools wheel cmake ninja && CMAKE_ARGS="-DGGML_METAL=on" FORCE_CMAKE=1 python -m pip install --upgrade --force-reinstall --no-cache-dir -r requirements-llama-cpp-metal.txt

После установки обязательно проверьте именно Metal и Qwen3-VL handler:

    python - <<'PY'
    from llama_cpp import llama_cpp
    try:
        from llama_cpp.llama_multimodal import Qwen3VLChatHandler
    except ImportError:
        from llama_cpp.llama_chat_format import Qwen3VLChatHandler
    assert llama_cpp.llama_supports_gpu_offload()
    print("Metal offload + Qwen3VLChatHandler: OK")
    PY

Если Qwen3VLChatHandler отсутствует, установленная сборка не подходит. Не заменяйте его на Llava15ChatHandler: это другой multimodal handler. Runtime завершится понятной ошибкой вместо незаметного CPU fallback.

## Включение

1. Откройте настройки анализа в нативном приложении.
2. Выберите llama.cpp / Metal — GGUF эксперимент.
3. Сохраните настройки; backend переключится для следующего задания.
4. Запустите тестовый ролик. Первый запуск загрузит GGUF и mmproj в HF cache.

Для возврата к MLX baseline выберите MLX / Metal — baseline и сохраните настройки.

## Ограничения и корректность измерений

- VLM Qwen3-VL требует Qwen3VLChatHandler и projector именно от той же модели.
- n_gpu_layers=-1 запрашивает offload всех слоёв; startup preflight проверяет поддержку GPU offload в native llama.cpp build. Это не заменяет фактическое профилирование Metal на целевом Mac.
- meta.params.inference_backend различает архитектуры; runtime_profile продолжает описывать только профиль Whisper/runtime scheduling.
- Для сравнения MLX и llama.cpp используйте один файл и одинаковые resolution, scene parameters, prompts и OCR. Отдельно сравнивайте качество JSON, а не только wall time.
- Размеры по умолчанию: около 5.03 GB для VLM Q4_K_M, 1.16 GB для projector и 6.17 GB для Text LLM Q4_K_M. Нужен запас unified memory под контекст и runtime.
