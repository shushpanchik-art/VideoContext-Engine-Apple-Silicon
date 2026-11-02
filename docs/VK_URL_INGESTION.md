# URL-очередь и автоматический выбор качества

Патч patches/patch_vce_url_batch_ingestion.py расширяет уже существующую пакетную очередь VideoContext Engine:

- локальные видео, выбранные системным диалогом macOS, и интернет-ссылки попадают в одну очередь;
- уже существующие local-token, отмена, индикаторы прогресса и отправка сохранённых per-request настроек остаются штатными;
- можно вставить несколько URL (по одной ссылке на строку), включая стандартную Markdown-обёртку [ссылка](ссылка);
- каждая ссылка обрабатывается последовательно через существующий POST /api/v1/analyze и его параметр video_url;
- каждая запись получает отдельный статус и ссылки на TXT/JSON, возвращаемые текущим API;
- ссылки передаются в существующее поле video_url; local-token для локальных видео и обычная multipart-загрузка браузера не меняются;
- загрузка URL выполняется через asyncio.to_thread, поэтому yt-dlp не блокирует event loop FastAPI и /ui, /docs остаются доступными.
- UI получает прогресс загрузки от yt-dlp; запрос остановки проверяется в progress hook и прерывает скачивание с HTTP 409, после чего очередь отмечает задачу как остановленную.

## Автовыбор разрешения

Перед скачиванием backend смотрит на параметры конкретного запроса, которые UI берёт из videocontext_settings.json:

- analysis_mode;
- vlm_resolution;
- enable_apple_ocr;
- enable_ffmpeg_ocr;
- skip_visual.

Сначала определяется нужная длинная сторона исходного кадра, затем к ней применяется небольшой запас на соотношение сторон. yt-dlp получает строгие фильтры по width И height. Неограниченного fallback на best нет.

Ориентиры:
- full, VLM 1280, OCR включён → предел примерно 1472 px, обычно до 720p;
- full, VLM 2048 → предел примерно 2355 px, обычно до 1080p;
- OCR/VLM отключены → небольшой поток для детектора сцен, обычно до 360p;
- OCR включён даже в режиме без Qwen3-VL → источник не ниже 720p-класса.

Точные доступные форматы зависят от площадки. Если она не отдаёт поток в заданном пределе, yt-dlp вернёт ошибку вместо скрытого скачивания 4K.

Это не отдельный audio-only конвейер: текущий /api/v1/analyze по-прежнему получает видеофайл для OpenCV/сцен. Патч уменьшает видео до достаточного источника по настройкам, но не удаляет видеоряд. Word timestamps, TXT/JSON и timings остаются частью обычного ответа движка.

Для URL-источника output_directory в этой версии движка остаётся текущей рабочей папкой сервера; готовые TXT/JSON доступны в карточке задачи через ссылки скачивания. Для системно выбранного локального файла сохранение по-прежнему выполняется рядом с исходником.

## Установка в уже настроенную копию

Сначала остановите сервер (Ctrl+C в окне Terminal либо штатно завершите приложение), затем выполните:

    PROJECT="$HOME/VideoContext-Engine"
    curl -fL --retry 3 \
      "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/vk-url-batch-ingestion-20261102/patches/patch_vce_url_batch_ingestion.py" \
      -o "$PROJECT/patch_vce_url_batch_ingestion.py"

    "$PROJECT/.venv/bin/python" "$PROJECT/patch_vce_url_batch_ingestion.py" --self-test
    "$PROJECT/.venv/bin/python" "$PROJECT/patch_vce_url_batch_ingestion.py" --project "$PROJECT" --dry-run
    "$PROJECT/.venv/bin/python" "$PROJECT/patch_vce_url_batch_ingestion.py" --project "$PROJECT"

Патчер до записи проверяет структуру текущего движка, UI и наличие per-request отправки сохранённых настроек. Если локальная версия отличается, он остановится без изменения файлов. Перед записью создаёт timestamped .bak для движка и UI.

После успешной установки перезапустите VideoContext Engine, откройте http://127.0.0.1:7555/ui и выполните жёсткое обновление Cmd+Shift+R. Вставьте URL по одному на строку, нажмите «Добавить ссылки в очередь», затем запустите очередь.

## Проверки

    "$PROJECT/.venv/bin/python" "$PROJECT/patch_vce_url_batch_ingestion.py" --self-test
    "$PROJECT/.venv/bin/python" -m unittest tests/test_vce_url_batch_ingestion.py -v

Эти тесты проверяют логику ограничения качества, преобразование backend и UI на фикстурах. Реальное скачивание и анализ нужно дополнительно проверить на Mac с активными настройками VLM/OCR и конкретными URL.
