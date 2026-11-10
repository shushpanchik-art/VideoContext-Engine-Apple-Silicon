import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "vce_run_nsrspec.command"


class TestNSRSpecForensicLauncher(unittest.TestCase):
    def test_launcher_has_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(LAUNCHER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_targets_the_requested_video_and_native_app_only(self):
        source = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn(
            "/Users/artemijvisnevskij/Downloads/NSR Specification/"
            "🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4",
            source,
        )
        self.assertIn('open -a "$APP"', source)
        self.assertIn('/Applications/VideoContext Engine.app', source)
        self.assertNotIn("open http", source)
        self.assertNotIn("open -a Safari", source)
        self.assertNotIn("playwright", source.lower())

    def test_full_archive_upload_is_private_dropbox_rclone_only(self):
        source = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("rclone copyto", source)
        self.assertIn("VCE_DROPBOX_REMOTE", source)
        self.assertIn("ditto -c -k", source)
        self.assertIn("Исходное видео никуда не загружалось", source)
        self.assertNotIn("gh gist create", source)


if __name__ == "__main__":
    unittest.main()
