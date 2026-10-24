import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "macos" / "VideoContextWindowApp.swift"
INSTALLER = ROOT / "macos" / "install_native_window_app.command"


class NativeWindowLauncherTests(unittest.TestCase):
    def test_native_webview_app_has_quit_confirmation(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("import WebKit", source)
        self.assertIn("WKWebView", source)
        self.assertIn('keyEquivalent: "q"', source)
        self.assertIn("func applicationShouldTerminate(", source)
        self.assertIn("Точно закрыть VideoContext Engine?", source)
        self.assertIn("Закрыть приложение", source)
        self.assertIn("func windowShouldClose(", source)

    def test_server_process_is_owned_only_when_app_launches_it(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("if await serverResponds()", source)
        self.assertIn("ownsServer = true", source)
        self.assertIn("guard ownsServer, let process = serverProcess, process.isRunning else { return }", source)
        self.assertIn("Library/Logs/VideoContext Engine", source)


    def test_installer_preserves_icon_from_current_app_or_backup(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("find_icon_in_app", installer)
        self.assertIn("CFBundleIconFile", installer)
        self.assertIn("VideoContext Engine.app.backup.*", installer)
        self.assertIn("Sохраняю иконку предыдущей версии", installer)

    def test_installer_compiles_before_replacing_installed_app_and_backs_it_up(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        compile_pos = installer.index("xcrun swiftc")
        backup_pos = installer.index('sudo /bin/mv "$APP_TARGET"')
        install_pos = installer.index('sudo /usr/bin/ditto "$STAGING_APP" "$APP_TARGET"')
        self.assertLess(compile_pos, backup_pos)
        self.assertLess(backup_pos, install_pos)
        self.assertIn("codesign --verify", installer)


if __name__ == "__main__":
    unittest.main()
