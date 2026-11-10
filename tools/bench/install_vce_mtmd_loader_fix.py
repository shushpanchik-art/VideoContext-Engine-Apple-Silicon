#!/usr/bin/env python3
"""Install a venv-scoped resolver fix that keeps llama, ggml and mtmd libraries on one build."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from datetime import datetime
from pathlib import Path

MODULE_CONTENT = '''"""Prefer the venv-bundled llama.cpp native libraries over Homebrew copies."""
import ctypes.util as _ctypes_util
import os as _os

# These overrides can force a different build than the Python handlers.
_os.environ.pop("MTMD_CPP_LIB", None)
_os.environ.pop("LLAMA_CPP_LIB_PATH", None)

# llama-cpp-python's loader calls ctypes.util.find_library BEFORE checking its
# own package/lib directory. Avoid pairing Homebrew libllama with the venv's
# libmtmd (or mixing ggml versions).
_VCE_BUNDLED_NATIVE_NAMES = {
    "mtmd", "llama", "ggml", "ggml-base", "ggml-metal", "ggml-cpu",
}
_original_find_library = _ctypes_util.find_library
if not getattr(_original_find_library, "_vce_native_library_fix", False):
    def _vce_find_library(name):
        normalized = str(name).lower()
        if normalized.startswith("lib"):
            normalized = normalized[3:]
        if normalized in _VCE_BUNDLED_NATIVE_NAMES:
            return None
        return _original_find_library(name)
    _vce_find_library._vce_native_library_fix = True
    _ctypes_util.find_library = _vce_find_library
'''
PTH_CONTENT = "import vce_mtmd_loader_fix\n"

VERIFY_CODE = r"""
import ctypes.util
import json
import tempfile
from pathlib import Path
import llama_cpp
import llama_cpp.llama_cpp as llama_core

blocked_names = ("mtmd", "llama", "ggml", "ggml-base")
still_resolved = {name: ctypes.util.find_library(name) for name in blocked_names if ctypes.util.find_library(name) is not None}
if still_resolved:
    raise SystemExit("System resolver still exposes core llama.cpp libraries: " + repr(still_resolved))

import llama_cpp.mtmd_cpp as mtmd_cpp
package_lib_dir = (Path(llama_cpp.__file__).resolve().parent / "lib").resolve()
loaded_libraries = {
    "llama": Path(str(llama_core._lib._name)).resolve(),
    "mtmd": Path(str(mtmd_cpp._libmtmd._name)).resolve(),
}
for name, loaded_path in loaded_libraries.items():
    if loaded_path.parent != package_lib_dir:
        raise SystemExit(
            f"Mixed llama.cpp native libraries: {name} loaded from {loaded_path}; "
            f"expected the same venv bundle directory {package_lib_dir}. "
            "Rebuild/reinstall the Qwen3-VL llama-cpp-python fork in this venv."
        )
    if not loaded_path.is_file():
        raise SystemExit(f"Loaded {name} library path is not a file: {loaded_path}")

expected_mtmd = package_lib_dir / "libmtmd.dylib"
if not expected_mtmd.is_file():
    raise SystemExit(f"Bundled mtmd dylib is missing: {expected_mtmd}")
if loaded_libraries["mtmd"] != expected_mtmd.resolve():
    raise SystemExit(f"Wrong mtmd dylib loaded: {loaded_libraries['mtmd']}; expected {expected_mtmd}")
getattr(mtmd_cpp._libmtmd, "mtmd_get_video_config")

from llama_cpp.llama_multimodal import Qwen3VLChatHandler
with tempfile.NamedTemporaryFile(suffix=".gguf") as dummy:
    handler = Qwen3VLChatHandler(mmproj_path=dummy.name, verbose=False)
    assert handler._mtmd_cpp is mtmd_cpp
    handler.close()

print(json.dumps({
    "status": "passed",
    "python": __import__("sys").executable,
    "llama_cpp_package": str(Path(llama_cpp.__file__).resolve()),
    "native_library_directory": str(package_lib_dir),
    "native_libraries_loaded": {key: str(value) for key, value in loaded_libraries.items()},
    "required_symbol": "mtmd_get_video_config",
    "qwen3vl_handler_constructor": "passed",
    "note": "This validates a coherent venv-native library pair and Python handler ABI; it does not parse the real mmproj/model weights."
}, ensure_ascii=False))
"""


def atomic_write(path: Path, content: str, backup_dir: Path) -> str | None:
    backup_name = None
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="replace")
        if old == content:
            return None
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{path.name}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(path, backup)
        backup_name = str(backup)
    tmp = path.with_name(path.name + ".vce-tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return backup_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", default=str(Path.home() / "Downloads" / "VCE_mtmd_fix_backups"))
    parser.add_argument("--report", default=None)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("STOP: this installer is for macOS.")
        return 2

    purelib = Path(sysconfig.get_paths()["purelib"])
    module_path = purelib / "vce_mtmd_loader_fix.py"
    pth_path = purelib / "vce_mtmd_loader_fix.pth"
    backup_dir = Path(args.backup_dir).expanduser()
    report_path = Path(args.report).expanduser() if args.report else None
    backups = {}

    if not args.verify_only:
        backups = {
            "module": atomic_write(module_path, MODULE_CONTENT, backup_dir),
            "pth": atomic_write(pth_path, PTH_CONTENT, backup_dir),
        }
        print("Installed venv-scoped llama.cpp native-library resolver hook:", pth_path)
        print("Homebrew libraries are not modified; core llama.cpp libraries must resolve from this venv bundle.")

    result = subprocess.run(
        [sys.executable, "-c", VERIFY_CODE],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0:
        message = {
            "status": "failed", "python": sys.executable, "purelib": str(purelib),
            "error": result.stderr.strip() or result.stdout.strip(), "backups": backups,
        }
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(message, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("STOP: coherent bundled llama.cpp/ggml/mtmd ABI preflight failed.")
        print(message["error"])
        return 3

    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        payload = {"status": "passed", "stdout": result.stdout.strip()}
    payload.update({"purelib": str(purelib), "backups": backups})
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Qwen3-VL native library pairing + mtmd ABI + handler import: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
