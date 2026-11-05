#!/usr/bin/env python3
"""Add a process-lifetime fail-closed latch for fatal MLX/Metal queue failures.

This does not attempt to repair a Metal timeout. It prevents VLM/Text-LLM GPU
submissions after the first timeout/SubmissionsIgnored event while leaving the
independent CPU Whisper/OCR/report paths available. The VCE process must be
restarted before GPU inference is attempted again.

Safe default: writes a patched copy next to the source. Use --apply only after
reviewing the generated diff; --apply keeps a timestamped backup first.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import os
import sys
import time
from pathlib import Path

MARKER = "# VCE_METAL_RUNTIME_FAILURE_LATCH_V1"

HELPERS = r'''# VCE_METAL_RUNTIME_FAILURE_LATCH_V1
# A Metal command-buffer timeout / SubmissionsIgnored is treated as a fatal
# process-local GPU runtime state, not as a recoverable allocation OOM.
_VCE_METAL_RUNTIME_FAILURE_LOCK = _vce_threading.Lock()
_VCE_METAL_RUNTIME_FAILURE = None


def _vce_metal_runtime_failure():
    with _VCE_METAL_RUNTIME_FAILURE_LOCK:
        return dict(_VCE_METAL_RUNTIME_FAILURE) if _VCE_METAL_RUNTIME_FAILURE else None


def _vce_mark_metal_runtime_failed(stage, exc):
    global _VCE_METAL_RUNTIME_FAILURE
    detail = _vce_exception_chain_text(exc)[:800]
    with _VCE_METAL_RUNTIME_FAILURE_LOCK:
        if _VCE_METAL_RUNTIME_FAILURE is None:
            _VCE_METAL_RUNTIME_FAILURE = {
                "stage": str(stage),
                "pid": os.getpid(),
                "thread_name": _vce_threading.current_thread().name,
                "timestamp": time.time(),
                "error": detail,
            }
        failure = dict(_VCE_METAL_RUNTIME_FAILURE)
    print(
        "[VCE MLX] Metal runtime latched FAILED; GPU inference is disabled until "
        "the VCE process is restarted: "
        + json.dumps(failure, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    return failure


def _vce_assert_metal_runtime_healthy(stage):
    failure = _vce_metal_runtime_failure()
    if failure is not None:
        raise RuntimeError(
            "[VCE MLX] Refusing GPU operation " + str(stage)
            + ": this process previously encountered a fatal Metal command-buffer "
            "timeout/submission rejection. Restart VideoContext Engine before "
            "attempting VLM or Text LLM again. First failure: "
            + str(failure.get("stage", "unknown")) + " / "
            + str(failure.get("error", "unknown"))[:500]
        )
'''


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {label} anchor; found {count}. Source was not written.")
    return source.replace(old, new, 1)


def _top_level_function(tree: ast.Module, name: str):
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one top-level {name}(); found {len(matches)}")
    return matches[0]


def _replace_in_function(source: str, function_name: str, old: str, new: str, label: str) -> str:
    tree = ast.parse(source)
    node = _top_level_function(tree, function_name)
    lines = source.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    segment = "".join(lines[start:end])
    count = segment.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {label} anchor inside {function_name}(); found {count}. Source was not written.")
    segment = segment.replace(old, new, 1)
    lines[start:end] = [segment]
    patched = "".join(lines)
    ast.parse(patched)
    return patched


def _insert_after_docstring(source: str, function_name: str, statement: str) -> str:
    tree = ast.parse(source)
    node = _top_level_function(tree, function_name)
    if not node.body:
        raise ValueError(f"{function_name}() has an empty body")
    first = node.body[0]
    if not (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        raise ValueError(f"{function_name}() has no docstring anchor; refusing an ambiguous edit")
    lines = source.splitlines(keepends=True)
    lines.insert(first.end_lineno, statement.rstrip() + "\n")
    patched = "".join(lines)
    ast.parse(patched)
    return patched


def patch_source(source: str) -> str:
    ast.parse(source)
    if MARKER in source:
        required = (
            "def _vce_metal_runtime_failure():",
            "def _vce_assert_metal_runtime_healthy(stage):",
            "_vce_mark_metal_runtime_failed(\"vlm_generation\", exc)",
            "Restart VideoContext Engine before",
            "_vce_assert_metal_runtime_healthy(\"text_llm\")",
        )
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("Found a partial Metal-latch patch; missing: " + ", ".join(missing))
        return source

    # Verify all target functions exist before changing any source text.
    tree = ast.parse(source)
    for name in (
        "_vce_mlx_run",
        "_vce_configure_mlx_metal",
        "_vce_prepare_mlx_vlm_runtime",
        "_vce_describe_scene_with_memory_fallback",
        "run_text_llm_postprocess",
        "analyze_video",
    ):
        _top_level_function(tree, name)

    configure = _top_level_function(tree, "_vce_configure_mlx_metal")
    lines = source.splitlines(keepends=True)
    lines.insert(configure.lineno - 1, HELPERS + "\n")
    patched = "".join(lines)

    # Central MLX executor: fail before queueing new GPU work; latch uncaught
    # timeouts in Text LLM or any other MLX operation too.
    patched = _replace_once(
        patched,
        "    call = _vce_mlx_functools.partial(_vce_mlx_invoke_traced, fn, *args, **kwargs)\n"
        "    return await loop.run_in_executor(_VCE_MLX_EXECUTOR, call)\n",
        "    operation = getattr(fn, \"__qualname__\", getattr(fn, \"__name__\", type(fn).__name__))\n"
        "    _vce_assert_metal_runtime_healthy(operation)\n"
        "    call = _vce_mlx_functools.partial(_vce_mlx_invoke_traced, fn, *args, **kwargs)\n"
        "    try:\n"
        "        return await loop.run_in_executor(_VCE_MLX_EXECUTOR, call)\n"
        "    except Exception as exc:\n"
        "        if _vce_is_metal_command_timeout(exc):\n"
        "            _vce_mark_metal_runtime_failed(operation, exc)\n"
        "        raise\n",
        "_vce_mlx_run executor return",
    )

    # Guard configuration and model-generation setup before importing/using MLX.
    patched = _insert_after_docstring(
        patched, "_vce_configure_mlx_metal",
        '    _vce_assert_metal_runtime_healthy("configure_mlx_metal:" + str(stage))',
    )
    patched = _insert_after_docstring(
        patched, "_vce_prepare_mlx_vlm_runtime",
        '    _vce_assert_metal_runtime_healthy("prepare_mlx_vlm_runtime")',
    )

    # A caught VLM timeout currently becomes a fallback sentinel; record it in
    # process state so later Text LLM and future VLM requests cannot re-submit.
    patched = _replace_in_function(
        patched,
        "_vce_describe_scene_with_memory_fallback",
        "        if _vce_is_metal_command_timeout(exc):\n",
        "        if _vce_is_metal_command_timeout(exc):\n"
        "            _vce_mark_metal_runtime_failed(\"vlm_generation\", exc)\n",
        "VLM timeout branch",
    )

    # Do not synchronize the damaged MLX queue after a timeout. The helper's
    # ordinary allocation-OOM path still performs cache cleanup and bounded retries.
    patched = _replace_in_function(
        patched,
        "_vce_describe_scene_with_memory_fallback",
        "            try:\n"
        "                vlm_engine.unload_model()\n"
        "            except Exception:\n"
        "                pass\n"
        "            _vce_clear_metal_memory_caches()\n"
        "            message = (\n",
        "            print(\"[VCE MLX] Skipping model unload and cache operations after fatal Metal timeout; "
        "restart the VCE process to release GPU resources.\", flush=True)\n"
        "            message = (\n",
        "post-timeout model unload and cache cleanup",
    )

    # If a prior request failed in this process, keep subsequent requests on
    # audio/OCR/report paths rather than failing during VLM model initialization.
    patched = _replace_in_function(
        patched,
        "analyze_video",
        "                if run_vlm:\n                    release_accelerator_memory('before VLM load')\n",
        "                if run_vlm and _vce_metal_runtime_failure() is not None:\n"
        "                    run_vlm = False\n"
        "                    print(\"[VCE MLX] Skipping VLM for this request because this process "
        "has a latched Metal failure; restart VCE to restore GPU inference.\", flush=True)\n"
        "                if run_vlm:\n                    release_accelerator_memory('before VLM load')\n",
        "analyze_video VLM load guard",
    )

    # Text LLM uses the same mlx-vlm / Metal backend. Fail before its GPU probe.
    patched = _insert_after_docstring(
        patched, "run_text_llm_postprocess",
        '    _vce_assert_metal_runtime_healthy("text_llm")',
    )

    ast.parse(patched)
    return patched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", type=Path, help="Path to VideoContextEngine_v3.19.py")
    parser.add_argument("--apply", action="store_true", help="Replace source in place after making a timestamped backup")
    parser.add_argument("--output", type=Path, help="Write patched copy here (default: <engine>.metal-latch.py)")
    args = parser.parse_args()

    engine = args.engine.expanduser().resolve()
    if not engine.is_file():
        parser.error(f"Engine file not found: {engine}")
    original = engine.read_text(encoding="utf-8")
    patched = patch_source(original)
    compile(patched, str(engine), "exec")
    if patched == original:
        print("Metal failure latch is already present; no changes made.")
        return 0

    old_sha = hashlib.sha256(original.encode()).hexdigest()
    new_sha = hashlib.sha256(patched.encode()).hexdigest()
    if args.apply:
        backup = engine.with_name(engine.name + f".backup-{time.strftime('%Y%m%dT%H%M%S')}")
        backup.write_text(original, encoding="utf-8")
        engine.write_text(patched, encoding="utf-8")
        print(f"Patched in place: {engine}")
        print(f"Backup: {backup}")
    else:
        output = args.output.expanduser().resolve() if args.output else engine.with_name(engine.name + ".metal-latch.py")
        if output == engine:
            raise SystemExit("Refusing to overwrite the input without --apply")
        if output.exists():
            raise SystemExit(f"Refusing to overwrite existing output: {output}")
        output.write_text(patched, encoding="utf-8")
        diff = difflib.unified_diff(
            original.splitlines(), patched.splitlines(),
            fromfile=str(engine), tofile=str(output), lineterm="",
        )
        print("\n".join(diff))
        print(f"\nPatched copy written: {output}")
    print(f"Original SHA256: {old_sha}")
    print(f"Patched SHA256:  {new_sha}")
    print("This is fail-closed recovery hardening, not a fix for the underlying Metal timeout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
