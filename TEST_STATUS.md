# Текущий статус проверок

Обновлено: 2026-11-02.

## Итоговое состояние GitHub

- Основная ветка: `main`.
- Единственная оставшаяся ветка: `main`; все 14 устаревших feature/research/benchmark веток удалены.
- Открытых Pull Requests нет.
- PR #15 объединён в `main` методом squash; merge commit: `549186bbf9c4653955cb8d8f4d1e967055e80b55`.
- PR #13 и #14 закрыты как заменённые выборочным переносом полезных исходников и тестов.
- Одноразовый cleanup workflow удалён.
- В финальном дереве нет пустых отслеживаемых файлов, резервных копий, локальных логов, пользовательских .env/cookie-файлов или полных per-video JSON outputs.

## CI на последнем коммите с изменениями кода

Все пять профильных workflow прошли успешно на `eec9fbaf44fcd6156630c55b302f2cf803776a28`:

- [VCE quality benchmark tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41544862011) — success; syntax checks и все `test_vce_*.py`.
- [VCE unattended benchmark validation](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41544861980) — success.
- [VCE speed profile and macOS menu bar tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41544861953) — success, включая компиляцию нативной menu-bar оболочки.
- [FFmpeg OCR worker setting tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41544861963) — success.
- [Audio resilience V6 patch tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41544861958) — success.

После заключительной очистки per-video артефактов все применимые workflow повторно прошли на `1f742ea72951bb171d198cbd9f90f3da6151557d`, а затем ещё раз прошли на документально-метаданном checkpoint `2f23a6b8cdc349f5a6dcb1c6e2c52c8ba82a0f42`:

- [VCE quality benchmark tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41545414261) — success.
- [FFmpeg OCR worker setting tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41545414089) — success.
- [Audio resilience V6 patch tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41545414115) — success.

## Публичные benchmark-данные

- Полные per-video VCE API JSON, манифесты с именами/хешами видео, сырые `llm_outputs.json` и локальный `comparison.md` удалены из текущего дерева.
- Оставлены только агрегированные timing/quality metrics с обезличенным идентификатором локального sample.
- Анонимизированная параметрическая сводка: `docs/VCE_PARAMETER_BENCHMARK_2026-10-25.md`.
- `.gitignore` исключает полные per-video VCE результаты и сырые LLM outputs.
- Benchmark runner требует явный путь к видео, выполняет dry-run и хранит результаты вне проекта; GitHub upload отсутствует.

## MLX / Metal — границы подтверждения

В опубликованном runtime присутствуют выделенный MLX executor, wired_limit compatibility, worker trace и раздельные поля configured device/stream. Исторические прогоны показывали расхождение `vlm.runtime.device` с GPU/stream telemetry. Metal smoke test сам по себе не доказывает выполнение каждого VLM operator на GPU. Этот предел доказательств явно указан в [диагностической инструкции](MLX_METAL_DIAGNOSTICS.md); отдельный свежий hardware acceptance run на Mac в рамках данной GitHub-ревизии не заявляется.

GitHub Actions проверяет код и автоматизированные тесты. Он не заменяет end-to-end прогон реального видео на конкретном Mac.

Текущий проверенный checkpoint до этой записи статуса: `2f23a6b8cdc349f5a6dcb1c6e2c52c8ba82a0f42`.
