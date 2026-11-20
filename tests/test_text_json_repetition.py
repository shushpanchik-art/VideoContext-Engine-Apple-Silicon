import ast
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


class RepetitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((Path(__file__).resolve().parents[1] / "VideoContextEngine_v3.19.py").read_text())
        helper = cls.fn("_text_llm_repeated_point_output")
        ns = {"json": json}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "helper", "exec"), ns)
        cls.detect = staticmethod(ns[helper.name])

    @classmethod
    def fn(cls, name):
        return next(n for n in cls.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

    def point(self, **kw):
        return dict({"text": "The same sufficiently long source point", "scene_id": 1,
                     "time": "00:00:01", "evidence": ["ocr"]}, **kw)

    def output(self, points):
        return json.dumps({"executive_summary": "A summary", "topics": [], "key_points": points,
                           "entities": [], "scene_enrichment": [], "uncertainties": []})

    def test_four_identical_complete_points(self):
        self.assertTrue(self.detect(self.output([self.point()] * 4)))
        self.assertFalse(self.detect(self.output([self.point()] * 3)))

    def test_same_text_with_distinct_evidence_identity(self):
        for points in ([self.point(scene_id=n) for n in range(4)],
                       [self.point(time=f"00:00:0{n}") for n in range(4)],
                       [self.point(evidence=[str(n)]) for n in range(4)]):
            with self.subTest(points=points):
                self.assertFalse(self.detect(self.output(points)))

    def test_only_top_level_key_points(self):
        self.assertFalse(self.detect(json.dumps({"other": {"key_points": [self.point()] * 4}})))

    def test_incomplete_json_only_counts_complete_objects(self):
        prefix = '{"key_points":[' + ','.join(json.dumps(self.point()) for _ in range(4))
        self.assertTrue(self.detect(prefix))
        self.assertFalse(self.detect(prefix[:-1]))

    def test_escaped_and_short_text(self):
        self.assertTrue(self.detect(self.output([self.point(text='A source point with "quotes" and a newline\n')] * 4)))
        self.assertFalse(self.detect(self.output([self.point(text="short")] * 4)))

    def test_stream_checks_boundary_when_counter_skips_64(self):
        fn = self.fn("run_text_llm_postprocess")
        loop = next(n for n in ast.walk(fn) if isinstance(n, ast.For)
                    and "generation_tokens" in ast.unparse(n))
        source = (
            "def stream():\n"
            "    reported_tokens = 0\n"
            "    next_repetition_check = 64\n"
            "    chunks = []\n"
            "    repetition_detected = False\n"
            "    started_stream = time.time()\n"
            + "\n".join("    " + line for line in ast.unparse(loop).splitlines())
            + "\n    return repetition_detected, reported_tokens\n"
        )
        payload = self.output([self.point()] * 4)
        calls = []
        def chunks(*args, **kwargs):
            for count, text in ((61, payload[:50]), (67, payload[50:]), (70, "")):
                calls.append(count)
                yield SimpleNamespace(text=text, generation_tokens=count)
        ns = {"stream_generate": chunks, "time": time, "model": None, "processor": None,
              "formatted_prompt": "", "generation_limit": 1024, "progress_callback": None,
              "_text_llm_repeated_point_output": self.detect}
        exec(source, ns)
        self.assertEqual(ns["stream"](), (True, 67))
        self.assertEqual(calls, [61, 67])

    def test_regeneration_uses_original_instruction(self):
        fn = self.fn("run_text_llm_postprocess")
        branches = [n for n in ast.walk(fn) if isinstance(n, ast.If)
                    and "repetition_detected or len" in ast.unparse(n.test)]
        self.assertEqual(len(branches), 1)
        self.assertIn("instruction +", ast.unparse(branches[0]))
        self.assertIn("regenerate_from_original_evidence", ast.unparse(branches[0]))
