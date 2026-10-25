from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "macos" / "VideoContext Engine.app" / "Contents" / "Resources" / "VideoContextStatusBar.swift"
REPAIR = ROOT / "macos" / "repair_installed_native_app.command"
INSTALLER = ROOT / "macos" / "install_video_context_mac.command"


class NativeAppServerLifecycleTests(unittest.TestCase):
    def test_native_app_launches_server_without_terminal(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("launchServerInBackground", source)
        self.assertIn('URL(fileURLWithPath: "/bin/bash")', source)
        self.assertIn("process.standardOutput = logHandle", source)
        self.assertIn("process.standardError = logHandle", source)
        self.assertIn('appendingPathComponent("server.log")', source)
        self.assertNotIn("launchServerInTerminal", source)
        self.assertNotIn('tell application "Terminal"', source)

    def test_reopening_native_window_preserves_live_ui_state(self):
        source = SWIFT.read_text(encoding="utf-8")
        self.assertIn("private func showMainWindow(url: URL, forceReload: Bool = false)", source)
        self.assertIn("let alreadyShowingRequestedPage", source)
        self.assertIn("if forceReload || !alreadyShowingRequestedPage", source)
        # Reopening the same page must not blindly reload WKWebView and erase
        # the in-memory queue/progress display. Fresh server startup can reload.
        self.assertIn("self.showMainWindow(url: self.uiURL, forceReload: true)", source)

    def test_repair_script_preserves_active_analysis_and_settings(self):
        source = REPAIR.read_text(encoding="utf-8")
        self.assertIn('lsof -nP -iTCP:7555 -sTCP:LISTEN', source)
        self.assertIn("Введи INSTALL", source)
        self.assertIn("Настройки VCE, выбор TXT/JSON и результаты анализа не меняются.", source)
        self.assertIn("-framework AppKit -framework WebKit", source)
        self.assertIn("codesign --force --deep --sign -", source)

    def test_full_installer_builds_webkit_app_from_repo_bundle(self):
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('APP_SOURCE="$REPO_ROOT/macos/VideoContext Engine.app"', source)
        self.assertIn("-framework AppKit -framework WebKit", source)
        self.assertIn("без отдельного окна Terminal", source)


if __name__ == "__main__":
    unittest.main()
