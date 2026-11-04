# Текущий статус репозитория и проверок

Обновлено: 2026-11-04. Состояние сверено с GitHub после слияния PR #30 и выполнения cleanup workflow.

## Текущий подтверждённый checkpoint

- Репозиторий: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`.
- Default branch: `main`.
- Проверенный commit: `ee132eeea4661b61980bfffe5cf232ac999b1a7d` (merge PR #30).
- Открытых PR: **0**.
- Открытых issues: **0**.
- Закрытых PR: **30** — **23 merged**, **7 closed without merge** (#3, #4, #6, #7, #12, #13, #14).
- Ветки GitHub после cleanup: **только `main`**.
- Последний merged PR: [#30 — Reconcile final repository audit and safely remove orphan topic branches](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/30).
- Последний runtime/application PR: [#27](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/27).

## Что изменилось в PR #30

- Публикационный манифест, статус тестов и commit info reconciled с audit-снимком.
- Cleanup workflow теперь удаляет merged PR branch, только если текущий tip совпадает с SHA коммита, который был слит. Это защищает ветки, которые повторно использовали после merge.
- Отдельно очищаются только orphan topic refs с разрешёнными префиксами и строго при `ahead_by = 0`.
- Сохраняются default branch, protected refs, открытые PR, non-topic ветки и все ветки с уникальными коммитами впереди `main`.
- Добавлены регрессионные тесты правил удаления веток и проверка синтаксиса JS cleanup script.
- Удалена дублирующая запись пути из CI path filter.

## Финальная уборка веток

На checkpoint до PR #30 была обнаружена ветка `chore/reconcile-audit-metadata-20261104`:
- `ahead_by = 0`;
- `behind_by = 1`;
- открытого PR не было.

После merge PR #30 cleanup workflow успешно выполнил run [41868806204](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41868806204). Он удалил:
- ветку PR #30 `chore/final-repository-cleanup-2026-11-04`;
- устаревшую `chore/reconcile-audit-metadata-20261104`.

После выполнения workflow GitHub branch inventory показывает только `main`.

## CI на checkpoint `ee132ee`

| Проверка | Run | Результат |
|---|---:|---|
| Full distribution installer validation — distribution job | [41868805849](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41868805849) | success |
| Audio resilience V6 patch tests | [41868805610](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41868805610) | success |
| FFmpeg OCR worker setting tests | [41868805636](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41868805636) | success |
| VCE quality benchmark tests | [41868805402](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41868805402) | success |
| Cleanup merged and fully integrated topic branches | [41868806204](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41868806204) | success |

Отдельная macOS job `native-swift-compile` в Full distribution run оставалась в очереди на момент этого аудита. Последняя успешная компиляция AppKit/WebKit Swift-приложения: [run 41716538619](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538619) на кодовом checkpoint `2276b939ce6f697f6f642453a4ba9ed698cba01e`. PR #30 не меняет Swift-исходники.

Успешные GitHub Actions не заменяют end-to-end прогон реального видео и MLX/Metal inference на физическом Apple Silicon Mac; нового hardware acceptance run в рамках этой уборки не проводилось.

## Исторические закрытые без merge PR

Эти PR оставлены в истории GitHub и не переоткрывались:
- #3 и #4 — ранние варианты macOS standalone window, заменены текущим AppKit/WebKit приложением.
- #6 — устаревшая timing-summary без первичных benchmark-данных.
- #7 — raw per-run/per-video benchmark outputs и settings; сырые payloads намеренно не публикуются.
- #12 — superseded PR #13.
- #13 и #14 — полезные исходники, тесты и документация были перенесены выборочно; сырые пользовательские outputs/telemetry исключены.

Итого: все нужные изменения, которые были признаны пригодными, уже интегрированы в `main`; эти семь PR — исторические закрытые решения, не незакрытая очередь на merge.

## Гигиена дерева

Рекурсивный Git Tree на checkpoint `ee132eeea4661b61980bfffe5cf232ac999b1a7d`:
- **131 tracked file**;
- **1,799,161 байт** содержимого файлов;
- дерево не обрезано;
- пустых tracked-файлов нет;
- targeted scan не обнаружил `.DS_Store`, backup/log/tmp/swap, `__pycache__`, `.venv`, `node_modules`, `.env`, пользовательский `videocontext_settings.json`, `llm_outputs.json`, сырые per-video JSON и generated comparison reports.

ML-модели, virtualenv, пользовательские настройки, локальные видео/аудио, credentials, логи, backups, per-video payloads и сырая telemetry не входят в публичный репозиторий. Обезличенные агрегированные benchmark-метрики оставлены намеренно.

## Правила поддержания порядка

1. Все функциональные и maintenance-изменения проходят отдельными PR в `main`.
2. Закрытые без merge PR остаются историей; повторно их не открывать без отдельного анализа.
3. Merged topic branch удаляется после merge.
4. Orphan topic branch удаляется только если у него нет уникальных коммитов впереди `main` (`ahead_by = 0`).
5. Ветка с изменившимся tip после старого merge, открытым PR, защитой или уникальными коммитами сохраняется.
6. Branch protection для `main` отключён; CI запускается, но не является обязательным merge gate. Результаты CI проверяются вручную.

**Снимок относится ровно к commit `ee132eeea4661b61980bfffe5cf232ac999b1a7d`.** Следующий PR создаст новый checkpoint; это не меняет корректность истории на указанном commit.
