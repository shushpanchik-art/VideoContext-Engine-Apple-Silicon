# VCE Full Profile Benchmark

- Run: `20261105_143212_1850e4`
- Finished: 2026-11-05T14:38:09-03:00
- Engine: VideoContext Engine v3.19; repository distribution version at script publication: 0.2.5
- Machine: `arm64` / `macOS-27.0.1-arm64-arm-64bit`; unified memory: 32.0 GiB
- Input: `<INPUT_VIDEO>`; 17.41 MiB; duration: 108.233333 s
- Storage: local only; no video, transcript, raw response or telemetry is uploaded to GitHub.

## Сравнение прогонов

| Профиль | Статус | Wall, с | VCE, с | Сцен | Слов Whisper | VLM, с | Whisper, с | Apple OCR, с | FFmpeg OCR, с | Qwen3.5, с | CPU||GPU overlap, с | Ошибки |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Оптимальный базовый профиль | completed | 356.9 | 354.6 | 14 | 139 | 258.4 | 145.8 | 4.8 | 0.0 | 84.8 | 143.8 | 0 |

## Мониторинг CPU / RAM / GPU

| Профиль | CPU процесса, max % | RSS дерева, max MiB | GPU samples | GPU residency avg/max, %* |
|---|---:|---:|---:|---:|
| baseline | 0.0 | 0.0 | 0 | — |

*GPU residency собирается `powermetrics` на уровне всей системы и не может быть приписана конкретно PID движка. Если permission отсутствует, значение будет пустым.*

## Что реально выполняется на каком устройстве

| Этап | Путь исполнения по текущему коду | Ускорение / доказательство |
|---|---|---|
| Детекция сцен | CPU | OpenCV; stage timer `scene_detection_seconds` |
| Whisper | CPU | `mlx-whisper`, закреплённый на `mx.cpu`; MPS не используется для Whisper inference в текущем runtime, несмотря на подпись UI |
| Qwen3-VL | GPU target | MLX + Metal, явный `Device(gpu)` и GPU stream smoke test; это конфигурация/stream evidence, не доказательство каждого GPU operator |
| Apple Vision OCR | аппаратный backend не раскрыт | Apple Vision API; нельзя достоверно приписать CPU, GPU или ANE по текущей телеметрии |
| FFmpeg/Tesseract OCR | CPU | FFmpeg + Tesseract CLI и ограниченные CPU workers, если фильтр/бинарники доступны |
| Qwen3.5 text post-processing | GPU target | MLX worker/Metal; проверить runtime fields конкретного ответа, без per-operator attribution |
| Keyframe prefetch | CPU | bounded CPU decode/resize worker; метрики worker/wait seconds |
| Whisper + VLM overlap | CPU || GPU | runtime возвращает `whisper_vlm_overlap_seconds`; измеренное перекрытие пишется в таблицу этапов |

## Важные оговорки

- `enable_stage_timing` включён во всех прогонах как измерительный инструмент; сам по себе он не ускоряет распознавание.
- `enable_mlx_whisper` в текущем `VideoContextEngine_v3.19.py` вызывает preflight-проверку модели, но не меняет backend: `resolve_whisper_device()` возвращает CPU, а `load_whisper_model()` создаёт MLX CPU adapter и при выключенном флаге. Не трактуйте соответствующий A/B-прогон как сравнение MPS vs MLX.
- `whisper_acceleration` — поле настроек/UI; текущая реализация закрепляет Whisper на CPU stream. Очистка `torch.mps` cache в helper-функции не означает MPS inference.
- WER/CER не рассчитываются без размеченного эталонного текста. Число слов и сцен — только coverage proxies, не оценка точности.
- Эксперименты one-factor-at-a-time: адаптивные кадры с верхним пределом 2; OCR cache; MLX Whisper validation flag; fast scene detection (1 fps); в конце combined all-on.
- VCE может возвращать частичный результат и предупреждения; изучите `errors.log`, `server_log_delta.log`, `response.json` каждого профиля.

## Файлы

- `run_summary.csv` — сводная таблица.
- `stage_comparison.csv` — этапы, секунды, CPU/GPU class и backend evidence.
- `device_monitoring.csv` — CPU/RSS и системная GPU телеметрия.
- `errors.csv` — ошибки/предупреждения по каждому прогону.
- Подпапки каждого прогона содержат полный JSON ответ и приватную телеметрию.
