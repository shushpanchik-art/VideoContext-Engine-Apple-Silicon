#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality-first audit of VCE Text LLM benchmark variants.

Uses only Python's standard library. This is a deterministic screening aid,
not a semantic truth oracle; the generated report always requires human review.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
from pathlib import Path

SCRIPT_VERSION = "2026.10.27.2-quality-anchors"
DEFAULT_RESULTS = Path.home() / "Downloads" / "vce_text_llm_benchmarks" / "20261027T005522Z_2cb3f5"
DEFAULT_SOURCE = (
    Path.home() / "Downloads" / "NSR Specification" /
    "Новые модули NSR Specification автоматизация проектирования _context.json"
)

SKIP_KEYS = {
    "scene_id", "id", "time", "timestamp", "start", "end", "start_time",
    "end_time", "duration", "iteration", "txt_download_id", "json_download_id",
    "output_directory", "video_path", "image_path", "frame_path", "thumbnail_path",
}
TEXT_KEYS = {
    "global_summary", "audio_transcript", "transcript", "speech_text",
    "ocr_text", "visual_description", "description", "text", "content",
    "caption", "title", "name", "value", "label", "topic", "improved_description",
}
KNOWN_TERMS = (
    "NSR Specification", "BuildChat", "CADLib", "nanoCAD", "КСИ",
    "ИИ ПОМОЩНИК", "ИИ-помощник",
)
STANDARD_RE = re.compile(
    r"(?i)\b(?:ГОСТ(?:\s+Р)?|СП|СНиП|ISO|IEC|EN|DIN|ASTM)\s*[-№:]?\s*"
    r"[0-9][A-ZА-ЯЁa-zа-яё0-9./_-]*"
)
DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
RATIO_RE = re.compile(r"\b\d{1,4}\s*[:/]\s*\d{1,4}\b")
PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
UNIT_RE = re.compile(
    r"(?i)\b\d+(?:[.,]\d+)?\s*(?:мм|см|м|км|кг|г|мг|секунд(?:а|ы)?|с|минут(?:а|ы)?|"
    r"мин|час(?:а|ов)?|°C|кВт|МВт|МПа|Па|ГПа|px|fps|Гц|МГц|ГБ|МБ|GB|MB)\b"
)
LONG_ID_RE = re.compile(r"(?<!\w)\d{4,}(?!\w)")
NUMERIC_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)")
GARBLED_RE = re.compile(r"(?i)\bAND\s+JU\b|\bNC\s+END\b|^\s*D_\s*$")
OVERCLAIM_RE = re.compile(
    r"(?i)(?:визуально\s+подтвержден\w*\s+(?:выявлен\w*\s+)?(?:коллизи|несоответств)|"
    r"на\s+экране\s+(?:явно\s+)?подтвержден\w*\s+(?:коллизи|несоответств)|"
    r"визуально\s+доказан\w*\s+(?:результат|несоответств|коллизи))"
)
GENERIC_RE = re.compile(
    r"(?i)(?:сцена\s+(?:содержит|показывает)\s+(?:технический\s+этап|результат\s+проверки|"
    r"работу\s+с\s+данными|итоговый\s+вывод)|"
    r"сцена\s+содержит\s+результат\s+проверки\s+или\s+итоговый\s+вывод)"
)
TRAILING_FRAGMENT_RE = re.compile(r"(?i)(?:\bAND|\bTHE|\bNC|[,:;—-])\s*$")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Не удалось прочитать JSON {path}: {exc}") from exc


def collect_strings(obj, *, skip_keys=None, parent=""):
    """Walk nested input/outputs, keeping readable text but excluding IDs/times."""
    skip_keys = SKIP_KEYS if skip_keys is None else skip_keys
    out = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            k = str(key).casefold()
            if k in {x.casefold() for x in skip_keys}:
                continue
            if isinstance(value, str) and (
                k in TEXT_KEYS
                or parent.casefold() in {"topics", "entities", "visual_tags", "tags", "evidence"}
                or len(value.strip()) >= 18
            ):
                if value.strip():
                    out.append(value.strip())
            elif isinstance(value, (dict, list)):
                out.extend(collect_strings(value, skip_keys=skip_keys, parent=k))
    elif isinstance(obj, list):
        for value in obj:
            # Preserve concise labels/names from topic, entity, tag and evidence lists.
            if isinstance(value, str) and parent.casefold() in {"topics", "entities", "visual_tags", "tags", "evidence", "key_points", "uncertainties"}:
                if value.strip():
                    out.append(value.strip())
            else:
                out.extend(collect_strings(value, skip_keys=skip_keys, parent=parent))
    elif isinstance(obj, str) and (
        len(obj.strip()) >= 18 or parent.casefold() in {"topics", "entities", "visual_tags", "tags", "evidence"}
    ):
        if obj.strip():
            out.append(obj.strip())
    return out


def source_data(path: Path):
    data = read_json(path)
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("В исходном JSON отсутствует непустой массив segments.")
    meta = data.get("meta") or {}
    evidence_parts = [str(meta.get("global_summary") or "")]
    for seg in segments:
        evidence_parts.extend(collect_strings(seg))
    evidence_text = "\n".join(x for x in evidence_parts if x)
    ids = []
    for i, seg in enumerate(segments, 1):
        sid = seg.get("scene_id", i) if isinstance(seg, dict) else i
        try:
            ids.append(int(sid))
        except (TypeError, ValueError):
            ids.append(i)
    if len(set(ids)) != len(ids):
        raise RuntimeError("В исходном JSON scene_id не уникальны; исправьте источник перед аудитом.")
    return data, segments, ids, evidence_text


def normalize_atom(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold().strip(".,;:()[]{}\"'")


# Core technical context: avoid counting video timecodes, frame counters and arbitrary OCR digits
# as critical facts. Context-qualified numbers are useful for engineering reports.
RATIO_CONTEXT_RE = re.compile(
    r"(?i)(?:уклон|пандус|норматив|эвакуац|соотношени|наклон|коэффициент|"
    r"пропорци|slope|ratio|grade)"
)
DATE_CONTEXT_RE = re.compile(
    r"(?i)(?:КСИ|классификатор|стандарт|верси|редакци|действи|ГОСТ|СП\s|ISO|"
    r"дата|утвержд|принят|актуальн)"
)
UNIT_CONTEXT_RE = re.compile(
    r"(?i)(?:уклон|пандус|норматив|размер|длин|ширин|высот|диаметр|толщин|"
    r"допустим|значени|коэффициент|расстояни|нагрузк|давлени|мощност|частот|"
    r"разрешени|параметр|характеристик)"
)
PERCENT_CONTEXT_RE = re.compile(
    r"(?i)(?:эффективност|процент|готовност|точност|доля|успеш|показател|"
    r"производительност|экономи|сокращени|увеличени)"
)
ID_CONTEXT_RE = re.compile(
    r"(?i)(?:код|идентификатор|шифр|номер|документ|запис|требовани|КСИ|ГОСТ|СП\s|"
    r"каталог|классификатор|артикул|id\b)"
)
# Do not treat durations such as "0.451452с" as core report facts.
ENGINEERING_UNIT_RE = re.compile(
    r"(?i)\b\d+(?:[.,]\d+)?\s*(?:мм|см|м|км|кг|г|мг|°C|кВт|МВт|МПа|Па|ГПа|"
    r"px|fps|Гц|МГц|ГБ|МБ|GB|MB)\b"
)


def _context_matches(regex, text: str, context_regex=None, radius: int = 100):
    found = set()
    for match in regex.finditer(text):
        if context_regex is not None:
            left = max(0, match.start() - radius)
            right = min(len(text), match.end() + radius)
            if not context_regex.search(text[left:right]):
                continue
        atom = normalize_atom(match.group(0))
        if atom:
            found.add(atom)
    return found


def find_claim_numbers(text: str):
    """Extract report-relevant technical anchors, not every OCR number/timecode."""
    claims = set()
    claims |= _context_matches(STANDARD_RE, text)
    claims |= _context_matches(RATIO_RE, text, RATIO_CONTEXT_RE)
    claims |= _context_matches(DATE_RE, text, DATE_CONTEXT_RE)
    claims |= _context_matches(ENGINEERING_UNIT_RE, text, UNIT_CONTEXT_RE)
    claims |= _context_matches(PERCENT_RE, text, PERCENT_CONTEXT_RE)
    claims |= _context_matches(LONG_ID_RE, text, ID_CONTEXT_RE)
    for term in KNOWN_TERMS:
        if term.casefold() in text.casefold():
            claims.add(normalize_atom(term))
    return claims


def important_atoms(evidence: str):
    """Core facts supported by the evidence bundle; timecodes/random OCR are excluded."""
    return find_claim_numbers(evidence)


def output_text(obj):
    # Ignore timestamps and IDs for content/fact comparisons.
    return "\n".join(collect_strings(obj))


def parse_seconds(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return float(raw)
    parts = raw.split(":")
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return None


def source_scene_times(segments, ids):
    result = {}
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        sid = ids[index]
        value = None
        for key in ("start", "start_time", "start_seconds", "timestamp", "time"):
            if key in segment:
                value = parse_seconds(segment.get(key))
                if value is not None:
                    break
        if value is not None:
            result[sid] = value
    return result


def is_garbled(text: str):
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return False
    if GARBLED_RE.search(clean):
        return True
    # Very short, all-caps fragments in a scene description often indicate truncation.
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", clean)
    if len(clean) <= 14 and len(words) <= 3 and words and clean.upper() == clean:
        if clean.casefold() not in {"the end", "ok", "n/a", "xml", "json"}:
            return True
    return False


def output_items(output, key):
    value = output.get(key)
    return value if isinstance(value, list) else []


def quality_run(output, source_segments, scene_ids, evidence, metric_run):
    issues = []
    warnings = []
    output = output if isinstance(output, dict) else {}
    required = ("executive_summary", "topics", "key_points", "entities")
    missing_fields = [key for key in required if key not in output or output.get(key) in (None, "", [])]
    if missing_fields:
        issues.append("missing_required_fields:" + ",".join(missing_fields))

    summary = str(output.get("executive_summary") or "").strip()
    topics = output_items(output, "topics")
    key_points = output_items(output, "key_points")
    enrichment = output_items(output, "scene_enrichment")
    expected = set(scene_ids)
    actual_ids = []
    invalid_ids = []
    for item in key_points:
        if not isinstance(item, dict):
            invalid_ids.append(str(item))
            continue
        try:
            actual_ids.append(int(item.get("scene_id")))
        except (TypeError, ValueError):
            invalid_ids.append(str(item.get("scene_id")))
    seen_ids = set(actual_ids)
    missing_scene_ids = sorted(expected - seen_ids)
    duplicate_scene_ids = sorted({sid for sid in actual_ids if actual_ids.count(sid) > 1})
    unexpected_scene_ids = sorted(seen_ids - expected)
    if invalid_ids:
        issues.append("key_points_missing_or_invalid_scene_id")
    if unexpected_scene_ids:
        issues.append("unexpected_scene_ids:" + ",".join(map(str, unexpected_scene_ids)))
    if duplicate_scene_ids:
        issues.append("duplicate_scene_ids:" + ",".join(map(str, duplicate_scene_ids)))
    if missing_scene_ids:
        issues.append("missing_scene_points:" + ",".join(map(str, missing_scene_ids)))

    point_texts = []
    for item in key_points:
        if isinstance(item, dict):
            point_texts.append(str(item.get("text") or item.get("description") or ""))
    garbled_points = [t for t in point_texts if is_garbled(t)]
    if garbled_points:
        issues.append("garbled_or_truncated_key_point")
    all_text = output_text(output)
    if len(summary) < 80:
        warnings.append("executive_summary_short_or_incomplete")
    if TRAILING_FRAGMENT_RE.search(summary):
        warnings.append("executive_summary_may_be_truncated")

    # A scene point should usually have a timestamp; the timestamp is separate from text.
    missing_times = []
    bad_times = []
    expected_times = source_scene_times(source_segments, scene_ids)
    for item in key_points:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("scene_id"))
        except (TypeError, ValueError):
            continue
        tval = item.get("time") or item.get("timestamp")
        parsed = parse_seconds(tval)
        if parsed is None:
            missing_times.append(sid)
        elif sid in expected_times and abs(parsed - expected_times[sid]) > max(8.0, 0.15 * max(expected_times[sid], 1.0)):
            bad_times.append({"scene_id": sid, "reported": tval, "source_start_seconds": expected_times[sid]})
    if missing_times:
        warnings.append("missing_key_point_timestamps:" + ",".join(map(str, sorted(set(missing_times)))))
    if bad_times:
        warnings.append("timestamps_differ_from_source_start:" + json.dumps(bad_times, ensure_ascii=False))

    source_atoms = important_atoms(evidence)
    generated_atoms = find_claim_numbers(all_text)
    missing_atoms = sorted(source_atoms - {normalize_atom(x) for x in generated_atoms | {normalize_atom(s) for s in re.findall(r"(?i)\b(?:NSR Specification|BuildChat|CADLib|nanoCAD|КСИ|ИИ ПОМОЩНИК|ИИ-помощник)\b", all_text)}})
    # Include entities explicitly named in source when counting technical-term coverage.
    terms_in_source = {normalize_atom(term) for term in KNOWN_TERMS if term.casefold() in evidence.casefold()}
    lower_output = all_text.casefold()
    missing_terms = sorted(term for term in terms_in_source if term not in normalize_atom(lower_output))
    missing_atoms = sorted(set(missing_atoms) | set(missing_terms))
    atom_coverage = 1.0 if not source_atoms else len(source_atoms & (generated_atoms | terms_in_source.intersection({normalize_atom(t) for t in KNOWN_TERMS if normalize_atom(t) in normalize_atom(lower_output)}))) / len(source_atoms)

    unsupported_atoms = sorted(generated_atoms - source_atoms)
    if unsupported_atoms:
        warnings.append("numeric_or_identifier_claims_not_found_in_source:" + ",".join(unsupported_atoms[:30]))
    if missing_atoms:
        warnings.append("important_source_atoms_missing:" + ",".join(missing_atoms[:30]))

    overclaims = []
    for match in OVERCLAIM_RE.finditer(all_text):
        overclaims.append(match.group(0))
    if overclaims:
        warnings.append("claims_of_visual_confirmation_need_manual_evidence_check")

    generic_texts = [str(item.get(field) or "") for item in enrichment if isinstance(item, dict)
                     for field in ("topic", "improved_description")]
    generic_hits = [t for t in generic_texts if GENERIC_RE.search(t)]
    if generic_hits:
        warnings.append(f"generic_scene_enrichment_texts:{len(generic_hits)}")
    repeated_points = len(point_texts) - len(set(re.sub(r"\W+", " ", x).strip().casefold() for x in point_texts if x.strip()))
    if repeated_points:
        warnings.append(f"duplicated_key_point_texts:{repeated_points}")

    json_valid = isinstance(output, dict) and bool(summary)
    near_limit = bool(metric_run.get("primary_output_near_token_limit"))
    repair_passes = int(metric_run.get("json_repair_passes") or 0)
    parse_failures = int(metric_run.get("json_parse_failures") or 0)
    if near_limit:
        issues.append("primary_output_near_token_limit")
    if repair_passes or parse_failures:
        issues.append(f"json_repair_or_parse_failure:repairs={repair_passes},parse_failures={parse_failures}")
    if not json_valid:
        issues.append("invalid_or_empty_output_object")

    scene_coverage = len(expected & seen_ids) / len(expected) if expected else 1.0
    point_timestamps_present = 1.0 if not key_points else max(0.0, (len(key_points) - len(missing_times)) / len(key_points))
    # Score is a screening heuristic. Gate issues override a high score.
    completeness_score = 30.0 * scene_coverage * point_timestamps_present
    structure_score = 15.0 if json_valid and not missing_fields and not invalid_ids else 5.0
    technical_score = 25.0 * max(0.0, min(1.0, atom_coverage))
    integrity_score = 15.0
    if garbled_points:
        integrity_score -= min(15.0, 8.0 * len(garbled_points))
    if TRAILING_FRAGMENT_RE.search(summary):
        integrity_score -= 4.0
    if generic_hits:
        integrity_score -= min(6.0, 1.5 * len(generic_hits))
    integrity_score = max(0.0, integrity_score)
    grounding_score = 15.0
    grounding_score -= min(8.0, 1.5 * len(unsupported_atoms))
    grounding_score -= min(7.0, 3.5 * len(overclaims))
    grounding_score = max(0.0, grounding_score)
    score = round(completeness_score + structure_score + technical_score + integrity_score + grounding_score, 1)

    critical = bool(issues)
    # These are not proofs of correctness: any semantic/grounding warning requires a reviewer.
    if critical:
        status = "HOLD"
    elif warnings or score < 85:
        status = "REVIEW"
    else:
        status = "MANUAL_REVIEW_REQUIRED"

    return {
        "quality_score_heuristic": score,
        "status": status,
        "hard_issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "scene_coverage_pct": round(scene_coverage * 100, 1),
        "expected_scene_count": len(expected),
        "key_points_count": len(key_points),
        "unique_scene_ids_count": len(seen_ids),
        "missing_scene_ids": missing_scene_ids,
        "duplicate_scene_ids": duplicate_scene_ids,
        "unexpected_scene_ids": unexpected_scene_ids,
        "garbled_key_points": garbled_points,
        "missing_timestamps_scene_ids": sorted(set(missing_times)),
        "timestamp_deviation_examples": bad_times[:10],
        "technical_atom_coverage_pct": round(atom_coverage * 100, 1),
        "source_technical_atoms_count": len(source_atoms),
        "missing_source_atoms": missing_atoms,
        "unsupported_numeric_or_identifier_atoms": unsupported_atoms,
        "visual_overclaim_phrases": overclaims,
        "generic_enrichment_snippets": generic_hits[:10],
        "near_output_token_limit": near_limit,
        "json_repair_passes": repair_passes,
        "json_parse_failures": parse_failures,
        "subscores": {
            "scene_completeness_and_timestamps": round(completeness_score, 1),
            "structure": round(structure_score, 1),
            "technical_fact_coverage": round(technical_score, 1),
            "text_integrity": round(integrity_score, 1),
            "grounding_proxy": round(grounding_score, 1),
        },
        "manual_review_required": True,
    }


def find_metrics_run(metrics_doc, variant, iteration):
    for row in metrics_doc.get("runs", []):
        if row.get("variant") == variant and int(row.get("iteration", -1)) == int(iteration):
            return row
    return {}


def latest_results_dir(root: Path):
    if (root / "metrics.local.json").is_file() and (root / "llm_outputs.json").is_file():
        return root
    options = []
    for child in root.iterdir() if root.exists() else []:
        if child.is_dir() and (child / "metrics.local.json").is_file() and (child / "llm_outputs.json").is_file():
            options.append(child)
    if not options:
        raise RuntimeError(
            f"Не найден results-dir с metrics.local.json и llm_outputs.json: {root}. "
            "Укажите --results-dir с каталогом завершённого запуска."
        )
    return max(options, key=lambda p: p.stat().st_mtime)


def build_report(report):
    rows = report["variants"]
    lines = [
        "# VCE Text LLM — quality-first audit",
        "",
        f"- Run: {report['run_id']}",
        f"- Audited at: {report['audited_at']}",
        f"- Source scenes: {report['source_scene_count']}",
        "- Policy: quality gate first; runtime is only a tie-breaker after defects are reviewed.",
        "- This is a deterministic heuristic audit, not proof of semantic correctness.",
        "",
        "| Variant | Quality heuristic | Status | Scene coverage | Technical atom coverage | Hard issues | Median seconds |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for name, row in sorted(rows.items(), key=lambda item: (
        0 if item[1]["status"] == "HOLD" else 1 if item[1]["status"] == "REVIEW" else 2,
        -item[1]["quality_score_heuristic"],
        item[1].get("median_seconds") or float("inf"),
    )):
        lines.append(
            f"| {name} | {row['quality_score_heuristic']:.1f}/100 | {row['status']} | "
            f"{row['scene_coverage_pct']:.1f}% | {row['technical_atom_coverage_pct']:.1f}% | "
            f"{len(row['hard_issues'])} | {row.get('median_seconds') if row.get('median_seconds') is not None else '—'} |"
        )
    lines += ["", "## Variant detail", ""]
    for name, row in rows.items():
        lines += [
            f"### {name}",
            "",
            f"- Status: **{row['status']}**",
            f"- Heuristic quality score: {row['quality_score_heuristic']:.1f}/100",
            f"- Median seconds: {row.get('median_seconds') if row.get('median_seconds') is not None else 'not recorded'}",
            f"- Hard issues: {', '.join(row['hard_issues']) if row['hard_issues'] else 'none detected by automatic checks'}",
            f"- Warnings: {', '.join(row['warnings']) if row['warnings'] else 'none detected by automatic checks'}",
            f"- Missing scene IDs: {row['missing_scene_ids']}",
            f"- Garbled snippets: {row['garbled_key_points']}",
            f"- Missing technical atoms: {row['missing_source_atoms'][:20]}",
            f"- Unsupported numeric/identifier atoms: {row['unsupported_numeric_or_identifier_atoms'][:20]}",
            "",
        ]
    lines += [
        "## Interpretation",
        "",
        "- HOLD means at least one automatic integrity/completeness blocker exists; do not adopt that variant yet.",
        "- REVIEW means the structure passed the hard gate but warnings need human review.",
        "- MANUAL_REVIEW_REQUIRED means no blocker was detected; it is not an automatic quality approval.",
        "- Compare the actual summary, scene points, OCR/transcript facts and uncertain claims against the source JSON before promoting any candidate.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Quality-first audit for VCE Text LLM benchmark variants.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {SCRIPT_VERSION}")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS,
                        help="Completed benchmark result directory containing metrics.local.json and llm_outputs.json")
    parser.add_argument("--source-json", type=Path, default=DEFAULT_SOURCE,
                        help="Local VCE context JSON whose segments[] are the source evidence")
    parser.add_argument("--output-dir", type=Path,
                        help="Where to save the quality audit; defaults to RESULTS_DIR/quality_audit")
    args = parser.parse_args()

    try:
        results_dir = latest_results_dir(args.results_dir.expanduser().resolve())
        source_path = args.source_json.expanduser().resolve()
        metrics_path = results_dir / "metrics.local.json"
        outputs_path = results_dir / "llm_outputs.json"
        if not source_path.is_file():
            raise RuntimeError(f"Не найден source JSON: {source_path}")
        metrics_doc = read_json(metrics_path)
        outputs_doc = read_json(outputs_path)
        _source, source_segments, scene_ids, evidence = source_data(source_path)
        output_variants = outputs_doc.get("outputs_by_variant")
        if not isinstance(output_variants, dict) or not output_variants:
            raise RuntimeError("В llm_outputs.json отсутствует outputs_by_variant.")

        report = {
            "script_version": SCRIPT_VERSION,
            "run_id": (metrics_doc.get("manifest") or {}).get("run_id") or (outputs_doc.get("manifest") or {}).get("run_id"),
            "audited_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_json_path": str(source_path),
            "results_dir": str(results_dir),
            "source_scene_count": len(scene_ids),
            "policy": "quality_gate_first_speed_tiebreak_only",
            "variants": {},
        }
        for variant, runs in output_variants.items():
            run_results = []
            durations = []
            for item in runs if isinstance(runs, list) else []:
                iteration = item.get("iteration", 0)
                model_run = find_metrics_run(metrics_doc, variant, iteration)
                audit = quality_run(item.get("output"), source_segments, scene_ids, evidence, model_run)
                run_seconds = model_run.get("total_seconds")
                if isinstance(run_seconds, (int, float)):
                    durations.append(float(run_seconds))
                run_results.append({
                    "iteration": iteration,
                    "model": model_run.get("model"),
                    "total_seconds": run_seconds if isinstance(run_seconds, (int, float)) else item.get("metrics", {}).get("total_seconds"),
                    **audit,
                })
            if not run_results:
                continue
            scores = [x["quality_score_heuristic"] for x in run_results]
            median_seconds = statistics.median(durations) if durations else None
            hard_issues = sorted({issue for r in run_results for issue in r["hard_issues"]})
            warnings = sorted({warning for r in run_results for warning in r["warnings"]})
            statuses = {r["status"] for r in run_results}
            if "HOLD" in statuses:
                status = "HOLD"
            elif "REVIEW" in statuses:
                status = "REVIEW"
            else:
                status = "MANUAL_REVIEW_REQUIRED"
            report["variants"][variant] = {
                "status": status,
                "quality_score_heuristic": round(statistics.median(scores), 1),
                "worst_run_score": min(scores),
                "median_seconds": round(median_seconds, 3) if median_seconds is not None else None,
                "scene_coverage_pct": round(statistics.median([r["scene_coverage_pct"] for r in run_results]), 1),
                "technical_atom_coverage_pct": round(statistics.median([r["technical_atom_coverage_pct"] for r in run_results]), 1),
                "hard_issues": hard_issues,
                "warnings": warnings,
                "missing_scene_ids": sorted({sid for r in run_results for sid in r["missing_scene_ids"]}),
                "garbled_key_points": sorted({text for r in run_results for text in r["garbled_key_points"]}),
                "missing_source_atoms": sorted({atom for r in run_results for atom in r["missing_source_atoms"]}),
                "unsupported_numeric_or_identifier_atoms": sorted({
                    atom for r in run_results for atom in r["unsupported_numeric_or_identifier_atoms"]
                }),
                "runs": run_results,
            }

        eligible = [
            (name, row) for name, row in report["variants"].items()
            if row["status"] == "MANUAL_REVIEW_REQUIRED" and not row["hard_issues"]
        ]
        promising = ["A_thinking_off", "B_fast_summary"]
        promising += [name for name in report["variants"] if name.startswith("D_Qwen3_5_4B")]
        report["promising_variants_checked"] = [name for name in promising if name in report["variants"]]
        if eligible:
            report["preliminary_recommendation"] = sorted(
                eligible, key=lambda item: (-item[1]["quality_score_heuristic"], item[1]["median_seconds"] or float("inf"))
            )[0][0]
            report["recommendation_note"] = (
                "Это только кандидат на ручную приёмку: автоматические проверки не доказывают смысловую точность."
            )
        else:
            report["preliminary_recommendation"] = None
            report["recommendation_note"] = (
                "Ни один вариант не прошёл автоматические проверки без блокирующих дефектов; "
                "сохраняйте baseline как контроль и исправьте качество/аудит прежде, чем выбирать ускоренный вариант."
            )

        outdir = (args.output_dir or (results_dir / "quality_audit")).expanduser().resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        json_path = outdir / "quality_audit.json"
        md_path = outdir / "quality_audit.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(build_report(report), encoding="utf-8")
        print(build_report(report))
        print("\nJSON:", json_path)
        print("Markdown:", md_path)
        print("Все выводы остаются локальными; этот скрипт ничего не загружает на GitHub.")
        return 0
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
