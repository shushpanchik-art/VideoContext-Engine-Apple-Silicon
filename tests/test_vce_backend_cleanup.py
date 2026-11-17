"""Run real cleanup/trace function bodies with fake backends, no native ML imports."""
import ast
import asyncio
import builtins
import contextlib
import io
import json
import os
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'VideoContextEngine_v3.19.py').read_text()
TREE = ast.parse(SOURCE)
NAMES = {'_vce_loaded_backend_module', 'release_accelerator_memory',
         '_vce_clear_metal_memory_caches', '_vce_mlx_trace_runtime',
         '_vce_mlx_trace_stream', '_vce_mlx_invoke_traced'}


class BackendCleanupTests(unittest.TestCase):
    def setUp(self):
        self.modules = {}
        self.gc = Mock()
        self.ns = {'gc': self.gc, '_vce_mlx_trace_sys': types.SimpleNamespace(modules=self.modules),
                   '_vce_mlx_trace_threading': threading, 'os': os, 'json': json}
        nodes = [n for n in TREE.body if getattr(n, 'name', '') in NAMES]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<cleanup>', 'exec'), self.ns)
        self.backend_imports = []
        original = builtins.__import__
        def reject_backend(name, *args, **kwargs):
            if name.split('.')[0] in {'mlx', 'mlx_vlm', 'mlx_whisper', 'torch'}:
                self.backend_imports.append(name)
                raise AssertionError('Cleanup attempted backend import: ' + name)
            return original(name, *args, **kwargs)
        self.import_mock = patch('builtins.__import__', side_effect=reject_backend).start()
        self.addCleanup(patch.stopall)

    def clean(self):
        self.ns['release_accelerator_memory']('test')
        self.ns['_vce_clear_metal_memory_caches']()

    def test_absent_backends_never_import_even_on_repeated_cleanup(self):
        self.clean(); self.clean()
        self.assertEqual(self.backend_imports, [])
        self.assertEqual(self.gc.collect.call_count, 8)

    def test_failed_import_removed_from_modules_is_not_retried(self):
        self.modules['mlx.core'] = types.SimpleNamespace(__spec__=types.SimpleNamespace(_initializing=True))
        self.clean()
        self.modules.pop('mlx.core')  # CPython removes a module after failed exec.
        self.clean()
        self.assertEqual(self.backend_imports, [])

    def test_in_progress_modules_are_not_touched(self):
        mx = Mock(__spec__=types.SimpleNamespace(_initializing=True))
        self.modules['mlx.core'] = mx
        self.clean()
        mx.clear_cache.assert_not_called(); mx.synchronize.assert_not_called()

    def test_loaded_mlx_is_cleared_and_oom_path_synchronizes(self):
        mx = types.SimpleNamespace(clear_cache=Mock(), synchronize=Mock())
        self.modules['mlx.core'] = mx
        self.clean()
        self.assertEqual(mx.clear_cache.call_count, 2)
        mx.synchronize.assert_called_once()
        self.assertEqual(self.backend_imports, [])

    def test_legacy_metal_cache_api_is_preserved(self):
        clear = Mock()
        self.modules['mlx.core'] = types.SimpleNamespace(metal=types.SimpleNamespace(clear_cache=clear))
        self.ns['release_accelerator_memory']()
        clear.assert_called_once()

    def test_mps_release_survives_synchronize_failure(self):
        mps = types.SimpleNamespace(synchronize=Mock(side_effect=RuntimeError('sync')), empty_cache=Mock())
        self.modules['torch'] = types.SimpleNamespace(mps=mps, backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True)))
        self.clean()
        self.assertEqual(mps.empty_cache.call_count, 2)

    def test_backend_errors_do_not_skip_final_collection(self):
        self.modules['mlx.core'] = types.SimpleNamespace(clear_cache=Mock(side_effect=RuntimeError('clear')), synchronize=Mock(side_effect=RuntimeError('sync')))
        with contextlib.redirect_stdout(io.StringIO()): self.clean()
        self.assertEqual(self.gc.collect.call_count, 4)

    def test_trace_does_not_retry_failed_load_or_replace_original_error(self):
        error = RuntimeError('original initializer failure')
        def load_model(): raise error
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(RuntimeError) as got:
                self.ns['_vce_mlx_invoke_traced'](load_model)
        self.assertIs(got.exception, error)
        self.assertEqual(out.getvalue().count('snapshot skipped'), 2)
        self.assertEqual(self.backend_imports, [])

    def test_trace_preserves_cancellation_without_backend_initialization(self):
        cancelled = asyncio.CancelledError()
        def describe_scene(): raise cancelled
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(asyncio.CancelledError) as got:
                self.ns['_vce_mlx_invoke_traced'](describe_scene)
        self.assertIs(got.exception, cancelled)
        self.assertEqual(self.backend_imports, [])

    def test_lifespan_shutdown_remains_passive(self):
        lifespan = next(n for n in TREE.body if getattr(n, 'name', '') == 'lifespan')
        yielded = next(i for i, n in enumerate(lifespan.body)
                       if isinstance(n, ast.Expr) and isinstance(n.value, ast.Yield))
        engine = types.SimpleNamespace(unload_model=Mock())
        self.ns.update(vlm_engine=engine, WHISPER_CACHE={})
        exec(compile(ast.Module(body=lifespan.body[yielded+1:], type_ignores=[]),
                     '<shutdown>', 'exec'), self.ns)
        engine.unload_model.assert_called_once()
        self.assertEqual(self.backend_imports, [])

    def test_loaded_trace_still_reports_device(self):
        self.modules['mlx.core'] = types.SimpleNamespace(default_device=lambda:'gpu', gpu='gpu', cpu='cpu', is_available=lambda x:True, metal=types.SimpleNamespace(is_available=lambda:True), default_stream=lambda x:x)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.ns['_vce_mlx_trace_runtime']('after', 'load_model')
        self.assertIn('"mlx_default_device": "gpu"', out.getvalue())
        self.assertEqual(self.backend_imports, [])

    def test_whisper_release_uses_existing_holder_only(self):
        cls = next(n for n in TREE.body if getattr(n,'name','') == '_VCEWhisperMLXCPU')
        release = next(n for n in cls.body if getattr(n,'name','') == 'release')
        self.ns['_VCE_MLX_WHISPER_LOCK'] = threading.RLock()
        exec(compile(ast.Module(body=[release], type_ignores=[]), '<release>', 'exec'), self.ns)
        owner = types.SimpleNamespace(model_path='own')
        self.ns['release'](owner)  # missing module must not import
        holder = types.SimpleNamespace(model_path='other', model=object())
        self.modules['mlx_whisper.transcribe'] = types.SimpleNamespace(ModelHolder=holder)
        self.ns['release'](owner)
        self.assertEqual(holder.model_path, 'other')
        holder.model_path = 'own'
        self.ns['release'](owner)
        self.assertIsNone(holder.model); self.assertIsNone(holder.model_path)

    def test_text_finally_does_not_import_missing_mlx(self):
        text = next(n for n in TREE.body if getattr(n,'name','') == 'run_text_llm_postprocess')
        final = next(n.finalbody for n in text.body if isinstance(n,ast.Try) and n.finalbody)
        self.ns.update(inference_backend='mlx', VCE_INFERENCE_BACKEND_MLX='mlx')
        exec(compile(ast.Module(body=final,type_ignores=[]), '<text-finally>', 'exec'), self.ns)
        self.assertEqual(self.backend_imports, [])

    def test_ram_minus_final_cleanup_retained_for_previously_cached_model(self):
        analyze = next(n for n in TREE.body if getattr(n,'name','') == 'analyze_video')
        candidates = [n for n in ast.walk(analyze) if isinstance(n,ast.If) and any(isinstance(x,ast.Constant) and x.value == '🧹 RAM- mode: unloading the VLM after processing.' for x in ast.walk(n))]
        final = min(candidates,key=lambda n:len(list(ast.walk(n))))
        engine = types.SimpleNamespace(unload_model=Mock())
        self.ns.update(ram_mode='ram-',run_vlm=False,vlm_engine=engine)
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(ast.Module(body=[final],type_ignores=[]),'<job-final>', 'exec'),self.ns)
        engine.unload_model.assert_called_once()
        self.assertEqual(self.backend_imports, [])


if __name__ == '__main__': unittest.main()
