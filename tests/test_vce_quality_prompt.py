from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "patch_vce_quality_prompt.py"


def load_patch():
    spec = importlib.util.spec_from_file_location("vce_quality_prompt_patch", PATCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualityPromptPatchTests(unittest.TestCase):
    def setUp(self):
        self.patch = load_patch()

    def test_rewrites_prompts_and_preserves_visual_description_in_summary(self):
        source = '''BASE_VISUAL_PROMPT = """old visual""".strip()

BASE_SUMMARY_PROMPT = """old summary""".strip()

def make_context(final_segments):
    for s in final_segments:
                        vt = s.get("visual_tags") or {}
                        if vt:
                            parts.append(
                                "Visuel: "
                                f"people={vt.get('people_count', '?')}, "
                                f"place={vt.get('place_type', '?')}, "
                                f"action={vt.get('main_action', '?')}, "
                                f"tone={vt.get('emotional_tone', '?')}, "
                                f"movement={vt.get('movement_level', '?')}"
                            )
                        elif s.get("visual_description"):
                            parts.append(f"Visuel: {s['visual_description']}")

'''
        result = self.patch.transform_engine_source(source)
        self.assertIn("Если видны слайды", result)
        self.assertIn("дословно сохраняй", result)
        self.assertIn("Visual details: {visual_description}", result)
        self.assertIn("Visual tags: ", result)
        self.assertNotIn('elif s.get("visual_description")', result)
        self.assertEqual(self.patch.transform_engine_source(result), result)

    def test_settings_only_replace_prompts_and_preserve_other_keys(self):
        original = {
            "vlm_model": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
            "vlm_resolution": 1280,
            "skip_visual": False,
            "custom_setting": {"keep": True},
            "visual_user_prompt": "old",
            "summary_user_prompt": "old summary",
        }
        updated = self.patch.optimize_settings(original)
        self.assertEqual(updated["vlm_model"], original["vlm_model"])
        self.assertEqual(updated["vlm_resolution"], 1280)
        self.assertFalse(updated["skip_visual"])
        self.assertEqual(updated["custom_setting"], {"keep": True})
        self.assertIn("дословно", updated["visual_user_prompt"])
        self.assertIn("хронологию", updated["summary_user_prompt"])


if __name__ == "__main__":
    unittest.main()
