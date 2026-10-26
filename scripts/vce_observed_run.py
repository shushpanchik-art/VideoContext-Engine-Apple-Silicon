#!/usr/bin/env python3
"""Run one local VCE analysis while recording progress, server logs and host metrics.

macOS-only observer. Reads local settings but never writes them. Results (including
the full response/transcript) are stored locally and are NOT uploaded to GitHub.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_API = "http://127.0.0.1:7555"
DEFAULT_SETTINGS = Path.home() / "VideoContext-Engine" / "videocontext_settings.json"
LOG_CANDIDATES = [
    Path("/tmp/vce-live-mlx-audio-test.log"),
    Path("/tmp/vce-live-mlx-test.log"),
    Path("/tmp/vce-live-mlx-integration.log"),
    Path.home() / "Library/Logs/VideoContext Engine/server.log",
]
FORM_EXCLUDE = {"video_file", "local_video_token", "video_url", "job_id"}
# The analyze endpoint's Form max_length limits can be shorter than saved prompts.
# The local compare runner omits unchanged prompt fields and lets VCE use its persisted prompts.
PERSISTED_PROMPT_FIELDS = {"visual_user_prompt", "summary_user_prompt"}
SAFETY_OVERRIDES = {
    "analysis_mode": "full",
    "skip_audio": False,
    "skip_visual": False,
    "generate_summary": True,
    "generate_txt": False,
    "generate_json": False,
    "response_format": "json",
    "enable_stage_timing": True,
    "enable_mlx_whisper": True,
    "text_llm_enabled": True,
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def encode_form_value(value: Any) -> str:
    """Encode a Python value in the textual form expected by FastAPI Form fields."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def flatten_timing_metrics(value: Any, prefix: str = "") -> list[tuple[str, float | int]]:
    """Return numeric timing leaves while excluding booleans and unrelated counts."""
    output: list[tuple[str, float | int]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            output.extend(flatten_timing_metrics(item, name))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        low = prefix.lower()
        timing_tokens = (
            "time", "seconds", "duration", "latency", "overlap",
            "worker_seconds", "wait_seconds",
        )
        if any(token in low for token in timing_tokens):
            output.append((prefix, value))
    return output


def request_json(url: str, timeout: float = 10.0) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    decoded: Any
    try:
        decoded = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        decoded = {"_raw": raw.decode("utf-8", errors="replace")[:4000]}
    return status, decoded


def get_form_schema(openapi: dict[str, Any]) -> tuple[dict[str, Any], str]:
    operation = openapi.get("paths", {}).get("/api/v1/analyze", {}).get("post")
    if not operation:
        raise RuntimeError("OpenAPI не содержит POST /api/v1/analyze.")
    content = operation.get("requestBody", {}).get("content", {})
    multipart = content.get("multipart/form-data", {})
    schema = multipart.get("schema", {})
    ref = schema.get("$ref")
    if ref:
        prefix = "#/components/schemas/"
        if not ref.startswith(prefix):
            raise RuntimeError(f"Неизвестная ссылка схемы запроса: {ref}")
        name = ref[len(prefix):]
        schema = openapi.get("components", {}).get("schemas", {}).get(name, {})
    properties = schema.get("properties", {})
    if "video_file" not in properties:
        raise RuntimeError("В OpenAPI не найдено поле video_file.")
    progress_path = next(
        (p for p in openapi.get("paths", {}) if "/progress/" in p and "{" in p),
        "",
    )
    return properties, progress_path


def resolve_form_fields(settings: dict[str, Any], properties: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    fields: dict[str, Any] = {}
    origins: dict[str, str] = {}
    for name, definition in properties.items():
        if name in FORM_EXCLUDE or name in PERSISTED_PROMPT_FIELDS:
            continue
        if name in settings:
            fields[name] = settings[name]
            origins[name] = "active VCE settings from /api/v1/ui-settings"
        elif isinstance(definition, dict) and "default" in definition:
            fields[name] = definition["default"]
            origins[name] = "OpenAPI default"
    for name in PERSISTED_PROMPT_FIELDS:
        if name in properties:
            origins[name] = "omitted from multipart; use running VCE's persisted prompt"
    for name, value in SAFETY_OVERRIDES.items():
        if name in properties:
            fields[name] = value
            origins[name] = "observed-run override"
    # Use the same bounded VCE job/progress mechanism as the web UI.
    if "job_id" in properties:
        fields["job_id"] = ""
        origins["job_id"] = "generated per-run job id"
    return fields, origins


def validate_prompt_limits(fields: dict[str, Any], properties: dict[str, Any]) -> list[str]:
    problems = []
    for name in ("visual_user_prompt", "summary_user_prompt"):
        value = fields.get(name)
        limit = properties.get(name, {}).get("maxLength")
        if isinstance(value, str) and isinstance(limit, int) and len(value) > limit:
            problems.append(f"{name}: {len(value)} символов, API допускает максимум {limit}")
    return problems


def multipart_body(fields: dict[str, Any], video_path: Path, boundary: str, job_id: str) -> bytes:
    chunks: list[bytes] = []
    marker = boundary.encode("ascii")

    def add_field(name: str, value: Any) -> None:
        chunks.extend([
            b"--" + marker + b"\r\n",
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            encode_form_value(value).encode("utf-8"),
            b"\r\n",
        ])

    for name, value in fields.items():
        if name == "job_id":
            value = job_id
        add_field(name, value)

    # Use a safe ASCII multipart filename to avoid non-ASCII MIME header issues.
    # The original Unicode path/name is preserved in run_metadata.json.
    safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", video_path.stem).strip("._") or "input_video"
    safe_filename = safe_filename[:80] + video_path.suffix.lower()
    chunks.extend([
        b"--" + marker + b"\r\n",
        f'Content-Disposition: form-data; name="video_file"; filename="{safe_filename}"\r\n'.encode("ascii"),
        b"Content-Type: video/mp4\r\n\r\n",
        video_path.read_bytes(),
        b"\r\n--" + marker + b"--\r\n",
    ])
    return b"".join(chunks)


def get_listener_pids() -> list[int]:
    lsof = "/usr/sbin/lsof"
    if not Path(lsof).exists():
        return []
    try:
        result = subprocess.run(
            [lsof, "-nP", "-tiTCP:7555", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        pids = []
        for token in result.stdout.split():
            try:
                pids.append(int(token))
            except ValueError:
                pass
        return sorted(set(pids))
    except Exception:
        return []


def process_snapshot(pids: list[int]) -> list[dict[str, Any]]:
    output = []
    for pid in pids:
        try:
            result = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "%cpu=", "-o", "rss=", "-o", "etime="],
                capture_output=True, text=True, timeout=3, check=False,
            )
            parts = result.stdout.strip().split(None, 2)
            item: dict[str, Any] = {"pid": pid}
            if len(parts) >= 1:
                try:
                    item["cpu_percent"] = float(parts[0])
                except ValueError:
                    item["cpu_percent_raw"] = parts[0]
            if len(parts) >= 2:
                try:
                    item["rss_kb"] = int(parts[1])
                    item["rss_mib"] = round(int(parts[1]) / 1024, 1)
                except ValueError:
                    item["rss_raw"] = parts[1]
            if len(parts) >= 3:
                item["elapsed"] = parts[2]
            output.append(item)
        except Exception as exc:
            output.append({"pid": pid, "error": str(exc)})
    return output


def memory_pressure_snapshot() -> str:
    command = shutil.which("memory_pressure") or "/usr/bin/memory_pressure"
    if not Path(command).exists():
        return "memory_pressure unavailable"
    try:
        result = subprocess.run(
            [command, "-Q"], capture_output=True, text=True, timeout=4, check=False,
        )
        text = (result.stdout or result.stderr).strip()
        return text[:2500] if text else f"exit={result.returncode}, no output"
    except Exception as exc:
        return f"memory_pressure error: {exc}"


def ffprobe_duration(video: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return float(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None
    except Exception:
        return None


def find_log(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    candidates = [p for p in LOG_CANDIDATES if p.is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def drain_server_log(log_path: Path | None, offset: int, event_path: Path) -> int:
    if not log_path or not log_path.is_file():
        return offset
    try:
        size = log_path.stat().st_size
        if size < offset:  # log was rotated/truncated while the run was active
            offset = 0
        with log_path.open("rb") as f:
            f.seek(offset)
            data = f.read()
            new_offset = f.tell()
        if data:
            text = data.decode("utf-8", errors="replace")
            for line in text.splitlines():
                append_jsonl(event_path, {"observed_at": now_iso(), "line": line})
        return new_offset
    except Exception:
        return offset


def progress_url(api_url: str, template: str, job_id: str) -> str:
    if not template:
        template = "/api/v1/progress/{job_id}"
    template = re.sub(r"\{[^/]+\}", urllib.parse.quote(job_id, safe=""), template)
    return api_url.rstrip("/") + template


def save_timing_outputs(run_dir: Path, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    timings = meta.get("timings") or data.get("timings") or {}
    if not isinstance(timings, dict):
        timings = {}
    write_json(run_dir / "stage_timings.json", timings)
    rows = flatten_timing_metrics(timings)
    with (run_dir / "stage_timings.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value", "unit"])
        for name, value in rows:
            unit = "seconds" if any(x in name.lower() for x in ("time", "seconds", "duration", "latency", "overlap")) else "count"
            writer.writerow([name, value, unit])
    return timings


def print_timing_summary(data: Any, http_status: int | None, request_wall_seconds: float | None) -> None:
    print("\n=== Итог запуска ===")
    print("HTTP:", http_status)
    if request_wall_seconds is not None:
        print(f"Wall time HTTP-запроса клиента: {request_wall_seconds:.3f} с")
    if not isinstance(data, dict):
        print("Ответ не является JSON-объектом.")
        return
    if "detail" in data:
        print("API_ERROR:", data.get("detail"))
        return
    meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    timings = meta.get("timings") or data.get("timings") or {}
    print("Видео:", meta.get("source"))
    print("Длительность видео:", meta.get("duration"), "с")
    print("Количество сцен:", meta.get("scene_count"))
    print("Время обработки VCE:", meta.get("process_time"), "с")
    print("=== Профиль этапов (полный JSON сохранён в stage_timings.json) ===")
    if isinstance(timings, dict):
        for section in ("scene_detection_seconds", "whisper", "vlm", "apple_ocr", "ffmpeg_ocr", "text_llm", "parallelism", "stage_diagnostics"):
            value = timings.get(section)
            if value is not None:
                print(section + ":", json.dumps(value, ensure_ascii=False))
    print("Ветви пропуска:", json.dumps(meta.get("params", {}).get("skipped", {}), ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="Путь к исходному видео.")
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS), help="Локальный videocontext_settings.json.")
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--out-root", default=str(Path.home() / "Downloads" / "VCE_observed_runs"))
    parser.add_argument("--server-log", default=None, help="Явный путь к логу сервера; по умолчанию ищется известный лог.")
    parser.add_argument("--interval", type=float, default=2.0, help="Интервал опроса прогресса/ресурсов в секундах.")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        print("STOP: этот наблюдатель рассчитан на macOS.")
        return 2
    video = Path(args.video).expanduser()
    settings_path = Path(args.settings).expanduser()
    if not video.is_file():
        print(f"STOP: видео не найдено: {video}")
        return 2
    if not settings_path.is_file():
        print(f"STOP: файл настроек не найден: {settings_path}")
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.out_root).expanduser() / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    print("Папка результатов:", run_dir)

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("settings root is not an object")
    except Exception as exc:
        write_json(run_dir / "preflight_error.json", {"error": str(exc), "at": now_iso()})
        print("STOP: не удалось прочитать JSON настроек:", repr(exc))
        return 2

    try:
        api_status, openapi = request_json(args.api_url.rstrip("/") + "/openapi.json")
        if api_status != 200 or not isinstance(openapi, dict):
            raise RuntimeError(f"OpenAPI HTTP {api_status}")
        active_status, active_settings = request_json(args.api_url.rstrip("/") + "/api/v1/ui-settings")
        if active_status != 200 or not isinstance(active_settings, dict):
            raise RuntimeError(
                f"Не удалось получить активные настройки VCE: GET /api/v1/ui-settings вернул HTTP {active_status}. "
                "Останавливаюсь, чтобы не запускать тест с неподтверждёнными настройками."
            )

        # Save both sources locally for audit; neither file is uploaded to GitHub.
        write_json(run_dir / "local_settings_snapshot.json", settings)
        write_json(run_dir / "active_server_settings_snapshot.json", active_settings)
        prompt_keys = PERSISTED_PROMPT_FIELDS
        common_keys = sorted((set(settings) & set(active_settings)) - prompt_keys)
        differences = {
            key: {"local_file": settings[key], "active_server": active_settings[key]}
            for key in common_keys if settings[key] != active_settings[key]
        }
        write_json(run_dir / "settings_comparison.json", {
            "local_settings_path": str(settings_path),
            "active_settings_endpoint": args.api_url.rstrip("/") + "/api/v1/ui-settings",
            "non_prompt_differences": differences,
            "prompt_lengths": {
                key: {
                    "local_file_chars": len(settings.get(key, "")) if isinstance(settings.get(key), str) else None,
                    "active_server_chars": len(active_settings.get(key, "")) if isinstance(active_settings.get(key), str) else None,
                    "api_maxLength": openapi.get("components", {}).get("schemas", {}).get(
                        next((ref.rsplit("/", 1)[-1] for ref in get_form_schema(openapi)[0].values() if isinstance(ref, dict) and "$ref" in ref), ""),
                        {}
                    ).get("maxLength")
                } for key in prompt_keys
            },
        })
        if differences:
            keys = ", ".join(list(differences)[:30])
            raise RuntimeError(
                "Локальный videocontext_settings.json отличается от активных настроек сервера "
                f"по полям: {keys}. Видео не отправлялось; проверь settings_comparison.json."
            )

        properties, progress_template = get_form_schema(openapi)
        # Use the running VCE settings as authority. Long persisted prompts are deliberately
        # omitted from multipart so the API can use its persisted values without truncation.
        fields, origins = resolve_form_fields(active_settings, properties)
        problems = validate_prompt_limits(fields, properties)
        if problems:
            raise RuntimeError("Передаваемые поля превышают ограничения API: " + "; ".join(problems))
    except Exception as exc:
        write_json(run_dir / "preflight_error.json", {"error": str(exc), "at": now_iso()})
        print("STOP: предварительная проверка не прошла:", str(exc))
        print("Видео ещё не отправлялось.")
        return 2

    job_id = f"vceobs_{stamp}_{uuid.uuid4().hex[:8]}"
    if "job_id" in properties:
        fields["job_id"] = job_id
    fields["video_file"] = "(attached)"
    write_json(run_dir / "request_settings.json", {
        "note": "Локальная копия фактически отправленных полей. Настройки на диске не менялись. Длинные persisted prompt-поля не отправляются, чтобы не обходить лимиты API; активные значения сохранены в active_server_settings_snapshot.json.",
        "settings_file": str(settings_path),
        "settings_sha256": sha256_file(settings_path),
        "field_origins": origins,
        "effective_fields": fields,
        "forced_overrides": SAFETY_OVERRIDES,
        "job_id": job_id,
    })
    # Rebuild multipart separately so file bytes are not included in the JSON settings snapshot.
    fields_for_upload = dict(fields)
    fields_for_upload.pop("video_file", None)
    boundary = "----VCEObserved" + uuid.uuid4().hex
    body = multipart_body(fields_for_upload, video, boundary, job_id)
    log_path = find_log(args.server_log)
    log_offset = log_path.stat().st_size if log_path else 0
    pids = get_listener_pids()
    video_duration = ffprobe_duration(video)
    started_at = now_iso()
    started_mono = time.monotonic()
    metadata = {
        "started_at": started_at,
        "video_path": str(video),
        "video_name": video.name,
        "video_bytes": video.stat().st_size,
        "video_mib": round(video.stat().st_size / (1024 * 1024), 3),
        "video_sha256": sha256_file(video),
        "video_duration_seconds_ffprobe": video_duration,
        "settings_path": str(settings_path),
        "settings_sha256": sha256_file(settings_path),
        "active_settings_source": args.api_url.rstrip("/") + "/api/v1/ui-settings",
        "active_settings_snapshot_path": str(run_dir / "active_server_settings_snapshot.json"),
        "active_settings_sha256": sha256_file(run_dir / "active_server_settings_snapshot.json"),
        "api_url": args.api_url,
        "job_id": job_id,
        "server_pids_at_start": pids,
        "server_log_path": str(log_path) if log_path else None,
        "python_version": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "openapi_path": "/api/v1/analyze",
        "form_field_names": sorted(k for k in fields_for_upload),
        "safety_overrides": SAFETY_OVERRIDES,
        "result_directory": str(run_dir),
        "note": "generate_txt/generate_json disabled for this request to avoid overwriting existing files; full API JSON is saved here. Other pipeline stages follow the saved settings except explicit full-analysis overrides.",
    }
    write_json(run_dir / "run_metadata.json", metadata)
    write_json(run_dir / "system_info.json", {
        "captured_at": now_iso(),
        "server_pids": pids,
        "server_processes": process_snapshot(pids),
        "memory_pressure": memory_pressure_snapshot(),
        "ffprobe_duration_seconds": video_duration,
    })

    response_state: dict[str, Any] = {
        "done": False, "http_status": None, "body": None,
        "error": None, "request_start": None, "request_end": None,
    }

    def post_request() -> None:
        response_state["request_start"] = now_iso()
        request = urllib.request.Request(
            args.api_url.rstrip("/") + "/api/v1/analyze",
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "User-Agent": "VCE-observed-run/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=6 * 60 * 60) as response:
                response_state["http_status"] = response.status
                response_state["body"] = response.read()
        except urllib.error.HTTPError as exc:
            response_state["http_status"] = exc.code
            response_state["body"] = exc.read()
        except Exception as exc:
            response_state["error"] = repr(exc)
        finally:
            response_state["request_end"] = now_iso()
            response_state["done"] = True

    print("\n=== Preflight passed; uploading video now ===")
    print(f"Source: {video}")
    print(f"Size: {metadata['video_mib']} MiB; duration: {video_duration if video_duration is not None else 'unknown'} s")
    print(f"Server PID(s): {pids or 'not found by lsof'}")
    print(f"Server log: {log_path or 'not found; progress/resource monitoring still enabled'}")
    print("Настройки отправки сверены с активным GET /api/v1/ui-settings.")
    print("Длинные visual_user_prompt/summary_user_prompt не отправляются повторно; VCE использует активные сохранённые промпты.")
    print(f"Job ID: {job_id}")
    print("Full audio + visual analysis and summary requested; this may take several minutes.")
    print("Do not restart the server or start another analysis while this run is active.\n")

    request_thread = threading.Thread(target=post_request, name="vce-analysis-request", daemon=True)
    request_thread.start()
    progress_path = run_dir / "progress_trace.jsonl"
    system_path = run_dir / "system_samples.jsonl"
    events_path = run_dir / "server_events.jsonl"
    offset = log_offset
    last_progress_key = None
    last_memory_sample = 0.0
    sample_index = 0
    progress_url_value = progress_url(args.api_url, progress_template, job_id)
    try:
        while not response_state["done"]:
            observed = now_iso()
            elapsed = time.monotonic() - started_mono
            progress_status = None
            progress_data = None
            try:
                status, progress_data = request_json(progress_url_value, timeout=4)
                progress_status = status
            except Exception as exc:
                progress_data = {"poll_error": repr(exc)}
            snapshot = {
                "observed_at": observed,
                "elapsed_seconds": round(elapsed, 3),
                "http_status": progress_status,
                "progress": progress_data,
            }
            append_jsonl(progress_path, snapshot)
            if isinstance(progress_data, dict):
                key = json.dumps(progress_data, ensure_ascii=False, sort_keys=True, default=str)
                if key != last_progress_key:
                    message = (
                        progress_data.get("message")
                        or progress_data.get("detail")
                        or progress_data.get("stage")
                        or progress_data.get("status")
                    )
                    pct = progress_data.get("progress", progress_data.get("percent"))
                    print(f"[{elapsed:7.1f}s] progress={pct!s:>5} {message if message is not None else key[:240]}")
                    last_progress_key = key
            pids_now = get_listener_pids()
            sample: dict[str, Any] = {
                "observed_at": observed,
                "elapsed_seconds": round(elapsed, 3),
                "server_pids": pids_now,
                "server_processes": process_snapshot(pids_now),
            }
            if time.monotonic() - last_memory_sample >= 15.0:
                sample["memory_pressure"] = memory_pressure_snapshot()
                last_memory_sample = time.monotonic()
            append_jsonl(system_path, sample)
            offset = drain_server_log(log_path, offset, events_path)
            sample_index += 1
            if sample_index % max(1, round(10 / max(args.interval, 0.5))) == 0:
                compact = sample["server_processes"]
                print(f"[{elapsed:7.1f}s] resource sample: {json.dumps(compact, ensure_ascii=False)}")
            time.sleep(max(0.5, args.interval))
    except KeyboardInterrupt:
        print("\nПрервано пользователем. Серверная задача может продолжить работу; проверьте VCE UI перед повторным запуском.")
        write_json(run_dir / "interrupted.json", {"at": now_iso(), "job_id": job_id})
        return 130
    finally:
        offset = drain_server_log(log_path, offset, events_path)

    request_thread.join(timeout=2)
    ended_at = now_iso()
    elapsed_total = time.monotonic() - started_mono
    raw_body = response_state.get("body")
    response_bytes = raw_body if isinstance(raw_body, bytes) else b""
    (run_dir / "response.json").write_bytes(response_bytes)
    try:
        response_data = json.loads(response_bytes.decode("utf-8", errors="replace")) if response_bytes else {}
    except Exception as exc:
        response_data = {"_response_parse_error": repr(exc), "_raw": response_bytes.decode("utf-8", errors="replace")[:10000]}
    write_json(run_dir / "response_summary.json", {
        "http_status": response_state["http_status"],
        "client_wall_seconds": round(elapsed_total, 3),
        "request_start": response_state["request_start"],
        "request_end": response_state["request_end"],
        "transport_error": response_state["error"],
        "api_error_detail": response_data.get("detail") if isinstance(response_data, dict) else None,
        "video_scene_count": response_data.get("meta", {}).get("scene_count") if isinstance(response_data, dict) else None,
        "video_process_time": response_data.get("meta", {}).get("process_time") if isinstance(response_data, dict) else None,
    })
    timings = save_timing_outputs(run_dir, response_data)
    metadata.update({
        "ended_at": ended_at,
        "client_wall_seconds": round(elapsed_total, 3),
        "http_status": response_state["http_status"],
        "transport_error": response_state["error"],
        "result_scene_count": response_data.get("meta", {}).get("scene_count") if isinstance(response_data, dict) else None,
        "result_process_time_seconds": response_data.get("meta", {}).get("process_time") if isinstance(response_data, dict) else None,
    })
    write_json(run_dir / "run_metadata.json", metadata)

    events: list[dict[str, Any]] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    patterns = ("Traceback", "ERROR", "Exception", "VCE Whisper", "VCE MLX", "VCE runtime",
                "PROFIL TEMPS", "scene_analysis", "summary", "OCR", "Metal", "RAM_MODE",
                "KeyError", "too many values", "chargement VLM", "inference")
    relevant = [
        str(item.get("line", "")) for item in events
        if any(p.lower() in str(item.get("line", "")).lower() for p in patterns)
    ]
    (run_dir / "server_log_key_lines.txt").write_text("\n".join(relevant) + ("\n" if relevant else ""), encoding="utf-8")
    print_timing_summary(response_data, response_state["http_status"], elapsed_total)
    print("\n=== Наблюдение завершено ===")
    print("Результаты сохранены локально:", run_dir)
    print("Файлы: response.json, stage_timings.json/csv, progress_trace.jsonl, system_samples.jsonl, server_events.jsonl, server_log_key_lines.txt, run_metadata.json")
    print("Публично ничего не загружалось. Настройки VCE на диске не изменялись.")
    if response_state["error"]:
        print("TRANSPORT_ERROR:", response_state["error"])
        return 3
    if response_state["http_status"] != 200:
        print("Запрос завершился с HTTP-ошибкой; response.json и диагностика сохранены.")
        return 3
    print("PASS: API завершил полный запрос с HTTP 200.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
