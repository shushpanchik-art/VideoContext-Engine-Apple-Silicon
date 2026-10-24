#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate VCE transcript quality and runtime from a private JSONL manifest.

No third-party packages are required. The tool never modifies input reports.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import statistics
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CSV_FIELDS = [
    "case_id", "run_id", "model", "backend", "profile",
    "video_seconds", "wall_seconds", "rtf", "stage_seconds_json",
    "reference_words", "hypothesis_words", "word_edits", "wer",
    "reference_characters", "hypothesis_characters", "character_edits", "cer",
    "expected_term_count", "found_term_count", "term_recall", "missing_terms_json",
]


def normalize_text(value: str) -> str:
    """Canonical Unicode + case + whitespace normalization, retaining punctuation."""
    value = unicodedata.normalize("NFC", value).casefold()
    return " ".join(value.split())


def normalize_words(value: str) -> list[str]:
    """Normalize for WER by making punctuation token separators, not deleting digits."""
    value = normalize_text(value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return value.split()


def edit_distance(reference: list[Any] | str, hypothesis: list[Any] | str) -> int:
    """Levenshtein edit distance with linear auxiliary memory."""
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for i, ref_item in enumerate(reference, start=1):
        current = [i]
        for j, hyp_item in enumerate(hypothesis, start=1):
            current.append(min(
                current[j - 1] + 1,
                previous[j] + 1,
                previous[j - 1] + (ref_item != hyp_item),
            ))
        previous = current
    return previous[-1]


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    return value


def _path_from(base_dir: Path, value: Any, label: str) -> Path:
    raw = _text(value, label).strip()
    if not raw:
        raise ValueError(f"{label} cannot be empty.")
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def _read_json_pointer(root: Any, pointer: str) -> list[str]:
    """Read a JSON Pointer; '*' expands object values or list items."""
    if pointer == "":
        nodes = [root]
    else:
        if not pointer.startswith("/"):
            raise ValueError("hypothesis_json_pointer must be a JSON Pointer (start with '/').")
        nodes = [root]
        for token in pointer.split("/")[1:]:
            token = token.replace("~1", "/").replace("~0", "~")
            next_nodes: list[Any] = []
            for node in nodes:
                if token == "*":
                    if isinstance(node, dict):
                        next_nodes.extend(node.values())
                    elif isinstance(node, list):
                        next_nodes.extend(node)
                elif isinstance(node, dict) and token in node:
                    next_nodes.append(node[token])
                elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
                    next_nodes.append(node[int(token)])
            if not next_nodes:
                raise ValueError(f"JSON Pointer did not resolve at token {token!r}.")
            nodes = next_nodes

    fragments: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            if value.strip():
                fragments.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict) and isinstance(value.get("text"), str):
            collect(value["text"])

    for node in nodes:
        collect(node)
    if not fragments:
        raise ValueError("The selected JSON Pointer contains no text strings.")
    return fragments


def _load_hypothesis(record: dict[str, Any], base_dir: Path) -> tuple[str, str]:
    if record.get("hypothesis_text_path"):
        path = _path_from(base_dir, record["hypothesis_text_path"], "hypothesis_text_path")
        return path.read_text(encoding="utf-8-sig"), str(path)
    if record.get("hypothesis_json_path"):
        pointer = record.get("hypothesis_json_pointer")
        if not isinstance(pointer, str):
            raise ValueError("hypothesis_json_path requires hypothesis_json_pointer.")
        path = _path_from(base_dir, record["hypothesis_json_path"], "hypothesis_json_path")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return " ".join(_read_json_pointer(payload, pointer)), str(path)
    raise ValueError("Each manifest record needs hypothesis_text_path or hypothesis_json_path + pointer.")


def _optional_nonnegative_number(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a non-negative number.")
    value = float(value)
    if value < 0 or value != value or value == float("inf"):
        raise ValueError(f"{key} must be a finite non-negative number.")
    return value


def _timings_payload(record: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    if not record.get("timings_json_path"):
        return {}
    path = _path_from(base_dir, record["timings_json_path"], "timings_json_path")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("timings_json_path must contain a JSON object.")
    return payload


def _load_stage_seconds(record: dict[str, Any], base_dir: Path, timings: dict[str, Any]) -> dict[str, float]:
    value = timings.get("stage_seconds", record.get("stage_seconds", {}))
    if not isinstance(value, dict):
        raise ValueError("stage_seconds must be an object mapping stage names to seconds.")
    result: dict[str, float] = {}
    for name, seconds in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("stage_seconds keys must be non-empty strings.")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError(f"Stage {name!r} must have a numeric duration.")
        seconds = float(seconds)
        if seconds < 0 or seconds != seconds or seconds == float("inf"):
            raise ValueError(f"Stage {name!r} must have a finite non-negative duration.")
        result[name] = seconds
    return result


def load_manifest(path: Path) -> list[dict[str, Any]]:
    manifest = path.expanduser().resolve()
    base_dir = manifest.parent
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("Each JSONL line must be an object.")
            case_id = _text(record.get("case_id"), "case_id").strip()
            run_id = _text(record.get("run_id", "default"), "run_id").strip()
            if not case_id or not run_id:
                raise ValueError("case_id and run_id must be non-empty.")
            unique_key = (run_id, case_id)
            if unique_key in seen:
                raise ValueError(f"Duplicate case_id {case_id!r} for run_id {run_id!r}.")
            seen.add(unique_key)

            timings = _timings_payload(record, base_dir)
            merged_record = dict(record)
            for key in ("video_seconds", "wall_seconds"):
                if key not in merged_record and key in timings:
                    merged_record[key] = timings[key]

            ref_path = _path_from(base_dir, record.get("reference_text_path"), "reference_text_path")
            reference = ref_path.read_text(encoding="utf-8-sig")
            hypothesis, hypothesis_source = _load_hypothesis(record, base_dir)
            video_seconds = _optional_nonnegative_number(merged_record, "video_seconds")
            wall_seconds = _optional_nonnegative_number(merged_record, "wall_seconds")
            if video_seconds == 0:
                raise ValueError("video_seconds must be greater than zero when specified.")
            stage_seconds = _load_stage_seconds(merged_record, base_dir, timings)

            terms = record.get("expected_terms", [])
            if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
                raise ValueError("expected_terms must be an array of non-empty strings.")
            record_out = {
                "case_id": case_id,
                "run_id": run_id,
                "model": str(record.get("model", "")),
                "backend": str(record.get("backend", "")),
                "profile": str(record.get("profile", "")),
                "reference": reference,
                "hypothesis": hypothesis,
                "reference_source": str(ref_path),
                "hypothesis_source": hypothesis_source,
                "video_seconds": video_seconds,
                "wall_seconds": wall_seconds,
                "stage_seconds": stage_seconds,
                "expected_terms": terms,
            }
            records.append(_score_record(record_out))
        except Exception as exc:
            raise ValueError(f"{manifest}:{line_number}: {exc}") from exc
    if not records:
        raise ValueError(f"Manifest contains no records: {manifest}")
    return records


def _score_record(record: dict[str, Any]) -> dict[str, Any]:
    reference = record["reference"]
    hypothesis = record["hypothesis"]
    ref_words = normalize_words(reference)
    hyp_words = normalize_words(hypothesis)
    word_edits = edit_distance(ref_words, hyp_words)
    wer = word_edits / len(ref_words) if ref_words else None

    ref_chars_text = normalize_text(reference)
    hyp_chars_text = normalize_text(hypothesis)
    ref_chars = list(ref_chars_text)
    hyp_chars = list(hyp_chars_text)
    char_edits = edit_distance(ref_chars, hyp_chars)
    cer = char_edits / len(ref_chars) if ref_chars else None

    expected_terms = record["expected_terms"]
    normalized_hypothesis = normalize_text(hypothesis)
    found_terms = [term for term in expected_terms if normalize_text(term) in normalized_hypothesis]
    missing_terms = [term for term in expected_terms if term not in found_terms]
    term_recall = len(found_terms) / len(expected_terms) if expected_terms else None

    video_seconds = record["video_seconds"]
    wall_seconds = record["wall_seconds"]
    rtf = wall_seconds / video_seconds if wall_seconds is not None and video_seconds else None
    return {
        **record,
        "reference_words": len(ref_words),
        "hypothesis_words": len(hyp_words),
        "word_edits": word_edits,
        "wer": wer,
        "reference_characters": len(ref_chars),
        "hypothesis_characters": len(hyp_chars),
        "character_edits": char_edits,
        "cer": cer,
        "expected_term_count": len(expected_terms),
        "found_term_count": len(found_terms),
        "term_recall": term_recall,
        "missing_terms": missing_terms,
        "rtf": rtf,
    }


def _mean(values: Iterable[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return statistics.mean(present) if present else None


def _median(values: Iterable[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return statistics.median(present) if present else None


def _round_metrics(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def summarize(records: list[dict[str, Any]], baseline: str | None = None) -> dict[str, Any]:
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_run[record["run_id"]].append(record)

    runs: dict[str, Any] = {}
    for run_id, items in sorted(by_run.items()):
        stage_names = sorted({stage for item in items for stage in item["stage_seconds"]})
        stage_medians = {
            name: _round_metrics(_median(item["stage_seconds"].get(name) for item in items))
            for name in stage_names
        }
        runs[run_id] = {
            "case_count": len(items),
            "quality_case_count": sum(item["wer"] is not None for item in items),
            "mean_wer": _round_metrics(_mean(item["wer"] for item in items)),
            "median_wer": _round_metrics(_median(item["wer"] for item in items)),
            "mean_cer": _round_metrics(_mean(item["cer"] for item in items)),
            "median_cer": _round_metrics(_median(item["cer"] for item in items)),
            "mean_term_recall": _round_metrics(_mean(item["term_recall"] for item in items)),
            "median_rtf": _round_metrics(_median(item["rtf"] for item in items)),
            "median_wall_seconds": _round_metrics(_median(item["wall_seconds"] for item in items)),
            "median_video_seconds": _round_metrics(_median(item["video_seconds"] for item in items)),
            "median_stage_seconds": stage_medians,
            "models": sorted({item["model"] for item in items if item["model"]}),
            "backends": sorted({item["backend"] for item in items if item["backend"]}),
            "profiles": sorted({item["profile"] for item in items if item["profile"]}),
        }

    comparisons: dict[str, Any] = {}
    if baseline:
        if baseline not in by_run:
            raise ValueError(f"Baseline run_id {baseline!r} not present in manifest.")
        base_by_case = {item["case_id"]: item for item in by_run[baseline]}
        for run_id, items in sorted(by_run.items()):
            if run_id == baseline:
                continue
            paired = [(base_by_case[item["case_id"]], item) for item in items if item["case_id"] in base_by_case]
            pairs_quality = [(a, b) for a, b in paired if a["wer"] is not None and b["wer"] is not None]
            pairs_cer = [(a, b) for a, b in paired if a["cer"] is not None and b["cer"] is not None]
            pairs_rtf = [(a, b) for a, b in paired if a["rtf"] is not None and b["rtf"] is not None]
            base_rtf = _mean(a["rtf"] for a, _ in pairs_rtf)
            candidate_rtf = _mean(b["rtf"] for _, b in pairs_rtf)
            comparisons[run_id] = {
                "baseline": baseline,
                "paired_case_count": len(paired),
                "paired_quality_case_count": len(pairs_quality),
                "mean_wer_delta_candidate_minus_baseline": _round_metrics(_mean(b["wer"] - a["wer"] for a, b in pairs_quality)),
                "mean_cer_delta_candidate_minus_baseline": _round_metrics(_mean(b["cer"] - a["cer"] for a, b in pairs_cer)),
                "mean_rtf_delta_candidate_minus_baseline": _round_metrics(_mean(b["rtf"] - a["rtf"] for a, b in pairs_rtf)),
                "rtf_change_percent": _round_metrics(
                    ((candidate_rtf / base_rtf) - 1.0) * 100
                    if base_rtf is not None and base_rtf > 0 and candidate_rtf is not None else None
                ),
            }

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "runs": runs,
        "baseline": baseline,
        "comparisons": comparisons,
    }


def write_outputs(records: list[dict[str, Any]], summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "per_case.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for item in records:
            row = {key: item.get(key) for key in CSV_FIELDS}
            row["stage_seconds_json"] = json.dumps(item["stage_seconds"], ensure_ascii=False, sort_keys=True)
            row["missing_terms_json"] = json.dumps(item["missing_terms"], ensure_ascii=False)
            writer.writerow(row)

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# VCE quality/speed benchmark",
        "",
        f"Generated (UTC): {summary['generated_at_utc']}",
        "",
        "WER/CER are lower-is-better. RTF = wall_seconds / video_seconds; below 1.0 means processing faster than real time.",
        "Empty reference transcripts are excluded from WER/CER aggregates. Stage times are recorded as provided and may overlap.",
        "",
        "| Run | Cases | Mean WER | Mean CER | Mean term recall | Median RTF | Median wall (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for run_id, item in summary["runs"].items():
        fmt = lambda x: "—" if x is None else f"{x:.4f}"
        lines.append(
            f"| {run_id} | {item['case_count']} | {fmt(item['mean_wer'])} | {fmt(item['mean_cer'])} | "
            f"{fmt(item['mean_term_recall'])} | {fmt(item['median_rtf'])} | {fmt(item['median_wall_seconds'])} |"
        )
        if item["median_stage_seconds"]:
            stages = ", ".join(f"{name}: {value:g}s" for name, value in item["median_stage_seconds"].items() if value is not None)
            lines.extend(["", f"- **{run_id} median stage times:** {stages}"])
    if summary["comparisons"]:
        lines.extend([
            "", "## Paired comparisons", "",
            "| Candidate | Paired cases | Δ mean WER | Δ mean CER | Δ mean RTF | RTF change |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for run_id, item in summary["comparisons"].items():
            fmt = lambda x: "—" if x is None else f"{x:+.4f}"
            rtf_pct = "—" if item["rtf_change_percent"] is None else f"{item['rtf_change_percent']:+.2f}%"
            lines.append(
                f"| {run_id} | {item['paired_case_count']} | {fmt(item['mean_wer_delta_candidate_minus_baseline'])} | "
                f"{fmt(item['mean_cer_delta_candidate_minus_baseline'])} | {fmt(item['mean_rtf_delta_candidate_minus_baseline'])} | {rtf_pct} |"
            )
    lines.extend(["", "Detailed per-case metrics are in per_case.csv; machine-readable summary is in summary.json.", ""])
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Private JSONL manifest. Paths are relative to the manifest directory.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for per_case.csv and summary files.")
    parser.add_argument("--baseline", help="Optional run_id to compare candidate runs against.")
    args = parser.parse_args(argv)

    try:
        records = load_manifest(args.manifest)
        summary = summarize(records, args.baseline)
        write_outputs(records, summary, args.out_dir)
    except (OSError, json.JSONDecodeError, ValueError, csv.Error) as exc:
        print(f"BENCHMARK ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Benchmark complete: {len(records)} run/case records")
    print(f"Per-case metrics: {(args.out_dir / 'per_case.csv').resolve()}")
    print(f"Summary: {(args.out_dir / 'summary.md').resolve()}")
    for run_id, metrics in summary["runs"].items():
        wer = "n/a" if metrics["mean_wer"] is None else f"{metrics['mean_wer']:.4f}"
        cer = "n/a" if metrics["mean_cer"] is None else f"{metrics['mean_cer']:.4f}"
        rtf = "n/a" if metrics["median_rtf"] is None else f"{metrics['median_rtf']:.3f}"
        print(f"  {run_id}: mean WER={wer}, mean CER={cer}, median RTF={rtf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
