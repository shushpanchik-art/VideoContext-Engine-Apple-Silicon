from __future__ import annotations
import ast
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "patches"

class PatchsetSanityTests(unittest.TestCase):
    def test_patchers_parse_as_python(self):
        files = sorted(PATCHES.glob("*.py"))
        self.assertGreaterEqual(len(files), 8)
        for path in files:
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_mps_word_timing_fix_is_present(self):
        src = (PATCHES / "patch_vce_stop_and_mps.py").read_text(encoding="utf-8")
        self.assertIn('MPS_NEW = "return dtw_cpu(x.cpu().double().numpy())"', src)
        self.assertIn('MPS_OLD = "return dtw_cpu(x.double().cpu().numpy())"', src)

    def test_batch_settings_cancel_and_save_hooks_are_in_patchers(self):
        batch = (PATCHES / "patch_vce_ui_batch_progress.py").read_text(encoding="utf-8")
        stop = (PATCHES / "patch_vce_stop_and_mps.py").read_text(encoding="utf-8")
        beside = (PATCHES / "patch_vce_save_next_to_video.py").read_text(encoding="utf-8")
        settings = (PATCHES / "patch_vce_settings_button.py").read_text(encoding="utf-8")
        self.assertIn("word_timestamps", batch)
        self.assertIn("generate_json", batch)
        self.assertIn("/api/v1/cancel/", stop)
        self.assertIn("Остановить распознавание", stop)
        self.assertIn("select-local-videos", beside)
        self.assertIn("OUTPUT_FILE_REGISTRY", beside)
        self.assertIn("videocontext_settings.json", settings)

    def test_name_reconciliation_prompt_requires_cross_field_consistency(self):
        patcher = (PATCHES / "patch_vce_name_reconciliation_prompt.py").read_text(encoding="utf-8")
        self.assertIn("единый канонический вариант", patcher)
        self.assertIn("executive_summary, topics, key_points, entities", patcher)
        self.assertIn("original_summary", patcher)
        self.assertIn("audio_transcript, ocr_text, visual_description", patcher)

    def test_name_consistency_v2_patch_has_ocr_gated_canonicalizer(self):
        patcher = (PATCHES / "patch_vce_name_consistency_v2.py").read_text(encoding="utf-8")
        self.assertIn("VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2", patcher)
        self.assertIn("СП 1.13.132.020", patcher)
        self.assertIn("СП 1.13130.2020", patcher)
        self.assertIn("original_summary", patcher)
        self.assertIn("VCE_NAME_CONSISTENCY_V2_GLOBAL_SUMMARY", patcher)

    def test_recommended_profile(self):
        cfg = json.loads((ROOT / "config/videocontext_settings.apple_silicon.example.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["vlm_model"], "mlx-community/Qwen3-VL-8B-Instruct-4bit")
        self.assertEqual(cfg["whisper_model"], "large")
        self.assertEqual(cfg["vlm_resolution"], 1280)
        self.assertEqual(cfg["max_scene_duration"], 20)
        self.assertEqual(cfg["keyframes_per_scene"], 1)
        self.assertTrue(cfg["enable_apple_ocr"])
        self.assertTrue(cfg["generate_json"])
        self.assertTrue(cfg["word_timestamps"])

    def test_shell_scripts_parse(self):
        for path in [ROOT / "install_patches_to_existing_checkout.command", ROOT / "macos/install_video_context_mac.command"]:
            with self.subTest(path=path):
                p = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)

if __name__ == "__main__":
    unittest.main()
