#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark a running local VideoContext Engine without changing its code or settings.

Standard-library only. Uploads the selected video to the local API, samples the
VCE server process while it runs, and writes a privacy-reduced JSON/Markdown/CSV
report. It never stores the API's transcript, scene descriptions, summary, or
the absolute input path in the report.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_PROJECTS = (
    Path.home() / "VideoContext-Engine",
    Path.home() / "Downloads" / "VideoContext-Engine",
)
MARKERS = {
    "parallel_cpu_asr_gpu_vlm": "VCE_PARALLEL_CPU_ASR_GPU_VLM_V1",
    "bounded_frame_prefetch": "VCE_BOUNDED_KEYFRAME_PREFETCH_V1",
    "mp4_recovery": "VCE_MP4_PREFLIGHT_RECOVERY_RETRY_V2",
}
SAFE_CONFIG_KEYS = (
    "vlm_model", "whisper_model", "response_format", "vlm_resolution",
    "scene_threshold", "min_scene_duration", "max_scene_duration",
    "keyframes_per_scene", "vlm_max_tokens_scene", "vlm_max_tokens_summary",
    "skip_audio", "skip_visual", "enable_audio_features", "generate_summary",
    "enable_apple_ocr", "enable_ffmpeg_ocr", "whisper_acceleration",
    "enable_mlx_whisper", "word_timestamps", "analysis_mode", "ram_mode",
    "enable_stage_timing",
)


def command_output(argv: list[str], timeout: float = 5.0) -> str:
    # Keep ps/sysctl/vm_stat output predictable even when macOS uses a localized
    # decimal separator or localized date format.
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False, env=env
        )
        return (p.stdout or "").strip()
    except Exception:
        return ""


def find_project(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    for candidate in DEFAULT_PROJECTS:
        if (candidate / "VideoContextEngine_v3.19.py").is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Не найден VideoContext-Engine. Передайте --project /путь/к/VideoContext-Engine."
    )


def listener_pids(port: int) -> list[int]:
    out = command_output(["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"])
    result = []
    for line in out.splitlines():
        try:
            result.append(int(line.strip()))
        except ValueError:
            pass
    return sorted(set(result))


def fetch_json(url: str, timeout: float = 5.0) -> dict:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "VCE-Benchmark/1.0"})
    with urlopen(req, timeout=timeout) as response:
        data = response.read()
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Ожидался JSON-объект.")
    return value


def resolve_schema_ref(openapi: dict, schema: dict) -> dict:
    current = schema
    seen = set()
    while isinstance(current, dict) and "$ref" in current:
        ref = current["$ref"]
        if ref in seen or not ref.startswith("#/"):
            break
        seen.add(ref)
        obj = openapi
        for part in ref[2:].split("/"):
            obj = obj.get(part, {}) if isinstance(obj, dict) else {}
        if not isinstance(obj, dict):
            break
        current = obj
    return current if isinstance(current, dict) else {}


def request_properties(openapi: dict) -> dict:
    route = openapi.get("paths", {}).get("/api/v1/analyze", {}).get("post", {})
    content = route.get("requestBody", {}).get("content", {})
    body = content.get("multipart/form-data") or content.get("application/x-www-form-urlencoded")
    if not isinstance(body, dict):
        raise RuntimeError("В /openapi.json не найдена схема формы для /api/v1/analyze.")
    schema = resolve_schema_ref(openapi, body.get("schema", {}))
    props = schema.get("properties", {})
    if not isinstance(props, dict) or "video_file" not in props:
        raise RuntimeError(
            "API не публикует поле video_file. Не отправляю запрос, чтобы не запустить другой тип анализа."
        )
    return props


def scalar_form_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_form_fields(
    settings: dict,
    properties: dict,
    adaptations: list[dict] | None = None,
) -> list[tuple[str, str]]:
    """Build the API form from current settings, respecting the live OpenAPI schema."""
    fields: list[tuple[str, str]] = []
    for pname, prop in properties.items():
        if pname == "video_file":
            continue
        if pname == "video_url":
            fields.append((pname, ""))
            continue
        if pname == "response_format":
            fields.append((pname, "json"))
            continue
        if pname in ("generate_txt", "generate_json"):
            # Avoid writing output files into the server's working directory.
            fields.append((pname, "false"))
            continue
        if pname in settings:
            value = settings[pname]
        elif isinstance(prop, dict) and "default" in prop:
            value = prop["default"]
        else:
            continue

        # The settings UI may store prompts longer than the API's Form(max_length=...)
        # accepts. Apply the schema limit to this benchmark request only; never edit settings.
        if isinstance(value, str) and isinstance(prop, dict):
            # Fall back to VCE v3.19 limits if a patched OpenAPI schema omits maxLength.
            known_limits = {"visual_user_prompt": 1000, "summary_user_prompt": 2000}
            max_length = prop.get("maxLength")
            if not isinstance(max_length, int):
                max_length = known_limits.get(pname)
            if isinstance(max_length, int) and len(value) > max_length:
                original_length = len(value)
                trimmed = value[:max_length]
                # Avoid cutting a word in half when the previous word boundary is nearby.
                boundary = trimmed.rfind(" ")
                if boundary >= max_length - 80:
                    trimmed = trimmed[:boundary].rstrip()
                value = trimmed
                if adaptations is not None:
                    adaptations.append({
                        "field": pname,
                        "reason": "truncated_to_api_maxLength_for_request_only",
                        "original_characters": original_length,
                        "sent_characters": len(value),
                        "api_maxLength": max_length,
                    })

        if isinstance(value, list):
            for item in value:
                fields.append((pname, scalar_form_value(item)))
        elif value is not None:
            fields.append((pname, scalar_form_value(value)))
    return fields

def multipart_post(
    base_url: str,
    video_path: Path,
    fields: list[tuple[str, str]],
    timeout: float,
) -> tuple[int, bytes, float]:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("По соображениям приватности API должен быть локальным HTTP URL (127.0.0.1).")
    port = parsed.port or 80
    boundary = "----VCEBenchmark" + uuid.uuid4().hex
    prefix_chunks = []
    for name, value in fields:
        safe_name = name.replace('"', "")
        prefix_chunks.append(
            ("--" + boundary + "\r\n"
             + f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n'
             + value + "\r\n").encode("utf-8")
        )
    filename = "benchmark_input" + video_path.suffix.lower()
    file_header = (
        "--" + boundary + "\r\n"
        + 'Content-Disposition: form-data; name="video_file"; filename="' + filename + '"\r\n'
        + "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    suffix = ("\r\n--" + boundary + "--\r\n").encode("utf-8")
    content_length = sum(map(len, prefix_chunks)) + len(file_header) + video_path.stat().st_size + len(suffix)
    path = parsed.path.rstrip("/") + "/api/v1/analyze"
    if parsed.query:
        path += "?" + parsed.query

    conn = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    started = time.monotonic()
    try:
        conn.putrequest("POST", path)
        conn.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        conn.putheader("Content-Length", str(content_length))
        conn.putheader("Accept", "application/json")
        conn.putheader("Connection", "close")
        conn.putheader("User-Agent", "VCE-Benchmark/1.0")
        conn.endheaders()
        for chunk in prefix_chunks:
            conn.send(chunk)
        conn.send(file_header)
        with video_path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                conn.send(chunk)
        conn.send(suffix)
        response = conn.getresponse()
        body = response.read()
        return response.status, body, time.monotonic() - started
    finally:
        conn.close()


def ps_process_tree(root_pid: int) -> tuple[float, float, int]:
    raw = command_output(["ps", "-axo", "pid=,ppid=,%cpu=,rss="])
    processes = {}
    children = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
            cpu, rss_kb = float(parts[2].replace(",", ".")), float(parts[3])
        except ValueError:
            continue
        processes[pid] = (ppid, cpu, rss_kb)
        children.setdefault(ppid, []).append(pid)
    if root_pid not in processes:
        return 0.0, 0.0, 0
    selected = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in selected:
            continue
        selected.add(pid)
        stack.extend(children.get(pid, []))
    return (
        sum(processes[p][1] for p in selected if p in processes),
        sum(processes[p][2] for p in selected if p in processes) / 1024.0,
        len(selected),
    )


def memory_snapshot() -> dict:
    info = {}
    total = command_output(["sysctl", "-n", "hw.memsize"])
    try:
        info["physical_memory_gb"] = round(int(total) / (1024 ** 3), 2)
    except ValueError:
        pass
    vm = command_output(["vm_stat"])
    page_size = 16384
    match = re.search(r"page size of (\d+) bytes", vm)
    if match:
        page_size = int(match.group(1))
    for label, key in (
        ("Pages free", "free_mb"),
        ("Pages active", "active_mb"),
        ("Pages inactive", "inactive_mb"),
        ("Pages speculative", "speculative_mb"),
        ("Pages wired down", "wired_mb"),
        ("Pages occupied by compressor", "compressed_mb"),
    ):
        match = re.search(re.escape(label) + r":\s+([\d,]+)", vm)
        if match:
            pages = int(match.group(1).replace(",", ""))
            info[key] = round(pages * page_size / (1024 ** 2), 1)
    return info


def process_start_time(pid: int) -> str | None:
    value = command_output(["ps", "-p", str(pid), "-o", "lstart="])
    return value or None


class ResourceSampler:
    def __init__(self, root_pid: int, run_index: int, interval: float = 2.0):
        self.root_pid = root_pid
        self.run_index = run_index
        self.interval = interval
        self.started = time.monotonic()
        self.stop_event = threading.Event()
        self.samples: list[dict] = []
        self.thread = threading.Thread(target=self._run, name="vce-bench-sampler", daemon=True)

    def _take_sample(self):
        cpu, rss, count = ps_process_tree(self.root_pid)
        self.samples.append({
            "run": self.run_index,
            "elapsed_s": round(time.monotonic() - self.started, 2),
            "process_tree_cpu_percent": round(cpu, 1),
            "process_tree_rss_mb": round(rss, 1),
            "process_count": count,
        })

    def _run(self):
        while not self.stop_event.wait(self.interval):
            self._take_sample()

    def start(self):
        self._take_sample()
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=self.interval + 1)
        self._take_sample()


def video_probe(path: Path) -> dict:
    result = {
        "extension": path.suffix.lower(),
        "size_mb": round(path.stat().st_size / (1024 ** 2), 2),
    }
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        result["ffprobe_available"] = False
        return result
    raw = command_output([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
        "-of", "json", str(path),
    ], timeout=15)
    try:
        value = json.loads(raw)
        fmt = value.get("format", {})
        if "duration" in fmt:
            result["duration_s"] = round(float(fmt["duration"]), 3)
        streams = []
        for stream in value.get("streams", []):
            item = {"type": stream.get("codec_type"), "codec": stream.get("codec_name")}
            if stream.get("codec_type") == "video":
                for key in ("width", "height", "avg_frame_rate"):
                    if key in stream:
                        item[key] = stream[key]
            streams.append(item)
        result["streams"] = streams
        result["ffprobe_available"] = True
    except Exception:
        result["ffprobe_available"] = True
        result["probe_parse_failed"] = True
    return result


def clean_metric_response(payload: dict) -> dict:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    timings = meta.get("timings") if isinstance(meta.get("timings"), dict) else {}
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    transcript_chars = visual_chars = ocr_chars = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        for key in ("audio_transcript", "transcript", "speech_text"):
            if isinstance(segment.get(key), str) and segment.get(key):
                transcript_chars += len(segment[key])
                break
        for key in ("visual_description", "description"):
            if isinstance(segment.get(key), str) and segment.get(key):
                visual_chars += len(segment[key])
                break
        value = segment.get("ocr_text", "")
        if isinstance(value, str):
            ocr_chars += len(value)
    return {
        "http_status": 200,
        "server_duration_s": meta.get("duration"),
        "server_process_time_s": meta.get("process_time"),
        "scene_count": meta.get("scene_count", len(segments)),
        "models": meta.get("models", {}),
        "skipped": meta.get("skipped", {}),
        "params": {
            key: meta.get("params", {}).get(key)
            for key in ("keyframes_per_scene", "enable_audio_features", "generate_summary",
                        "vlm_max_tokens_scene", "vlm_max_tokens_summary", "ram_mode")
            if key in meta.get("params", {})
        },
        "timings": timings,
        "result_shape_only": {
            "segments_returned": len(segments),
            "transcript_characters": transcript_chars,
            "visual_description_characters": visual_chars,
            "ocr_characters": ocr_chars,
        },
    }



def safe_http_error(status: int, body: bytes) -> str:
    """Summarize HTTP failures without copying prompts, file paths, or API input values."""
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return f"HTTP {status}: API error; response body omitted for privacy."
    details = payload.get("detail") if isinstance(payload, dict) else None
    safe_details = []
    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            entry = {}
            location = item.get("loc")
            if isinstance(location, (list, tuple)):
                entry["field"] = ".".join(str(part) for part in location)
            error_type = item.get("type")
            if isinstance(error_type, str):
                entry["type"] = error_type
            if entry:
                safe_details.append(entry)
    if safe_details:
        return f"HTTP {status}: validation failure " + json.dumps(safe_details, ensure_ascii=False)
    return f"HTTP {status}: API error; response body omitted for privacy."

def make_summary(report: dict) -> str:
    lines = [
        "# VideoContext Engine benchmark report",
        "",
        "- Script: scripts/benchmark_vce_macos.py",
        "- Timestamp UTC: " + report.get("timestamp_utc", "unknown"),
        "- VCE API: " + report.get("api", {}).get("base_url", "unknown"),
        "- Privacy: no video path, transcript text, scene descriptions, global summary or prompt text stored.",
        "",
        "## Machine and runtime",
        "",
        "| Property | Value |",
        "|---|---|",
    ]
    machine = report.get("machine", {})
    for key in ("macOS", "architecture", "cpu", "logical_cpu_count", "physical_memory_gb"):
        if machine.get(key) is not None:
            lines.append(f"| {key} | {machine[key]} |")
    lines += ["", "## Installed code markers", "", "| Feature | Detected in local engine file |", "|---|---|"]
    for key, value in report.get("engine_markers", {}).items():
        lines.append(f"| {key} | {'yes' if value else 'no'} |")
    lines += ["", "## Video metadata (no path/name)", ""]
    lines.append(json.dumps(report.get("video", {}), ensure_ascii=False, indent=2))
    adaptations = report.get("request_adaptations", [])
    if adaptations:
        lines += ["", "## Request-only compatibility adjustments", ""]
        lines.append("These fields were shortened to API limits for this benchmark request only; local settings were not changed.")
        for item in adaptations:
            lines.append(
                f"- {item.get('field')}: {item.get('original_characters')} to "
                f"{item.get('sent_characters')} characters (API maxLength {item.get('api_maxLength')})."
            )
    lines += ["", "## Runs", ""]
    lines += [
        "| Run | HTTP wall (s) | Server processing (s) | Whisper (s) | VLM (s) | Scenes | Mean CPU % | Peak RSS (MB) | Prefetch wait (s) | Fallbacks |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, run in enumerate(report.get("runs", []), 1):
        metric = run.get("metrics", {})
        timing = metric.get("timings", {})
        whisper = timing.get("whisper", {}) if isinstance(timing, dict) else {}
        vlm = timing.get("vlm", {}) if isinstance(timing, dict) else {}
        diag = timing.get("stage_diagnostics", {}) if isinstance(timing, dict) else {}
        samples = run.get("resource_summary", {})
        lines.append(
            f"| {index} | {run.get('client_wall_s', 'n/a')} | {metric.get('server_process_time_s', 'n/a')} | "
            f"{whisper.get('inference_time', 'n/a')} | {vlm.get('inference_time', 'n/a')} | "
            f"{metric.get('scene_count', 'n/a')} | {samples.get('mean_cpu_percent', 'n/a')} | "
            f"{samples.get('peak_rss_mb', 'n/a')} | {diag.get('frame_prefetch_wait_seconds', 'n/a')} | "
            f"{diag.get('frame_prefetch_fallback_count', 'n/a')} |"
        )
    if report.get("warnings"):
        lines += ["", "## Warnings", ""]
        lines.extend("- " + item for item in report["warnings"])
    lines += [
        "",
        "## Interpretation notes",
        "",
        "- client_wall_s includes file upload and API response; server_process_time_s is the VCE's own timer.",
        "- CPU/RSS samples cover the process listening on port 7555 and its descendants, not the whole machine.",
        "- frame_prefetch_wait_seconds and frame_prefetch_worker_seconds are only available if the patched API returns timings.stage_diagnostics.",
        "- GPU utilization is not inferred from CPU metrics. MLX model selection is evidence of the intended backend, not a direct GPU-utilization measurement.",
        "- Compare at least 3 runs with identical video and settings; the first run may include cold-start/model loading costs.",
        "",
    ]
    return "\n".join(lines)



GITHUB_REPOSITORY = "shushpanchik-art/VideoContext-Engine-Apple-Silicon"


def publish_report_to_github(out_dir: Path, report: dict, branch: str) -> dict:
    """Publish only generated, privacy-reduced report files through authenticated gh CLI."""
    if not shutil.which("gh"):
        raise RuntimeError("Не найден GitHub CLI (gh). Установите gh и выполните gh auth login.")
    auth = subprocess.run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if auth.returncode != 0:
        raise RuntimeError("GitHub CLI не авторизован. Выполните gh auth login и повторите запуск.")

    stamp = out_dir.name.removeprefix("VCE-Benchmark-")
    prefix = f"results/vce-benchmark/{stamp}"
    names = ["summary.md", "resources.csv", "report.json"]
    urls = {
        name: f"https://github.com/{GITHUB_REPOSITORY}/blob/{branch}/{prefix}/{name}"
        for name in names if (out_dir / name).is_file() or name == "report.json"
    }

    def put_one(name: str) -> None:
        local_path = out_dir / name
        if not local_path.is_file():
            return
        remote_path = f"{prefix}/{name}"
        body = json.dumps({
            "message": f"chore: add VCE benchmark report {stamp} ({name})",
            "content": base64.b64encode(local_path.read_bytes()).decode("ascii"),
            "branch": branch,
        })
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{GITHUB_REPOSITORY}/contents/{remote_path}",
                "--method", "PUT",
                "--input", "-",
            ],
            input=body,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            # Do not expose credentials or any request body in a diagnostic.
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            detail = detail[-1][:240] if detail else f"exit code {result.returncode}"
            raise RuntimeError(f"gh api failed for {name}: {detail}")

    for name in ("summary.md", "resources.csv"):
        put_one(name)
    report.setdefault("privacy", {})["uploaded_to_github"] = True
    report["github_publication"] = {
        "repository": GITHUB_REPOSITORY,
        "branch": branch,
        "directory": prefix,
        "files": urls,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Publish report.json last so it contains the predictable GitHub URLs for the bundle.
    put_one("report.json")
    return urls

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="Путь к тестовому видео (MP4/MOV/MKV и т. п.).")
    parser.add_argument("--project", help="Папка VideoContext-Engine; по умолчанию ищется в домашней папке/Downloads.")
    parser.add_argument("--base-url", default="http://127.0.0.1:7555", help="Локальный URL VCE.")
    parser.add_argument("--runs", type=int, default=1, help="Число последовательных прогонов одного видео (по умолчанию 1).")
    parser.add_argument("--timeout", type=float, default=14400, help="HTTP timeout в секундах; по умолчанию 4 часа.")
    parser.add_argument("--sample-interval", type=float, default=2.0, help="Интервал CPU/RSS sampling в секундах.")
    parser.add_argument("--report-dir", help="Папка отчёта; по умолчанию создаётся на Desktop.")
    parser.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение перед запуском анализа.")
    parser.add_argument("--publish-github", action="store_true", help="После прогона предложить опубликовать обезличенный отчёт в GitHub.")
    parser.add_argument("--github-branch", default="feat/vce-parallel-cpu-asr-vlm-20261025", help="Ветка GitHub для отчёта.")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        print("ОШИБКА: скрипт предназначен для macOS.", file=sys.stderr)
        return 2
    if args.runs < 1 or args.runs > 10:
        print("ОШИБКА: --runs должен быть от 1 до 10.", file=sys.stderr)
        return 2
    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        print("ОШИБКА: видео не найдено.", file=sys.stderr)
        return 2
    if video.suffix.lower() not in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
        print("ОШИБКА: неподдерживаемое расширение видео.", file=sys.stderr)
        return 2
    parsed_base = urlsplit(args.base_url)
    if parsed_base.scheme != "http" or parsed_base.hostname not in ("127.0.0.1", "localhost", "::1"):
        print("ОШИБКА: API должен быть локальным HTTP URL (127.0.0.1/localhost).", file=sys.stderr)
        return 2

    try:
        project = find_project(args.project)
        engine_path = project / "VideoContextEngine_v3.19.py"
        settings_path = project / "videocontext_settings.json"
        if not engine_path.is_file() or not settings_path.is_file():
            raise FileNotFoundError("В каталоге проекта не найдены движок или videocontext_settings.json.")
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        engine_source = engine_path.read_text(encoding="utf-8", errors="replace")
        pids = listener_pids(parsed_base.port or 7555)
        if not pids:
            raise RuntimeError("VCE не слушает порт API. Запустите VideoContext Engine и повторите команду.")
        openapi = fetch_json(args.base_url.rstrip("/") + "/openapi.json", timeout=10)
        props = request_properties(openapi)
        request_adaptations = []
        fields = build_form_fields(settings, props, request_adaptations)
    except Exception as exc:
        print("ОШИБКА предварительной проверки: " + str(exc), file=sys.stderr)
        return 2

    pid = pids[0]
    started = process_start_time(pid)
    engine_mtime = dt.datetime.fromtimestamp(engine_path.stat().st_mtime).astimezone()
    try:
        server_started = dt.datetime.strptime(started, "%a %b %d %H:%M:%S %Y").astimezone() if started else None
    except Exception:
        server_started = None
    warnings = []
    if server_started and server_started.timestamp() < engine_path.stat().st_mtime:
        warnings.append(
            "Процесс VCE мог быть запущен до последнего изменения файла движка. Перезапустите приложение, "
            "если после установки патчей это ещё не сделано."
        )

    print("VideoContext Engine benchmark")
    print(f"Видео: расширение {video.suffix.lower()}, {video.stat().st_size / (1024 ** 2):.1f} MB")
    print(f"VCE listener PID: {pid}; число прогонов: {args.runs}")
    print("Будет отправлен локальный запрос на /api/v1/analyze с текущими настройками.")
    print("Чтобы избежать побочных файлов, generate_txt/generate_json выключаются только в запросе.")
    for item in request_adaptations:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: поле {item['field']} сокращено с {item['original_characters']} "
            f"до {item['sent_characters']} символов (ограничение API {item['api_maxLength']}); "
            "изменён только тестовый запрос, файл настроек не тронут."
        )
    if warnings:
        for warning in warnings:
            print("ПРЕДУПРЕЖДЕНИЕ: " + warning)
    if not args.yes:
        answer = input("Убедитесь, что другой анализ VCE не идёт. Для запуска введите RUN: ").strip()
        if answer != "RUN":
            print("Отменено; файлы проекта и настройки не изменены.")
            return 0

    report_root = Path(args.report_dir).expanduser() if args.report_dir else (
        Path.home() / "Desktop" if (Path.home() / "Desktop").is_dir() else Path.home()
    )
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = report_root / f"VCE-Benchmark-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=False)

    machine = {
        "macOS": command_output(["sw_vers", "-productVersion"]) or platform.mac_ver()[0],
        "architecture": platform.machine(),
        "cpu": command_output(["sysctl", "-n", "machdep.cpu.brand_string"]) or None,
        "logical_cpu_count": os.cpu_count(),
        **memory_snapshot(),
    }
    report = {
        "schema_version": 1,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "api": {"base_url": args.base_url.rstrip("/"), "listener_pid": pid, "server_process_start": started},
        "machine": machine,
        "video": video_probe(video),
        "settings_summary": {key: settings.get(key) for key in SAFE_CONFIG_KEYS if key in settings},
        "request_adaptations": request_adaptations,
        "engine_markers": {key: marker in engine_source for key, marker in MARKERS.items()},
        "engine_mtime_local": engine_mtime.isoformat(timespec="seconds"),
        "server_started_before_engine_mtime": bool(server_started and server_started.timestamp() < engine_path.stat().st_mtime),
        "runs": [],
        "warnings": warnings,
        "resource_samples_csv": "resources.csv",
        "privacy": {
            "absolute_video_path_saved": False,
            "video_filename_saved": False,
            "transcript_or_scene_text_saved": False,
            "custom_prompt_text_saved": False,
            "uploaded_to_github": False,
        },
    }
    all_samples = []
    try:
        for run_index in range(1, args.runs + 1):
            print(f"\n[{run_index}/{args.runs}] Запуск анализа...")
            sampler = ResourceSampler(pid, run_index, interval=max(1.0, args.sample_interval))
            sampler.start()
            metrics = {}
            error = None
            client_wall = None
            try:
                status, body, client_wall = multipart_post(args.base_url, video, fields, timeout=args.timeout)
                if status < 200 or status >= 300:
                    raise RuntimeError(safe_http_error(status, body))
                payload = json.loads(body.decode("utf-8"))
                metrics = clean_metric_response(payload)
                metrics["http_status"] = status
                print(
                    f"Готово: client wall {client_wall:.1f}s; "
                    f"VCE process {metrics.get('server_process_time_s')}s; сцен {metrics.get('scene_count')}"
                )
            except Exception as exc:
                error = str(exc).replace(str(video), "<video>").replace(str(project), "<project>")[:1600]
                report["warnings"].append(f"Run {run_index}: {error}")
                print("ОШИБКА прогона: " + error[:500], file=sys.stderr)
            finally:
                sampler.stop()
                all_samples.extend(sampler.samples)
            cpu = [s["process_tree_cpu_percent"] for s in sampler.samples if s["process_count"]]
            rss = [s["process_tree_rss_mb"] for s in sampler.samples if s["process_count"]]
            run = {
                "run_index": run_index,
                "client_wall_s": round(client_wall, 3) if isinstance(client_wall, (int, float)) else None,
                "metrics": metrics,
                "resource_summary": {
                    "samples": len(sampler.samples),
                    "mean_cpu_percent": round(sum(cpu) / len(cpu), 1) if cpu else None,
                    "peak_cpu_percent": round(max(cpu), 1) if cpu else None,
                    "peak_rss_mb": round(max(rss), 1) if rss else None,
                },
            }
            if error:
                run["error"] = error
            report["runs"].append(run)
    except KeyboardInterrupt:
        report["warnings"].append("Benchmark interrupted by keyboard.")
    finally:
        report["machine_memory_after"] = memory_snapshot()
        (out_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / "summary.md").write_text(make_summary(report), encoding="utf-8")
        if all_samples:
            with (out_dir / "resources.csv").open("w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=[
                    "run", "elapsed_s", "process_tree_cpu_percent", "process_tree_rss_mb", "process_count"
                ])
                writer.writeheader()
                writer.writerows(all_samples)

    if args.publish_github and not any(run.get("metrics") for run in report["runs"]):
        print("Публикация пропущена: ни один прогон не завершился успешно. Отчёт с ошибками остался локальным.")
    elif args.publish_github:
        answer = input(
            "\nОпубликовать report.json, summary.md и resources.csv в GitHub? "
            "В отчёте нет пути/имени видео или текста транскрипции. Введите PUBLISH: "
        ).strip()
        if answer == "PUBLISH":
            try:
                urls = publish_report_to_github(out_dir, report, args.github_branch)
                print("\n✅ Отчёт опубликован в GitHub:")
                for name, url in urls.items():
                    print(f"  {name}: {url}")
            except Exception as exc:
                report.setdefault("warnings", []).append(
                    "GitHub publication failed: " + type(exc).__name__ + ": " + str(exc)[:400]
                )
                report.setdefault("privacy", {})["uploaded_to_github"] = False
                (out_dir / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                (out_dir / "summary.md").write_text(make_summary(report), encoding="utf-8")
                print("⚠️ Публикация не завершена; локальные отчёты сохранены. " + str(exc)[:300])
        else:
            print("Публикация в GitHub отменена; отчёт остался только на этом Mac.")

    print("\n=== Отчёт сохранён локально ===")
    print(str(out_dir))
    print("Передайте report.json и summary.md в чат; resources.csv поможет разобрать пики CPU/RAM.")
    print("Видео, расшифровка и текст сцен в отчёт не включаются.")
    if report.get("privacy", {}).get("uploaded_to_github"):
        print("Обезличенный отчёт опубликован в GitHub; ссылки показаны выше.")
    else:
        print("Отчёт остался локальным; в GitHub ничего не отправлено.")
    if any("stage_diagnostics" not in (run.get("metrics", {}).get("timings", {})) for run in report["runs"] if run.get("metrics")):
        print("ПРИМЕЧАНИЕ: stage_diagnostics отсутствует в ответе API; отчёт всё равно содержит wall time и Whisper/VLM timings.")
    return 0 if report["runs"] and any(run.get("metrics") for run in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
