#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the non-experimental Apple-Silicon profile and real PyTorch/MPS Whisper path.

The patch is intentionally narrow and idempotent. It preserves target-Mac local
runtime hardening (including the reviewed Metal latch), creates timestamped
backups, validates all three files before writing, and keeps user prompts and
unrelated settings intact.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

MARKER = "VCE_PYTORCH_WHISPER_MPS_SERIAL_V1"
UI_MARKER = "VCE_FAST_MPS_PROFILE_UI_V1"
LLAMA_UI_MARKER = "VCE_FAST_MPS_LLAMA_CPP_UI_V1"
LLAMA_MARKER = "VCE_LLAMA_CPP_METAL_SHARED_MODEL_V1"
LLAMA_VLM_MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M"
LLAMA_TEXT_MODEL_ID = "gguf/Qwen3.5-9B-GGUF:Q4_K_M"

PROFILE: dict[str, Any] = {
    "analysis_mode": "full",
    "vlm_model": LLAMA_VLM_MODEL_ID,
    "whisper_model": "large",
    "text_llm_enabled": True,
    "text_llm_model": LLAMA_TEXT_MODEL_ID,
    "text_llm_max_tokens": 4096,
    "vlm_resolution": 1280,
    "scene_threshold": 0.15,
    "min_scene_duration": 1.5,
    "max_scene_duration": 20,
    "keyframes_per_scene": 1,
    "vlm_max_tokens_scene": 1024,
    "vlm_max_tokens_summary": 2048,
    "whisper_acceleration": True,
    "enable_apple_ocr": True,
    "enable_ffmpeg_ocr": True,
    "ffmpeg_ocr_interval": 5,
    "ffmpeg_ocr_workers": 3,
    "enable_ocr_cache": True,
    "enable_audio_features": True,
    "generate_summary": True,
    "generate_txt": True,
    "generate_json": True,
    "word_timestamps": True,
    "enable_stage_timing": True,
    "skip_audio": False,
    "skip_visual": False,
    "ram_mode": "ram-",
    "response_format": "json",
}

REMOVED_SETTINGS = {
    "enable_adaptive_keyframes",
    "enable_mlx_whisper",
    "enable_fast_scene_detection",
    "mlx_device",
}

OLD_WHISPER_BLOCK = '''def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Whisper is pinned to MLX CPU stream to overlap with MLX/Metal inference."""
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Return an mlx-whisper adapter; MLX model weights load inside the CPU stream."""
    return _VCEWhisperMLXCPU(model_name)
'''

NEW_WHISPER_BLOCK = '''# VCE_PYTORCH_WHISPER_MPS_SERIAL_V1
def resolve_whisper_device(acceleration_enabled: bool = True) -> str:
    """Choose PyTorch MPS on Apple Silicon; never silently fall back from requested MPS."""
    if not acceleration_enabled:
        return "cpu"
    is_apple_silicon = (
        platform.system() == "Darwin"
        and platform.machine().lower() in {"arm64", "aarch64"}
    )
    if is_apple_silicon:
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        available = bool(mps_backend is not None and mps_backend.is_available())
        if available and hasattr(mps_backend, "is_built"):
            available = bool(mps_backend.is_built())
        if not available:
            raise RuntimeError(
                "PyTorch MPS was requested on Apple Silicon but is unavailable. "
                "Refusing to silently run Whisper on CPU."
            )
        return "mps"
    if bool(getattr(torch, "cuda", None)) and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_whisper_model(model_name: str, acceleration_enabled: bool = True):
    """Load openai-whisper explicitly on the resolved PyTorch device."""
    device = resolve_whisper_device(acceleration_enabled)
    print(f"[VCE Whisper] backend=openai-whisper device={device} model={model_name}", flush=True)
    model = whisper.load_model(model_name, device=device)
    actual_device = getattr(model, "device", None)
    if actual_device is None:
        try:
            actual_device = next(model.parameters()).device
        except Exception as exc:
            raise RuntimeError(
                "Cannot verify the actual device of the loaded openai-whisper model."
            ) from exc
    actual_type = getattr(actual_device, "type", str(actual_device).split(":", 1)[0])
    if str(actual_type) != device:
        raise RuntimeError(
            f"Whisper device mismatch: requested {device}, model reports {actual_type}."
        )
    VCE_BACKEND_DIAGNOSTICS["whisper"] = {
        "backend": "openai-whisper",
        "device": device,
        "model": model_name,
        "device_verified": True,
    }
    return model
'''

OLD_FFMPEG_SCENE_BRANCH = '''        if enable_fast_scene_detection:
            scenes_raw = await asyncio.to_thread(
                _vce_detect_scenes_fast,
                temp_path,
                threshold=scene_threshold,
                min_duration=min_scene_duration,
                max_duration=max_scene_duration,
            )
        else:
            scenes_raw = await asyncio.to_thread(
                detect_scenes,
                temp_path,
                threshold=scene_threshold,
                min_duration=min_scene_duration,
                max_duration=max_scene_duration,
            )'''

NEW_FFMPEG_SCENE_BRANCH = '''        scenes_raw = await asyncio.to_thread(
            detect_scenes,
            temp_path,
            threshold=scene_threshold,
            min_duration=min_scene_duration,
            max_duration=max_scene_duration,
        )'''

OLD_WHISPER_CLEANUP = '''                        if RAM_MODE == "ram-":
                            if hasattr(w_model, "release"):
                                await asyncio.to_thread(w_model.release)
                            del w_model
                            gc.collect()'''

NEW_WHISPER_CLEANUP = '''                        # MPS and MLX/Metal share Apple unified memory. Release Whisper
                        # before loading the VLM, even if the previous RAM mode was ram+.
                        if WHISPER_CACHE.get(target_whisper) is w_model:
                            WHISPER_CACHE.pop(target_whisper, None)
                        del w_model
                        gc.collect()
                        if whisper_device == "mps":
                            try:
                                await asyncio.to_thread(torch.mps.synchronize)
                                await asyncio.to_thread(torch.mps.empty_cache)
                            except Exception as release_error:
                                print(
                                    "[VCE Whisper] MPS cache release warning: "
                                    + type(release_error).__name__,
                                    flush=True,
                                )'''

def _ensure_threading_alias_for_local_runtime(source: str) -> str:
    """Keep/restore the alias when local Metal runtime code still uses it."""
    alias_import = "import threading as _vce_threading"
    if re.search(r"(?m)^import threading as _vce_threading\s*$", source):
        return source
    if not re.search(r"\b_vce_threading\b", source):
        return source

    tree = ast.parse(source)
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    for node in tree.body:
        if isinstance(node, ast.Import) and any(alias.name == "threading" for alias in node.names):
            insert_at = offsets[node.end_lineno]
            return source[:insert_at] + alias_import + "\n" + source[insert_at:]

    raise ValueError(
        "Local runtime references _vce_threading but has no top-level threading import; "
        "no files were written."
    )


def _line_numbers_containing(source: str, token: str) -> list[int]:
    """Return 1-based source line numbers containing a token, without echoing source text."""
    return [
        line_number
        for line_number, line in enumerate(source.splitlines(), start=1)
        if token in line
    ]


def _replace_exact(source: str, old: str, new: str, label: str, expected: int = 1) -> str:
    count = source.count(old)
    if count != expected:
        raise ValueError(
            f"{label}: expected {expected} exact match(es), found {count}; no files were written."
        )
    return source.replace(old, new, expected)


def _replace_one_of_exact(
    source: str,
    variants: tuple[tuple[str, str], ...],
    label: str,
) -> str:
    """Apply exactly one known source-layout variant; reject absent/ambiguous anchors."""
    match_count = sum(source.count(old) for old, _new in variants)
    if match_count != 1:
        raise ValueError(
            f"{label}: expected exactly 1 supported exact anchor match, found {match_count}; "
            "no files were written."
        )
    for old, new in variants:
        if old in source:
            return source.replace(old, new, 1)
    raise AssertionError("exact anchor count changed unexpectedly")


def _remove_ast_nodes(source: str, names: set[str]) -> str:
    """Remove named top-level functions/classes/assignments without touching adjacent code."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans: list[tuple[int, int]] = []
    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    break
        if name in names:
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno)
            start = offsets[start_line - 1]
            end = offsets[end_line]
            # Consume one following blank line for clean formatting.
            while end < len(source) and source[end] in "\r\n":
                end += 1
            spans.append((start, end))
    if names - {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    } - {
        target.id
        for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }:
        missing = sorted(
            names - {
                node.name for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            } - {
                target.id
                for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(target, ast.Name)
            }
        )
        raise ValueError("Expected obsolete runtime definition(s) missing: " + ", ".join(missing))
    for start, end in sorted(spans, reverse=True):
        source = source[:start] + source[end:]
    return source



def _serialize_whisper_before_vlm(source: str) -> str:
    """Await the single Whisper task at its creation site, preserving intervening guards."""
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]+)_vce_whisper_task = "
        r"asyncio\.create_task\(_vce_run_whisper_mps_stage\(\)\)[ \t]*$"
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            "Sequential Whisper-before-VLM scheduling: expected exactly one "
            f"Whisper task creation, found {len(matches)}; no files were written."
        )
    match = matches[0]
    indent = match.group("indent")
    line_start = source.rfind("\n", 0, match.start()) + 1
    prefix = source[:line_start]
    parent_indent = indent[:-4]
    if not re.search(
        r"(?m)^" + re.escape(parent_indent) + r"if not skip_audio:\s*$",
        prefix,
    ):
        raise ValueError(
            "Whisper task creation is not directly governed by the expected "
            "'if not skip_audio' block; refusing to patch."
        )
    insertion = (
        "\n" + indent
        + "# Await ASR before loading Qwen: MPS Whisper and MLX/Metal VLM\n"
        + indent + "# are intentionally serialized on the shared Apple GPU.\n"
        + indent + "whisper_segments = await _vce_whisper_task"
    )
    result = source[:match.end()] + insertion + source[match.end():]
    await_pos = result.find("whisper_segments = await _vce_whisper_task", match.start())
    vlm_pos = result.find("if run_vlm:", await_pos)
    if await_pos < 0 or vlm_pos < 0 or await_pos >= vlm_pos:
        raise ValueError("Whisper await/VLM ordering postcondition failed.")
    ast.parse(result)
    return result

def _remove_optional_ast_nodes(source: str, names: set[str]) -> str:
    """Remove optional top-level definitions/imports, rejecting duplicate names."""
    tree = ast.parse(source)
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    matches: dict[str, list[ast.AST]] = {name: [] for name in names}
    for node in tree.body:
        node_names: set[str] = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node_names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            node_names.update(t.id for t in targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.Import):
            node_names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            node_names.update(alias.asname or alias.name for alias in node.names)
        for name in node_names & names:
            matches[name].append(node)
    ambiguous = sorted(name for name, nodes in matches.items() if len(nodes) > 1)
    if ambiguous:
        raise ValueError("Optional runtime definition is ambiguous: " + ", ".join(ambiguous))
    spans: list[tuple[int, int]] = []
    for nodes in matches.values():
        if not nodes:
            continue
        node = nodes[0]
        start = offsets[node.lineno - 1]
        end = offsets[getattr(node, "end_lineno", node.lineno)]
        while end < len(source) and source[end] in "\r\n":
            end += 1
        spans.append((start, end))
    for start, end in sorted(spans, reverse=True):
        source = source[:start] + source[end:]
    return source


def _replace_top_level_definition(source: str, name: str, replacement: str) -> str:
    tree = ast.parse(source)
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one top-level definition {name}, found {len(matches)}.")
    node = matches[0]
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    start = offsets[node.lineno - 1]
    end = offsets[node.end_lineno]
    while end < len(source) and source[end] in "\r\n":
        end += 1
    return source[:start] + replacement.rstrip() + "\n\n" + source[end:]


def _insert_after_top_level_definition(source: str, name: str, addition: str) -> str:
    tree = ast.parse(source)
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one top-level definition {name}, found {len(matches)}.")
    node = matches[0]
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    insert_at = offsets[node.end_lineno]
    return source[:insert_at] + "\n\n" + addition.rstrip() + "\n" + source[insert_at:]


LLAMA_SERVER_RUNTIME = r'''
# VCE_LLAMA_CPP_METAL_SHARED_MODEL_V1
VCE_BACKEND_DIAGNOSTICS: Dict[str, Dict[str, Any]] = globals().get("VCE_BACKEND_DIAGNOSTICS", {})
_VCE_LLAMA_VLM_MODEL = LLAMA_VLM_MODEL_ID
_VCE_LLAMA_TEXT_MODEL = LLAMA_TEXT_MODEL_ID
_VCE_LLAMA_ALLOWED_MODELS = {_VCE_LLAMA_VLM_MODEL, _VCE_LLAMA_TEXT_MODEL}
_VCE_LLAMA_SERVER_HOST = "127.0.0.1"
_VCE_LLAMA_SERVER_PORT = int(os.environ.get("VCE_LLAMA_SERVER_PORT", "7556"))
_VCE_LLAMA_SERVER_URL = f"http://{_VCE_LLAMA_SERVER_HOST}:{_VCE_LLAMA_SERVER_PORT}"
_VCE_LLAMA_SERVER_PROCESS = None
_VCE_LLAMA_SERVER_ACTIVE_MODEL: Optional[str] = None
_VCE_LLAMA_SERVER_LOCK = threading.RLock()


def _vce_llama_server_health(timeout: float = 2.0) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(_VCE_LLAMA_SERVER_URL + "/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _vce_stop_llama_server_locked() -> None:
    """Stop the managed model process while the re-entrant server lock is held."""
    global _VCE_LLAMA_SERVER_PROCESS, _VCE_LLAMA_SERVER_ACTIVE_MODEL
    process = _VCE_LLAMA_SERVER_PROCESS
    model = _VCE_LLAMA_SERVER_ACTIVE_MODEL
    _VCE_LLAMA_SERVER_PROCESS = None
    _VCE_LLAMA_SERVER_ACTIVE_MODEL = None
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except Exception:
        process.kill()
        try:
            process.wait(timeout=5)
        except Exception:
            pass
    print(
        "[VCE backend] llama.cpp Metal server stopped; model memory released"
        + (": " + str(model) if model else ""),
        flush=True,
    )


def _vce_stop_llama_server() -> None:
    with _VCE_LLAMA_SERVER_LOCK:
        _vce_stop_llama_server_locked()


def _vce_ensure_llama_server(model_path: str) -> None:
    """Start/switch one local llama.cpp Metal server between the preserved VLM and Text LLM GGUFs."""
    global _VCE_LLAMA_SERVER_PROCESS, _VCE_LLAMA_SERVER_ACTIVE_MODEL
    model_path = str(model_path)
    if model_path not in _VCE_LLAMA_ALLOWED_MODELS:
        raise ValueError(
            "This VCE profile only permits the reviewed VLM/Text GGUFs: "
            + ", ".join(sorted(_VCE_LLAMA_ALLOWED_MODELS))
            + "; refusing to start an unreviewed model."
        )

    with _VCE_LLAMA_SERVER_LOCK:
        if _VCE_LLAMA_SERVER_PROCESS is not None:
            if _VCE_LLAMA_SERVER_PROCESS.poll() is not None:
                code = _VCE_LLAMA_SERVER_PROCESS.returncode
                _VCE_LLAMA_SERVER_PROCESS = None
                _VCE_LLAMA_SERVER_ACTIVE_MODEL = None
                raise RuntimeError(f"llama-server exited unexpectedly with code {code}.")

            if _VCE_LLAMA_SERVER_ACTIVE_MODEL == model_path:
                deadline = time.monotonic() + 900.0
                while time.monotonic() < deadline:
                    if _vce_llama_server_health():
                        return
                    if _VCE_LLAMA_SERVER_PROCESS.poll() is not None:
                        code = _VCE_LLAMA_SERVER_PROCESS.returncode
                        _VCE_LLAMA_SERVER_PROCESS = None
                        _VCE_LLAMA_SERVER_ACTIVE_MODEL = None
                        raise RuntimeError(f"llama-server exited during startup with code {code}.")
                    time.sleep(0.25)
                raise RuntimeError("The managed llama-server process is alive but its health endpoint is not ready.")

            # VLM and Text LLM remain their original model families. They share
            # the Metal server binary, but are loaded one at a time to limit memory.
            _vce_stop_llama_server_locked()

        if _vce_llama_server_health():
            raise RuntimeError(
                f"Port {_VCE_LLAMA_SERVER_PORT} is occupied by an unmanaged llama-server. "
                "Stop that process before starting VCE; refusing to attach to an unknown model."
            )

        executable = shutil.which("llama-server")
        if not executable:
            raise RuntimeError(
                "llama-server is not installed. Install the Metal-enabled llama.cpp package "
                "with brew install llama.cpp, then restart VCE."
            )
        try:
            probe = subprocess.run(
                [executable, "--list-devices"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:
            raise RuntimeError("Could not verify llama-server Metal support: " + type(exc).__name__) from exc
        devices = (probe.stdout or "") + "\n" + (probe.stderr or "")
        if probe.returncode != 0 or "metal" not in devices.lower():
            raise RuntimeError(
                "The installed llama-server does not advertise a Metal device. "
                "Install/rebuild llama.cpp with macOS Metal support; CPU fallback is disabled."
            )

        command = [
            executable,
            "-hf", model_path,
            "--host", _VCE_LLAMA_SERVER_HOST,
            "--port", str(_VCE_LLAMA_SERVER_PORT),
            "--n-gpu-layers", "all",
            "--ctx-size", "32768",
            "--parallel", "1",
            "--no-webui",
            "--chat-template-kwargs", '{"enable_thinking":false}',
        ]
        _VCE_LLAMA_SERVER_PROCESS = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
        )
        _VCE_LLAMA_SERVER_ACTIVE_MODEL = model_path
        deadline = time.monotonic() + 900.0
        while time.monotonic() < deadline:
            if _vce_llama_server_health():
                stage = "vlm" if model_path == _VCE_LLAMA_VLM_MODEL else "text_llm"
                VCE_BACKEND_DIAGNOSTICS["llama_cpp_" + stage] = {
                    "backend": "llama.cpp",
                    "device": "Metal",
                    "model": model_path,
                    "server_url": _VCE_LLAMA_SERVER_URL,
                    "vision_projector": "auto",
                    "thinking": False,
                }
                print(
                    "[VCE backend] llama.cpp server ready; backend=llama.cpp; device=Metal; "
                    "stage=" + stage + "; model=" + model_path,
                    flush=True,
                )
                return
            if _VCE_LLAMA_SERVER_PROCESS.poll() is not None:
                code = _VCE_LLAMA_SERVER_PROCESS.returncode
                _VCE_LLAMA_SERVER_PROCESS = None
                _VCE_LLAMA_SERVER_ACTIVE_MODEL = None
                raise RuntimeError(f"llama-server failed during model/vision-projector loading (exit {code}).")
            time.sleep(1.0)

        process = _VCE_LLAMA_SERVER_PROCESS
        _VCE_LLAMA_SERVER_PROCESS = None
        _VCE_LLAMA_SERVER_ACTIVE_MODEL = None
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except Exception:
                process.kill()
        raise RuntimeError(
            "llama-server did not become healthy within 15 minutes while downloading/loading "
            "the GGUF model and its vision projector."
        )


def _vce_llama_chat_completion(
    model_path: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float = 0.0,
    stream: bool = False,
    progress_callback: Optional[Any] = None,
) -> str:
    """Call llama-server's local OpenAI-compatible chat endpoint for the requested stage model."""
    import urllib.error
    import urllib.request

    _vce_ensure_llama_server(model_path)
    payload = {
        "model": str(model_path),
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": bool(stream),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        _VCE_LLAMA_SERVER_URL + "/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            if not stream:
                body = json.loads(response.read().decode("utf-8"))
                content = body["choices"][0]["message"].get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        str(item.get("text", "")) for item in content
                        if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
                    )
                return str(content or "").strip()

            chunks: list[str] = []
            started = time.monotonic()
            reported_tokens = 0
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if isinstance(delta, list):
                    delta = "".join(
                        str(item.get("text", "")) for item in delta
                        if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
                    )
                if isinstance(delta, str) and delta:
                    chunks.append(delta)
                    token_estimate = max(reported_tokens, max(1, sum(len(part) for part in chunks) // 4))
                    if token_estimate > reported_tokens:
                        reported_tokens = token_estimate
                    elapsed = max(0.001, time.monotonic() - started)
                    if progress_callback:
                        progress_callback("generating", reported_tokens, reported_tokens / elapsed)
            return "".join(chunks).strip()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
        except Exception:
            pass
        raise RuntimeError(f"llama-server HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError("llama-server request failed: " + str(exc)[:1000]) from exc


class LlamaServerEngine(VLMProvider):
    """Original Qwen3-VL GGUF model served through llama.cpp Metal."""
    def load_model(self, model_path: str):
        if str(model_path) != _VCE_LLAMA_VLM_MODEL:
            raise ValueError("VLM stage is pinned to " + _VCE_LLAMA_VLM_MODEL)
        if self.current_model_path == model_path and _VCE_LLAMA_SERVER_ACTIVE_MODEL == model_path and _vce_llama_server_health():
            self.last_load_time = 0.0
            return
        started = time.time()
        _vce_ensure_llama_server(model_path)
        self.current_model_path = model_path
        self.last_load_time = time.time() - started
        print(
            f"[VCE VLM] backend=llama.cpp device=Metal model={model_path} "
            f"server_start={self.last_load_time:.2f}s",
            flush=True,
        )

    def describe_scene(self, images: List[Image.Image], prompt: str, max_tokens: int) -> str:
        if self.current_model_path != _VCE_LLAMA_VLM_MODEL:
            self.load_model(_VCE_LLAMA_VLM_MODEL)
        content: list[dict[str, Any]] = []
        for image in images:
            buffered = BytesIO()
            image.save(buffered, format="JPEG", quality=88)
            encoded = base64.b64encode(buffered.getvalue()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + encoded},
            })
        content.append({"type": "text", "text": prompt})
        return _vce_llama_chat_completion(
            _VCE_LLAMA_VLM_MODEL,
            [{"role": "user", "content": content}],
            max_tokens=max_tokens,
            temperature=0.0,
            stream=False,
        )

    def unload_model(self):
        # Release the VLM checkpoint before the distinct Text LLM checkpoint is loaded.
        self.current_model_path = None
        _vce_stop_llama_server()
        gc.collect()


def get_vlm_engine() -> VLMProvider:
    return LlamaServerEngine()
'''


def _patch_text_llm_to_llama_server(source: str) -> str:
    tree = ast.parse(source)
    node_matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_text_llm_postprocess"
    ]
    if len(node_matches) != 1:
        raise ValueError(
            "Text LLM backend patch: expected one run_text_llm_postprocess function, "
            f"found {len(node_matches)}."
        )
    node = node_matches[0]
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    start, end = offsets[node.lineno - 1], offsets[node.end_lineno]
    function_source = source[start:end]

    old_head = '''    import gc
    _vce_configure_mlx_metal("text_llm")
    from mlx_vlm import load, generate
    try:
        from mlx_vlm.generate import stream_generate
    except Exception:
        stream_generate = None
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    model = processor = config = None
'''
    function_source = _replace_exact(
        function_source, old_head, "    import gc\n    model = processor = config = None\n",
        "Text LLM MLX imports"
    )
    function_source = _replace_exact(
        function_source,
        '''        model, processor = load(model_path)
        config = load_config(model_path)
''',
        "        _vce_ensure_llama_server(model_path)\n",
        "Text LLM llama.cpp server startup"
    )
    function_source = _replace_exact(
        function_source,
        '''        try:
            # For this constrained extraction task, avoid spending time on long reasoning output.
            formatted_prompt = apply_chat_template(processor, config, instruction, num_images=0, enable_thinking=True)
        except TypeError:
            formatted_prompt = apply_chat_template(processor, config, instruction, num_images=0)
''',
        "",
        "Text LLM MLX prompt template"
    )

    stream_start = function_source.find('        raw = ""\n        if stream_generate is not None:')
    parse_start = function_source.find('        try:\n            parsed = _text_llm_parse_json(raw)', stream_start)
    if stream_start < 0 or parse_start < 0 or parse_start <= stream_start:
        raise ValueError("Text LLM generation block no longer matches the reviewed MLX source layout.")
    stream_replacement = '''        raw = _vce_llama_chat_completion(
            model_path,
            [{"role": "user", "content": instruction}],
            max_tokens=generation_limit,
            temperature=0.1,
            stream=True,
            progress_callback=progress_callback,
        )
'''
    function_source = function_source[:stream_start] + stream_replacement + function_source[parse_start:]

    repair_start = function_source.find('                try:\n                    repair_prompt = apply_chat_template')
    repair_end = function_source.find('                if not isinstance(repaired_raw, str):', repair_start)
    if repair_start < 0 or repair_end < 0 or repair_end <= repair_start:
        raise ValueError("Text LLM repair block no longer matches the reviewed MLX source layout.")
    repair_replacement = '''                repaired_raw = _vce_llama_chat_completion(
                    model_path,
                    [{"role": "user", "content": repair_instruction}],
                    max_tokens=4096,
                    temperature=0.0,
                    stream=False,
                )
'''
    function_source = function_source[:repair_start] + repair_replacement + function_source[repair_end:]

    function_source = _replace_exact(
        function_source,
        '''        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass
''',
        "        gc.collect()\n",
        "Text LLM MLX cache cleanup"
    )
    ast.parse(function_source)
    return source[:start] + function_source + source[end:]


def _assert_no_mlx_runtime(source: str) -> None:
    tree = ast.parse(source)
    bad_imports = []
    bad_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad_imports.extend(
                alias.name for alias in node.names
                if alias.name == "mlx" or alias.name.startswith("mlx.")
                or alias.name == "mlx_vlm" or alias.name.startswith("mlx_vlm.")
                or alias.name == "mlx_whisper" or alias.name.startswith("mlx_whisper.")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                module == "mlx" or module.startswith("mlx.")
                or module == "mlx_vlm" or module.startswith("mlx_vlm.")
                or module == "mlx_whisper" or module.startswith("mlx_whisper.")
            ):
                bad_imports.append(module)
        elif isinstance(node, ast.Name) and (
            node.id == "MLXEngine" or node.id == "LlamaCppEngine"
            or node.id.startswith("_vce_mlx")
        ):
            bad_names.append(node.id)
    if bad_imports or bad_names:
        details = sorted(set(bad_imports + bad_names))
        raise ValueError("MLX runtime remains after backend migration: " + ", ".join(details))
    for forbidden in (
        "mlx-community/",
        "MLXEngine",
        "LlamaCppEngine",
        "_vce_mlx_run",
        "mlx_vlm",
        "mlx_whisper",
        "import mlx",
    ):
        if forbidden in source:
            raise ValueError("MLX runtime/model reference remains after backend migration: " + forbidden)


def _patch_engine_to_llama_cpp(source: str) -> str:
    if LLAMA_MARKER in source:
        required = (
            "class LlamaServerEngine(VLMProvider):",
            "_vce_ensure_llama_server",
            "_vce_llama_chat_completion",
            'LLAMA_VLM_MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M"',
            'LLAMA_TEXT_MODEL_ID = "gguf/Qwen3.5-9B-GGUF:Q4_K_M"',
            "VCE_BACKEND_DIAGNOSTICS",
        )
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("llama.cpp marker exists but required code is incomplete: " + ", ".join(missing))
        _assert_no_mlx_runtime(source)
        return source

    source = source.replace("VCE_MLX_RUNTIME_DIAGNOSTICS", "VCE_BACKEND_DIAGNOSTICS")
    source = source.replace("await _vce_mlx_run(", "await asyncio.to_thread(")

    source = _replace_top_level_definition(
        source,
        "_vce_clear_metal_memory_caches",
        '''def _vce_clear_metal_memory_caches():
    """Best-effort cleanup of PyTorch MPS cache and Python objects after an OOM."""
    try:
        gc.collect()
    except Exception:
        pass
    try:
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            try:
                torch.mps.synchronize()
            except Exception:
                pass
            torch.mps.empty_cache()
    except Exception:
        pass
    try:
        gc.collect()
    except Exception:
        pass''',
    )

    source = _replace_top_level_definition(
        source,
        "release_accelerator_memory",
        '''def release_accelerator_memory(stage: str = "") -> None:
    """Release unused PyTorch MPS allocator cache between inference stages."""
    gc.collect()
    try:
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            try:
                torch.mps.synchronize()
            except Exception:
                pass
            torch.mps.empty_cache()
    except Exception as exc:
        print(f"⚠️ Не удалось очистить кэш PyTorch MPS ({stage}): {exc}")
    gc.collect()''',
    )

    source = _remove_optional_ast_nodes(
        source,
        {
            "MLXEngine",
            "LlamaCppEngine",
            "get_vlm_engine",
            "_vce_mlx_run",
            "_vce_mlx_trace_stream",
            "_vce_mlx_trace_runtime",
            "_vce_mlx_invoke_traced",
            "_vce_prepare_mlx_vlm_runtime",
            "_vce_configure_mlx_metal",
            "_vce_mlx_whisper_model_id",
            "_VCEWhisperMLXCPU",
            "_vce_validate_mlx_whisper_model",
            "_vce_load_whisper_model",
            "_VCE_MLX_EXECUTOR",
            "_VCE_MLX_WHISPER_LOCK",
            "_VCE_MLX_WHISPER_REPOS",
            "DEFAULT_VLM_MODEL_MLX",
            "DEFAULT_VLM_REPO_GGUF",
            "DEFAULT_VLM_MODEL_GGUF",
            "DEFAULT_MMPROJ_GGUF",
            "_vce_mlx_concurrent_futures",
            "_vce_mlx_functools",
            "_vce_mlx_trace_sys",
            "_vce_mlx_trace_threading",
            "_vce_mlx_contextlib",
        },
    )

    # Remove imports whose only consumer was the deleted MLX runtime.
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    import_spans = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        if any(name == "mlx" or name.startswith("mlx.") or name == "mlx_vlm" or name.startswith("mlx_vlm.") for name in modules):
            import_spans.append((offsets[node.lineno - 1], offsets[node.end_lineno]))
    for left, right in sorted(import_spans, reverse=True):
        source = source[:left] + source[right:]

    source = source.replace(
        'DEFAULT_VLM_MODEL_MLX = "mlx-community/Qwen3-VL-8B-Instruct-4bit"',
        "DEFAULT_VLM_MODEL_LLAMA = LLAMA_VLM_MODEL_ID",
    )
    source = source.replace(
        'if platform.system() == "Darwin":\n    ACTIVE_DEFAULT_VLM = DEFAULT_VLM_MODEL_MLX\nelse:\n    ACTIVE_DEFAULT_VLM = DEFAULT_VLM_MODEL_GGUF',
        "ACTIVE_DEFAULT_VLM = DEFAULT_VLM_MODEL_LLAMA",
    )
    source = source.replace(
        'TEXT_LLM_DEFAULT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"',
        "TEXT_LLM_DEFAULT_MODEL = LLAMA_TEXT_MODEL_ID",
    )
    source = source.replace("mlx-community/Qwen3-VL-8B-Instruct-4bit", LLAMA_VLM_MODEL_ID)
    source = source.replace("mlx-community/Qwen3-VL-4B-Instruct-4bit", LLAMA_VLM_MODEL_ID)
    source = source.replace("mlx-community/Qwen3.5-9B-MLX-4bit", LLAMA_TEXT_MODEL_ID)
    source, allowed_count = re.subn(
        r"(?m)^[ \t]*allowed_vlm = \{[^\n]*\}$",
        '    allowed_vlm = {"' + LLAMA_VLM_MODEL_ID + '"}',
        source,
    )
    if allowed_count != 1:
        raise ValueError(f"Expected one VLM model allow-list, found {allowed_count}.")
    source = source.replace(
        "VCE_MLX_RUNTIME_DIAGNOSTICS",
        "VCE_BACKEND_DIAGNOSTICS",
    )
    source = source.replace(
        "snapshot_download(repo_id=text_llm_model, local_files_only=True)",
        'snapshot_download(repo_id="gguf/Qwen3.5-9B-GGUF", local_files_only=True)',
    )

    source = _insert_after_top_level_definition(source, "VLMProvider", LLAMA_SERVER_RUNTIME)
    source = _patch_text_llm_to_llama_server(source)

    source = source.replace(
        '        if owns_temp_path and temp_path and os.path.exists(temp_path):\n            os.remove(temp_path)',
        '        _vce_stop_llama_server()\n        if owns_temp_path and temp_path and os.path.exists(temp_path):\n            os.remove(temp_path)',
        1,
    )
    if "LLAMA_VLM_MODEL_ID = \"Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M\"" not in source:
        source = source.replace(
            'DEFAULT_WHISPER_MODEL = "large"',
            'LLAMA_VLM_MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct-GGUF:Q4_K_M"\nLLAMA_TEXT_MODEL_ID = "gguf/Qwen3.5-9B-GGUF:Q4_K_M"\nDEFAULT_VLM_MODEL_LLAMA = LLAMA_VLM_MODEL_ID\nACTIVE_DEFAULT_VLM = DEFAULT_VLM_MODEL_LLAMA\nDEFAULT_WHISPER_MODEL = "large"',
            1,
        )
    source = source.replace("Qwen3.5-9B-MLX-4bit", "Qwen3.5-9B-GGUF Q4_K_M")
    source = source.replace("VCE_MLX", "VCE_LLAMA_CPP")
    source = source.replace("Qwen3-VL-8B-Instruct-4bit", "Qwen3-VL-8B GGUF Q4_K_M")
    source = source.replace("CPU Whisper / MLX VLM", "PyTorch Whisper MPS / llama.cpp Metal")
    source = source.replace("before the llama.cpp Metal VLM stage", "before the llama.cpp Metal VLM stage")
    source = source.replace("PyTorch MPS -> MLX VLM", "PyTorch MPS -> llama.cpp Metal VLM + Text LLM")
    source = source.replace("Qwen3.5-9B-MLX-4bit", "Qwen3.5-9B-GGUF Q4_K_M")
    if LLAMA_MARKER not in source:
        source = source.replace(
            "VCE_PYTORCH_WHISPER_MPS_SERIAL_V1",
            "VCE_PYTORCH_WHISPER_MPS_SERIAL_V1\n# " + LLAMA_MARKER,
            1,
        )
    ast.parse(source)
    _assert_no_mlx_runtime(source)
    return source


def patch_engine(source: str) -> str:
    if MARKER in source:
        source = _ensure_threading_alias_for_local_runtime(source)
        ast.parse(source)
        required = (
            '"backend": "openai-whisper"',
            "await _vce_whisper_task",
            '"whisper_backend": "openai-whisper"',
            "enable_ocr_cache: bool = Form(bool(setting",
        )
        missing = [item for item in required if item not in source]
        if missing:
            raise ValueError("MPS patch marker exists but required code is incomplete: " + ", ".join(missing))
        return _patch_engine_to_llama_cpp(source)

    source = _replace_exact(source, OLD_WHISPER_BLOCK, NEW_WHISPER_BLOCK, "Whisper device loader")
    source = _replace_exact(
        source,
        "load_whisper_model, target_whisper, acceleration_enabled=False",
        "load_whisper_model, target_whisper, acceleration_enabled=whisper_acceleration",
        "Whisper loader acceleration (RAM+ / RAM-)",
        expected=2,
    )

    source = source.replace("_vce_run_whisper_cpu_stage", "_vce_run_whisper_mps_stage")
    source = _replace_exact(
        source,
        "Whisper CPU: распознаём речь параллельно с MLX/Metal VLM",
        "Whisper PyTorch: отдельный MPS-этап перед llama.cpp/Metal VLM",
        "Whisper progress label",
    )
    source = _replace_exact(
        source,
        'print("[VCE Whisper] Starting CPU ASR in parallel with the GPU stage.")',
        'print(f"[VCE Whisper] Starting openai-whisper on {whisper_device} before the llama.cpp Metal VLM stage.")',
        "Whisper start log",
    )
    source = _replace_exact(
        source,
        'f"Whisper CPU завершён: фрагментов речи {len(result)}"',
        'f"Whisper завершён ({whisper_device}): фрагментов речи {len(result)}"',
        "Whisper completion label",
    )
    source = _replace_exact(
        source,
        'print(f"[VCE Whisper] CPU inference: {whisper_infer_time:.2f}s")',
        'print(f"[VCE Whisper] backend=openai-whisper device={whisper_device} inference={whisper_infer_time:.2f}s")',
        "Whisper timing log",
    )
    source = _replace_exact(
        source,
        OLD_WHISPER_CLEANUP,
        NEW_WHISPER_CLEANUP,
        "Whisper memory release",
    )

    # Insert the await immediately after task creation. This deliberately does
    # not replace/rewrite the following VLM guard block: target Macs may carry a
    # local Metal-failure latch between task creation and "if run_vlm".
    source = _serialize_whisper_before_vlm(source)

    source = _replace_exact(
        source,
        'print(f"[VCE parallelism] CPU Whisper / MLX VLM overlap: {_vce_overlap_seconds:.2f}s")',
        'print("[VCE stage order] PyTorch Whisper MPS completed before llama.cpp Metal VLM; GPU inference stages are serialized.")',
        "Sequential stage-order log",
    )

    source = _replace_exact(source, OLD_FFMPEG_SCENE_BRANCH, NEW_FFMPEG_SCENE_BRANCH, "Baseline scene detection")
    source = source.replace(
        "max_frames_by_scene=[_vce_adaptive_keyframe_limit(s, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene for s in scenes_raw],",
        "max_frames_by_scene=[keyframes_per_scene for _ in scenes_raw],",
    )
    source = source.replace(
        "effective_keyframes = _vce_adaptive_keyframe_limit(scene, keyframes_per_scene) if enable_adaptive_keyframes else keyframes_per_scene",
        "effective_keyframes = keyframes_per_scene",
    )

    source = _replace_exact(
        source,
        '''def _vce_transcribe_audio(audio_path, model_name, model, use_mlx, *args, **kwargs):
    """Use the selected ASR backend while keeping Whisper-compatible output fields."""
    if not use_mlx:
        return model.transcribe(audio_path, *args, **kwargs)
    import mlx_whisper
    repo = _vce_validate_mlx_whisper_model(model_name)
    return mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=repo,
        **kwargs,
    )''',
        '''def _vce_transcribe_audio(audio_path, model_name, model, use_mlx=False, *args, **kwargs):
    """Use only the PyTorch openai-whisper model; MLX Whisper is not a runtime option."""
    if use_mlx:
        raise RuntimeError("MLX Whisper support was removed; use PyTorch Whisper with MPS or CPU.")
    return model.transcribe(audio_path, *args, **kwargs)''',
        "Remove MLX Whisper transcription branch",
    )

    # Remove the runtime/API paths for the other experimental algorithms.
    source = re.sub(
        r'(?m)^[ \t]*enable_(?:adaptive_keyframes|mlx_whisper|fast_scene_detection): bool = Form\([^\n]*\n',
        "",
        source,
    )
    source = _replace_exact(
        source,
        '''        if enable_mlx_whisper:
            _vce_validate_mlx_whisper_model(target_whisper)
''',
        "",
        "Remove MLX Whisper request validation",
    )
    source = _replace_exact(
        source,
        '''        "enable_stage_timing": False,
        "enable_adaptive_keyframes": False,
        "enable_ocr_cache": False,
        "enable_mlx_whisper": False,
        "enable_fast_scene_detection": False,''',
        '''        "enable_stage_timing": True,
        "enable_ocr_cache": True,''',
        "Stable settings defaults",
    )
    source = _replace_exact(
        source,
        '''        "enable_stage_timing", "enable_adaptive_keyframes", "enable_ocr_cache",
        "enable_mlx_whisper", "enable_fast_scene_detection",''',
        '''        "enable_stage_timing", "enable_ocr_cache",''',
        "Settings allow-list cleanup",
    )
    source = _replace_one_of_exact(
        source,
        (
            (
                '"analysis_mode": "full", "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 2,',
                '"analysis_mode": "full", "enable_ffmpeg_ocr": True, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 3,',
            ),
            (
                '"analysis_mode": "full", "recognition_languages": _vce_legacy_apple_languages(persisted.get("apple_ocr_languages", ["ru-RU", "en-US"])), "enable_ffmpeg_ocr": False, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 2,',
                '"analysis_mode": "full", "recognition_languages": _vce_legacy_apple_languages(persisted.get("apple_ocr_languages", ["ru-RU", "en-US"])), "enable_ffmpeg_ocr": True, "ffmpeg_ocr_interval": 5,\n        "ffmpeg_ocr_workers": 3,',
            ),
        ),
        "Settings API OCR defaults",
    )
    source = _replace_exact(
        source,
        'enable_ffmpeg_ocr: bool = Form(False, description="Дополнительный OCR через FFmpeg/Tesseract"),',
        'enable_ffmpeg_ocr: bool = Form(bool(setting("enable_ffmpeg_ocr", True)), description="Дополнительный OCR через FFmpeg/Tesseract"),',
        "FFmpeg OCR request default",
    )
    source = _replace_exact(
        source,
        'ffmpeg_ocr_interval: int = Form(5, ge=1, le=60, description="Интервал выборки FFmpeg OCR, секунд"),',
        'ffmpeg_ocr_interval: int = Form(int(setting("ffmpeg_ocr_interval", 5)), ge=1, le=60, description="Интервал выборки FFmpeg OCR, секунд"),',
        "FFmpeg OCR interval default",
    )
    source = _replace_exact(
        source,
        'ffmpeg_ocr_workers: int = Form(int(setting("ffmpeg_ocr_workers", 2)), ge=1, le=8, description="Число параллельных CPU-процессов Tesseract"),',
        'ffmpeg_ocr_workers: int = Form(int(setting("ffmpeg_ocr_workers", 3)), ge=1, le=8, description="Число параллельных CPU-процессов Tesseract"),',
        "FFmpeg OCR worker default",
    )
    source = _replace_exact(
        source,
        'enable_stage_timing: bool = Form(False, description="Добавлять подробные тайминги этапов в JSON-ответ"),',
        'enable_stage_timing: bool = Form(bool(setting("enable_stage_timing", True)), description="Добавлять подробные тайминги этапов в JSON-ответ"),',
        "Stage timing request default",
    )
    source = _replace_exact(
        source,
        'enable_ocr_cache: bool = Form(False, description="Кэшировать OCR для повторяющихся кадров"),',
        'enable_ocr_cache: bool = Form(bool(setting("enable_ocr_cache", True)), description="Кэшировать OCR для повторяющихся кадров"),',
        "OCR cache request default",
    )

    source = _replace_exact(
        source,
        '''            "whisper": {
                "model": target_whisper,
                "device": whisper_device,''',
        '''            "whisper": {
                "backend": "openai-whisper",
                "model": target_whisper,
                "device": whisper_device,''',
        "Whisper backend timing metadata",
    )
    source = _replace_exact(
        source,
        '''                "whisper_backend": "mlx-whisper",
                "whisper_device": "cpu",''',
        '''                "whisper_backend": "openai-whisper",
                "whisper_device": whisper_device,
                "gpu_stages_serialized": True,''',
        "Whisper runtime provenance metadata",
    )
    source = _replace_exact(
        source,
        '''                        "enable_apple_ocr": enable_apple_ocr,
                        "whisper_acceleration": whisper_acceleration,''',
        '''                        "enable_apple_ocr": enable_apple_ocr,
                        "enable_ocr_cache": enable_ocr_cache,
                        "enable_ffmpeg_ocr": enable_ffmpeg_ocr,
                        "whisper_acceleration": whisper_acceleration,''',
        "OCR cache request provenance",
    )

    source = _replace_exact(
        source,
        "import threading as _vce_threading\n_VCE_MLX_WHISPER_LOCK = _vce_threading.Lock()\n\n",
        "import threading as _vce_threading\n\n",
        "Remove MLX Whisper lock but retain shared threading import",
    )
    if source.count("_vce_threading") == 1:
        source = _replace_exact(
            source,
            "import threading as _vce_threading\n",
            "",
            "Remove unused threading alias",
        )

    source = _remove_ast_nodes(
        source,
        {
            "_vce_mlx_whisper_model_id",
            "_VCEWhisperMLXCPU",
            "_VCE_MLX_WHISPER_REPOS",
            "_vce_validate_mlx_whisper_model",
            "_vce_load_whisper_model",
            "_vce_adaptive_keyframe_limit",
            "_vce_detect_scenes_fast",
        },
    )

    # Some local engine variants expose the Whisper language catalog through
    # an extra helper. Preserve its call sites, but source the catalog from the
    # installed openai-whisper package instead of the removed MLX Whisper backend.
    legacy_language_helper = "_vce_whisper_supported_languages"
    legacy_language_import = "from mlx_whisper.tokenizer import LANGUAGES"
    helper_references = source.count(legacy_language_helper)
    language_imports = source.count(legacy_language_import)
    if helper_references:
        if language_imports != 1:
            raise ValueError(
                "Local Whisper language helper: expected exactly one MLX tokenizer import, "
                f"found {language_imports}; no files were written."
            )
        source = source.replace(
            legacy_language_import,
            "from whisper.tokenizer import LANGUAGES",
            1,
        )
    elif language_imports:
        raise ValueError(
            "MLX tokenizer import exists without its expected local helper; "
            "no files were written."
        )

    source = source.replace(
        "# VCE_OPTIMIZATION_PRIORITY_CHECKBOXES_V1",
        "# VCE_FAST_PROFILE_BASELINE_OCR_CACHE_V1",
    )
    source = source.replace(
        "# VCE_MLX_METAL_CPU_PARALLEL_MLXWHISPER_V2",
        "# VCE_MLX_METAL_RUNTIME_DIAGNOSTICS_V2",
    )
    source = source.replace(
        'print(f"[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")',
        'print("[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")',
    )

    # Reject actual MLX Whisper imports/usages. The legacy helper's name is
    # retained for compatibility with local callers, but its import is redirected
    # to the installed openai-whisper tokenizer above.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        elif isinstance(node, ast.Name) and node.id == "mlx_whisper":
            modules = ["mlx_whisper"]
        else:
            modules = []
        if any(name == "mlx_whisper" or name.startswith("mlx_whisper.") for name in modules):
            raise ValueError(
                "Experimental MLX Whisper import/reference remains after patch; "
                "refusing to write any project files."
            )

    # The new runtime intentionally has no active MLX Whisper implementation or
    # switches. Keep the cache, Apple Vision, and FFmpeg/Tesseract stages intact.
    for forbidden in (
        "enable_adaptive_keyframes",
        "enable_mlx_whisper",
        "enable_fast_scene_detection",
        "_VCEWhisperMLXCPU",
        "_vce_validate_mlx_whisper_model",
        "_vce_detect_scenes_fast",
        "_vce_adaptive_keyframe_limit",
    ):
        if forbidden in source:
            locations = _line_numbers_containing(source, forbidden)
            where = ", ".join(str(number) for number in locations)
            raise ValueError(
                "Experimental runtime reference remains after patch: "
                + forbidden
                + (f" (line(s): {where})" if where else "")
                + "; refusing to write any project files."
            )

    source = source.replace(
        'print("[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")',
        'print("[VCE stage order] PyTorch Whisper completed before MLX/Metal VLM; GPU inference stages are serialized.")\n# ' + MARKER,
        1,
    )
    ast.parse(source)
    return _patch_engine_to_llama_cpp(source)


def _upgrade_ui_to_llama_cpp(ui: str) -> str:
    """Change VLM and Text LLM controls to their original GGUF models under llama.cpp Metal."""
    ui = ui.replace("локальная обработка · Apple Silicon / MLX", "локальная обработка · Apple Silicon / llama.cpp Metal")
    ui = ui.replace("Qwen3-VL", "Qwen3-VL GGUF")
    ui = ui.replace("Qwen3.5-9B-MLX-4bit", "Qwen3.5-9B GGUF Q4_K_M")
    ui = ui.replace("очередь работает последовательно, чтобы не перегружать память и MLX", "очередь работает последовательно, чтобы не перегружать память и llama.cpp/Metal")
    ui = ui.replace(
        "Улучшенная структура и смысловой анализ (Qwen3.5-9B)",
        "Улучшенная структура и смысловой анализ (Qwen3.5-9B · llama.cpp Metal)",
    )

    # Keep the original VLM and Text LLM model choices as separate GGUFs.
    vlm_pattern = re.compile(r'(?m)^(?P<indent>[ \t]*)<select id="cfg-vlm-model">.*?</select>[ \t]*$')
    vlm_matches = list(vlm_pattern.finditer(ui))
    fixed_vlm = '<input id="cfg-vlm-model" type="text" value="' + LLAMA_VLM_MODEL_ID + '" readonly>'
    if len(vlm_matches) == 1:
        match = vlm_matches[0]
        ui = ui[:match.start()] + match.group("indent") + fixed_vlm + ui[match.end():]
    elif len(vlm_matches) > 1:
        raise ValueError("UI has multiple VLM model selectors; refusing ambiguous rewrite.")
    elif fixed_vlm not in ui:
        raise ValueError("UI VLM selector is not in the expected legacy or llama.cpp layout.")

    text_pattern = re.compile(r'(<input id="cfg-text-llm-model" type="text" value=")[^"]+(" readonly>)')
    ui, count = text_pattern.subn(
        lambda match: match.group(1) + LLAMA_TEXT_MODEL_ID + match.group(2),
        ui,
    )
    if count != 1:
        raise ValueError(f"Expected one Text LLM model field, found {count}.")

    ui = ui.replace("Apple Silicon / MLX", "Apple Silicon / llama.cpp Metal")
    ui = ui.replace("MLX", "llama.cpp")
    ui = ui.replace("mlx-community/", "")
    if 'id="cfg-vlm-model"' not in ui or 'id="cfg-text-llm-model"' not in ui:
        raise ValueError("The VLM and Text LLM model fields are missing from the UI.")
    return ui


def _validate_llama_ui(ui: str) -> None:
    required = (
        LLAMA_UI_MARKER,
        "Apple Silicon / llama.cpp Metal",
        LLAMA_VLM_MODEL_ID,
        LLAMA_TEXT_MODEL_ID,
        "cfg-vlm-model",
        "cfg-text-llm-model",
        "cfg-ocr-cache",
        "enable_ocr_cache: document.getElementById('cfg-ocr-cache').checked",
        "cfg-whisper-acceleration",
    )
    missing = [item for item in required if item not in ui]
    if missing:
        raise ValueError("llama.cpp UI is incomplete: " + ", ".join(missing))
    for forbidden in (
        "MLX",
        "mlx-community/",
        "mlx_vlm",
        "cfg-mlx-whisper",
        "enable_mlx_whisper",
    ):
        if forbidden in ui:
            raise ValueError("MLX reference remains in the UI: " + forbidden)


def patch_ui(ui: str) -> str:
    if LLAMA_UI_MARKER in ui:
        _validate_llama_ui(ui)
        return ui

    if UI_MARKER in ui:
        # This is the already-patched MPS/OCR UI from the previous launcher.
        for forbidden in (
            'id="cfg-stage-timing"',
            'id="cfg-adaptive-keyframes"',
            'id="cfg-mlx-whisper"',
            'id="cfg-fast-scene-detection"',
        ):
            if forbidden in ui:
                raise ValueError("Existing UI marker is inconsistent: " + forbidden)
    else:
        # Remove the four experimental settings that the reviewed baseline excludes.
        for key in (
            "enable_stage_timing",
            "enable_adaptive_keyframes",
            "enable_mlx_whisper",
            "enable_fast_scene_detection",
        ):
            ui = re.sub(
                r"(?m)^[ \t]*" + re.escape(key) + r":[^\n]*\n",
                "",
                ui,
            )

        for mapping in (
            "      'cfg-stage-timing':'enable_stage_timing',\n",
            "      'cfg-adaptive-keyframes':'enable_adaptive_keyframes',\n",
            "      'cfg-mlx-whisper':'enable_mlx_whisper',\n",
            "      'cfg-fast-scene-detection':'enable_fast_scene_detection',",
        ):
            ui = ui.replace(mapping, "")

        for control_id in (
            "cfg-stage-timing",
            "cfg-adaptive-keyframes",
            "cfg-mlx-whisper",
            "cfg-fast-scene-detection",
        ):
            pattern = re.compile(
                r'(?m)^[ \t]*<label[^\n]*id="' + re.escape(control_id) + r'"[^\n]*\n'
            )
            ui, _count = pattern.subn("", ui)

        ui = ui.replace(
            "Экспериментальные оптимизации (все выключены по умолчанию)",
            "Базовый профиль OCR",
        )
        ui = ui.replace(
            "Кэш OCR Apple Vision для повторяющихся кадров",
            "Кэш Apple Vision OCR для повторяющихся кадров",
        )
        ui = ui.replace(
            "Дополнительный OCR через FFmpeg/Tesseract (экспериментально)",
            "Дополнительный OCR через FFmpeg/Tesseract",
        )

    if 'id="cfg-ocr-cache"' not in ui:
        raise ValueError("Apple Vision OCR cache control was removed unexpectedly.")
    if "enable_ocr_cache: document.getElementById('cfg-ocr-cache').checked" not in ui:
        raise ValueError("Apple Vision OCR cache is no longer wired to the settings payload.")
    if "cfg-whisper-acceleration" not in ui:
        raise ValueError("Whisper MPS control was removed unexpectedly.")

    ui = _upgrade_ui_to_llama_cpp(ui)
    ui = ui.replace(
        "</body>",
        f"<!-- {UI_MARKER} -->\n<!-- {LLAMA_UI_MARKER} -->\n</body>",
        1,
    )
    _validate_llama_ui(ui)
    return ui


def migrate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    updated = dict(settings)
    for key in REMOVED_SETTINGS:
        updated.pop(key, None)
    updated.update(PROFILE)
    return updated


def _atomic_write(path: Path, text: str, original_mode: int | None) -> None:
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_text(text, encoding="utf-8")
        if original_mode is not None:
            temp.chmod(original_mode)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def apply_project(project: Path) -> list[Path]:
    engine = project / "VideoContextEngine_v3.19.py"
    ui_file = project / "videocontext_web_ui.html"
    settings_file = project / "videocontext_settings.json"
    for required in (engine, ui_file):
        if not required.is_file():
            raise FileNotFoundError(f"Required VCE project file is missing: {required}")

    engine_old = engine.read_text(encoding="utf-8")
    ui_old = ui_file.read_text(encoding="utf-8")
    try:
        settings_old = settings_file.read_text(encoding="utf-8") if settings_file.is_file() else "{}\n"
        settings_data = json.loads(settings_old)
        if not isinstance(settings_data, dict):
            raise ValueError("videocontext_settings.json must contain a JSON object.")
    except Exception as exc:
        raise ValueError(f"Cannot safely read VCE settings: {type(exc).__name__}: {exc}") from exc

    engine_new = patch_engine(engine_old)
    ui_new = patch_ui(ui_old)
    settings_new_data = migrate_settings(settings_data)
    settings_new = json.dumps(settings_new_data, ensure_ascii=False, indent=2) + "\n"
    ast.parse(engine_new, filename=str(engine))

    changed = []
    candidates = (
        (engine, engine_old, engine_new),
        (ui_file, ui_old, ui_new),
        (settings_file, settings_old, settings_new),
    )
    for path, old, new in candidates:
        if old != new:
            changed.append(path)
    if not changed:
        print("Fast MPS profile already applied; no files changed.")
        return []

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backups: dict[Path, Path] = {}
    modes = {path: stat.S_IMODE(path.stat().st_mode) if path.exists() else None for path, _, _ in candidates}
    for path in changed:
        if path.exists():
            backup = path.with_name(path.name + f".backup-fast-mps-{stamp}")
            shutil.copy2(path, backup)
            backups[path] = backup

    try:
        for path, old, new in candidates:
            if old != new:
                _atomic_write(path, new, modes[path])
    except Exception:
        for path in changed:
            if path in backups:
                shutil.copy2(backups[path], path)
            else:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise

    for path in changed:
        print(f"Updated: {path.name}")
        if path in backups:
            print(f"Backup:  {backups[path].name}")
    print("Profile: Whisper large / PyTorch MPS -> llama.cpp Metal VLM + Text LLM; one shared GGUF model.")
    print("OCR: Apple Vision ON; OCR cache ON; FFmpeg/Tesseract ON (5 s, 3 workers).")
    print("Removed UI/API experiments: adaptive keyframes, MLX Whisper, fast scene detection. VLM/Text LLM now use llama.cpp Metal.")
    return list(backups.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        backups = apply_project(args.project_dir.expanduser().resolve())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("No partial profile should be used; inspect any timestamped backups before retrying.", file=sys.stderr)
        return 2
    print(f"Patch complete. Backup files: {len(backups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
