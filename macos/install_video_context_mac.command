#!/bin/bash
set -Eeuo pipefail
PROJECT_DIR="$HOME/Downloads/VideoContext-Engine"
APP_SOURCE="$PROJECT_DIR/VideoContext Engine.app"
APP_TARGET="/Applications/VideoContext Engine.app"
DESKTOP_ALIAS="$HOME/Desktop/VideoContext Engine"
DESKTOP_LINK="$HOME/Desktop/VideoContext Engine.app"

fail() { echo "ОШИБКА: $*"; echo "Проверьте сообщение выше. Нажмите Enter для закрытия."; read -r || true; exit 1; }

[[ -d "$PROJECT_DIR" ]] || fail "Не найдена папка проекта $PROJECT_DIR"
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || fail "Не найден .venv/bin/python. Сначала восстановите виртуальное окружение проекта."
[[ -f "$PROJECT_DIR/VideoContextEngine_v3.19.py" ]] || fail "Не найден VideoContextEngine_v3.19.py в папке проекта."
[[ -f "$PROJECT_DIR/videocontext_web_ui.html" ]] || fail "Не найден videocontext_web_ui.html. Сначала установите пакет пакетного интерфейса."
[[ -f "$PROJECT_DIR/patch_vce_stop_and_mps.py" ]] || fail "Не найден patch_vce_stop_and_mps.py. Распакуйте полный ZIP в папку проекта."
[[ -d "$APP_SOURCE/Contents" ]] || fail "Не найден комплект VideoContext Engine.app внутри папки проекта."

printf '\n[1/3] Исправление Whisper/MPS и добавление кнопки остановки…\n'
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/patch_vce_stop_and_mps.py" || fail "Патч не прошёл проверку. Приложение не устанавливалось."

printf '\n[2/3] Установка приложения в /Applications…\n'
STAMP="$(date +%Y%m%d_%H%M%S)"
if [[ -e "$APP_TARGET" ]]; then
  echo "Старая копия найдена; сохраняю её как: ${APP_TARGET}.backup.${STAMP}"
  sudo /bin/mv "$APP_TARGET" "${APP_TARGET}.backup.${STAMP}" || fail "Не удалось сохранить старую копию приложения."
fi
sudo /bin/mkdir -p /Applications || fail "Нет разрешения создать /Applications."
sudo /usr/bin/ditto "$APP_SOURCE" "$APP_TARGET" || fail "Не удалось скопировать приложение в /Applications."
sudo /bin/chmod 755 "$APP_TARGET/Contents/MacOS/VideoContextLauncher" "$APP_TARGET/Contents/Resources/start_server.command" || fail "Не удалось выставить права запуска."

printf '\n[3/3] Создание ярлыка на рабочем столе…\n'
# Remove an older alias or symlink with this name only; do not touch the project.
if [[ -e "$DESKTOP_ALIAS" || -L "$DESKTOP_ALIAS" ]]; then
  /bin/rm -rf "$DESKTOP_ALIAS"
fi
if [[ -L "$DESKTOP_LINK" ]]; then
  /bin/rm -f "$DESKTOP_LINK"
fi
if ! /usr/bin/osascript <<'APPLESCRIPT'
set homePath to system attribute "HOME"
set appAlias to (POSIX file "/Applications/VideoContext Engine.app") as alias
set desktopFolder to (POSIX file (homePath & "/Desktop")) as alias
tell application "Finder"
    make new alias file at desktopFolder to appAlias with properties {name:"VideoContext Engine"}
end tell
APPLESCRIPT
then
  echo "Finder не смог создать системный alias; создаю функциональную ссылку на приложение вместо него."
  /bin/ln -s "/Applications/VideoContext Engine.app" "$DESKTOP_LINK"
fi

printf '\nГОТОВО.\nПриложение: /Applications/VideoContext Engine.app\nЯрлык: рабочий стол → VideoContext Engine\nИнтерфейс: http://127.0.0.1:7555/ui\nSwagger: http://127.0.0.1:7555/docs\n\nЗакройте старый сервер в Terminal сочетанием Ctrl+C, затем запускайте через ярлык на рабочем столе.\nНажмите Enter для закрытия этого установщика.\n'
read -r || true
