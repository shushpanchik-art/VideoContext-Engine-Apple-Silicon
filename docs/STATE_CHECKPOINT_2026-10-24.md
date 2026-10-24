# Снимок состояния VCE — 2026-10-24

Этот документ фиксирует состояние перед добавлением исследовательских выводов и нового плана работ. Это контрольная точка репозитория и пользовательского локального экземпляра; локальный файл движка не загружается в Git.

## GitHub

- Репозиторий: https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon
- Рабочая ветка: `fix/vce-audio-resilience-acceleration-v6`
- HEAD до этого контрольного коммита: `735caa857f7f0f336e9af8f9264e57baa80daae8`
- Pull request: [#1 — Add VCE audio resilience and CPU/OCR overlap (V6)](https://github.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/pull/1)
- Статус PR на момент снимка: открыт, не слит в `main`.
- На HEAD перед снимком успешно завершились CI-проверки `Audio resilience V6 patch tests` и `FFmpeg OCR worker setting tests`.

## Состав рабочей ветки

- `patches/patch_vce_audio_resilience_acceleration_v6.py` — патчер V6 для обнаружения отсутствующего аудио, безопасного пропуска распознавания при типичных ошибках декодирования, поиска бинарников macOS/Homebrew и фонового FFmpeg/Tesseract OCR.
- `tests/test_vce_audio_resilience_acceleration_v6.py` и workflow `.github/workflows/audio-resilience-v6-tests.yml`.
- `patches/patch_vce_ffmpeg_ocr_workers_setting_v1.py` — отдельный патчер, добавляющий `ffmpeg_ocr_workers` в defaults, диапазон проверки настроек и Form-default.
- `tests/test_vce_ffmpeg_ocr_workers_setting_v1.py` и workflow `.github/workflows/ffmpeg-ocr-workers-setting-tests.yml`.
- Предшествующие патчи профилей анализа, OCR, сохранения отчётов и согласования названий.

## Локальный экземпляр на Mac — по сообщённым логам

Путь проекта: `$HOME/VideoContext-Engine`.

Подтверждено пользователем:
- Патчер V6 применён; повторный запуск сообщил «Патч audio resilience + CPU/OCR overlap уже установлен; изменений нет».
- `python3 -m py_compile "$HOME/VideoContext-Engine/VideoContextEngine_v3.19.py"` завершился без сообщения об ошибке до исправления настроек workers и после него.
- Патчер `patch_vce_ffmpeg_ocr_workers_setting_v1.py` применён и создал резервную копию `VideoContextEngine_v3.19.py.before_ffmpeg_ocr_workers_20261024_165522.bak`.
- Сервер VideoContext Engine v3.19 запускался; UI отвечал `200 OK`.

Не подтверждено на момент снимка:
- Успешное сохранение настроек через `POST /api/v1/ui-settings` после добавления `ffmpeg_ocr_workers`. До локального hotfix был ответ `422 Unprocessable Content`; повторный POST после исправления не представлен.
- Полный успешный анализ файла без аудиодорожки после применения V6.
- Измеренный выигрыш скорости после включения фонового OCR.
- Сравнительная оценка качества Whisper/OCR и фактической полноты TXT/JSON на контрольном наборе.

## Правила следующих изменений

1. Не менять локальный рабочий движок напрямую через широкие/непроверенные замены.
2. Для нового патча обязательны синтетические fixture-тесты плюс проверки соответствия настоящей структуры v3.19; fixture-тесты не считать эквивалентом smoke-теста на Mac.
3. Не включать в итоговые отчёты неподтверждённые числа скорости или качества.
4. Сохранять исходные ASR/OCR-результаты и связывать исправления/выводы с временными метками и доказательствами.
