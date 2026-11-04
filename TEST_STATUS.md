# Текущий статус репозитория и проверок

Обновлено: 2026-11-04. Аудитный снимок зафиксирован на `main` commit `d7f0a83793340bde7010112e2f2ac16feeea7046`, до PR #30.

## Сводка репозитория на указанном checkpoint

- Репозиторий: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`.
- Default branch: `main`.
- Открытых PR: **0**.
- Закрытых PR: **29** — **22 merged**, **7 закрыты без merge**.
- Последний merged PR: [#29 — Finalize repository audit metadata after PR #28](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/29), merge commit `d7f0a83793340bde7010112e2f2ac16feeea7046`.
- Последний PR с изменениями runtime / приложения: [#27](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/27), merge commit `2276b939ce6f697f6f642453a4ba9ed698cba01e`.
- Последний PR с метаданными и CI до текущего аудита: [#29](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/29).
- Ветка `main` — единственная ветка, содержащая актуальную разработку.

### Обнаруженная устаревшая ветка

На момент снимка в GitHub оставалась ветка `chore/reconcile-audit-metadata-20261104`.

Сравнение с `main`:
- `ahead_by = 0`: у ветки нет уникальных коммитов;
- `behind_by = 1`: ветка отстаёт на один коммит;
- открытого PR с этой веткой нет.

Ветка безопасна для удаления. PR #30 расширяет cleanup workflow: он удаляет такие бесхозные topic refs только при `ahead_by = 0`, без открытого PR и при отсутствии защиты ветки. Default, protected, открытые и расходящиеся ветки сохраняются. После merge PR #30 cleanup workflow должен оставить только `main`.

## История pull requests

- PR #1–#2 и #5, #8–#11, #15–#29 — полезные изменения уже вошли в `main`.
- PR #3, #4 — ранние варианты нативного окна macOS; заменены текущим AppKit/WebKit приложением.
- PR #6 — устаревшая сводка таймингов из вывода Terminal; не выбрана как канонический benchmark-артефакт.
- PR #7 — сырые per-run/per-video benchmark payloads и настройки; намеренно не публикуются. В `main` оставлены обезличенные агрегаты.
- PR #12 — superseded исходной реализацией PR #13.
- PR #13 — закрыт после выборочного переноса пригодных observed-run/watchdog исходников, тестов и документации.
- PR #14 — закрыт после выборочного переноса structural audit и source-aware lexical grounding, тестов и документации.

Эти семь закрытых без merge PR — исторические решения, не готовые ветки для повторного слияния. Содержимое PR #13 и #14 перенесено выборочно; сырые пользовательские данные и telemetry не переносились.

## CI на checkpoint `d7f0a83`

| Проверка | Run | Состояние на момент аудита |
|---|---:|---|
| Full distribution installer validation | [41865224044](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41865224044) | distribution выполнялся; macOS Swift compile ожидал runner |
| Audio resilience V6 patch tests | [41865224056](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41865224056) | в очереди |
| FFmpeg OCR worker setting tests | [41865223887](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41865223887) | success |
| VCE quality benchmark tests | [41865224048](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41865224048) | success |
| Cleanup merged topic branches | [41865223909](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41865223909) | выполнялся; старое правило orphan-ветку не удаляет |

Предыдущая успешная компиляция AppKit/WebKit Swift-приложения: [run 41716538619](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538619) на кодовом checkpoint `2276b939ce6f697f6f642453a4ba9ed698cba01e`.

CI проверяет код, shell, JSON и контрактные тесты; он не заменяет end-to-end MLX/Metal запуск реального видео на физическом Apple Silicon Mac. После изменения workflow PR #30 добавляет contract-тесты правил удаления веток.

## Проверка дерева

Полное рекурсивное Git Tree на checkpoint `d7f0a83793340bde7010112e2f2ac16feeea7046`:
- 130 tracked files;
- 1,793,311 байт содержимого файлов;
- дерево не обрезано;
- пустых tracked-файлов нет;
- targeted scan не обнаружил `.DS_Store`, backup/log/tmp/swap, `__pycache__`, `.venv`, `node_modules`, `.env`, пользовательский `videocontext_settings.json`, `llm_outputs.json`, сырые per-video JSON и generated comparison reports.

В публичной поставке намеренно отсутствуют локальные видео/аудио, модели, виртуальные окружения, пользовательские настройки, credentials, логи, резервные копии и сырая telemetry. Два обезличенных aggregate benchmark metrics файла и сводка tuning оставлены намеренно.

## Правила работы с ветками

1. Все функциональные и maintenance-изменения проходят через отдельные PR в `main`.
2. Закрытые без merge PR остаются историей; не открывать их повторно без нового анализа полезных коммитов.
3. После merge cleanup workflow удаляет ветку merged PR.
4. Дополнительно он удаляет только бесхозные ветки с разрешёнными topic-префиксами, если их `ahead_by = 0`; open PR, protected, default и ветки с уникальными коммитами сохраняются.
5. Branch protection на `main` пока отключён. CI запускается, но не является обязательным merge gate; результат необходимо проверить вручную.

**Аудитный снимок выше относится ровно к commit `d7f0a83793340bde7010112e2f2ac16feeea7046`.** PR #30 меняет только политику очистки, audit-документы и тесты; после его merge live-состояние следует сверять со страницами [branches](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/branches) и [pull requests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pulls).
