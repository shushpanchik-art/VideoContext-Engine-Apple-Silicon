# Проверки и тестирование

Этот файл — постоянная инструкция по тестированию, а не снимок состояния конкретного коммита. Актуальные результаты проверок всегда находятся в [GitHub Actions для ветки main](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions?query=branch%3Amain).

## Полный локальный набор регрессионных тестов

Из корня репозитория:

```bash
python3 -m unittest discover -s tests -v
```

## Проверки установщика и дистрибутива

```bash
bash -n scripts/install_video_context_engine.command
bash -n macos/install_video_context_mac.command
bash -n "macos/VideoContext Engine.app/Contents/Resources/start_server.command"
python3 -m json.tool config/videocontext_settings.apple_silicon.example.json >/dev/null
python3 -m json.tool PUBLICATION_PACKAGE_MANIFEST.json >/dev/null
python3 -m unittest discover -s tests -p "test_vce_public_installer.py" -v
python3 -m unittest discover -s tests -p "test_vce_repository_hygiene.py" -v
```

## Основные GitHub Actions

- [Full distribution installer validation](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/workflows/full-distribution-installer.yml) — shell syntax, JSON validation, installer contracts, regression tests и компиляция AppKit/WebKit-приложения на macOS runner.
- [Audio resilience V6 patch tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/workflows/audio-resilience-v6-tests.yml) — тесты аудиоустойчивости и параллельной обработки.
- [FFmpeg OCR worker setting tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/workflows/ffmpeg-ocr-workers-setting-tests.yml) — тесты конфигурации OCR workers.
- [VCE quality benchmark tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/workflows/vce-quality-benchmark-tests.yml) — тесты инструментов оценки качества.
- [VCE speed profile tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/workflows/vce-speed-profile-tests.yml) — тесты профилей производительности и интеграции с macOS.
- [Merged topic branch cleanup](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/workflows/cleanup-merged-topic-branches.yml) — безопасная очистка только интегрированных веток.

Workflow запускаются по релевантным изменениям в PR и/или после push в `main`. Проверяйте статус конкретного PR и самый свежий запуск `main` непосредственно в GitHub; постоянные номера запусков в этот документ не копируются.

## Что CI не подтверждает

Успешная компиляция приложения и автоматические тесты не заменяют сквозной прогон реального видео на физическом Apple Silicon Mac. Отдельно проверяются локальный MLX/Metal inference, Whisper, Apple Vision OCR, FFmpeg, отмена задачи, корректность итоговых TXT/JSON и завершение процессов после закрытия приложения. Такие hardware acceptance-тесты нельзя считать выполненными только на основании GitHub Actions.

## Политика приватности тестов

Тесты и CI не должны публиковать исходные видео/аудио, персональные настройки, модели, локальные отчёты, сырые watchdog/Metal telemetry и per-video JSON. GitHub upload для benchmark-результатов выключен по умолчанию; любое разрешённое исключение должно быть отдельным явным действием.
