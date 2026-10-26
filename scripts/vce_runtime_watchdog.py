#!/usr/bin/env python3
"""Observe one local VCE analysis and collect runtime, process, memory and GPU evidence.

This tool does not patch the running application, alter settings, or upload video/results.
GPU power is system-wide evidence; it does not prove per-operator execution on Metal.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, platform, re, shutil, signal, subprocess, sys, threading, time, urllib.request
from pathlib import Path
from typing import Any

DEFAULT_VIDEO = Path.home() / "Downloads/NSR Specification/🔥 Новые модули NSR Specification автоматизация проектирования с ИИ 🔥.mp4"
DEFAULT_SETTINGS = Path.home() / "VideoContext-Engine/videocontext_settings.json"
DEFAULT_OUTPUT = Path.home() / "Downloads/VCE_watchdog_runs"
EVIDENCE_MARKERS = ("[VCE runtime]", "[VCE MLX]", "[VCE MLX TRACE]", "[VCE Whisper]", "Device(cpu", "Device(gpu", "mlx-whisper", "metal_smoke_test", "Metal", "MPSGraph", "CoreML", "tesseract", "ffmpeg", "Qwen", "Traceback", "KeyError", "Exception", "ERROR", "generation_stream")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def append_jsonl(path: Path, obj: Any) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def capture(argv: list[str], timeout: float = 8.0) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
        return None, out, (err + "\ntimeout").strip()
    except Exception as exc:
        return None, "", repr(exc)


def parse_process_table_output(output: str) -> list[dict[str, Any]]:
    """Parse macOS ps output with dot OR comma CPU decimal separators."""
    found = []
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+([\d.,]+)\s+(\d+)\s+(\S+)\s+(.*)$", line)
        if not match:
            continue
        pid_s, ppid_s, cpu_s, rss_s, elapsed, command = match.groups()
        try:
            cpu = float(cpu_s.replace(",", "."))
            pid, ppid, rss_kb = int(pid_s), int(ppid_s), int(rss_s)
        except ValueError:
            continue
        found.append({"pid": pid, "ppid": ppid, "cpu_percent": cpu, "rss_kb": rss_kb,
                      "rss_mib": round(rss_kb / 1024, 1), "elapsed": elapsed, "command": command[:1200]})
    return found


def process_table() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rc, out, err = capture(["/bin/ps", "-axo", "pid=,ppid=,%cpu=,rss=,etime=,command="], timeout=4)
    rows = parse_process_table_output(out) if rc == 0 else []
    return rows, {"returncode": rc, "rows_parsed": len(rows), "stderr": err[:1000], "sample_prefix": out[:500] if not rows else ""}


def pids_listening(port: int) -> list[int]:
    rc, out, _ = capture(["/usr/sbin/lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"], timeout=3)
    if rc not in (0, 1):
        return []
    return sorted({int(token) for token in out.split() if token.isdigit()})


def select_processes(rows: list[dict[str, Any]], roots: set[int]) -> list[dict[str, Any]]:
    by_pid = {row["pid"]: row for row in rows}
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row["ppid"] in selected and row["pid"] not in selected:
                selected.add(row["pid"])
                changed = True
    for row in rows:
        if re.search(r"VideoContext|uvicorn|mlx_vlm|mlx-whisper|videocontext", row.get("command", ""), re.I):
            selected.add(row["pid"])
    return [by_pid[pid] for pid in sorted(selected) if pid in by_pid]


def summarize_process_samples(path: Path) -> dict[str, Any]:
    by_pid: dict[int, dict[str, Any]] = {}
    count = invalid = 0
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                invalid += 1
                continue
            count += 1
            for proc in record.get("processes", []):
                pid = proc.get("pid")
                if not isinstance(pid, int):
                    continue
                info = by_pid.setdefault(pid, {"pid": pid, "command": proc.get("command"), "max_rss_mib": 0.0, "max_cpu_percent": 0.0, "sample_count": 0})
                info["sample_count"] += 1
                info["max_rss_mib"] = max(info["max_rss_mib"], float(proc.get("rss_mib") or 0))
                info["max_cpu_percent"] = max(info["max_cpu_percent"], float(proc.get("cpu_percent") or 0))
                if proc.get("command"):
                    info["command"] = proc["command"]
    ranked = sorted(by_pid.values(), key=lambda x: x["max_rss_mib"], reverse=True)
    return {"sample_records": count, "invalid_jsonl_records": invalid, "processes_ranked_by_max_rss": ranked,
            "max_observed_rss_mib": ranked[0]["max_rss_mib"] if ranked else None,
            "max_observed_rss_pid": ranked[0]["pid"] if ranked else None,
            "note": "Maximum across processes selected by ps polling; RSS is not total unified-memory or Metal allocation."}


def memory_sample() -> dict[str, Any]:
    info = {"captured_at": now()}
    for key, command in (
        ("memory_pressure", ["/usr/bin/memory_pressure", "-Q"]),
        ("vm_stat", ["/usr/bin/vm_stat"]),
        ("swap", ["/usr/sbin/sysctl", "vm.swapusage"]),
    ):
        rc, out, err = capture(command, timeout=5)
        info[key] = {"returncode": rc, "stdout": (out or err)[:12000]}
    return info


def powermetrics_command() -> tuple[list[str] | None, str]:
    binary = "/usr/bin/powermetrics"
    if not Path(binary).is_file():
        return None, "not_installed"
    command = [binary, "--samplers", "gpu_power", "-i", "1000", "-n", "1"]
    if os.geteuid() == 0:
        return command, "root"
    sudo = shutil.which("sudo")
    return ([sudo, "-n", *command], "sudo_noninteractive") if sudo else (None, "sudo_missing")


def save_vmmap(pid: int, out_dir: Path, elapsed: float) -> dict[str, Any]:
    path = out_dir / f"vmmap_pid{pid}_t{int(elapsed)}.txt"
    rc, out, err = capture(["/usr/bin/vmmap", str(pid)], timeout=15)
    path.write_text(out, encoding="utf-8")
    pattern = re.compile(r"mlx|metal|mpsgraph|coreml|torch|whisper|tesseract|ffmpeg|onnx|\.metallib|ane|gpu", re.I)
    return {"pid": pid, "elapsed_seconds": elapsed, "returncode": rc, "stderr": err[:1000], "file": str(path),
            "matching_lines": [line.strip() for line in out.splitlines() if pattern.search(line)][:500]}


def save_stack(pid: int, out_dir: Path, elapsed: float) -> dict[str, Any]:
    path = out_dir / f"sample_pid{pid}_t{int(elapsed)}.txt"
    rc, out, err = capture(["/usr/bin/sample", str(pid), "2", "1", "-file", str(path)], timeout=10)
    if not path.exists() and out:
        path.write_text(out, encoding="utf-8")
    return {"pid": pid, "elapsed_seconds": elapsed, "returncode": rc, "stderr": err[:1000], "file": str(path), "exists": path.exists()}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def runtime_verdict(observed_dir: Path | None, console_path: Path, mappings_path: Path, power_path: Path) -> dict[str, Any]:
    timings = load_json(observed_dir / "stage_timings.json") if observed_dir else {}
    timings = timings if isinstance(timings, dict) else {}
    vlm = timings.get("vlm", {}) if isinstance(timings.get("vlm"), dict) else {}
    llm = timings.get("text_llm", {}) if isinstance(timings.get("text_llm"), dict) else {}
    vlm_runtime = vlm.get("runtime", {}) if isinstance(vlm.get("runtime"), dict) else {}
    llm_runtime = llm.get("runtime", {}) if isinstance(llm.get("runtime"), dict) else {}
    vlm_device, llm_device = str(vlm_runtime.get("device", "unknown")), str(llm_runtime.get("device", "unknown"))
    vlm_configured_device = str(vlm_runtime.get("configured_device", vlm_device))
    vlm_default_device_observed = str(vlm_runtime.get("default_device_observed", "not_recorded"))
    vlm_device_source = str(vlm_runtime.get("device_source", "legacy_or_unspecified"))
    console = console_path.read_text(encoding="utf-8", errors="replace") if console_path.exists() else ""
    log_parts = [console]
    if observed_dir:
        for log_path in (observed_dir / "server_events.jsonl", observed_dir / "server_log_key_lines.txt"):
            if not log_path.exists():
                continue
            for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(raw_line)
                    line_text = str(event.get("line", raw_line)) if isinstance(event, dict) else raw_line
                except Exception:
                    line_text = raw_line
                log_parts.append(line_text)
    app_text = "\n".join(log_parts)
    trace_snapshots = []
    for line_text in app_text.splitlines():
        marker_pos = line_text.find("[VCE MLX TRACE] ")
        if marker_pos < 0:
            continue
        try:
            obj = json.loads(line_text[marker_pos + len("[VCE MLX TRACE] "):])
            trace_snapshots.append({
                "phase": obj.get("phase"),
                "function": obj.get("function"),
                "thread_name": obj.get("thread_name"),
                "thread_ident": obj.get("thread_ident"),
                "mlx_default_device": obj.get("mlx_default_device"),
                "mlx_gpu_available": obj.get("mlx_gpu_available"),
                "metal_available": obj.get("metal_available"),
                "common_generation_stream": obj.get("mlx_vlm.generate.common.generation_stream"),
                "dispatch_generation_stream": obj.get("mlx_vlm.generate.dispatch.generation_stream"),
                "mlx_snapshot_error": obj.get("mlx_snapshot_error"),
                "error": obj.get("error"),
            })
        except Exception:
            pass
    vlm_generation_stream_devices = sorted({
        str(stream.get("device"))
        for snap in trace_snapshots
        for stream in (snap.get("common_generation_stream"), snap.get("dispatch_generation_stream"))
        if isinstance(stream, dict) and stream.get("device")
    })
    vlm_scene_trace_snapshots = [
        snap for snap in trace_snapshots
        if "describe_scene" in str(snap.get("function", "")).lower()
    ]
    gpu_generation_stream_seen = any("gpu" in device.lower() for device in vlm_generation_stream_devices)
    maps = mappings_path.read_text(encoding="utf-8", errors="replace") if mappings_path.exists() else ""
    power_records = []
    if power_path.exists():
        for line in power_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
                if obj.get("status") == "sampled" and obj.get("returncode") == 0:
                    power_records.append(obj)
            except Exception:
                pass
    power_text = "\n".join(str(x.get("stdout", "")) + "\n" + str(x.get("stderr", "")) for x in power_records)
    gpu_stream_setup = bool(re.search(r"Applied GPU-aware|generation_stream.*Device\(gpu", app_text, re.I))
    mlx_reported = bool(vlm_runtime.get("backend") == "MLX" or llm_runtime.get("backend") == "MLX" or "mlx-whisper" in app_text or "[VCE runtime]" in app_text or "[VCE MLX TRACE]" in app_text)
    metal_reported = bool(vlm_runtime.get("accelerator") == "Metal" or llm_runtime.get("accelerator") == "Metal" or vlm_runtime.get("metal_smoke_test") == "passed" or re.search(r"accelerator\s*[=:]\s*Metal|metal_smoke_test.{0,40}passed", app_text, re.I))
    gpu_power_label = bool(re.search(r"GPU Power|GPU active residency|GPU HW active|GPU frequency|GPU active", power_text, re.I))
    mapped = bool(re.search(r"mlx|libmetal|metal\.framework|mpsgraph|coreml", maps, re.I))
    if "cpu" in vlm_device.lower() and "gpu" in llm_device.lower():
        status = "VLM_CPU_REPORTED_TEXT_LLM_GPU_REPORTED"
    elif "cpu" in vlm_device.lower():
        status = "VLM_CPU_REPORTED"
    elif "gpu" in vlm_device.lower():
        status = "VLM_GPU_REPORTED"
    else:
        status = "VLM_DEVICE_UNKNOWN"
    explicit_gpu_target = (vlm_device_source == "explicit_gpu_target"
                           and "gpu" in vlm_configured_device.lower())
    if explicit_gpu_target and "cpu" in vlm_default_device_observed.lower() and gpu_generation_stream_seen:
        overall = "VLM_GPU_TARGET_CONFIGURED_BUT_DEFAULT_DEVICE_SNAPSHOT_CPU"
    elif explicit_gpu_target and gpu_generation_stream_seen:
        overall = "VLM_GPU_TARGET_AND_GENERATION_STREAM_CONFIGURED_EXECUTION_UNPROVEN"
    elif status == "VLM_GPU_REPORTED" and mlx_reported and metal_reported and gpu_power_label:
        overall = "MLX_METAL_AND_SYSTEM_GPU_ACTIVITY_CORROBORATED"
    elif "cpu" in vlm_device.lower() and gpu_stream_setup:
        overall = "VLM_DEVICE_CPU_CONFLICTS_WITH_GPU_STREAM_SETUP"
    elif mlx_reported and metal_reported:
        overall = "MLX_METAL_REPORTED_BUT_GPU_EXECUTION_NOT_PROVEN"
    elif mlx_reported:
        overall = "MLX_REPORTED_METAL_UNCONFIRMED"
    else:
        overall = "NO_DIRECT_MLX_RUNTIME_EVIDENCE"
    return {"overall_verdict": overall, "vlm_device": vlm_device, "text_llm_device": llm_device,
            "vlm_configured_device": vlm_configured_device,
            "vlm_device_source": vlm_device_source,
            "vlm_default_device_observed": vlm_default_device_observed,
            "vlm_generation_stream_devices": vlm_generation_stream_devices,
            "vlm_scene_trace_snapshot_count": len(vlm_scene_trace_snapshots),
            "gpu_generation_stream_seen": gpu_generation_stream_seen,
            "vlm_runtime": vlm_runtime, "text_llm_runtime": llm_runtime,
            "mlx_reported": mlx_reported, "metal_reported": metal_reported,
            "gpu_stream_setup_log_found": gpu_stream_setup,
            "successful_powermetrics_samples": len(power_records), "gpu_power_activity_label_found": gpu_power_label,
            "ml_or_metal_libraries_mapped": mapped,
            "runtime_trace_snapshot_count": len(trace_snapshots),
            "runtime_trace_snapshots": trace_snapshots[-16:],
            "limits": [
                "Device values come from structured stage_timings.json, not from arbitrary stream-setup text.",
                "gpu_stream_smoke_test proves only that a small arithmetic probe was explicitly scheduled on the GPU stream.",
                "configured_device records the chosen target; default_device_observed is a process-global snapshot and may reflect another thread's mx.stream context.",
                "Generation-stream snapshots prove configuration, not that every VLM operator executed on GPU.",
                "powermetrics is system-wide and cannot attribute GPU activity to the VCE PID.",
                "vmmap proves mappings, not execution; sample captures CPU stacks, not GPU kernels.",
                "Per-operator GPU execution needs VLM-side instrumentation or a profiler that can attribute GPU work to the process.",
            ]}


def main() -> int:
    if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
        print("ERROR: watchdog рассчитан на macOS / Apple Silicon.", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--settings", default=str(DEFAULT_SETTINGS))
    ap.add_argument("--runner", required=True, help="Path to scripts/vce_observed_run.py")
    ap.add_argument("--api-url", default="http://127.0.0.1:7555")
    ap.add_argument("--out-root", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--powermetrics", action="store_true")
    ap.add_argument("--sample-stacks", action="store_true")
    ap.add_argument("--vmmap", action="store_true")
    args = ap.parse_args()
    video, settings, runner = Path(args.video).expanduser(), Path(args.settings).expanduser(), Path(args.runner).expanduser()
    for label, path in (("video", video), ("settings", settings), ("runner", runner)):
        if not path.is_file():
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            return 2
    try:
        with urllib.request.urlopen(args.api_url.rstrip("/") + "/openapi.json", timeout=3) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
    except Exception as exc:
        print(f"ERROR: VCE API not reachable at {args.api_url}: {exc}", file=sys.stderr)
        return 2

    root = Path(args.out_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    out = root / ("watchdog_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    out.mkdir()
    console_path, process_path = out / "observed_runner_console.log", out / "process_samples.jsonl"
    memory_path, power_path = out / "memory_samples.jsonl", out / "powermetrics_gpu_power.jsonl"
    mapping_path, stack_path = out / "runtime_mappings.jsonl", out / "stack_samples.jsonl"
    evidence_path, ps_debug_path = out / "runtime_evidence.jsonl", out / "ps_parser_diagnostics.jsonl"
    config = {"started_at": now(), "platform": platform.platform(), "machine": platform.machine(),
              "python": sys.version, "video": str(video), "settings": str(settings), "runner": str(runner),
              "api_url": args.api_url, "interval_seconds": args.interval, "powermetrics_requested": args.powermetrics,
              "sample_stacks": args.sample_stacks, "vmmap": args.vmmap,
              "note": "No video/response upload. Settings are read-only."}
    (out / "watchdog_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    port_match = re.search(r":(\d+)(?:/|$)", args.api_url.rstrip("/"))
    port = int(port_match.group(1)) if port_match else 7555
    cmd = [sys.executable, str(runner), "--video", str(video), "--settings", str(settings),
           "--api-url", args.api_url, "--out-root", str(out / "observed_runs"), "--interval", str(max(0.5, args.interval))]
    pow_cmd, pow_mode = powermetrics_command() if args.powermetrics else (None, "disabled")
    append_jsonl(power_path, {"at": now(), "status": pow_mode, "command": pow_cmd})
    print("VCE Runtime Watchdog v2")
    print("Output:", out)
    print("Runner:", runner)
    print("GPU power sampler:", pow_mode if args.powermetrics else "disabled")
    print("Starting analysis; do not launch another job on this server.")

    child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    def drain() -> None:
        assert child.stdout is not None
        with console_path.open("w", encoding="utf-8") as log:
            for line in child.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
                if any(marker.lower() in line.lower() for marker in EVIDENCE_MARKERS):
                    append_jsonl(evidence_path, {"at": now(), "source": "runner_console", "line": line.rstrip()})
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    start = time.monotonic()
    next_memory, next_map, next_stack, next_power = start, start + 8, start + 15, start
    roots: set[int] = set()
    interrupted = False
    try:
        while child.poll() is None:
            elapsed = round(time.monotonic() - start, 3)
            active = pids_listening(port)
            roots.update(active)
            rows, ps_diag = process_table()
            selected = select_processes(rows, roots | set(active))
            append_jsonl(ps_debug_path, {"at": now(), "elapsed_seconds": elapsed, **ps_diag})
            append_jsonl(process_path, {"at": now(), "elapsed_seconds": elapsed, "listener_pids": active, "processes": selected})
            if time.monotonic() >= next_memory:
                snap = memory_sample()
                snap["elapsed_seconds"] = elapsed
                append_jsonl(memory_path, snap)
                next_memory = time.monotonic() + 15
            if args.powermetrics and time.monotonic() >= next_power:
                if pow_cmd:
                    rc, out_text, err_text = capture(pow_cmd, timeout=9)
                    append_jsonl(power_path, {"at": now(), "elapsed_seconds": elapsed, "returncode": rc,
                        "stdout": out_text, "stderr": err_text, "mode": pow_mode,
                        "status": "sampled" if rc == 0 else "unavailable_or_permission_denied"})
                    next_power = time.monotonic() + (12 if rc == 0 else 60)
                else:
                    append_jsonl(power_path, {"at": now(), "elapsed_seconds": elapsed, "status": pow_mode, "detail": "No command available"})
                    next_power = time.monotonic() + 60
            if args.vmmap and time.monotonic() >= next_map:
                for pid in sorted(roots):
                    if any(r.get("pid") == pid for r in selected):
                        append_jsonl(mapping_path, save_vmmap(pid, out, elapsed))
                next_map = time.monotonic() + 30
            if args.sample_stacks and time.monotonic() >= next_stack:
                for pid in sorted(roots):
                    if any(r.get("pid") == pid for r in selected):
                        append_jsonl(stack_path, save_stack(pid, out, elapsed))
                next_stack = time.monotonic() + 30
            time.sleep(max(0.5, args.interval))
    except KeyboardInterrupt:
        interrupted = True
        print("\nCtrl-C: stopping runner; the server-side job may continue.")
        try:
            child.send_signal(signal.SIGINT)
        except Exception:
            pass
    finally:
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=3)
        reader.join(timeout=3)

    observed_root = out / "observed_runs"
    observed_dirs = sorted((p for p in observed_root.glob("run_*") if p.is_dir())) if observed_root.exists() else []
    observed_dir = observed_dirs[-1] if observed_dirs else None
    metrics = summarize_process_samples(process_path)
    metrics["ps_parser_diagnostics"] = str(ps_debug_path)
    (out / "process_resource_summary.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verdict = runtime_verdict(observed_dir, console_path, mapping_path, power_path)
    (out / "watchdog_verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# VCE Runtime Watchdog v2", "", f"- Finished: {now()}", f"- Runner exit code: {child.returncode}",
              f"- Observed run: {observed_dir}", f"- Max sampled RSS (MiB): {metrics.get('max_observed_rss_mib')}",
              f"- Max RSS PID: {metrics.get('max_observed_rss_pid')}", f"- VLM device: {verdict['vlm_device']}",
              f"- Text LLM device: {verdict['text_llm_device']}", f"- Verdict: **{verdict['overall_verdict']}**",
              "", "## Runtime evidence", json.dumps(verdict, ensure_ascii=False, indent=2), "",
              "## Interpretation limits", *[f"- {line}" for line in verdict["limits"]], "",
              "Artifacts: " + ", ".join(p.name for p in (console_path, process_path, memory_path, power_path, mapping_path, stack_path, evidence_path, ps_debug_path)),
              "Do not upload full response/transcript to public repositories."]
    (out / "watchdog_verdict.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    summary = {"finished_at": now(), "runner_exit_code": child.returncode, "interrupted": interrupted,
               "watchdog_directory": str(out), "observed_run_directory": str(observed_dir) if observed_dir else None,
               "process_resource_summary": metrics, "verdict": verdict,
               "note": "GPU power is system-wide. Runtime traces record per-call configuration, not per-op GPU proof."}
    (out / "watchdog_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n=== WATCHDOG COMPLETE ===")
    print("Output:", out)
    print("Verdict:", verdict["overall_verdict"])
    print("Max sampled RSS MiB:", metrics.get("max_observed_rss_mib"))
    print("Summary:", out / "watchdog_summary.json")
    return 130 if interrupted else (child.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
