# Отдельное окно macOS и подтверждение выхода

Текущий web UI VCE остаётся локальным сайтом на http://127.0.0.1:7555/ui. Этот патч меняет способ отображения: вместо передачи URL Safari/браузеру launcher открывает собственный контейнер AppKit + WKWebView. Сервер по-прежнему работает отдельно в Terminal, поэтому логи и Ctrl+C остаются доступны.

В приложении сделано:
- собственное resizable-окно macOS без вкладок Safari/Chrome;
- обработка target="_blank" внутри текущего окна, а не в новой вкладке;
- меню приложения и сочетание ⌘Q;
- диалог «Закрыть VideoContext Engine?» при ⌘Q и закрытии последнего окна. Выбор «Не закрывать» отменяет выход;
- предупреждение, что сервер в Terminal может продолжить работу после закрытия UI.

## Установка в существующее приложение

1. Закрой окно VCE. Не нажимай Ctrl+C в Terminal, если там идёт анализ или сервер должен остаться запущенным.
2. Скачай скрипт установки и запусти:

   ~~~bash
   curl -fL \
     "https://raw.githubusercontent.com/shushpanchik-art/VideoContext-Engine-Apple-Silicon/feat/macos-native-window-confirm-quit/scripts/install_native_window_app.command" \
     -o "$HOME/Downloads/install_native_window_app.command"
   chmod +x "$HOME/Downloads/install_native_window_app.command"
   "$HOME/Downloads/install_native_window_app.command"
   ~~~

3. Скрипт собирает Swift-компонент локально системным swiftc, делает резервную копию изменяемых файлов в папке ~/Downloads/VideoContext-Engine-native-window-backup-<timestamp>/ и меняет только launcher/добавляет native webview helper внутри .app. Python-движок, виртуальное окружение, JSON-настройки и отчёты не меняются.
4. Запусти VideoContext Engine.app ещё раз.

Если приложение установлено не в /Applications, передай полный путь первым аргументом, например:

   ~~~bash
   "$HOME/Downloads/install_native_window_app.command" "$HOME/VideoContext-Engine/VideoContext Engine.app"
   ~~~

Если swiftc отсутствует, сначала установи Command Line Tools по подсказке xcode-select --install.

## Ограничения

- WKWebView показывает тот же UI и обращается к локальному API. Сам скрипт не встраивает Python-модели в приложение.
- Закрытие окна приложения не гарантирует остановку inference: сервер отдельно живёт в Terminal. Для остановки сервера используй Ctrl+C в окне Terminal, а для поддерживаемой остановки анализа — кнопку VCE.
- Изменение нужно проверить на целевом Mac: Swift-компиляция в CI проверяет код, но не заменяет ручной smoke-test поведения окна.
