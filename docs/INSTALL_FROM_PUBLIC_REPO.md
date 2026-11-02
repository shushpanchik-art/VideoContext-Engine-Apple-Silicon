# Чистая установка VideoContext Engine на macOS (Apple Silicon)

В репозитории находятся полный исходный Python-движок v3.19, веб-интерфейс, набор доработок Apple Silicon, тесты и исходники нативной оболочки macOS.

## 1. Установите системные инструменты

Установите Homebrew, если он ещё не установлен, затем выполните:

    brew install python ffmpeg

## 2. Скачайте проект и подготовьте окружение

    git clone https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon.git ~/VideoContext-Engine
    cd ~/VideoContext-Engine
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install -r requirements-apple-silicon.txt

## 3. Создайте локальный файл настроек

    cp config/videocontext_settings.apple_silicon.example.json videocontext_settings.json

Профиль apple_silicon.example отражает проверенную конфигурацию MacBook Pro M1 Max с 32 ГБ памяти (Qwen3-VL 8B и Whisper large). Для Mac с меньшим объёмом памяти измените модели и параметры качества в videocontext_settings.json перед первым запуском.

Файл config/videocontext_settings.local-example.json, если он присутствует, — обезличенный пример индивидуальных настроек сопровождающего проекта. Он не содержит рабочие ключи или локальные абсолютные пути и не должен автоматически заменять ваш конфиг.

## 4. Запустите движок

    source .venv/bin/activate
    python VideoContextEngine_v3.19.py

Откройте веб-интерфейс:

    http://127.0.0.1:7555/ui

Документация API:

    http://127.0.0.1:7555/docs

При первом анализе необходимые ML-модели загружаются в обычный пользовательский кэш. В Git они не включаются: размер моделей может составлять несколько гигабайт.

## 5. (Необязательно) Соберите и установите нативное приложение macOS

После установки зависимостей и создания videocontext_settings.json можно собрать оболочку AppKit/WebKit и установить её в /Applications:

    cp patches/patch_vce_stop_and_mps.py ./patch_vce_stop_and_mps.py
    chmod +x macos/install_video_context_mac.command
    bash macos/install_video_context_mac.command

Для этого нужны Xcode Command Line Tools (xcode-select --install). Установщик попросит остановить сервер и подтвердить действие словом STOP. Не выполняйте его во время анализа видео.

## Что не публикуется

В репозитории нет виртуального окружения Python, загруженных моделей, ML-кэшей, исходных видео, частных отчётов, журналов, резервных копий или реальных локальных настроек. Перед публикацией индивидуальный конфиг проверяется и сохраняется только как обезличенный пример.

## Платформа и лицензии

Инструкции выше предназначены для macOS на Apple Silicon. Другие платформы требуют отдельной проверки зависимостей и backend. Проект основан на VideoContext Engine v3.19; сведения об upstream и условиях лицензии находятся в UPSTREAM.md, NOTICE.md и LICENSE.
