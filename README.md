# VideoContext Engine — Apple Silicon

Полная независимая сборка VideoContext Engine v3.19 с доработками для macOS на Apple Silicon. Репозиторий содержит рабочий Python runtime, соответствующий веб-интерфейс, набор патчей, URL-очередь, тесты и исходники нативной оболочки macOS.

> Это не официальный форк и не релиз upstream. Проект основан на VideoContext Engine от dolphin-creator; см. [UPSTREAM.md](UPSTREAM.md), [NOTICE.md](NOTICE.md) и [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Быстрая установка: macOS / Apple Silicon

Подробная инструкция: [docs/INSTALL_FROM_PUBLIC_REPO.md](docs/INSTALL_FROM_PUBLIC_REPO.md).

Требуются Homebrew, Python 3.11 и FFmpeg. В Terminal:

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

При первом запуске ML-модели загрузятся в локальный кэш пользователя. Это может занять несколько гигабайт и не входит в Git-репозиторий.

## Что входит

- Полный изменённый VideoContext Engine v3.19: `VideoContextEngine_v3.19.py`.
- Соответствующий Web UI: `videocontext_web_ui.html`.
- Локальная очередь видеофайлов и URL (VK Video, YouTube, RuTube через yt-dlp), отмена, прогресс и отчёты TXT/JSON.
- Apple Vision OCR, FFmpeg OCR, Whisper/MPS и MLX VLM оптимизации, безопасная работа с памятью и временными файлами.
- Нативная macOS-оболочка на AppKit/WebKit с пунктом меню, launcher и установщиком.
- Тесты, GitHub Actions workflows, инструменты сравнительного benchmark и техническая документация.

## Настройки

`config/videocontext_settings.apple_silicon.example.json` — чистый стартовый профиль. Он ориентирован на MacBook Pro M1 Max с 32 ГБ памяти и моделями Qwen3-VL 8B / Whisper large; для менее мощных Mac измените модели и разрешение.

`config/videocontext_settings.local-example.json` — обезличенный пример текущей конфигурации сопровождающего проекта. Это необязательная отправная точка, а не рабочий персональный конфиг.

Рабочий `videocontext_settings.json` создаётся локально и не публикуется. Не помещайте в Git токены, пароли, персональные пути, исходное видео или полученные отчёты.

## Нативное приложение macOS

После установки Python-зависимостей скопируйте установочный патчер и запустите installer:

```bash
cp patches/patch_vce_stop_and_mps.py ./patch_vce_stop_and_mps.py
xcode-select --install
bash macos/install_video_context_mac.command
```

Установщику нужны Xcode Command Line Tools и подтверждение остановки сервера. Не запускайте его во время анализа. Исходники AppKit/WebKit включены; заранее собранного и подписанного `.app` в репозитории нет.

## Границы поддержки

Полная установка документирована для macOS на Apple Silicon. Windows/Linux требуют отдельной проверки зависимостей и backend. Текущий upstream обозначает v3.19 как beta, поэтому перед обновлением версии следует повторять тесты.

## Лицензии и атрибуция

Исходный движок содержит upstream-заголовок MIT; лицензия и атрибуция upstream собраны в [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Лицензия локальных доработок находится в [LICENSE](LICENSE).

## Разработка и проверки

```bash
python3 -m unittest discover -s tests -v
```

CI запускает отдельные проверки URL ingestion, OCR workers, audio resilience, quality benchmark, optimization checkboxes и macOS lifecycle.
