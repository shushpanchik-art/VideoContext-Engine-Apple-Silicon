#!/usr/bin/env python3
"""Patch the downloaded benchmark runner in its temporary directory.

This adapter:
- converts the JSONL writer path from str to pathlib.Path;
- optionally limits execution to the baseline profile;
- optionally makes a speech-only diagnostic request that PRESERVES Whisper and
  disables Qwen3-VL, OCR and Qwen3.5 post-processing for clean fault isolation.

It never edits the installed VCE source or persisted settings.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _top_level_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level {name}() definition; found {len(matches)}"
        )
    return matches[0]


def _insert_after_function(source: str, function_name: str, wrapper: str, marker: str) -> str:
    if marker in source:
        return source
    tree = ast.parse(source)
    target = _top_level_function(tree, function_name)
    lines = source.splitlines(keepends=True)
    insertion = target.end_lineno
    if insertion is None:
        raise RuntimeError(f"Cannot determine end of {function_name}()")
    block = wrapper.rstrip() + "\n"
    lines.insert(insertion, block)
    patched = "".join(lines)
    compile(patched, "<patched-vce-benchmark-runner>", "exec")
    return patched


def patch_source(source: str) -> str:
    ast.parse(source)

    source = _insert_after_function(
        source,
        "jsonl_append",
        """# VCE_BENCH_JSONL_PATH_COMPAT
_vce_original_jsonl_append = jsonl_append
def jsonl_append(path, *args, **kwargs):
    from pathlib import Path as _VCEPath
    return _vce_original_jsonl_append(_VCEPath(path), *args, **kwargs)""",
        "# VCE_BENCH_JSONL_PATH_COMPAT",
    )

    source = _insert_after_function(
        source,
        "build_plan",
        """# VCE_BENCH_DIAGNOSTIC_BASELINE_WRAPPER
_vce_original_build_plan = build_plan
def build_plan(*args, **kwargs):
    _vce_plan = _vce_original_build_plan(*args, **kwargs)
    if __import__('os').environ.get('VCE_BENCH_ONLY_BASELINE') == '1':
        if not isinstance(_vce_plan, (list, tuple)) or not _vce_plan:
            raise RuntimeError("build_plan() returned an unexpected value; cannot select baseline")
        return _vce_plan[:1]
    return _vce_plan""",
        "# VCE_BENCH_DIAGNOSTIC_BASELINE_WRAPPER",
    )

    source = _insert_after_function(
        source,
        "build_base_fields",
        """# VCE_BENCH_DIAGNOSTIC_WHISPER_ONLY_WRAPPER
_vce_original_build_base_fields = build_base_fields
def build_base_fields(*args, **kwargs):
    _vce_result = _vce_original_build_base_fields(*args, **kwargs)
    if __import__('os').environ.get('VCE_BENCH_WHISPER_ONLY') != '1':
        return _vce_result
    if not isinstance(_vce_result, tuple) or len(_vce_result) != 2:
        raise RuntimeError("build_base_fields() returned an unexpected value; cannot apply Whisper-only mode")
    _vce_fields, _vce_missing = _vce_result
    if not isinstance(_vce_fields, dict):
        raise RuntimeError("build_base_fields() did not return a settings dictionary")
    _vce_fields = dict(_vce_fields)
    # Preserve speech recognition and word-level timing.
    _vce_fields['analysis_mode'] = 'whisper_ocr'
    _vce_fields['skip_audio'] = False
    _vce_fields['word_timestamps'] = True
    _vce_fields['enable_audio_features'] = True
    # Isolate Whisper from concurrent visual/secondary model work.
    _vce_fields['skip_visual'] = True
    _vce_fields['enable_apple_ocr'] = False
    _vce_fields['enable_ffmpeg_ocr'] = False
    _vce_fields['text_llm_enabled'] = False
    _vce_fields['generate_summary'] = False
    return _vce_fields, _vce_missing""",
        "# VCE_BENCH_DIAGNOSTIC_WHISPER_ONLY_WRAPPER",
    )

    compile(source, "<patched-vce-benchmark-runner>", "exec")
    return source


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_vce_full_profile_benchmark.py <runner.py>", file=sys.stderr)
        return 2
    runner = Path(sys.argv[1])
    source = runner.read_text(encoding="utf-8")
    patched = patch_source(source)
    if patched != source:
        runner.write_text(patched, encoding="utf-8")
        print("Applied benchmark compatibility and opt-in Whisper-only diagnostic guards.")
    else:
        print("Benchmark compatibility and diagnostic guards already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
