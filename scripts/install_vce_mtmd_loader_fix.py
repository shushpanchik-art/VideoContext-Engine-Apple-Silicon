#!/usr/bin/env python3
"""Install a venv-scoped ctypes resolver fix for the bundled llama_cpp/lib/libmtmd.dylib."""
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

MODULE_CONTENT = '''"""Prefer llama-cpp-python's bundled libmtmd over system Homebrew ggml."""
import ctypes.util as _ctypes_util
import os as _os
# Do not let a stale environment override force a global/Homebrew mtmd dylib.
_os.environ.pop("MTMD_CPP_LIB", None)
_original_find_library = _ctypes_util.find_library
if not getattr(_original_find_library, "_vce_mtmd_loader_fix", False):
    def _vce_find_library(name):
        if str(name).lower() in {"mtmd", "libmtmd"}:
            return None
        return _original_find_library(name)
    _vce_find_library._vce_mtmd_loader_fix = True
    _ctypes_util.find_library = _vce_find_library
'''
PTH_CONTENT = "import vce_mtmd_loader_fix\n"

VERIFY_CODE = r"""
import ctypes.util
import json
import tempfile
from pathlib import Path
import llama_cpp
assert ctypes.util.find_library("mtmd") is None, "system find_library still resolves mtmd"
import llama_cpp.mtmd_cpp as mtmd_cpp
package_lib = Path(llama_cpp.__file__).resolve().parent / "lib" / "libmtmd.dylib"
loaded_lib = Path(str(mtmd_cpp._libmtmd._name)).resolve()
if not package_lib.is_file():
    raise SystemExit(f"Bundled mtmd dylib is missing: {package_lib}")
if loaded_lib != package_lib.resolve():
    raise SystemExit(f"Wrong mtmd dylib loaded: {loaded_lib}; expected {package_lib}")
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
    "mtmd_library_loaded": str(loaded_lib),
    "required_symbol": "mtmd_get_video_config",
    "qwen3vl_handler_constructor": "passed",
    "note": "A temporary placeholder validates import, symbol and handler constructor without downloading model weights."
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
        print("Installed venv-scoped mtmd resolver hook:", pth_path)
        print("Homebrew /opt/homebrew/lib/libmtmd.dylib was not modified.")

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
        print("STOP: bundled mtmd/Qwen3-VL ABI preflight failed.")
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
    print("Qwen3-VL mtmd ABI + handler constructor: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
