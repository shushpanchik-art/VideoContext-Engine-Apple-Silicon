from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_video_context_engine.command"
APP_INSTALLER = ROOT / "macos" / "install_video_context_mac.command"
START_SERVER = ROOT / "macos" / "VideoContext Engine.app" / "Contents" / "Resources" / "start_server.command"
STOP_PATCHER = ROOT / "patches" / "patch_vce_stop_and_mps.py"
README = ROOT / "README.md"
INSTALL_DOC = ROOT / "docs" / "INSTALL_FROM_PUBLIC_REPO.md"


class FullDistributionInstallerTests(unittest.TestCase):
    def test_one_command_installer_is_safe_and_targets_the_public_repo(self):
        script = INSTALLER.read_text(encoding="utf-8")
        for required in (
            "VideoContext-Engine-Apple-Silicon.git",
            '[[ "$(uname -m)" == "arm64" ]]',
            "python@3.11",
            "requirements-apple-silicon.txt",
            "install_vce_recognition_languages_patch.command",
            "test_vce_recognition_languages_patch.py",
            "install_video_context_mac.command",
            ".backup.${STAMP}",
            "videocontext_settings.json",
            "pull --ff-only origin main",
        ):
            self.assertIn(required, script)
        self.assertIn("status --porcelain --untracked-files=no", script)
        self.assertNotIn("/Users/", script)
        self.assertNotIn("NSR Specification", script)

    def test_foreign_default_clone_uses_safe_side_by_side_install_path_before_brew(self):
        script = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('REQUESTED_PROJECT_DIR="${VCE_PROJECT_DIR:-}"', script)
        self.assertIn("select_safe_project_dir", script)
        self.assertIn('PROJECT_DIR="$HOME/VideoContext-Engine-Apple-Silicon"', script)
        self.assertIn("Старый каталог останется без изменений.", script)
        self.assertLess(script.index("select_safe_project_dir\n"), script.index('echo "[1/7] Системные зависимости"'))

    def test_native_app_build_does_not_modify_git_checkout(self):
        script = APP_INSTALLER.read_text(encoding="utf-8")
        self.assertIn("APP_STAGING_ROOT=", script)
        self.assertIn('ditto "$APP_SOURCE" "$APP_STAGED"', script)
        self.assertIn('swiftc -O "$STATUS_SOURCE"', script)
        self.assertIn('ditto "$APP_STAGED" "$APP_TARGET"', script)
        self.assertNotIn('swiftc -O "$STATUS_SOURCE" -framework AppKit -framework WebKit -o "$APP_SOURCE', script)
        self.assertIn("VCE_PROJECT_DIR", script)
        self.assertIn('APP_SUPPORT_DIR="$HOME/Library/Application Support/VideoContext Engine"', script)
        self.assertIn('echo "$PROJECT_DIR" > "$APP_SUPPORT_DIR/project_path"', script)
        self.assertIn('patches/patch_vce_stop_and_mps.py', script)

    def test_stop_patcher_resolves_project_root_from_patches_directory(self):
        source = STOP_PATCHER.read_text(encoding="utf-8")
        self.assertIn('PATCHER_DIR.parent if PATCHER_DIR.name == "patches" else PATCHER_DIR', source)
        self.assertIn('ENGINE = PROJECT / "VideoContextEngine_v3.19.py"', source)

    def test_native_launcher_uses_saved_project_path(self):
        script = START_SERVER.read_text(encoding="utf-8")
        self.assertIn("Application Support/VideoContext Engine/project_path", script)
        self.assertIn("VCE_PROJECT_DIR", script)
        self.assertIn("VideoContext-Engine", script)

    def test_documentation_has_one_command_and_model_download_boundary(self):
        readme = README.read_text(encoding="utf-8")
        doc = INSTALL_DOC.read_text(encoding="utf-8")
        command = "scripts/install_video_context_engine.command"
        self.assertIn(command, readme)
        self.assertIn(command, doc)
        self.assertIn("модели", readme.lower())
        self.assertIn("не включены", readme.lower())
        self.assertIn("Скачать", readme)
        self.assertIn("Tesseract", doc)

    def test_example_config_includes_recognition_languages(self):
        config = json.loads((ROOT / "config" / "videocontext_settings.apple_silicon.example.json").read_text(encoding="utf-8"))
        self.assertEqual(config["recognition_languages"], ["ru", "en"])
        self.assertEqual(config["vlm_resolution"], 768)

    def test_apple_silicon_requirements_include_mlx_whisper(self):
        requirements = (ROOT / "requirements-apple-silicon.txt").read_text(encoding="utf-8")
        self.assertIn("mlx-whisper", requirements.splitlines())


if __name__ == "__main__":
    unittest.main(verbosity=2)
