# Сравнение запусков VideoContext Engine

- Исходное видео: /Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4
- Создано: 2026-10-24T21:42:09-03:00
- Эталонная транскрипция не требуется; WER/CER не вычисляются.

| Профиль | Статус | VCE, с | HTTP, с | Whisper, с | VLM, с | Apple OCR, с | FFmpeg OCR, с | Qwen3.5, с | Сцен | Папка |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| baseline-current | ok | 512.44 | 512.878 | 30.5 | 354.1 | 7.5 | 35.6 | 90.7 | 14 | runs/baseline-current |

## baseline-current

- Результат: runs/baseline-current/report.txt
- Полный JSON: runs/baseline-current/result.json
- Настройки: runs/baseline-current/settings.json

| whisper-ocr-no-vlm | ok | 146.22 | 146.531 | 30.5 | — | 5.8 | 35.5 | 86.5 | 14 | runs/whisper-ocr-no-vlm |

## whisper-ocr-no-vlm

- Результат: runs/whisper-ocr-no-vlm/report.txt
- Полный JSON: runs/whisper-ocr-no-vlm/result.json
- Настройки: runs/whisper-ocr-no-vlm/settings.json

| whisper-medium | ok | 557.08 | 557.348 | 21.9 | 346.8 | 7.6 | 35.1 | 99.5 | 14 | runs/whisper-medium |

## whisper-medium

- Результат: runs/whisper-medium/report.txt
- Полный JSON: runs/whisper-medium/result.json
- Настройки: runs/whisper-medium/settings.json

| vlm-4b | ok | 378.17 | 378.428 | 30.8 | 227.5 | 7.6 | 35.6 | 89.5 | 14 | runs/vlm-4b |

## vlm-4b

- Результат: runs/vlm-4b/report.txt
- Полный JSON: runs/vlm-4b/result.json
- Настройки: runs/vlm-4b/settings.json

| ffmpeg-ocr-on | ok | 416.01 | 416.348 | 31.0 | 251.3 | 7.9 | 13.1 | 95.9 | 14 | runs/ffmpeg-ocr-on |

## ffmpeg-ocr-on

- Результат: runs/ffmpeg-ocr-on/report.txt
- Полный JSON: runs/ffmpeg-ocr-on/result.json
- Настройки: runs/ffmpeg-ocr-on/settings.json

