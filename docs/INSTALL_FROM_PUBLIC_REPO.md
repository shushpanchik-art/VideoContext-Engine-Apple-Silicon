# Чистая установка VideoContext Engine на macOS (Apple Silicon)

Репозиторий содержит полный изменённый runtime VideoContext Engine v3.19, соответствующий веб-интерфейс, патчеры, тесты и исходники нативной оболочки macOS.

## 1. Системные инструменты

Установите Homebrew, если он ещё не установлен. Затем:

    brew install python@3.11 ffmpeg

Для сборки нативной оболочки дополнительно нужны Xcode Command Line Tools (см. шаг 6).

## 2. Клонирование и окружение Python

    git clone https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon.git ~/VideoContext-Engine
    cd ~/VideoContext-Engine
    python3.11 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install -r requirements-apple-silicon.txt

Виртуальное окружение создаётся локально и не входит в Git.

## 3. Локальная конфигурация

    cp config/videocontext_settings.apple_silicon.example.json videocontext_settings.json

Этот стартовый профиль рассчитан на Apple Silicon Mac с 32 ГБ unified memory и использует Qwen3-VL 8B и Whisper large. На менее мощном Mac уменьшите модель и/или разрешение до первого запуска.

Файл config/videocontext_settings.local-example.json — дополнительный обезличенный пример индивидуальных настроек сопровождающего проекта. Это справочная копия; он не содержит рабочих ключей, паролей или абсолютных путей. Не копируйте его поверх своей конфигурации без просмотра.

Рабочий videocontext_settings.json хранится только локально и исключён из Git.

## 4. Запуск

    source .venv/bin/activate
    python VideoContextEngine_v3.19.py

Веб-интерфейс: http://127.0.0.1:7555/ui

Swagger / API: http://127.0.0.1:7555/docs

При первом использовании Whisper и VLM загрузят ML-модели в локальный пользовательский кэш. Модели не распространяются в этом репозитории и могут занимать несколько гигабайт.

## 5. Очередь локальных видео и URL

В веб-интерфейсе доступны локальные видеофайлы и URL-источники, добавляемые в последовательную очередь. Для URL-источников используются доступные экстракторы yt-dlp (включая VK Video, YouTube и RuTube). Качество загрузки ограничивается настройками активного VLM/OCR профиля; недоступный подходящий поток приводит к ошибке, а не к неограниченной загрузке максимального разрешения.

## 6. Дополнительно: нативное приложение macOS

После чистой установки зависимостей и создания videocontext_settings.json можно локально собрать AppKit/WebKit оболочку и установить её в /Applications:

    xcode-select --install
    cp patches/patch_vce_stop_and_mps.py ./patch_vce_stop_and_mps.py
    chmod +x macos/install_video_context_mac.command
    bash macos/install_video_context_mac.command

Установщик проверит окружение, попросит остановить сервер и подтвердить действие словом STOP. Не запускайте установщик во время анализа видео. Он создаёт/обновляет приложение и может запросить пароль администратора для /Applications.

В репозитории находятся исходники оболочки и installer, а не заранее собранный подписанный .app.

## Исключённые данные

В публичном Git нет Python virtualenv, ML-моделей и кэшей, исходных локальных видео и аудио, созданных отчётов, журналов, резервных копий, токенов или рабочего локального settings-файла. Эти данные остаются на компьютере владельца.

## Платформа и лицензии

Инструкция предназначена для macOS на Apple Silicon. Windows/Linux и Intel Mac требуют отдельной проверки зависимостей и backend. Attribution и сведения о лицензиях: UPSTREAM.md, NOTICE.md, THIRD_PARTY_LICENSES.md и LICENSE.
