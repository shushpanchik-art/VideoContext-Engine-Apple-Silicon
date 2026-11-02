# Текущий статус проверок

Обновлено: 2026-11-02.

## Публикация

- PR #15 `Publish full VideoContext Engine v3.19 runtime for Apple Silicon` объединён в `main`.
- Метод объединения: squash.
- Merge commit: `549186bbf9c4653955cb8d8f4d1e967055e80b55`.

## GitHub Actions перед объединением

Все шесть проверок прошли успешно на HEAD PR `6ea3cb8681dae56c9d4ad9f36d4f1677fcfa804c`:

- URL batch ingestion.
- FFmpeg OCR worker setting.
- Audio resilience V6.
- VCE quality benchmark.
- VCE optimization checkboxes.
- VCE speed profile and macOS menu bar.

## Проверки после объединения

На merge commit `549186bbf9c4653955cb8d8f4d1e967055e80b55` прошли post-merge workflow: speed profile/macOS menu bar, FFmpeg OCR workers, audio resilience V6, VCE quality benchmark и Graph Update dependency workflow.

## Что не покрывается GitHub Actions

CI проверяет код и отдельные патчи в автоматизированной среде. Он не заменяет полный end-to-end прогон реального локального видео на конкретном Mac, проверку загрузки выбранных пользователем URL, фактическое сохранение TXT/JSON на диск или проверку поведения GPU/MLX на конкретной конфигурации Apple Silicon. Такие результаты нужно фиксировать отдельно от CI.
