# Текущий статус проверок

Обновлено: 2026-11-04. Сверено с GitHub API и полным деревом `main`.

## Снимок состояния репозитория

Снимок сделан на `main` до открытия заключительного metadata PR #29.

- Репозиторий: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`.
- Основная и единственная ветка: `main`.
- Проверенный HEAD на момент аудита: `f71c7efb0b6d466eda315c1182061bab18dd7f3d`.
- Последний PR с изменениями runtime / приложения: [#27](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/27), merge commit `2276b939ce6f697f6f642453a4ba9ed698cba01e`.
- Последний слитый PR до этого аудита: [#28](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/28), metadata/CI reconciliation, merge commit `f71c7efb0b6d466eda315c1182061bab18dd7f3d`.
- Открытых PR на момент снимка: 0.
- Закрытых PR: 28 — 21 merged и 7 закрытых без merge (#3, #4, #6, #7, #12, #13, #14). Исторические закрытые PR не переоткрываются автоматически: #13/#14 superseded после выборочного переноса пригодных исходников и тестов; остальные не представлены как готовые изменения для main.
- Удалённых исторических superseded веток: 14. После PR #28 на GitHub осталась только `main`.
- Постоянная очистка merged PR branches: [workflow](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/blob/main/.github/workflows/cleanup-merged-topic-branches.yml), последний run [41860685874](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41860685874) — success на HEAD `f71c7ef`.

> Число PR и SHA выше — зафиксированный снимок перед PR #29. После слияния самого PR #29 счётчики изменятся; актуальные live-значения следует смотреть на страницах [branches](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/branches) и [pull requests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pulls).

## CI на проверенном HEAD f71c7ef

- [Full distribution installer validation — run 41860686648](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41860686648): distribution job завершился успешно — shell syntax, example JSON, publication manifest JSON и installer contract tests. macOS `native-swift-compile` job оставался в очереди на момент снимка.
- [Audio resilience V6 patch tests — run 41860685802](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41860685802): success.
- [FFmpeg OCR worker setting tests — run 41860685871](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41860685871): success.
- [VCE quality benchmark tests — run 41860685856](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41860685856): success.
- [Cleanup merged topic branches — run 41860685874](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41860685874): success.
- Предыдущая полная distribution-проверка на кодовом checkpoint `2276b939ce6f697f6f642453a4ba9ed698cba01e` включала успешную компиляцию AppKit/WebKit Swift-приложения: [run 41716538619](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538619).

GitHub Actions проверяет исходники и автоматизированные тесты. Это не заменяет end-to-end запуск реального видео и MLX/Metal inference на физическом Apple Silicon Mac; нового hardware acceptance run в этом аудите нет.

## Гигиена дерева

Полное рекурсивное дерево commit `f71c7ef` проверено через GitHub Git Trees API:

- 130 файлов, общий размер 1,786,005 байт; дерево не обрезано.
- Пустых tracked-файлов нет.
- Целевой поиск не обнаружил `.DS_Store`, backup-файлов, логов, временных файлов, swap-файлов, `__pycache__`, `.venv`, `node_modules`, `.env`, пользовательского `videocontext_settings.json`, сырых `llm_outputs.json` или per-video comparison/raw JSON артефактов.
- Ветка не содержит пользовательских видео/аудио, моделей, локальной виртуальной среды и локальной телеметрии; два обезличенных aggregate benchmark metrics файла оставлены намеренно.

## История уборки и текущая политика

- PR #18 добавил постоянный workflow очистки: удаляются только ветки merged PR из этого репозитория; default branch и ветки открытых PR сохраняются.
- Одноразовая очистка 14 исторических superseded branches была выполнена ранее; постоянный workflow заменил временный механизм.
- PR #22/#26 разделили Metal command-buffer timeout и allocation/OOM и прекратили повторные VLM попытки после зависшей Metal queue.
- PR #24 обновил app patcher независимо от пропуска `git pull` из-за локальных изменений.
- PR #25 исправил Homebrew PATH для GUI-запуска и проверки `ffmpeg`/`ffprobe`.
- PR #27 синхронизировал жизненный цикл native app и проектного Python engine.
- PR #28 сверил публикационный манифест, changelog и CI; заключительный аудит исправляет оставшиеся post-merge счётчики и checkpoint.

Защита `main` через branch protection не включена (`protected=false`); CI workflows запускаются, но не являются обязательными merge checks. Слияния следует выполнять через PR и проверять CI вручную.

## Закрытые PR без merge: итоговое решение

Все семь закрытых без merge PR просмотрены по их GitHub описаниям и составу файлов. Это история принятых решений, а не очередь на слияние:

| PR | Почему не мержим повторно |
|---|---|
| #3, #4 | Два ранних варианта standalone macOS window. Они уступили место текущему управляемому AppKit/WebKit приложению и lifecycle-реализации PR #27. |
| #6 | Только timing-summary, восстановленный из вывода Terminal, без исходных benchmark/API результатов. В main оставлены проверяемые обезличенные агрегаты и профильная документация. |
| #7 | Полный набор локальных per-run/per-video отчётов и настроек. Такие сырые материалы намеренно не публикуются; оставлены обезличенные aggregate metrics. |
| #12 | Сам PR помечен superseded: исправленная source-compatible реализация продолжилась в PR #13. Старую ветку не восстанавливаем. |
| #13 | Закрыт после выборочного переноса пригодных observed-run/watchdog исходников, тестов и документации. В main присутствуют `scripts/vce_observed_run.py`, `scripts/vce_runtime_watchdog.py` и соответствующие tests/docs; установочные snapshots и сырая telemetry не переносились. |
| #14 | Закрыт после выборочного переноса универсального structural audit и source-aware lexical grounding. В main присутствуют инструменты, тесты и документация; per-video Text LLM output bundles исключены. |

Следовательно, ни один из этих PR не является готовой незакрытой веткой, которую нужно повторно мержить. Удалённых refs для них нет среди текущих веток GitHub; единственная рабочая ветка — `main`.
