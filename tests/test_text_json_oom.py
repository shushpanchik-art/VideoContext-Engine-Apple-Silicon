import ast
import unittest
from pathlib import Path

class TextOOMFallbackTests(unittest.TestCase):
    def test_all_stream_exception_handlers_propagate_oom_before_fallback(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/"VideoContextEngine_v3.19.py").read_text())
        classifier=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="is_gpu_memory_error")
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="run_text_llm_postprocess")
        handlers=[n for n in ast.walk(fn) if isinstance(n,ast.ExceptHandler) and n.name in ("stream_error","fallback_stream_error") and isinstance(n.type,ast.Name) and n.type.id=="Exception"]
        self.assertEqual(len(handlers),2)
        for handler in handlers:
            for message in ("[METAL] Insufficient Memory", "kIOGPUCommandBufferCallbackErrorOutOfMemory", "failed to allocate"):
                error=RuntimeError(message)
                body=ast.Try(body=[ast.Raise(exc=ast.Name(id="original",ctx=ast.Load()),cause=None)],handlers=[handler],orelse=[],finalbody=[])
                module=ast.fix_missing_locations(ast.Module(body=[classifier,body],type_ignores=[]))
                ns={"original":error,"print":lambda *a:None}
                with self.assertRaises(RuntimeError) as caught:exec(compile(module,"handler","exec"),ns)
                self.assertIs(caught.exception,error)
            ns={"original":RuntimeError("unsupported stream"),"print":lambda *a:None}
            exec(compile(module,"handler","exec"),ns)
            self.assertEqual(ns["raw"],"")
