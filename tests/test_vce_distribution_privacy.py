from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DistributionPrivacyDefaultsTests(unittest.TestCase):
    def test_benchmark_and_watchdog_require_explicit_video_paths(self):
        bench = (ROOT / "tools" / "bench_text_llm_summary.py").read_text(encoding="utf-8")
        watchdog = (ROOT / "scripts" / "vce_runtime_watchdog.py").read_text(encoding="utf-8")
        self.assertNotIn("/Users/", bench)
        self.assertNotIn("/Users/", watchdog)
        self.assertIn('if not args.input_json and args.video is None:', bench)
        self.assertIn('ap.add_argument("--video", required=True', watchdog)

    def test_benchmark_upload_is_opt_in(self):
        bench = (ROOT / "tools" / "bench_text_llm_summary.py").read_text(encoding="utf-8")
        self.assertIn('ap.add_argument("--upload", action="store_true"', bench)
        self.assertIn('ap.add_argument("--upload-outputs", action="store_true"', bench)
        self.assertIn('if args.upload:', bench)
        self.assertIn('if args.upload_outputs:', bench)


if __name__ == "__main__":
    unittest.main()
