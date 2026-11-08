from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DistributionPrivacyDefaultsTests(unittest.TestCase):
    def test_benchmark_and_watchdog_require_explicit_video_paths(self):
        bench = (ROOT / "tools" / "bench" / "bench_text_llm_summary.py").read_text(encoding="utf-8")
        watchdog = (ROOT / "tools" / "bench" / "vce_runtime_watchdog.py").read_text(encoding="utf-8")
        self.assertNotIn("/Users/", bench)
        self.assertNotIn("/Users/", watchdog)
        self.assertIn('if not args.input_json and args.video is None:', bench)
        self.assertIn('ap.add_argument("--video", required=True', watchdog)

    def test_benchmark_upload_is_opt_in(self):
        bench = (ROOT / "tools" / "bench" / "bench_text_llm_summary.py").read_text(encoding="utf-8")
        self.assertIn('ap.add_argument("--upload", action="store_true"', bench)
        self.assertIn('ap.add_argument("--upload-outputs", action="store_true"', bench)
        self.assertIn('if args.upload:', bench)
        self.assertIn('if args.upload_outputs:', bench)


    def test_unattended_vce_benchmark_is_local_and_requires_video(self):
        runner = (ROOT / "scripts" / "vce_unattended_benchmark.py").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts" / "start_vce_benchmark.command").read_text(encoding="utf-8")
        self.assertNotIn("/Users/", runner)
        self.assertNotIn("NSR Specification", runner)
        self.assertNotIn("feat/automated-vce-benchmark", runner + launcher)
        self.assertIn('ap.add_argument("--video", type=pathlib.Path, required=True', runner)
        self.assertIn('"storage": "local_only"', runner)
        self.assertNotIn("gh repo clone", runner)
        self.assertNotIn("git push", runner)
        self.assertNotIn("BASE_URL", runner)
        self.assertNotIn("curl -fL", launcher)
        self.assertIn('--video "$VIDEO"', launcher)


if __name__ == "__main__":
    unittest.main()
