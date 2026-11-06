# VideoContext Engine — Apple Silicon

Полная публичная сборка VideoContext Engine v3.19 для macOS на Apple Silicon: Python runtime, Web UI, очередь локальных файлов и URL, языковые чекбоксы распознавания, патчеры, тесты и исходники нативного macOS-приложения.

> Это независимая пользовательская сборка, не официальный релиз upstream. Атрибуция и условия сторонних компонентов: [UPSTREAM.md](UPSTREAM.md), [NOTICE.md](NOTICE.md), [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md), [LICENSE](LICENSE).

## Установка одной командой

Нужен Mac с Apple Silicon и Homebrew. Откройте Terminal и выполните:

```bash
curl -fsSL https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/main/scripts/install_video_context_engine.command -o /tmp/install_video_context_engine.command && bash /tmp/install_video_context_engine.command
```

Установщик сам скачает полный проект в `~/VideoContext-Engine`, установит Python 3.11, FFmpeg и Python-зависимости, создаст виртуальное окружение, сохранит старую локальную папку в резервную копию при необходимости, перенесёт пользовательский `videocontext_settings.json`, применит языковой патч через dry-run, выполнит smoke tests и соберёт `VideoContext Engine.app` в `/Applications`.

Для сборки .app требуются Xcode Command Line Tools. При их отсутствии установщик запустит системный запрос на установку и попросит повторить команду после её завершения. Во время установки введите `STOP`, когда установщик приложения попросит подтвердить, что сервер остановлен.

[Подробная инструкция и ручная установка](docs/INSTALL_FROM_PUBLIC_REPO.md).

## Скачать исходники ZIP

[Скачать весь актуальный исходный проект в ZIP](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/archive/refs/heads/main.zip).

ZIP содержит исходный код и установщики, но не виртуальное окружение, не ML-модели и не готовый подписанный .app. Нативное приложение собирается локально на Mac во время установки.

## Что входит в проект

- Полный runtime `VideoContextEngine_v3.19.py` и соответствующий Web UI `videocontext_web_ui.html`.
- Множественная очередь локальных видео и URL (VK Video, YouTube, RuTube и другие сайты, поддерживаемые yt-dlp).
- Автоматический выбор качества загрузки по настройкам активного VLM/OCR профиля.
- Whisper/MLX Whisper с временными метками слов, MLX VLM, Apple Vision OCR и необязательный FFmpeg/Tesseract OCR.
- Единые языковые чекбоксы распознавания; установщик применяет патчер к runtime и UI, а языковая доступность определяется локально.
- JSON/TXT-отчёты, настройки, профили анализа, контроль качества и безопасности памяти.
- Исходники AppKit/WebKit-приложения macOS, сборка, установка и восстановление приложения.
- Патчеры, тесты, GitHub Actions и документация.

## Первый запуск и модели

При первом анализе Whisper и VLM загружают выбранные модели в пользовательский кэш Hugging Face/MLX. Модели занимают несколько гигабайт и намеренно не включены в исходный ZIP. Размер и время загрузки зависят от выбранных моделей и скорости сети.

Настройки находятся в `~/VideoContext-Engine/videocontext_settings.json`. Пример профиля: [config/videocontext_settings.apple_silicon.example.json](config/videocontext_settings.apple_silicon.example.json). Исходные видео и готовые отчёты остаются на компьютере пользователя.

## Языки OCR

Установщик предлагает опционально установить Tesseract и дополнительные языковые данные `tesseract-lang`. Без них Tesseract-языки будут отображаться как недоступные; Apple Vision использует системную поддержку macOS, Whisper — встроенные языки установленной модели. Для установки дополнительных языков Tesseract позже используйте:

```bash
brew install tesseract
```

Если формула дополнительных языков доступна в Homebrew, установщик предложит её установить автоматически; иначе используйте документацию Tesseract для добавления нужных traineddata.

## Запуск и обновление

После установки откройте `/Applications/VideoContext Engine.app`. Интерфейс также доступен по адресу http://127.0.0.1:7555/ui, Swagger — http://127.0.0.1:7555/docs. Журнал сервера: `~/Library/Logs/VideoContext Engine/server.log`.

Обновляйте только после завершения текущих анализов. Установщик проверяет Git-состояние и не перезаписывает неизвестные локальные изменения. Если обнаружены пользовательские изменения, сначала сохраните их или укажите отдельный `VCE_PROJECT_DIR`.

## Работа с агентами и аппаратные тесты

Обязательные правила работы, полный тестовый видеоматериал, требования к автоматической выгрузке результатов и handoff следующему агенту: [USER_WORKFLOW_RULES.md](docs/USER_WORKFLOW_RULES.md) и [HANDOFF_NEXT_AGENT.md](docs/HANDOFF_NEXT_AGENT.md). Для упаковки локальной версии VCE с Mac и отправки в приватное staging-хранилище используйте [collect_vce_local_snapshot.command](scripts/collect_vce_local_snapshot.command).

## Приватность и границы поставки

В Git не включены Python virtualenv, ML-модели/кэши, пользовательские настройки, исходные видео/аудио, персональные результаты, логи, резервные копии и сырые benchmark outputs. Транскрипты и JSON-отчёты не публикуются автоматически.

Поддерживаемая платформа этой поставки — macOS на Apple Silicon. Windows, Linux и Intel Mac требуют отдельной проверки backend и установочных процедур. Приложение собирается локально и подписывается ad-hoc; это не нотарифицированный Developer ID релиз.

## Разработка и тестирование

```bash
python3 -m unittest discover -s tests -v
```

Документы об upstream и сторонних лицензиях обязательны к сохранению в производных копиях.
