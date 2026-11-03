from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "patches" / "patch_vce_stop_and_mps.py"
ENGINE_PATH = ROOT / "VideoContextEngine_v3.19.py"
UI_PATH = ROOT / "videocontext_web_ui.html"


def load_patcher():
    spec = importlib.util.spec_from_file_location("vce_stop_mps_patcher", PATCHER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CurrentRuntimeStopPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = load_patcher()

    def test_current_async_engine_is_recognized_and_left_unchanged(self):
        engine = ENGINE_PATH.read_text(encoding="utf-8")
        self.assertIn("scenes_raw = await asyncio.to_thread(", engine)
        self.assertIn("_vce_describe_scene_with_memory_fallback", engine)
        self.assertEqual(self.patcher.patch_engine(engine), engine)

    def test_current_stop_ui_is_recognized_and_left_unchanged(self):
        ui = UI_PATH.read_text(encoding="utf-8")
        self.assertIn('id="stop"', ui)
        self.assertIn("stop.addEventListener('click'", ui)
        self.assertEqual(self.patcher.patch_ui(ui), ui)

    def test_stop_runtime_detection_does_not_depend_on_async_detector_or_executor_helper(self):
        engine = ENGINE_PATH.read_text(encoding="utf-8")
        engine = engine.replace(
            "scenes_raw = await asyncio.to_thread(",
            "scenes_raw = detect_scenes(",
        )
        engine = engine.replace(
            "await _vce_mlx_run(vlm_engine.load_model, target_vlm)",
            "vlm_engine.load_model(target_vlm)",
        )
        self.assertEqual(self.patcher.patch_engine(engine), engine)


if __name__ == "__main__":
    unittest.main(verbosity=2)
