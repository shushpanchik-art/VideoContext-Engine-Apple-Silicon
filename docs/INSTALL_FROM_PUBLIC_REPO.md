# Чистая установка VideoContext Engine на macOS (Apple Silicon)

## Рекомендуемый способ: один установщик

Нужны macOS на Apple Silicon, Homebrew и доступ в интернет. Выполните:

```bash
curl -fsSL https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/main/scripts/install_video_context_engine.command -o /tmp/install_video_context_engine.command && bash /tmp/install_video_context_engine.command
```

Установщик:
1. Проверяет архитектуру Mac, Homebrew, Git, свободный порт 7555 и Xcode Command Line Tools.
2. Устанавливает Homebrew Python 3.11 и FFmpeg.
3. Предлагает поставить Tesseract и дополнительные языковые данные.
4. Клонирует репозиторий в `~/VideoContext-Engine`. Если там уже лежит не-Git папка, перемещает её в датированную резервную копию; старый пользовательский `videocontext_settings.json` переносится.
5. Создаёт `.venv` и устанавливает `requirements.txt` вместе с `requirements-apple-silicon.txt`, включая MLX Whisper.
6. Создаёт настройки из Apple Silicon example, если пользовательские настройки отсутствуют.
7. Запускает языковой установщик: показывает dry-run и diff, затем применяет идемпотентный патч к runtime/UI/settings с резервными копиями.
8. Проверяет Python-синтаксис и языковые regression tests.
9. Собирает и устанавливает AppKit/WebKit-приложение в `/Applications/VideoContext Engine.app`.

Если Xcode Command Line Tools отсутствуют, macOS откроет системный установщик. Завершите его и запустите команду выше повторно.

Во время последнего шага введите `STOP`, чтобы подтвердить, что старый сервер остановлен. Затем нажмите Enter после установки.

## Безопасность существующей установки

Если `~/VideoContext-Engine` существует и не является Git-клоном этого репозитория, установщик не удаляет его, а переименовывает в датированную резервную копию. Если это Git-клон репозитория с чистыми отслеживаемыми файлами, он обновляется fast-forward до `main`. При изменённых tracked-файлах установщик останавливается и не перезаписывает их.

Если нужно установить в отдельную папку:

```bash
VCE_PROJECT_DIR="$HOME/VideoContext-Engine-Public" bash /tmp/install_video_context_engine.command
```

Путь сохраняется для нативного приложения в `~/Library/Application Support/VideoContext Engine/project_path`.

## Ручная установка

```bash
brew install python@3.11 ffmpeg
# Tesseract необязателен:
brew install tesseract

git clone https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon.git ~/VideoContext-Engine
cd ~/VideoContext-Engine
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-apple-silicon.txt
cp config/videocontext_settings.apple_silicon.example.json videocontext_settings.json
bash scripts/install_vce_recognition_languages_patch.command
bash macos/install_video_context_mac.command
```

Подтвердите остановку сервера словом `STOP`. Нативная оболочка компилируется локально на этом Mac.

## После установки

Откройте `/Applications/VideoContext Engine.app`. Локальная панель: http://127.0.0.1:7555/ui. Swagger: http://127.0.0.1:7555/docs. Лог: `~/Library/Logs/VideoContext Engine/server.log`.

При первом анализе Whisper и VLM загружают модели в локальный кэш Hugging Face/MLX. Модели не входят в исходный ZIP и могут занимать несколько гигабайт.

## Жизненный цикл нативного приложения

Закрытие главного окна завершает приложение и его локальный Python-сервер. При следующем запуске приложение сначала ищет оставшиеся процессы `VideoContextEngine_v3.19.py` только по сохранённому пути текущего проекта, отправляет им SIGTERM и применяет SIGKILL к тем, которые не завершились за короткий таймаут. Другие Python-программы и другие клоны проекта не затрагиваются. Это намеренно прерывает текущую обработку при закрытии приложения; дождитесь завершения или сохраните результаты перед закрытием.

## Языки и OCR

Tesseract показывает только реально установленные traineddata. Установщик предлагает поставить Tesseract; дополнительные языки могут требовать отдельной установки данных. Apple Vision поддерживает только языки, доступные текущей версии macOS. Whisper использует языковой набор модели; VLM/LLM получают разрешённый список как инструкцию, а не как гарантию семантической точности.

## Что публикуется и что остаётся локально

Публичный репозиторий содержит исходники, runtime, Web UI, установщики, патчеры, тесты и документацию. Он не содержит виртуальное окружение, скачанные ML-модели, пользовательские настройки, исходные видео/аудио и созданные отчёты. Полные ответы API и транскрипты не отправляются в GitHub.

Сборка приложения подписывается локальной ad-hoc подписью. Это не нотарифицированный Developer ID релиз. Для установки доступны исходники и автоматический локальный build; заранее собранный бинарный .app в ZIP не вкладывается.

Готовый ZIP для чистой установки на Apple Silicon описан в [инструкции](CLEAN_INSTALL_MACOS.md).
