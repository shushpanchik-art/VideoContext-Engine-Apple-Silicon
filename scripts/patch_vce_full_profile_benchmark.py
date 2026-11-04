#!/usr/bin/env python3
"""Apply a small compatibility fix to the downloaded benchmark runner.

The published compressed runner historically passed a str to jsonl_append(),
while that helper used Path.open(). Normalize the value at the helper boundary.
This patch is applied only to a temporary copy in the launcher's temp directory.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def patch_source(source: str) -> str:
    tree = ast.parse(source)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "jsonl_append"
    ]
    if not matches:
        raise RuntimeError("Downloaded runner has no jsonl_append() helper")
    target = matches[0]
    lines = source.splitlines(keepends=True)
    function_source = "".join(lines[target.lineno - 1 : target.end_lineno])
    marker = "__import__('pathlib').Path(path)"
    if marker in function_source or '__import__("pathlib").Path(path)' in function_source:
        return source

    body = target.body
    if not body:
        raise RuntimeError("jsonl_append() has an empty body")
    first_statement = body[1] if ast.get_docstring(target) and len(body) > 1 else body[0]
    insert_at = first_statement.lineno - 1
    indentation = " " * first_statement.col_offset
    lines.insert(insert_at, indentation + "path = __import__('pathlib').Path(path)\n")
    patched = "".join(lines)
    compile(patched, "<patched-vce-benchmark-runner>", "exec")
    return patched


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_vce_full_profile_benchmark.py <runner.py>", file=sys.stderr)
        return 2
    runner = Path(sys.argv[1])
    source = runner.read_text(encoding="utf-8")
    patched = patch_source(source)
    if patched != source:
        runner.write_text(patched, encoding="utf-8")
        print("Applied JSONL path compatibility guard.")
    else:
        print("JSONL path compatibility guard already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
