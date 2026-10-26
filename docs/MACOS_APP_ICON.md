# Восстановление иконки VideoContext Engine для macOS

В старом каталоге UI использовался PNG-значок:

`~/Downloads/VideoContext_UI_Weighted_Progress_v4/favicon.png`

Самостоятельного файла `.icns` в шаблоне приложения не было, а `Info.plist` не ссылался на файл значка. Из-за этого macOS могла показывать стандартную или пустую иконку.

## Восстановить установленное приложение

Из корня checkout репозитория запусти:

```bash
bash scripts/install_vce_macos_icon.command
```

Скрипт проверяет наличие исходного PNG и установленного приложения, создаёт резервную копию `.app` в `~/Downloads/VideoContext-Engine-icon-backup-<timestamp>/`, создаёт набор размеров с помощью системных `sips` и `iconutil`, устанавливает `VideoContextEngine.icns`, прописывает `CFBundleIconFile`, обновляет подпись и регистрацию приложения в macOS. Python-движок, виртуальное окружение, пользовательские настройки и отчёты не изменяются.

Можно передать другие пути:

```bash
bash scripts/install_vce_macos_icon.command "/путь/к/favicon.png" "/Applications/VideoContext Engine.app"
```

Исходник найден в размере 64×64 px. Скрипт создаёт необходимые размеры для macOS увеличением этого PNG; значок останется узнаваемым, но при крупном отображении может быть менее резким, чем у исходного файла высокого разрешения.
