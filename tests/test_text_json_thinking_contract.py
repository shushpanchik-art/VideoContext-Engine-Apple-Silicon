"""Do not spend the JSON extraction output budget on reasoning generation."""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'VideoContextEngine_v3.19.py'

class TextJSONThinkingContract(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        self.function = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'run_text_llm_postprocess')
        self.calls = [x for x in ast.walk(self.function) if isinstance(x, ast.Call)]

    def test_every_explicit_thinking_request_is_disabled(self):
        values = [kw.value for call in self.calls for kw in call.keywords if kw.arg == 'enable_thinking']
        self.assertGreaterEqual(len(values), 5)  # primary template/stream/generate + repair template/generate
        for value in values:
            self.assertIsInstance(value, ast.Constant)
            self.assertIs(value.value, False)

    def test_primary_stream_and_fallback_explicitly_disable_thinking(self):
        for name in ('stream_generate', 'generate', 'apply_chat_template'):
            calls = [c for c in self.calls if isinstance(c.func, ast.Name) and c.func.id == name]
            self.assertTrue(any(any(k.arg == 'enable_thinking' and isinstance(k.value, ast.Constant) and k.value.value is False for k in c.keywords) for c in calls), name)

    def test_no_thinking_budget_implies_no_hidden_reasoning_limit_claim(self):
        self.assertFalse(any(k.arg == 'thinking_budget' for c in self.calls for k in c.keywords))

if __name__ == '__main__': unittest.main()
