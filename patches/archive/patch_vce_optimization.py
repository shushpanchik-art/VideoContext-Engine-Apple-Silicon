#!/usr/bin/env python3
"""Apply two reversible test hooks to VideoContext Engine v3.19.

1. Preserve source-video aspect ratio when preparing frames for the VLM.
2. Add an optional CPU-only MLX benchmark switch via VIDEOCONTEXT_MLX_DEVICE=cpu.

Run from the folder containing VideoContextEngine_v3.19.py. The patcher makes a
backup first and refuses to modify files when expected source patterns are absent.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import shutil

TARGET = Path("VideoContextEngine_v3.19.py")

HELPER = '''def resize_keep_aspect(frame: np.ndarray, max_side: int) -> np.ndarray:
    """Resize a frame so its longest side is max_side, without distortion."""
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        return frame
    scale = float(max_side) / float(max(height, width))
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(frame, (new_width, new_height), interpolation=interpolation)


'''


def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: {TARGET.resolve()} not found.")
        print("Put this patch script in the project folder and run it there.")
        return 2

    source = TARGET.read_text(encoding="utf-8")
    original = source

    keyframe_old = "frame = cv2.resize(frame, (base_res, base_res))"
    keyframe_new = "frame = resize_keep_aspect(frame, base_res)"
    firstframe_old = "f_frame = cv2.resize(f_frame, resize_tuple)"
    firstframe_new = "f_frame = resize_keep_aspect(f_frame, vlm_resolution)"
    tuple_old = "resize_tuple = (vlm_resolution, vlm_resolution)"

    # Validate aspect-ratio patch patterns before editing.
    if keyframe_old not in source and keyframe_new not in source:
        print("ERROR: couldn't find the expected keyframe resize line.")
        print("No file changed. This patcher may not match your local version.")
        return 3
    if firstframe_old not in source and firstframe_new not in source:
        print("ERROR: couldn't find the expected first-frame resize line.")
        print("No file changed. This patcher may not match your local version.")
        return 3

    # Add the helper and redirect both VLM frame-resize paths.
    if "def resize_keep_aspect(" not in source:
        anchor = "def sample_keyframes_for_scene("
        if anchor not in source:
            print("ERROR: couldn't find sample_keyframes_for_scene() to insert helper.")
            print("No file changed.")
            return 3
        source = source.replace(anchor, HELPER + anchor, 1)

    source = source.replace(keyframe_old, keyframe_new, 1)
    source, tuple_count = re.subn(
        r"^[ \t]*resize_tuple = \(vlm_resolution, vlm_resolution\)\n",
        "",
        source,
        count=1,
        flags=re.MULTILINE,
    )
    if tuple_count != 1 and tuple_old in source:
        print("ERROR: found resize_tuple but could not remove it safely.")
        print("No file changed.")
        return 3
    source = source.replace(firstframe_old, firstframe_new, 1)

    # Add an opt-in CPU mode for controlled MLX/GPU-vs-CPU tests. Default 'auto'
    # leaves MLX behavior unchanged (GPU on Apple Silicon when available).
    if "VIDEOCONTEXT_MLX_DEVICE" not in source:
        pattern = re.compile(r"(?m)^([ \t]*)from mlx_vlm import load, generate[ \t]*$")
        matches = list(pattern.finditer(source))
        if len(matches) != 1:
            print("ERROR: expected exactly one 'from mlx_vlm import load, generate' line,")
            print(f"but found {len(matches)}. No file changed.")
            return 3
        match = matches[0]
        indent = match.group(1)
        sub = indent + "    "
        replacement = (
            f'{indent}device_override = os.environ.get("VIDEOCONTEXT_MLX_DEVICE", "auto").strip().lower()\n'
            f'{indent}if device_override == "cpu":\n'
            f'{sub}import mlx.core as mx\n'
            f'{sub}mx.set_default_device(mx.cpu)\n'
            f'{sub}print("🧮 [MLX] VLM device forced to CPU (benchmark mode)")\n'
            f'{indent}elif device_override != "auto":\n'
            f'{sub}raise ValueError("VIDEOCONTEXT_MLX_DEVICE must be auto or cpu")\n'
            f'{indent}from mlx_vlm import load, generate'
        )
        source = source[:match.start()] + replacement + source[match.end():]

    if source == original:
        print("Both optimizations already appear to be applied; no changes made.")
        return 0
    if keyframe_old in source or firstframe_old in source or tuple_old in source:
        print("ERROR: some square-resize patterns remain; refusing to overwrite the file.")
        return 4
    if "VIDEOCONTEXT_MLX_DEVICE" not in source:
        print("ERROR: CPU benchmark switch wasn't inserted; refusing to overwrite the file.")
        return 4

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = TARGET.with_name(f"{TARGET.name}.before_optimization_{timestamp}.bak")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(source, encoding="utf-8")

    print("Optimization test hooks applied successfully.")
    print(f"Backup: {backup.resolve()}")
    print("- Video frames keep their original aspect ratio.")
    print("- Default MLX device behavior is unchanged (auto / GPU when available).")
    print("- For a CPU-only benchmark, launch with VIDEOCONTEXT_MLX_DEVICE=cpu.")
    print("Next quality trial: vlm_resolution=1440, keyframes_per_scene=2; keep other VLM settings fixed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
