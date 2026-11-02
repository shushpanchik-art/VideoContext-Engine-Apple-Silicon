# Локальный benchmark параметров VCE

`scripts/vce_unattended_benchmark.py` выполняет последовательный one-factor-at-a-time benchmark параметров локального VideoContext Engine. Для каждого запуска изменяется один параметр относительно активного профиля. До обработки проверяются локальный API и OpenAPI-схема; если обязательного поля нет, скрипт останавливается.

## Preflight без обработки

```bash
source .venv/bin/activate
python scripts/vce_unattended_benchmark.py \
  --video "$HOME/path/to/video.mp4" \
  --dry-run
```

## Полный запуск

```bash
python scripts/vce_unattended_benchmark.py \
  --video "$HOME/path/to/video.mp4"
```

Можно указать `--project PATH`, `--api-url URL` и `--results-dir PATH`. Папка результатов должна находиться вне каталога проекта. По умолчанию она создаётся под `~/Downloads/VCE_benchmark_runs/`.

Launcher для длительного прогона:

```bash
bash scripts/start_vce_benchmark.command "$HOME/path/to/video.mp4"
```

Он выполняет preflight, а затем запускает benchmark в фоне. Журнал: `~/Library/Logs/VideoContext Engine benchmark.log`.

## Приватность и настройки

- Видео явно указывается через обязательный `--video`; заранее заданного пользовательского пути в скрипте нет.
- Скрипт читает текущий `videocontext_settings.json`, но не изменяет его.
- Если длинные промпты превышают лимиты API, для запроса используется сокращённая версия только в памяти; локальные настройки остаются прежними.
- Полные JSON-ответы VCE, извлечённый текст, имя видео и контрольная сумма сохраняются только локально, вне Git checkout.
- GitHub CLI не нужен. Benchmark не клонирует репозиторий, не создаёт ветки и не отправляет результаты в GitHub.
- Не коммитьте каталог результатов: он может содержать пользовательский текст из видео.

Benchmark может занять длительное время и выполнять несколько полных анализов одного видео. Сначала всегда запускайте `--dry-run` и убедитесь, что VCE не обрабатывает другое задание.
