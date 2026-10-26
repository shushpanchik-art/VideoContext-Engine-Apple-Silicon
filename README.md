# VideoContext Engine — Apple Silicon Optimization & macOS Workflow

Русскоязычный набор патчей и macOS launcher для **VideoContext Engine v3.19**. Репозиторий описывает локальные доработки, которые применяются поверх исходного проекта; он не является официальным форком и не связан с автором upstream-проекта.

**Исходный проект:** [dolphin-creator/VideoContext-Engine](https://github.com/dolphin-creator/VideoContext-Engine) — локальный Video RAG / FastAPI engine, объединяющий детекцию сцен, Whisper ASR, Qwen3-VL и сводку.

Исходная база, проверенная для этого набора: `main` на коммите `4a42b4a309d9d7d8f7f944c2e210a027940208f3` (4 декабря 2025); файл `VideoContextEngine_v3.19.py` имел blob SHA `cfa22be84dfbb5f9e6f29269c9906c376e90ef17` при проверке 23 октября 2026. Сверяйте код перед применением к более новой версии upstream.

> **Ключевая работа — практический поиск баланса качество/скорость на Apple Silicon.** Профиль подобран по замерам на MacBook Pro M1 Max, 32 ГБ RAM, MLX Qwen3-VL-8B-4bit и Whisper `large`, а не только теоретически. Результаты и ограничения опубликованы в [`docs/APPLE_SILICON_BENCHMARKS.md`](docs/APPLE_SILICON_BENCHMARKS.md).

## Что доработано

- Сценарии анализа `full` / `whisper_ocr` / `ocr_only`; режимы без Qwen3-VL сохраняют Qwen3.5 для смысловой сборки отчёта из Whisper и OCR.
- Необязательный FFmpeg/Tesseract OCR с проверкой доступности фильтра и сообщением вместо сбоя, если системная сборка его не содержит.
- Аспектно-сохраняющее уменьшение изображения для VLM; тестовый профиль 1280 px вместо дорогих больших входных кадров.
- Apple Vision OCR с точным режимом и оригинальным разрешением исходного кадра; текст OCR передаётся Qwen как подсказка и включается в результаты.
- JSON-файл постоянных настроек и их панель в веб-интерфейсе.
- Переключатель ускорения Whisper для MPS/CUDA/CPU и использование MPS на Apple Silicon.
- Временные метки слов Whisper, распределение распознанной речи по сценам и аудиопризнаки сцены.
- Точечное исправление Whisper DTW на MPS: `x.double().cpu()` → `x.cpu().double()`.
- Выбор нескольких видео через системное окно macOS, последовательная очередь и отдельный статус/прогресс для каждого файла.
- Кооперативная кнопка **«Остановить распознавание»**: текущий GPU/MLX шаг завершается до безопасной точки, следующие файлы очереди не запускаются.
- Автоматическое сохранение TXT и JSON рядом с исходным видео и ссылки на результаты в интерфейсе.
- Нативное macOS-приложение с окном `WKWebView` и постоянным значком меню (`NSStatusItem`). Сервер запускается фоновым процессом приложения без отдельного окна Terminal; журнал доступен в `~/Library/Logs/VideoContext Engine/server.log`.
- Отдельный конфигурационный патчер `patch_vce_speed_profile.py`: ускоряет VLM-конвейер, не отключая VLM и сохраняя аудиопуть.

## Рекомендуемый профиль для презентаций и лекций

См. [пример настроек](config/videocontext_settings.apple_silicon.example.json). Важнейшие параметры:

| Параметр | Значение |
|---|---|
| VLM | `mlx-community/Qwen3-VL-8B-Instruct-4bit` |
| Whisper | `large` |
| Whisper acceleration | `true` — MPS на Apple Silicon |
| VLM resolution | `1280` |
| Scene threshold | `0.15` |
| Min / max scene duration | `1.5` / `20` секунд |
| Keyframes per scene | `1` для преимущественно статичных слайдов |
| Apple Vision OCR | enabled, `accurate`, `ru-RU` + `en-US` |
| Audio features / global summary | enabled |
| TXT + JSON autosave | enabled |
| RAM mode | `ram-` |

Для демонстраций интерфейса, видео с движением и быстрыми переходами используйте 2 кадра на сцену и/или меньшую длительность сцены. Рекомендация `20 s / 1 frame` — компромисс, проверенный на презентационном видео, а не универсальный оптимум для всех типов контента.

## Установка на macOS

### Вариант A — свежий checkout upstream v3.19

1. Установите исходный проект из [upstream README](https://github.com/dolphin-creator/VideoContext-Engine) в `~/Downloads/VideoContext-Engine` и подготовьте `.venv`.
2. Для Apple Silicon установите исходные и дополнительные зависимости в активном окружении (сверяйте команды с upstream README):

   ```bash
   cd ~/Downloads/VideoContext-Engine
   source .venv/bin/activate
   python -m pip install fastapi 'uvicorn[standard]' opencv-python yt-dlp pillow numpy openai-whisper huggingface_hub
   python -m pip install mlx-vlm torchvision ocrmac certifi
   ```

3. Скопируйте содержимое этого репозитория/архива и выполните из Terminal:

   ```bash
   bash ./install_patches_to_existing_checkout.command "$HOME/Downloads/VideoContext-Engine"
   ```

4. Скрипт скопирует патчеры в проект и применит их в нужном порядке. Он делает резервные копии, которые создаёт каждый патчер.
5. Введите пароль macOS, когда установщик спросит его для записи `/Applications`.
6. Запустите **VideoContext Engine** из приложения/ярлыка рабочего стола. Интерфейс: <http://127.0.0.1:7555/ui>; Swagger: <http://127.0.0.1:7555/docs>.

**Внимание:** установщик предназначен для свежего исходного checkout ожидаемой версии. Не запускайте всю последовательность заново поверх уже настроенного экземпляра: часть первоначальных патчеров устанавливает рекомендуемые значения по умолчанию. Для обновления работающего экземпляра изучайте и применяйте только нужный патч.

### Вариант B — текущая уже установленная копия

Не запускайте `install_patches_to_existing_checkout.command` поверх рабочей копии без резервной копии. Ваш рабочий инстанс может иметь более свежие патчи и пользовательские настройки. Используйте индивидуальный патчер только после проверки его README и бэкапов.


#### Исправить уже установленное приложение без сброса настроек

Если интерфейс открывается через Terminal или сервер требует вручную запускать `start_server.command`, обнови только нативную оболочку. Этот recovery-скрипт не меняет Python-движок, `videocontext_settings.json`, выбор генерации TXT/JSON или готовые результаты. Перед обновлением дождись окончания анализа, останови сервер, закрой приложение и выполни:

```bash
curl -fL --retry 3 \
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/fix/vce-speed-profile-and-menubar-20261024/macos/repair_installed_native_app.command" \
  -o "$HOME/Downloads/repair_installed_native_app.command"
bash "$HOME/Downloads/repair_installed_native_app.command"
```

Скрипт сохранит старый бинарный файл на рабочий стол и пересоберёт приложение с AppKit/WebKit. Если порт 7555 занят, он не останавливает сервер, но требует явного подтверждения `NO_ACTIVE_ANALYSIS`: при активной задаче нельзя закрывать оболочку, иначе исчезнет отображение очереди/прогресса. Затем требуется ввести `INSTALL`. Новая оболочка запускает сервер в фоне без Terminal и пишет журнал в `~/Library/Logs/VideoContext Engine/server.log`. Закрытие окна не останавливает анализ; при следующем запуске оболочка подключается к уже работающему серверу.

#### Усиление качества технического конспекта

Для существующего проекта есть отдельный патчер промптов. Он усиливает дословное сохранение команд, путей, чисел и читаемого текста с экрана; просит отделять факты от догадок; а также исправляет сборку контекста глобального резюме, чтобы визуальное описание не терялось при наличии тегов. Патчер не меняет модель или режимы анализа и сохраняет прочие параметры.

Сначала скачайте и проверьте его без изменений:

```bash
PROJECT="$HOME/VideoContext-Engine"
mkdir -p "$PROJECT/patches"
curl -fL --retry 3 \
  "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/fix/vce-speed-profile-and-menubar-20261024/patches/patch_vce_quality_prompt.py" \
  -o "$PROJECT/patches/patch_vce_quality_prompt.py"

"$PROJECT/.venv/bin/python" "$PROJECT/patches/patch_vce_quality_prompt.py" \
  --project "$PROJECT" --dry-run
```

Если dry-run перечисляет ожидаемые изменения, остановите сервер VCE через `Ctrl+C` и примените:

```bash
"$PROJECT/.venv/bin/python" "$PROJECT/patches/patch_vce_quality_prompt.py" \
  --project "$PROJECT"
```

Патчер делает резервные копии `VideoContextEngine_v3.19.py` и `videocontext_settings.json` перед записью. После применения перезапустите сервер; качество всё равно нужно проверить на типичных роликах (слайды, терминал/код, мелкий текст, динамический UI и видео без звука).

## Ошибки Metal OutOfMemory и «Fichier vidéo invalide»

Для уже настроенной локальной копии есть отдельный безопасный патчер: [инструкция по очистке GPU-кэша, повтору VLM с меньшим кадром и fallback-декодированию через FFmpeg](docs/MEMORY_SAFETY_AND_VIDEO_COMPATIBILITY.md). Он сохраняет выбранную модель, аудиопуть, OCR, JSON и текущий выбор TXT; оригинальные видео не переписываются. Не применяйте его во время активной обработки и не запускайте затем speed-profile patcher, если хотите сохранить сниженный memory-safe предел 768 px.

## Выходные файлы

Для видео, выбранного через нативный file picker macOS, каждый включённый тип отчёта сохраняется рядом с оригинальным роликом. Если генерация TXT или JSON отключена в настройках, соответствующий файл намеренно не создаётся; `txt_filename: null` при выключенном TXT — ожидаемое поведение, а не ошибка сохранения. Это работает потому, что сервис получает реальный локальный путь через локальный API; обычный браузерный `<input type=file>` не раскрывает исходный абсолютный путь.

## Важные технические детали

- Выбор локальных видео через `osascript` доступен для macOS и ограничен локальным loopback клиентом.
- Видео в очереди обрабатываются последовательно, чтобы не конкурировать за unified memory/GPU.
- «Остановить» — кооперативная отмена; она не уничтожает принудительно текущий вызов Whisper/Qwen/MLX.
- Процент загрузки файла измеряется фактически; во время инференса UI показывает состояние занятости, а не выдуманный прогресс модели.
- Выходные папки/видео и персональный `videocontext_settings.json` не должны попадать в Git.

## Проверка на реальном Mac

Ручной тест из лога пользователя (23 октября 2026): `/ui` и `/api/v1/ui-settings` отвечали `200 OK`, системный выбор файлов `/api/v1/select-local-videos` — `200 OK`, `/api/v1/analyze` — `200 OK`; Whisper `large` работал на MPS, Qwen3-VL-8B и Apple OCR завершились. Для одного видео в этом запуске записаны Whisper 46.13 s, VLM 140.31 s, OCR 5.65 s, total 210.44 s. Это пользовательский smoke test, не автоматический CI. На предоставленном фрагменте лога нет подтверждения завершения второго видео очереди и явного пути сохранённых TXT/JSON, поэтому эти две вещи следует проверять отдельно.

## Состав репозитория

- `patches/` — исходники миграционных патчеров.
- `macos/` — launcher `.app` и установочный shell script.
- `config/` — пример профиля Apple Silicon.
- `docs/APPLE_SILICON_BENCHMARKS.md` — исторические замеры и базовые рекомендации.
- `docs/PERFORMANCE_OPTIMIZATION_AND_MENUBAR.md` — выводы по реальному пяти-профильному прогону, быстрый VLM-профиль и исправление значка меню macOS.
- `docs/ANALYSIS_PROFILES.md` — режимы анализа, опциональный FFmpeg OCR и установка патчера.
- `install_patches_to_existing_checkout.command` — orchestration для свежего checkout upstream.

## Дополнительная настройка согласования ASR/OCR

Для локальной сборки, в которой **уже установлено расширение текстовой постобработки Qwen3.5**, можно добавить редактируемый промпт согласования названий:

    python3 patches/patch_vce_name_reconciliation_prompt.py --project "$HOME/VideoContext-Engine" --dry-run
    python3 patches/patch_vce_name_reconciliation_prompt.py --project "$HOME/VideoContext-Engine"

Патчер добавляет поле в «Параметры анализа», сохраняет значение в videocontext_settings.json и включает его в инструкцию текстовой постобработки. Он создаёт резервные копии, проверяет Python/JSON и останавливается без изменений, если не найдены ожидаемые точки подключения. Это **опциональный патч**: он не входит в последовательность установки свежего upstream v3.19, потому что стандартная upstream-версия не содержит указанного расширения Qwen3.5. После применения перезапустите приложение и сохраните промпт через UI.

## Attribution

Основой является [VideoContext Engine v3.19](https://github.com/dolphin-creator/VideoContext-Engine), автор upstream — `dolphin-creator`. Эта ветка содержит сторонний набор оптимизаций/интеграций для конкретного рабочего процесса. Перед публикацией исходного кода upstream изучите лицензию и условия оригинального репозитория. Этот репозиторий распространяет patch scripts и launcher templates, а не копию всего upstream source file.
