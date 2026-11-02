# Текущий статус проверок

Обновлено: 2026-11-02.

## Публикация и pull requests

- PR #15 `Publish full VideoContext Engine v3.19 runtime for Apple Silicon` объединён в `main` (squash), merge commit `549186bbf9c4653955cb8d8f4d1e967055e80b55`.
- PR #14 закрыт как superseded: полезные универсальные audit-инструменты, тесты и документация перенесены в `main`; исторические per-video output artifacts не импортировались.
- PR #13 закрыт как superseded: runtime thread-affinity/telemetry код уже присутствует в опубликованном engine; локальные observer/watchdog инструменты и тесты перенесены в `main`. Исторические сырые телеметрические дампы и app ZIP исключены.
- Открытых PR нет.

## Последняя полная проверка основной ветки

На коммите `f2ce946a00ab998500341c943ec59532de6d0cd1` успешно завершились все четыре релевантных workflow:

- [VCE quality benchmark tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41543631627) — success; включая Python syntax check и весь набор `test_vce_*.py`.
- [VCE unattended benchmark validation](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41543631606) — success.
- [FFmpeg OCR worker setting tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41543631613) — success.
- [Audio resilience V6 patch tests](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/actions/runs/41543631717) — success.

На предыдущем коммите `5dd86af118f336e929a92c33b60f317e2c431d73` также прошли speed profile/macOS menu bar tests. Шесть workflow PR #15 прошли до merge.

## Интерпретация MLX / Metal

Текущий runtime содержит выделенный MLX executor, wired_limit compatibility, worker trace и отдельные поля configured device/stream. Документация не считает Metal smoke test доказательством исполнения каждого VLM operator на GPU. Историческое расхождение `vlm.runtime.device` и GPU/stream telemetry сохранено как диагностическая оговорка; для строгого hardware acceptance нужен отдельный свежий прогон на конкретном Mac.

## Ограничения CI

GitHub Actions проверяет код, синтаксис и автоматизированные тесты. Он не заменяет end-to-end тест реального видео и конкретного Apple Silicon устройства, проверку фактического появления файлов на диске или подтверждение per-operator GPU placement.
