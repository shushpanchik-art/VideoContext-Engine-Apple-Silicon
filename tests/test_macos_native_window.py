from __future__ import annotations

import plistlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "macos" / "VideoContext Engine.app" / "Contents"
SWIFT = ROOT / "macos" / "VideoContextWebView.swift"
LAUNCHER = ROOT / "macos" / "VideoContextLauncher.sh"
INSTALLER = ROOT / "scripts" / "install_native_window_app.command"


class NativeWindowAppTests(unittest.TestCase):
    def test_app_bundle_keeps_shell_bootstrap_as_main_executable(self):
        payload = plistlib.loads((APP / "Info.plist").read_bytes())
        self.assertEqual(payload["CFBundleExecutable"], "VideoContextLauncher")
        self.assertEqual(payload["CFBundlePackageType"], "APPL")

    def test_swift_wrapper_uses_wkwebview_and_quit_confirmation(self):
        source = SWIFT.read_text(encoding="utf-8")
        for token in (
            "import AppKit",
            "import WebKit",
            "WKWebView(",
            "http://127.0.0.1:7555/ui",
            "applicationShouldTerminate(",
            "Закрыть VideoContext Engine?",
            "Не закрывать",
            'keyEquivalent: "q"',
            "NSAlert()",
        ):
            self.assertIn(token, source)
        self.assertIn("applicationShouldTerminateAfterLastWindowClosed", source)
        self.assertIn("windowShouldClose", source)
        self.assertIn("skipNextTerminationConfirmation", source)
        self.assertIn("createWebViewWith", source)

    def test_launcher_execs_native_window_instead_of_opening_browser(self):
        source = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('WEBVIEW_BIN="$APP_CONTENTS/MacOS/VideoContextWebView"', source)
        self.assertIn('exec "$WEBVIEW_BIN"', source)
        self.assertIn('tell application "Terminal"', source)
        self.assertNotIn('/usr/bin/open "$UI_URL"', source)

    def test_installer_compiles_and_backs_up_without_touching_project_engine(self):
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("/usr/bin/swiftc -parse-as-library -O -framework AppKit -framework WebKit", source)
        self.assertIn("VideoContext-Engine-native-window-backup", source)
        self.assertIn("VideoContextLauncher", source)
        self.assertNotIn("VideoContextEngine_v3.19.py", source)
        self.assertIn("Contents/Resources/VideoContextWebView.swift", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
