import ast,json,unittest
from pathlib import Path
from typing import Optional

class RootClosureTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  tree=ast.parse((Path(__file__).resolve().parents[1]/'VideoContextEngine_v3.19.py').read_text())
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_text_llm_close_complete_root')
  ns={'json':json,'Optional':Optional};exec(compile(ast.Module(body=[fn],type_ignores=[]),'helper','exec'),ns);cls.repair=staticmethod(ns[fn.name])
 def fixture(self):
  return json.dumps({'executive_summary':'Complete summary','topics':['Topic'],'key_points':[{'text':'Known fact'}],'entities':[],'scene_enrichment':[],'uncertainties':[]})
 def test_only_adds_root_and_preserves_content(self):
  full=self.fixture();self.assertEqual(self.repair(full[:-1]),full);self.assertEqual(json.loads(self.repair(full[:-1])),json.loads(full))
 def test_valid_not_repaired(self):self.assertIsNone(self.repair(self.fixture()))
 def test_cut_string_rejected(self):self.assertIsNone(self.repair(self.fixture().split('Known fact')[0]+'Known'))
 def test_cut_array_rejected(self):self.assertIsNone(self.repair(self.fixture()[:-2]))
 def test_missing_field_rejected(self):self.assertIsNone(self.repair('{"executive_summary":"complete"'))
 def test_empty_summary_rejected(self):self.assertIsNone(self.repair(self.fixture().replace('Complete summary','')[:-1]))
 def test_duplicate_key_rejected(self):self.assertIsNone(self.repair(self.fixture()[:-1]+',"topics":[]'))
 def test_extra_prose_rejected(self):self.assertIsNone(self.repair(self.fixture()[:-1]+' explanation'))
 def test_wrong_type_rejected(self):self.assertIsNone(self.repair(self.fixture().replace('"uncertainties": []','"uncertainties": "unknown"')[:-1]))
 def test_thinking_and_unclosed_reasoning_rejected(self):self.assertIsNone(self.repair('<think>'+self.fixture()[:-1]))
 def test_nonstandard_constant_rejected(self):self.assertIsNone(self.repair(self.fixture().replace('"entities": []','"entities": [NaN]')[:-1]))
