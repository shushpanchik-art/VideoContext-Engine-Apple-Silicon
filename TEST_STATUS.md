# Текущий статус проверок

Обновлено: 2026-11-04. Состояние сверено с GitHub.

## Состояние репозитория

- Репозиторий: `shushpanchik-art/VideoContext-Engine-Apple-Silicon`.
- Основная ветка: `main`.
- Текущий HEAD: `2276b939ce6f697f6f642453a4ba9ed698cba01e` — merge PR #27 от 2026-11-03.
- Ветка GitHub: только `main`; открытых PR нет.
- В истории: 27 закрытых PR, из них 20 merged и 7 закрытых без слияния (#3, #4, #6, #7, #12, #13, #14). Эти исторические PR не переоткрываются и не сливаются автоматически; их записи сохранены как аудит истории.
- PR #13 и #14 закрыты как superseded после выборочного переноса пригодных исходников и тестов.
- Ранее удалённые 14 устаревших research/feature-веток зафиксированы в истории. Для последующих слияний действует постоянный workflow `.github/workflows/cleanup-merged-topic-branches.yml`: он удаляет только ветки merged PR из этого же репозитория, не затрагивая default branch и ветки открытых PR.
- Последний запуск очистки веток — [run 41716538573](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538573), success на текущем HEAD.
- Защита `main` через branch protection пока не включена; GitHub Actions запускаются при push, но обязательные проверки для прямого push/merge не настроены.

## CI на текущем HEAD

Все шесть push-workflow на коммите `2276b939ce6f697f6f642453a4ba9ed698cba01e` завершились успешно:

| Workflow | Результат |
|---|---|
| [Full distribution installer validation](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538619) | success — компиляция AppKit/WebKit Swift-приложения на macOS, shell syntax, JSON и installer contract tests |
| [Audio resilience V6 patch tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538540) | success |
| [FFmpeg OCR worker setting tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538534) | success |
| [VCE speed profile and macOS menu bar tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538552) | success |
| [VCE quality benchmark tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538538) | success |
| [Cleanup merged topic branches](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41716538573) | success |

GitHub Actions подтверждает сборку Swift и автоматические проверки. Это не заменяет end-to-end тест реального видео и MLX/Metal inference на физическом Apple Silicon Mac; свежий hardware acceptance run здесь не заявляется.

## Публичные benchmark-данные и приватность

- Два файла в `artifacts/text-llm-benchmarks/` сохранены: это агрегированные timing/quality metrics, а не полные исходные видео или per-video VCE payloads.
- В обоих сохранённых манифестах `video_identity` задан как `redacted_local_sample`, `video_sha256` равен null, а `raw_video_and_vce_scene_payload_uploaded` — false.
- Полные per-video VCE JSON, сырые Text LLM outputs, локальные comparison reports, пользовательские видео/аудио, telemetry dumps, логи, резервные копии и локальные настройки в публикацию не входят.
- Агрегированные результаты оставлены для воспроизводимости и документированы в `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md`.

## Ключевые изменения, уже слитые в main

- PR #22 и #26 — обработка Metal command-buffer timeout отдельно от allocation/OOM; прекращение повторных VLM попыток после сбоя очереди.
- PR #24 — обновление управляемого app patcher даже когда локальные изменения пропускают `git pull`.
- PR #25 — корректный Homebrew PATH для GUI-запуска и ранняя диагностика `ffmpeg`/`ffprobe`.
- PR #27 — управляемый жизненный цикл native app и Python engine-процессов при запуске и закрытии приложения.
- PR #18 — постоянная автоматическая очистка веток после слияния PR.
