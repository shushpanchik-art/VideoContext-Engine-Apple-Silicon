from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestNsrspecForensicRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "scripts/vce_run_nsrspec.command").read_text(encoding="utf-8")
        cls.observer = (ROOT / "scripts/vce_observed_run.py").read_text(encoding="utf-8")
        cls.installer = (ROOT / "scripts/install_video_context_engine.command").read_text(encoding="utf-8")

    def test_launcher_targets_the_requested_video_and_native_app_only(self):
        self.assertIn(
            '/Users/artemijvisnevskij/Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4',
            self.launcher,
        )
        self.assertIn('open -a "$APP"', self.launcher)
        self.assertNotIn('open "$API', self.launcher)
        self.assertIn('exec > >(tee -a "$OUT_ROOT/launcher.log") 2>&1', self.launcher)

    def test_launcher_requires_profile_and_whisper_mps_fix_before_analysis(self):
        self.assertIn('"runtime_profile" not in properties', self.launcher)
        self.assertIn('baseline_mps_sequential', self.launcher)
        self.assertIn('x.cpu().double().numpy()', self.launcher)

    def test_launcher_detects_existing_dropbox_remote_and_verifies_upload(self):
        self.assertIn("vce-dropbox:", self.launcher)
        self.assertIn("dropbox:", self.launcher)
        self.assertIn("20261104_224554_1f098d/VCE_Forensic_Runs", self.launcher)
        self.assertNotIn("20261104_224554_1f098d/тест2", self.launcher)
        self.assertIn('rclone copyto "$ARCHIVE" "$REMOTE_FILE"', self.launcher)
        self.assertIn('rclone size "$REMOTE_FILE" --json', self.launcher)
        self.assertIn('DROPBOX_UPLOAD=SUCCESS', self.launcher)
        self.assertIn('ditto -c -k --sequesterRsrc --keepParent "$OUT_ROOT" "$ARCHIVE"', self.launcher)

    def test_observer_forces_the_documented_baseline_models(self):
        self.assertIn('"runtime_profile": "baseline_mps_sequential"', self.observer)
        self.assertIn('"enable_mlx_whisper": False', self.observer)
        self.assertIn('"vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit"', self.observer)
        self.assertIn('"text_llm_model": "mlx-community/Qwen3.5-9B-MLX-4bit"', self.observer)
        self.assertIn('if "runtime_profile" not in properties:', self.observer)

    def test_installer_applies_and_verifies_mps_dtw_patch(self):
        self.assertIn('"$PROJECT_DIR/.venv/bin/python" "$PATCHER_PATH"', self.installer)
        self.assertIn('x.cpu().double().numpy()', self.installer)
        self.assertIn('Whisper MPS DTW fix verified.', self.installer)


if __name__ == "__main__":
    unittest.main()
