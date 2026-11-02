# Текущий статус проверок

Обновлено: 2026-11-02.

## Итоговое состояние GitHub

- Основная ветка: `main`.
- Единственная оставшаяся ветка: `main`; все 14 устаревших feature/research/benchmark веток удалены.
- Открытых Pull Requests нет.
- PR #15 объединён в `main` методом squash; merge commit: `549186bbf9c4653955cb8d8f4d1e967055e80b55`.
- PR #13 и #14 закрыты как заменённые выборочным переносом полезных исходников и тестов в `main`; per-video результаты и сырые дампы не переносились.
- Одноразовый cleanup workflow удалён после удаления веток.
- На последнем проверенном дереве нет пустых отслеживаемых файлов.

## Последние CI-проверки на финальном cleanup-коммите `7eab34491c7ef2694fd0a0fbbed647f3b842f1ab`

Все три workflow завершились успешно:

- [VCE quality benchmark tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41543974375) — success; syntax checks и все `test_vce_*.py`.
- [FFmpeg OCR worker setting tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41543974485) — success.
- [Audio resilience V6 patch tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41543974958) — success.

Также на предыдущих cleanup-коммитах успешно прошли VCE unattended benchmark validation и speed profile/macOS menu bar tests. Шесть проверок PR #15 прошли до merge.

## Публичные benchmark-данные

- Полные per-video VCE API JSON и манифесты с именами/хешами видео удалены из текущего дерева.
- Сырые `llm_outputs.json` удалены; оставлены только агрегированные метрики с обезличенным идентификатором локального sample.
- Локальный `comparison.md` с абсолютным домашним путём удалён.
- Сводка параметрического бенчмарка сохранена в `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md`; в ней нет исходного имени видео или полного ответа API.
- `.gitignore` исключает `results/vce-benchmark/` и `artifacts/text-llm-benchmarks/*/llm_outputs.json`.

## MLX / Metal — границы подтверждения

В опубликованном runtime присутствуют выделенный MLX executor, wired_limit compatibility, worker trace и раздельные поля configured device/stream. Исторические прогоны показывали расхождение `vlm.runtime.device` с GPU/stream telemetry. Metal smoke test сам по себе не доказывает выполнение каждого VLM operator на GPU. Этот предел доказательств явно указан в [диагностической инструкции](MLX_METAL_DIAGNOSTICS.md); отдельный свежий hardware acceptance run на Mac в рамках данной GitHub-ревизии не заявляется.

## Приватность публикации

- Бенчмарк требует явного `--video` или `--input-json`.
- Отправка в GitHub выключена по умолчанию; `--upload` и дополнительный `--upload-outputs` требуют явного выбора.
- Watchdog требует явный путь к видео.
- Личные видео, полные ответы, локальные отчёты и сырая process/memory telemetry не включены в публичные артефакты.

GitHub Actions проверяет код и автоматизированные тесты. Он не заменяет отдельный end-to-end прогон реального видео на конкретном Mac.
