from __future__ import annotations
import ast
import importlib.util
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
        self.assertIn("один канонический вариант", patcher)
        self.assertIn("executive_summary, topics, key_points, entities", patcher)
        self.assertIn("original_summary", patcher)
        self.assertIn("audio_transcript, ocr_text, visual_description", patcher)
        self.assertIn("Не объявляй действие завершённым", patcher)

    def test_name_consistency_v2_patch_has_ocr_gated_canonicalizer(self):
        patch_path = PATCHES / "patch_vce_name_consistency_v2.py"
        patch_source = patch_path.read_text(encoding="utf-8")
        self.assertIn("VCE_CONFIRMED_STANDARD_CANONICALIZATION_V2", patch_source)
        self.assertIn("СП 1.13.132.020", patch_source)
        self.assertIn("СП 1.13130.2020", patch_source)
        self.assertIn("original_summary", patch_source)
        self.assertIn("VCE_NAME_CONSISTENCY_V2_GLOBAL_SUMMARY", patch_source)

        spec = importlib.util.spec_from_file_location("vce_name_consistency_patch", patch_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        namespace = {"Any": object, "Dict": dict, "List": list}
        exec(module.CANONICALIZER_SOURCE, namespace)
        normalize = namespace["_vce_canonicalize_confirmed_standard_names"]
        payload = {
            "executive_summary": "См. СП 1.13.132.020",
            "topics": ["СП 1.13.132.020"],
            "entities": [{"name": "СП 1.13.132.020", "type": "standard"}],
            "original_summary": "СП 1.13.132.020",
        }
        result = normalize(payload, [{"ocr_text": "СП 1.13130.2020 Системы противопожарной защиты"}])
        self.assertEqual(result["topics"], ["СП 1.13130.2020"])
        self.assertEqual(result["entities"][0]["name"], "СП 1.13130.2020")
        self.assertEqual(result["original_summary"], "СП 1.13.132.020")
        untouched = normalize({"topics": ["СП 1.13.132.020"]}, [{"ocr_text": "СП 59.13330.2020"}])
        self.assertEqual(untouched["topics"], ["СП 1.13.132.020"])

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
