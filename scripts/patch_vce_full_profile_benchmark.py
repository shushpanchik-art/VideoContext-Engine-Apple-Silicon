#!/usr/bin/env python3
"""Patch a downloaded VCE benchmark runner in its temporary directory.

The adapter fixes observed runner defects and hardens per-profile classification.
It also provides opt-in, single-profile VLM-only and Whisper-only isolation modes.
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
        r'''# VCE_BENCH_DIAGNOSTIC_ISOLATION_WRAPPER_V3
_vce_original_build_base_fields = build_base_fields
def build_base_fields(*args, **kwargs):
    import os as _vce_os
    _vce_whisper_only = _vce_os.environ.get("VCE_BENCH_WHISPER_ONLY") == "1"
    _vce_vlm_only = _vce_os.environ.get("VCE_BENCH_VLM_ONLY") == "1"
    if _vce_whisper_only and _vce_vlm_only:
        raise RuntimeError("Choose only one of VCE_BENCH_WHISPER_ONLY or VCE_BENCH_VLM_ONLY")
    _vce_result = _vce_original_build_base_fields(*args, **kwargs)
    if not (_vce_whisper_only or _vce_vlm_only):
        return _vce_result
    if not isinstance(_vce_result, tuple) or len(_vce_result) != 2:
        raise RuntimeError("build_base_fields() returned an unexpected value; cannot apply isolation mode")
    _vce_fields, _vce_missing = _vce_result
    if not isinstance(_vce_fields, dict):
        raise RuntimeError("build_base_fields() did not return a settings dictionary")
    _vce_fields = dict(_vce_fields)
    if _vce_whisper_only:
        # Keep Whisper inference, word timing and audio features; disable every visual/secondary model.
        _vce_fields["analysis_mode"] = "whisper_ocr"
        _vce_fields["skip_audio"] = False
        _vce_fields["word_timestamps"] = True
        _vce_fields["enable_audio_features"] = True
        _vce_fields["skip_visual"] = True
        _vce_fields["enable_apple_ocr"] = False
        _vce_fields["enable_ffmpeg_ocr"] = False
        _vce_fields["text_llm_enabled"] = False
        _vce_fields["generate_summary"] = False
    else:
        # Keep the visual/VLM path; disable Whisper/audio, OCR and Text LLM to isolate VLM.
        _vce_fields["skip_audio"] = True
        _vce_fields["word_timestamps"] = False
        _vce_fields["enable_audio_features"] = False
        _vce_fields["skip_visual"] = False
        _vce_fields["enable_apple_ocr"] = False
        _vce_fields["enable_ffmpeg_ocr"] = False
        _vce_fields["text_llm_enabled"] = False
        _vce_fields["generate_summary"] = False
    return _vce_fields, _vce_missing''',
        "# VCE_BENCH_DIAGNOSTIC_ISOLATION_WRAPPER_V3",
    )

    source = _insert_after_function(
        source,
        "run_one",
        r'''# VCE_BENCH_STAGE_CLASSIFIER_V3
def _vce_classify_profile_result(record, requested_fields, payload, log_text):
    """Separate HTTP completion from evidence that each requested ML stage completed."""
    import re as _vce_re

    if not isinstance(record, dict):
        raise TypeError("run_one() must return a result dictionary for stage classification")
    if not isinstance(requested_fields, dict):
        requested_fields = {}
    if not isinstance(payload, dict):
        payload = {}

    _meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    _meta_params = _meta.get("params") if isinstance(_meta.get("params"), dict) else {}
    _meta_timings = _meta.get("timings") if isinstance(_meta.get("timings"), dict) else {}
    _skipped = _meta.get("skipped") if isinstance(_meta.get("skipped"), dict) else {}
    _vlm_timing = _meta_timings.get("vlm") if isinstance(_meta_timings.get("vlm"), dict) else {}
    _whisper_timing = _meta_timings.get("whisper") if isinstance(_meta_timings.get("whisper"), dict) else {}
    _text_llm = payload.get("text_llm") if isinstance(payload.get("text_llm"), dict) else {}

    def _positive_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    _http_status = record.get("http_status")
    _api_status = "http_200" if _http_status == 200 else "http_error"
    try:
        _runtime_error_count = max(0, int(record.get("error_count") or 0))
    except (TypeError, ValueError, OverflowError):
        _runtime_error_count = 1
    _log = str(log_text or "")
    _metal_failure = bool(_vce_re.search(
        r"command buffer execution failed|kIOGPUCommandBufferCallbackErrorSubmissionsIgnored|submissionsignored",
        _log, _vce_re.I
    ))
    _vlm_guard = bool(_vce_re.search(
        r"VLM memory guard.{0,240}(?:disabl|timeout|fail|error)|disabling VLM for the rest of this video",
        _log, _vce_re.I | _vce_re.S
    ))
    _vlm_specific_failure = bool(_vce_re.search(
        r"(?:\[VLM\].{0,180}(?:failed|failure|error|fallback)|VLM.{0,100}(?:disabled|fallback|failed))",
        _log, _vce_re.I
    ))

    _analysis_mode = str(requested_fields.get("analysis_mode", "full")).lower()
    _vlm_requested = not bool(requested_fields.get("skip_visual", False)) and _analysis_mode not in {"whisper_ocr", "audio", "transcription"}
    _text_requested = bool(requested_fields.get("text_llm_enabled", False))
    _whisper_requested = not bool(requested_fields.get("skip_audio", False)) and _analysis_mode not in {"visual", "video"}
    _audio_missing = _meta_params.get("audio_track_available") is False or _meta_params.get("audio_auto_skipped") is True

    _vlm_inference = _vlm_timing.get("inference_time")
    _run_vlm = _meta_params.get("run_vlm")
    if not _vlm_requested:
        _vlm_status, _vlm_reason = "skipped", "not requested by this profile"
    elif _vlm_guard or _vlm_specific_failure or _run_vlm is False:
        _vlm_status, _vlm_reason = "failed", "VLM was disabled or failed in the runtime"
    elif _positive_number(_vlm_inference):
        _vlm_status, _vlm_reason = "completed", "positive VLM inference timing returned"
    else:
        _vlm_status, _vlm_reason = "degraded", "VLM was requested but completion telemetry is absent"

    _text_enabled = _text_llm.get("enabled")
    _text_state = str(_text_llm.get("status", "")).lower()
    _text_inference = _text_llm.get("inference_time")
    _text_error = _text_llm.get("error")
    if not _text_requested:
        _text_status, _text_reason = "skipped", "not requested by this profile"
    elif _text_state == "failed" or _text_error:
        _text_status, _text_reason = "failed", "Text LLM returned failed status or an error"
    elif _text_state == "success" and _positive_number(_text_inference) and _text_enabled is not False and payload.get("enhanced_analysis") is not None:
        _text_status, _text_reason = "completed", "Text LLM success status and output returned"
    else:
        _text_status, _text_reason = "degraded", "Text LLM was requested but successful completion is not evidenced"

    _whisper_inference = _whisper_timing.get("inference_time")
    if not _whisper_requested:
        _whisper_status, _whisper_reason = "skipped", "Whisper/audio inference was not requested by this profile"
    elif _audio_missing:
        _whisper_status, _whisper_reason = "degraded", "Whisper was requested but the input has no available audio track"
    elif _positive_number(_whisper_inference):
        _whisper_status, _whisper_reason = "completed", "positive Whisper inference timing returned"
    else:
        _whisper_status, _whisper_reason = "degraded", "Whisper was requested but completion telemetry is absent"

    _metal_status = "failed" if _metal_failure else "no_failure_observed"
    _stage_status = {
        "api": {"status": _api_status, "http_status": _http_status},
        "metal": {"status": _metal_status},
        "vlm": {"requested": _vlm_requested, "status": _vlm_status, "reason": _vlm_reason, "inference_seconds": _vlm_inference},
        "text_llm": {"requested": _text_requested, "status": _text_status, "reason": _text_reason, "inference_seconds": _text_inference},
        "whisper": {"requested": _whisper_requested, "status": _whisper_status, "reason": _whisper_reason, "inference_seconds": _whisper_inference},
        "runtime_errors": {
            "count": _runtime_error_count,
            "status": "degraded" if _runtime_error_count else "none",
            "reason": "runner reported errors; inspect local errors.csv before accepting profile" if _runtime_error_count else "runner reported no errors",
        },
    }

    _reasons = []
    if _http_status != 200:
        _reasons.append("HTTP request did not complete with status 200")
    if not _meta:
        _reasons.append("HTTP response lacks the required non-empty meta payload")
    if _metal_failure:
        _reasons.append("Metal command-buffer submission failure observed")
    if _vlm_requested and _vlm_status != "completed":
        _reasons.append("required VLM stage is " + _vlm_status)
    if _text_requested and _text_status != "completed":
        _reasons.append("required Text LLM stage is " + _text_status)
    if _whisper_requested and _whisper_status != "completed":
        _reasons.append("requested Whisper stage is " + _whisper_status)
    if _runtime_error_count:
        _reasons.append(f"runner reported {_runtime_error_count} error(s); inspect local errors.csv before accepting profile")

    if _http_status != 200 or not _meta or _metal_failure or (_vlm_requested and _vlm_status == "failed"):
        _overall = "failed"
    elif _runtime_error_count or (_vlm_requested and _vlm_status != "completed") or (_text_requested and _text_status != "completed") or (_whisper_requested and _whisper_status != "completed"):
        _overall = "degraded"
    else:
        _overall = "completed"

    return {
        "status": _overall,
        "api_status": _api_status,
        "stage_status": _stage_status,
        "classification_reasons": _reasons,
    }


def _vce_filter_informational_ffmpeg_ocr_diagnostics(record, run_dir):
    """Move the known positive FFmpeg/Tesseract availability banner out of errors."""
    import json as _vce_json
    import re as _vce_re
    from pathlib import Path as _VCEPath

    if not isinstance(record, dict):
        return record
    _run_dir = _VCEPath(run_dir)
    _errors_path = _run_dir / "errors.json"
    if not _errors_path.is_file():
        return record
    try:
        _error_data = _vce_json.loads(_errors_path.read_text(encoding="utf-8"))
    except Exception:
        return record
    if not isinstance(_error_data, dict) or not isinstance(_error_data.get("errors"), list):
        return record

    _errors = _error_data["errors"]
    try:
        _json_count = int(_error_data.get("error_count"))
        _record_count = int(record.get("error_count"))
    except (TypeError, ValueError, OverflowError):
        return record
    # Fail closed if the runner's counters do not agree with the exact local list.
    if _json_count != len(_errors) or _record_count != len(_errors):
        return record

    _info_pattern = _vce_re.compile(
        r"^\s*FFmpeg OCR:\s*FFmpeg\s*\+\s*Tesseract CLI\s+"
        r"(?:доступны|available)\s*;\s*"
        r"(?:используются только выбранные установленные языки|"
        r"only selected installed languages are used)\s*:",
        _vce_re.I,
    )
    _informational = [
        item for item in _errors
        if isinstance(item, str) and _info_pattern.search(item)
    ]
    if not _informational:
        return record

    _remaining = [item for item in _errors if item not in _informational]
    record["error_count"] = len(_remaining)
    record["error_excerpt"] = _remaining[:5]
    record["informational_diagnostics"] = _informational[:10]

    _error_data["error_count"] = len(_remaining)
    _error_data["errors"] = _remaining
    _error_data["informational_diagnostics"] = _informational
    _error_data["classification_note"] = (
        "Known positive FFmpeg/Tesseract CLI availability messages are diagnostics, "
        "not runtime errors."
    )
    _errors_path.write_text(
        _vce_json.dumps(_error_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    _errors_log = _run_dir / "errors.log"
    if _errors_log.exists():
        _errors_log.write_text(
            "\n".join(str(item) for item in _remaining)
            + ("\n" if _remaining else "No API/server error markers were captured.\n"),
            encoding="utf-8",
        )
    (_run_dir / "diagnostics.log").write_text(
        "Informational diagnostics (excluded from runtime error count):\n"
        + "\n".join(str(item) for item in _informational)
        + "\n",
        encoding="utf-8",
    )
    return record


_vce_original_run_one = run_one
def run_one(*args, **kwargs):
    import json as _vce_json
    from pathlib import Path as _VCEPath

    _record = _vce_original_run_one(*args, **kwargs)
    if not isinstance(_record, dict):
        return _record
    _base_fields = kwargs.get("base_fields", args[3] if len(args) > 3 else {})
    _overrides = kwargs.get("overrides", args[2] if len(args) > 2 else {})
    _requested_fields = dict(_base_fields) if isinstance(_base_fields, dict) else {}
    if isinstance(_overrides, dict):
        _requested_fields.update(_overrides)

    _result_directory = _record.get("result_directory")
    if not _result_directory:
        return _record
    _run_dir = _VCEPath(_result_directory)
    _record = _vce_filter_informational_ffmpeg_ocr_diagnostics(_record, _run_dir)
    try:
        _payload = _vce_json.loads((_run_dir / "response.json").read_text(encoding="utf-8"))
    except Exception:
        _payload = {}
    try:
        _log_text = (_run_dir / "server_log_delta.log").read_text(encoding="utf-8", errors="replace")
    except Exception:
        _log_text = ""
    _classification = _vce_classify_profile_result(_record, _requested_fields, _payload, _log_text)
    _record.update(_classification)
    (_run_dir / "stage_status.json").write_text(
        _vce_json.dumps(_classification, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    (_run_dir / "run_summary.json").write_text(
        _vce_json.dumps(_record, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8"
    )
    _print = print
    _print(
        "  CLASSIFIER OVERRIDE: {status} (API {api_status}); wall={wall:.1f}s; VCE={vce}s; scenes={scenes}; errors={errors}".format(
            status=_record.get("status"),
            api_status=_record.get("api_status"),
            wall=float(_record.get("client_wall_seconds") or 0.0),
            vce=_record.get("vce_process_seconds", "—"),
            scenes=_record.get("scene_count", "—"),
            errors=_record.get("error_count", 0),
        ),
        flush=True,
    )
    return _record''',
        "# VCE_BENCH_STAGE_CLASSIFIER_V3",
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
    print("Applied VCE benchmark patch v5: stage-aware classification and informational FFmpeg OCR diagnostic filtering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
