from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_vce_macos_icon.command"
DOC = ROOT / "docs" / "MACOS_APP_ICON.md"


class VceMacOSIconRecoveryTests(unittest.TestCase):
    def test_icon_installer_has_backup_and_bundle_icon_configuration(self):
        script = INSTALLER.read_text(encoding="utf-8")
        for required in (
            "VideoContext_UI_Weighted_Progress_v4/favicon.png",
            "VideoContext-Engine-icon-backup-",
            "iconutil -c icns",
            "CFBundleIconFile",
            "VideoContextEngine.icns",
            "codesign --verify --deep --strict",
            "lsregister",
        ):
            self.assertIn(required, script)

    def test_icon_recovery_is_documented_and_warns_about_source_resolution(self):
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("favicon.png", doc)
        self.assertIn("резервную копию", doc)
        self.assertIn("64×64 px", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
