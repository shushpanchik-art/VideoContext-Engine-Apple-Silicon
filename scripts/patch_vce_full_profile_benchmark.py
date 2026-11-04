#!/usr/bin/env python3
"""Patch a downloaded VCE benchmark runner in its temporary directory.

The adapter fixes two observed runner defects:
- converts local JSONL paths to pathlib.Path;
- redirects an API progress endpoint accidentally passed to jsonl_append()
  into the run's local progress_trace.jsonl file.

It also provides opt-in baseline-only and Whisper-only diagnostic modes.
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
        raise RuntimeError(f"Expected exactly one top-level {name}() definition; found {len(matches)}")
    return matches[0]


def _insert_after_function(source: str, function_name: str, wrapper: str, marker: str) -> str:
    if marker in source:
        return source
    tree = ast.parse(source)
    target = _top_level_function(tree, function_name)
    lines = source.splitlines(keepends=True)
    if target.end_lineno is None:
        raise RuntimeError(f"Cannot determine end of {function_name}()")
    lines.insert(target.end_lineno, wrapper.rstrip() + "\n")
    patched = "".join(lines)
    compile(patched, "<patched-vce-benchmark-runner>", "exec")
    return patched


def patch_source(source: str) -> str:
    ast.parse(source)

    source = _insert_after_function(
        source,
        "jsonl_append",
        r'''# VCE_BENCH_JSONL_PATH_COMPAT_V2
_vce_original_jsonl_append = jsonl_append
def jsonl_append(path, *args, **kwargs):
    from pathlib import Path as _VCEPath
    _raw_path = str(path)
    if _raw_path.startswith("/api/v1/progress/") or "/api/v1/progress/" in _raw_path:
        import inspect as _vce_inspect
        _caller = _vce_inspect.currentframe().f_back
        _locals = _caller.f_locals if _caller is not None else {}
        _run_root = _locals.get("run_root")
        if _run_root is None:
            raise RuntimeError("VCE progress endpoint was passed to jsonl_append(), but run_root is unavailable")
        _run_root = _VCEPath(_run_root)
        _profile_key = str(_locals.get("key", "baseline"))
        try:
            _profile_index = int(_locals.get("index", 1))
        except (TypeError, ValueError):
            _profile_index = 1
        _profile_dir = None
        for _name in ("profile_dir", "case_dir", "run_dir", "output_dir"):
            _candidate = _locals.get(_name)
            if isinstance(_candidate, (str, _VCEPath)):
                _candidate = _VCEPath(_candidate)
                try:
                    _candidate.relative_to(_run_root)
                except ValueError:
                    continue
                if _candidate.exists() and _candidate.is_dir():
                    _profile_dir = _candidate
                    break
        if _profile_dir is None:
            _profile_dir = _run_root / f"{_profile_index:02d}_{_profile_key}"
        _profile_dir.mkdir(parents=True, exist_ok=True)
        path = _profile_dir / "progress_trace.jsonl"
    else:
        path = _VCEPath(path)
    return _vce_original_jsonl_append(path, *args, **kwargs)''',
        "# VCE_BENCH_JSONL_PATH_COMPAT_V2",
    )

    source = _insert_after_function(
        source,
        "build_plan",
        r'''# VCE_BENCH_DIAGNOSTIC_BASELINE_WRAPPER_V2
_vce_original_build_plan = build_plan
def build_plan(*args, **kwargs):
    _vce_plan = _vce_original_build_plan(*args, **kwargs)
    if __import__('os').environ.get('VCE_BENCH_ONLY_BASELINE') == '1':
        if not isinstance(_vce_plan, (list, tuple)) or not _vce_plan:
            raise RuntimeError("build_plan() returned an unexpected value; cannot select baseline")
        return _vce_plan[:1]
    return _vce_plan''',
        "# VCE_BENCH_DIAGNOSTIC_BASELINE_WRAPPER_V2",
    )

    source = _insert_after_function(
        source,
        "build_base_fields",
        r'''# VCE_BENCH_DIAGNOSTIC_WHISPER_ONLY_WRAPPER_V2
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
    return _vce_fields, _vce_missing''',
        "# VCE_BENCH_DIAGNOSTIC_WHISPER_ONLY_WRAPPER_V2",
    )

    compile(source, "<patched-vce-benchmark-runner>", "exec")
    return source


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_vce_full_profile_benchmark.py <runner.py>", file=sys.stderr)
        return 2
    runner = Path(sys.argv[1])
    patched = patch_source(runner.read_text(encoding="utf-8"))
    runner.write_text(patched, encoding="utf-8")
    print("Applied VCE benchmark patch v2: JSONL path routing, baseline limit and Whisper-only diagnostic mode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
