# VideoContext Engine — Apple Silicon

Полная независимая сборка VideoContext Engine v3.19 с доработками для macOS на Apple Silicon. В репозитории находятся рабочий Python runtime, согласованный веб-интерфейс, патчеры, URL-очередь, тесты и исходники нативного macOS-приложения.

> Это не официальный релиз upstream и не официальный форк автора. Проект основан на VideoContext Engine от dolphin-creator; сведения об исходной версии, атрибуции и лицензиях: [UPSTREAM.md](UPSTREAM.md), [NOTICE.md](NOTICE.md), [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Быстрая установка: macOS / Apple Silicon

Полная инструкция: [docs/INSTALL_FROM_PUBLIC_REPO.md](docs/INSTALL_FROM_PUBLIC_REPO.md).

Нужны Homebrew, Python 3.11 и FFmpeg:

```bash
brew install python@3.11 ffmpeg
git clone https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon.git ~/VideoContext-Engine
cd ~/VideoContext-Engine
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-apple-silicon.txt
cp config/videocontext_settings.apple_silicon.example.json videocontext_settings.json
python VideoContextEngine_v3.19.py
```

Откройте http://127.0.0.1:7555/ui. Документация API: http://127.0.0.1:7555/docs.

При первом запуске Whisper и VLM загрузят модели в локальный пользовательский кэш. Модели могут занимать несколько гигабайт и не входят в Git.

## Что входит в сборку

- Полный изменённый движок `VideoContextEngine_v3.19.py` и соответствующий `videocontext_web_ui.html`.
- Очередь локальных видеофайлов и URL (VK Video, YouTube, RuTube через yt-dlp), последовательная обработка, прогресс и отмена.
- TXT/JSON-отчёты, временные метки слов, Whisper/MPS, MLX VLM, Apple Vision OCR и необязательный FFmpeg OCR.
- Настройки Apple Silicon, профили анализа, средства контроля качества и безопасности памяти.
- Нативная macOS-оболочка AppKit/WebKit с окном приложения и пунктом меню; исходники и локальный installer.
- Патчеры, регрессионные тесты, GitHub Actions workflows, benchmark-инструменты и документация.

## Настройки

`config/videocontext_settings.apple_silicon.example.json` — профиль для старта, ориентированный на MacBook Pro M1 Max с 32 ГБ памяти, Qwen3-VL 8B и Whisper large. На менее мощных Mac следует уменьшить модель и/или разрешение.

`config/videocontext_settings.local-example.json` — дополнительный обезличенный пример индивидуальных настроек сопровождающего проекта. Он не заменяет персональную конфигурацию автоматически.

Рабочий `videocontext_settings.json` создаётся локально и не публикуется. Не помещайте в Git токены, пароли, персональные пути, исходные видео или приватные отчёты.

## Нативное приложение macOS

После установки Python-зависимостей можно собрать оболочку локально:

```bash
xcode-select --install
cp patches/patch_vce_stop_and_mps.py ./patch_vce_stop_and_mps.py
bash macos/install_video_context_mac.command
```

Установщику нужны Xcode Command Line Tools и подтверждение остановки сервера. Не запускайте установщик во время анализа. В репозитории есть исходники и installer, но нет заранее собранного и подписанного `.app`.

## Проверка качества Text LLM

- `tools/quality_check_text_llm_variants.py` — универсальная структурная проверка JSON, scene IDs, полноты `scene_enrichment` и текстовой целостности.
- `tools/evaluate_text_llm_grounding.py` — лексические указатели на source evidence по сценам и модальностям. Это подсказки для ручной проверки, а не семантическое доказательство.
- `tools/bench_text_llm_summary.py` — локальный A/B-бенчмарк Text LLM с отдельным учётом сырого ответа модели и финализированного VCE payload.

Подробности и ограничения: [docs/TEXT_LLM_QUALITY_AUDIT.md](docs/TEXT_LLM_QUALITY_AUDIT.md).

Бенчмарк требует явного `--video PATH` или `--input-json PATH`. Он не публикует данные в GitHub по умолчанию. Для публикации метрик нужен явный `--upload`; для отправки сгенерированных текстов дополнительно нужен `--upload-outputs`. Модельные ответы могут содержать пользовательскую информацию — проверяйте их перед отправкой.

## Диагностика MLX / Metal

Для локальной проверки конфигурации MLX, потоков VLM и ресурсов анализа доступны observer/watchdog-скрипты. Они сохраняют полный ответ, транскрипт и системные сэмплы только локально. Используйте [инструкцию диагностики](docs/MLX_METAL_DIAGNOSTICS.md); полученные отчёты не коммитьте.

Metal smoke test или поле configured GPU не являются доказательством того, что каждый оператор VLM исполнялся на GPU. Документация отдельно описывает интерпретацию device/stream телеметрии.

## Приватность и границы поставки

В Git не включаются Python virtualenv, скачанные ML-модели, пользовательские настройки, оригинальные видео/аудио, локальные TXT/JSON-отчёты, резервные копии и необработанные watchdog-дампы. Бенчмарк работает локально и загружает данные только при явном указании upload-флагов.

Полная установка документирована для macOS на Apple Silicon. Windows, Linux и Intel Mac требуют отдельной проверки зависимостей и вычислительного backend.

## Разработка и проверки

Запуск unit tests локально:

```bash
python3 -m unittest discover -s tests -v
```

CI включает проверки URL ingestion, OCR workers, audio resilience, quality benchmark, optimization checkboxes и macOS lifecycle.
