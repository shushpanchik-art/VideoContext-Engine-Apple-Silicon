#!/usr/bin/env python3
"""Verify MLX GPU/Metal runtime availability and model package presence."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mlx_metal_runtime import configure_mlx_gpu  # noqa: E402


def package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    modules = {
        "mlx": "mlx",
        "mlx-vlm": "mlx_vlm",
        "mlx-lm (optional)": "mlx_lm",
        "openai-whisper": "whisper",
    }
    result = {
        "python": sys.executable,
        "python_version": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "packages": {
            dist: {
                "installed": importlib.util.find_spec(module) is not None,
                "version": package_version(dist),
            }
            for dist, module in modules.items()
        },
    }
    try:
        result["mlx_gpu_runtime"] = configure_mlx_gpu()
    except Exception as exc:
        result["mlx_gpu_runtime_error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    missing = [
        dist
        for dist in ("mlx-vlm", "openai-whisper")
        if not result["packages"][dist]["installed"]
    ]
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
