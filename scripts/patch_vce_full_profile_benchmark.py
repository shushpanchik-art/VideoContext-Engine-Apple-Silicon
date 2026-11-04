#!/usr/bin/env python3
"""Patch a downloaded VCE benchmark runner in its temporary directory.

The compatibility guard normalizes the JSONL path argument before Path.open().
Two opt-in environment switches support short, isolated crash-diagnostic runs.
No installed engine file or persisted VCE setting is modified.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if not matches:
        raise RuntimeError(f"Downloaded runner has no {name}() helper")
    return matches[0]


def _insert_guard_before_return(
    source: str,
    function_name: str,
    predicate,
    guard: str,
    sentinel: str,
) -> str:
    if sentinel in source:
        return source
    tree = ast.parse(source)
    target = _function(tree, function_name)
    returns = [
        node for node in ast.walk(target)
        if isinstance(node, ast.Return) and predicate(node.value)
    ]
    if not returns:
        raise RuntimeError(f"Cannot locate the expected return in {function_name}()")
    ret = returns[-1]
    indentation = " " * ret.col_offset
    block = "".join(indentation + line if line else "" for line in guard.splitlines(keepends=True))
    if not block.endswith("\n"):
        block += "\n"
    lines = source.splitlines(keepends=True)
    lines.insert(ret.lineno - 1, block)
    patched = "".join(lines)
    compile(patched, "<patched-vce-benchmark-runner>", "exec")
    return patched


def patch_source(source: str) -> str:
    tree = ast.parse(source)
    target = _function(tree, "jsonl_append")
    lines = source.splitlines(keepends=True)
    function_source = "".join(lines[target.lineno - 1 : target.end_lineno])
    marker = "__import__('pathlib').Path(path)"
    if marker not in function_source and '__import__("pathlib").Path(path)' not in function_source:
        body = target.body
        if not body:
            raise RuntimeError("jsonl_append() has an empty body")
        first_statement = body[1] if ast.get_docstring(target) and len(body) > 1 else body[0]
        insert_at = first_statement.lineno - 1
        indentation = " " * first_statement.col_offset
        lines.insert(insert_at, indentation + "path = __import__('pathlib').Path(path)\n")
        source = "".join(lines)
        compile(source, "<patched-vce-benchmark-runner>", "exec")

    # Opt-in crash isolation: retain only the baseline profile.
    source = _insert_guard_before_return(
        source,
        "build_plan",
        lambda value: isinstance(value, ast.Name) and value.id == "plan",
        """# VCE_BENCH_DIAGNOSTIC_ONLY_BASELINE
if __import__('os').environ.get('VCE_BENCH_ONLY_BASELINE') == '1':
    plan = plan[:1]""",
        "# VCE_BENCH_DIAGNOSTIC_ONLY_BASELINE",
    )

    # Opt-in audio bypass: test the visual/MLX/OCR path without Whisper inference.
    source = _insert_guard_before_return(
        source,
        "build_base_fields",
        lambda value: (
            isinstance(value, ast.Tuple)
            and len(value.elts) == 2
            and isinstance(value.elts[0], ast.Name) and value.elts[0].id == "fields"
            and isinstance(value.elts[1], ast.Name) and value.elts[1].id == "missing"
        ),
        """# VCE_BENCH_DIAGNOSTIC_SKIP_AUDIO
if __import__('os').environ.get('VCE_BENCH_SKIP_AUDIO') == '1':
    fields['skip_audio'] = True
    fields['enable_audio_features'] = False
    fields['word_timestamps'] = False""",
        "# VCE_BENCH_DIAGNOSTIC_SKIP_AUDIO",
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
        print("Applied benchmark path guard and opt-in diagnostic switches.")
    else:
        print("Benchmark compatibility and diagnostic guards already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
