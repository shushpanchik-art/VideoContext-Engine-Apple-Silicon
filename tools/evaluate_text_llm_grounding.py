#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-linked lexical overlap diagnostics for arbitrary VCE video outputs.

This is deliberately NOT a semantic entailment model. Low lexical overlap is a
review cue, not proof that a claim is false; high overlap is not proof it is true.
The evaluator is local-only and never modifies source or generated content.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "2026.10.27.2-independent-evidence"
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
SKIP_KEYS = {
    "id", "scene_id", "time", "timestamp", "start", "end", "start_time",
    "end_time", "duration", "iteration", "video_path", "image_path",
    "frame_path", "thumbnail_path", "output_directory", "txt_download_id",
    "json_download_id", "created_at", "updated_at",
}
TEXT_FIELD_NAMES = {
    "global_summary", "executive_summary", "audio_transcript", "transcript",
    "speech_text", "spoken_text", "utterance", "dialogue", "ocr_text",
    "recognized_text", "screen_text", "subtitle_text", "visual_description",
    "description", "text", "content", "caption", "title", "name", "value",
    "label", "topic", "improved_description", "summary", "speech", "transcription",
}
LIST_TEXT_PARENTS = {"topics", "entities", "visual_tags", "tags", "evidence", "key_points", "uncertainties"}

# Evidence is admitted only from VCE's source-modality fields. In particular,
# segments[].text_llm.*, precomputed summaries, topics, key points and enrichments
# must never be used as evidence for another Text LLM result (self-grounding leak).
SOURCE_EVIDENCE_ROOTS = {
    "audio_transcript", "transcript", "speech_text", "spoken_text", "spoken_words",
    "utterance", "dialogue", "transcription", "speech", "asr",
    "ocr_text", "ffmpeg_ocr_text", "recognized_text", "screen_text",
    "subtitle_text", "caption_text",
    "visual_description", "visual_tags", "image_description", "frame_description",
    "detected_objects", "object_detections",
}
DERIVED_OR_SUMMARY_KEYS = {
    "text_llm", "llm", "global_summary", "executive_summary", "summary",
    "topic", "topics", "key_points", "entities", "scene_enrichment",
    "improved_description", "uncertainties", "generated_summary",
}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot read JSON {path}: {exc}") from exc


def scene_id_for(segment: Any, index: int) -> int | None:
    if not isinstance(segment, dict):
        return index + 1
    value = segment.get("scene_id", index + 1)
    try:
        return int(value)
    except (TypeError, ValueError):
        return index + 1


def lexical_tokens(value: str) -> set[str]:
    """Unicode-aware, language-neutral token set; no domain or language stopword list."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return {
        token for token in TOKEN_RE.findall(normalized)
        if len(token) >= 2 or token.isdigit()
    }


def is_ignored_key(key: str) -> bool:
    folded = key.casefold()
    return (
        folded in SKIP_KEYS
        or folded.endswith(("_id", "_path", "_url", "_seconds", "_timestamp"))
    )


def looks_like_text(key: str, value: str, parent_key: str = "") -> bool:
    if not value.strip():
        return False
    folded = key.casefold()
    parent = parent_key.casefold()
    return (
        folded in TEXT_FIELD_NAMES
        or folded.endswith(("_text", "_transcript", "_description", "_caption", "_summary", "_label", "_title"))
        or parent in LIST_TEXT_PARENTS
        or len(value.strip()) >= 24
    )


def modality_for(path_parts: list[str]) -> str:
    """Classify by source-field provenance, never by the vocabulary of the claim."""
    keys = [part.casefold() for part in path_parts]
    joined = ".".join(keys)
    if any(re.search(r"(?:audio|transcript|speech|spoken|utterance|dialogue|asr|voice)", key) for key in keys):
        return "audio_transcript"
    if any(re.search(r"(?:ocr|recognized_text|screen_text|subtitle_text|caption_text)", key) for key in keys):
        return "ocr"
    if any(re.search(r"(?:visual|image|frame|visual_tags|objects|scene_description|tags|caption)", key) for key in keys):
        return "visual_description_or_tags"
    return "other_text"


def collect_segment_evidence(segment: dict[str, Any], scene_id: int, segment_index: int) -> list[dict[str, Any]]:
    """Collect only independent source-modality text, never derived Text LLM fields."""
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(text: str, path_parts: list[str], display_path: str) -> None:
        clean = text.strip()
        key = (display_path, clean)
        if not clean or key in seen:
            return
        seen.add(key)
        evidence.append({
            "scene_id": scene_id,
            "field_path": f"segments[{segment_index}].{display_path}",
            "modality": modality_for(path_parts),
            "text": clean,
            "tokens": lexical_tokens(clean),
        })

    def walk(value: Any, path_parts: list[str], display_path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                name = str(key)
                if is_ignored_key(name) or name.casefold() in DERIVED_OR_SUMMARY_KEYS:
                    continue
                child_path = f"{display_path}.{name}" if display_path else name
                walk(child, path_parts + [name], child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_path = f"{display_path}[{index}]"
                walk(child, path_parts + [str(index)], child_path)
        elif isinstance(value, str) and value.strip():
            add(value, path_parts, display_path)

    for key, value in segment.items():
        name = str(key)
        if is_ignored_key(name) or name.casefold() not in SOURCE_EVIDENCE_ROOTS:
            continue
        walk(value, [name], name)
    return evidence


def build_source_index(source_doc: dict[str, Any]) -> tuple[dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    segments = source_doc.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Source JSON must contain a non-empty segments[] array.")
    by_scene: dict[int, list[dict[str, Any]]] = defaultdict(list)
    all_evidence: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        sid = scene_id_for(segment, index)
        if sid is None:
            continue
        if sid in seen_ids:
            raise RuntimeError(f"Duplicate source scene_id: {sid}")
        seen_ids.add(sid)
        items = collect_segment_evidence(segment, sid, index)
        by_scene[sid].extend(items)
        all_evidence.extend(items)

    # meta.global_summary is model-derived context, not independent source evidence.
    # It is deliberately excluded to avoid a summary supporting itself by token overlap.
    return dict(by_scene), all_evidence


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_SPLIT_RE.split(str(text or "").strip()) if part.strip()]


def parse_scene_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def output_claims(output: Any) -> list[dict[str, Any]]:
    """Extract report-facing claims with pointers to their output fields."""
    if not isinstance(output, dict):
        return []
    claims: list[dict[str, Any]] = []

    def add_text(value: Any, field: str, scene_id: int | None = None, *, split: bool = True) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        pieces = split_sentences(value) if split else [value.strip()]
        for index, piece in enumerate(pieces):
            claims.append({
                "claim_index": len(claims) + 1,
                "output_field": field if len(pieces) == 1 else f"{field}#sentence_{index + 1}",
                "scene_id": scene_id,
                "text": piece,
                "tokens": lexical_tokens(piece),
            })

    add_text(output.get("executive_summary"), "executive_summary")
    topics = output.get("topics")
    if isinstance(topics, list):
        for index, item in enumerate(topics):
            if isinstance(item, str):
                add_text(item, f"topics[{index}]", split=False)
            elif isinstance(item, dict):
                add_text(item.get("topic") or item.get("name") or item.get("text"), f"topics[{index}]")
    points = output.get("key_points")
    if isinstance(points, list):
        for index, item in enumerate(points):
            if isinstance(item, dict):
                sid = parse_scene_id(item.get("scene_id"))
                add_text(item.get("text") or item.get("description") or item.get("content"),
                         f"key_points[{index}].text", sid)
            elif isinstance(item, str):
                add_text(item, f"key_points[{index}]", split=False)
    enrichments = output.get("scene_enrichment")
    if isinstance(enrichments, list):
        for index, item in enumerate(enrichments):
            if not isinstance(item, dict):
                continue
            sid = parse_scene_id(item.get("scene_id"))
            add_text(item.get("topic"), f"scene_enrichment[{index}].topic", sid, split=False)
            add_text(item.get("improved_description"), f"scene_enrichment[{index}].improved_description", sid)
            nested = item.get("key_points")
            if isinstance(nested, list):
                for nested_index, nested_item in enumerate(nested):
                    if isinstance(nested_item, str):
                        add_text(nested_item, f"scene_enrichment[{index}].key_points[{nested_index}]", sid)
                    elif isinstance(nested_item, dict):
                        add_text(
                            nested_item.get("text") or nested_item.get("description") or nested_item.get("content"),
                            f"scene_enrichment[{index}].key_points[{nested_index}].text", sid,
                        )
    entities = output.get("entities")
    if isinstance(entities, list):
        for index, item in enumerate(entities):
            if isinstance(item, str):
                add_text(item, f"entities[{index}]", split=False)
            elif isinstance(item, dict):
                add_text(item.get("name") or item.get("label") or item.get("value"),
                         f"entities[{index}].name", split=False)
    return claims


def overlap_detail(claim_tokens: set[str], evidence_items: list[dict[str, Any]], top_n: int = 3) -> dict[str, Any]:
    usable = [item for item in evidence_items if item.get("tokens")]
    source_union: set[str] = set()
    modality_union: dict[str, set[str]] = defaultdict(set)
    for item in usable:
        source_union.update(item["tokens"])
        modality_union[item["modality"]].update(item["tokens"])
    coverage = (len(claim_tokens & source_union) / len(claim_tokens)) if claim_tokens and usable else None
    modality_coverage = {
        modality: round(len(claim_tokens & tokens) / len(claim_tokens), 4)
        for modality, tokens in sorted(modality_union.items())
    } if claim_tokens and usable else {}

    ranked = []
    for item in usable:
        matched = claim_tokens & item["tokens"]
        if not matched:
            continue
        recall = len(matched) / len(claim_tokens) if claim_tokens else 0.0
        precision = len(matched) / len(item["tokens"]) if item["tokens"] else 0.0
        f1 = (2 * recall * precision / (recall + precision)) if recall + precision else 0.0
        ranked.append({
            "scene_id": item["scene_id"],
            "field_path": item["field_path"],
            "modality": item["modality"],
            "claim_token_recall": round(recall, 4),
            "token_f1": round(f1, 4),
            "overlap_tokens": sorted(matched)[:20],
            "evidence_excerpt": item["text"][:360],
            "_sort": (recall, len(matched), f1),
        })
    ranked.sort(key=lambda item: item["_sort"], reverse=True)
    for item in ranked:
        item.pop("_sort", None)
    return {
        "claim_token_recall": round(coverage, 4) if coverage is not None else None,
        "modality_token_recall": modality_coverage,
        "best_evidence": ranked[:top_n],
        "textual_evidence_items": len(usable),
    }


def evaluate_claim(claim: dict[str, Any], by_scene: dict[int, list[dict[str, Any]]],
                   all_evidence: list[dict[str, Any]], min_token_recall: float) -> dict[str, Any]:
    tokens = claim.pop("tokens")
    sid = claim["scene_id"]
    scoped_evidence = by_scene.get(sid, []) if sid is not None else all_evidence
    scoped = overlap_detail(tokens, scoped_evidence)
    global_detail = overlap_detail(tokens, all_evidence)
    findings: list[str] = []
    if not tokens:
        findings.append("insufficient_tokens_for_lexical_check")
    elif not scoped_evidence or scoped["textual_evidence_items"] == 0:
        findings.append("no_textual_source_evidence_in_scope")
    elif scoped["claim_token_recall"] is not None and scoped["claim_token_recall"] < min_token_recall:
        findings.append("low_lexical_overlap_requires_manual_review")
    if sid is not None and global_detail["claim_token_recall"] is not None:
        local_recall = scoped["claim_token_recall"]
        global_recall = global_detail["claim_token_recall"]
        if local_recall is not None and local_recall < min_token_recall <= global_recall:
            findings.append("overlap_found_elsewhere_in_video_not_in_referenced_scene")
    if not findings:
        findings.append("lexical_overlap_found_manual_review_still_required")
    claim.update({
        "claim_token_count": len(tokens),
        "scope": "scene" if sid is not None else "whole_video",
        "lexical_evidence": scoped,
        "whole_video_lexical_evidence": global_detail if sid is not None else None,
        "findings": findings,
        "manual_review_required": True,
    })
    return claim


def build_report(source_doc: dict[str, Any], outputs_doc: dict[str, Any],
                 min_token_recall: float = 0.15, *, source_json_path: str | None = None,
                 results_dir: str | None = None) -> dict[str, Any]:
    by_scene, all_evidence = build_source_index(source_doc)
    variants = outputs_doc.get("outputs_by_variant")
    if not isinstance(variants, dict) or not variants:
        raise RuntimeError("llm_outputs.json must contain a non-empty outputs_by_variant object.")
    modality_counts = Counter(item["modality"] for item in all_evidence)
    report: dict[str, Any] = {
        "script_version": SCRIPT_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_json_path": source_json_path,
        "results_dir": results_dir,
        "evaluation_type": "source_aware_lexical_overlap_screen",
        "semantic_correctness_evaluated": False,
        "semantic_quality_score": None,
        "min_token_recall_review_threshold": min_token_recall,
        "limitations": [
            "This tool measures lexical overlap only; it does not infer entailment, contradiction, truth, or visual facts.",
            "Low overlap can be correct paraphrase, translation, inference, or missing textual evidence; it is a manual-review cue, not a failure verdict.",
            "High overlap does not mean a claim is supported or true; copied text can still be misattributed or misinterpreted.",
            "Images and audio waveforms are not inspected directly. Only text in the source context JSON is considered.",
            "No language-specific stopword list, domain vocabulary, product-name registry, or video-specific rule is used.",
        ],
        "source_scene_count": len(by_scene),
        "source_text_evidence_count": len(all_evidence),
        "source_evidence_by_modality": dict(sorted(modality_counts.items())),
        "source_evidence_scope": sorted(SOURCE_EVIDENCE_ROOTS),
        "excluded_derived_source_fields": sorted(DERIVED_OR_SUMMARY_KEYS),
        "variants": {},
    }
    for variant, runs in variants.items():
        run_reports = []
        if not isinstance(runs, list):
            continue
        for run in runs:
            if not isinstance(run, dict):
                continue
            output = run.get("output")
            claims = [
                evaluate_claim(claim, by_scene, all_evidence, min_token_recall)
                for claim in output_claims(output)
            ]
            evaluable = [claim for claim in claims if claim["claim_token_count"] and
                         claim["lexical_evidence"]["claim_token_recall"] is not None]
            low_overlap = [claim for claim in claims if "low_lexical_overlap_requires_manual_review" in claim["findings"]]
            coverages = [claim["lexical_evidence"]["claim_token_recall"] for claim in evaluable]
            run_reports.append({
                "iteration": run.get("iteration"),
                "model": run.get("model"),
                "claim_count": len(claims),
                "evaluable_claim_count": len(evaluable),
                "low_overlap_review_cue_count": len(low_overlap),
                "mean_claim_token_recall_pct_not_semantic_score": (
                    round(statistics.mean(coverages) * 100, 1) if coverages else None
                ),
                "claims": claims,
            })
        if run_reports:
            report["variants"][variant] = {
                "run_count": len(run_reports),
                "claim_count": sum(r["claim_count"] for r in run_reports),
                "low_overlap_review_cue_count": sum(r["low_overlap_review_cue_count"] for r in run_reports),
                "low_overlap_review_cue_pct": (
                    round(
                        100 * sum(r["low_overlap_review_cue_count"] for r in run_reports)
                        / sum(r["claim_count"] for r in run_reports), 1
                    ) if sum(r["claim_count"] for r in run_reports) else None
                ),
                "runs": run_reports,
            }
    if not report["variants"]:
        raise RuntimeError("No usable variant runs were found in outputs_by_variant.")
    return report


def build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# VCE Text LLM — source-aware lexical overlap review",
        "",
        f"- Generated at: {report['generated_at']}",
        f"- Evaluation: {report['evaluation_type']}",
        f"- Source scenes: {report['source_scene_count']}",
        f"- Text evidence items: {report['source_text_evidence_count']}",
        f"- Review threshold: {report['min_token_recall_review_threshold']:.2f} claim-token coverage",
        "- **Semantic correctness is not evaluated by this report. There is no semantic quality score.**",
        "",
        "| Variant | Runs | Claims | Low-overlap cues | Cue rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in report["variants"].items():
        lines.append(
            f"| {name} | {row['run_count']} | {row['claim_count']} | {row['low_overlap_review_cue_count']} | "
            f"{(str(row['low_overlap_review_cue_pct']) + '%') if row['low_overlap_review_cue_pct'] is not None else 'n/a'} |"
        )
    for name, row in report["variants"].items():
        lines.extend(["", f"## {name}", ""])
        for run in row["runs"]:
            lines.extend([
                f"### Iteration {run.get('iteration', 'unknown')} ({run.get('model') or 'model not recorded'})",
                "",
                f"- Claims: {run['claim_count']}; evaluable: {run['evaluable_claim_count']}; low-overlap cues: {run['low_overlap_review_cue_count']}",
                "",
                "| Output field | Scene | Lexical coverage in scope | Review cues | Claim excerpt | Best evidence pointer |",
                "|---|---:|---:|---|---|---|",
            ])
            for claim in run["claims"]:
                coverage = claim["lexical_evidence"]["claim_token_recall"]
                coverage_label = "not evaluated" if coverage is None else f"{coverage * 100:.1f}%"
                cues = ", ".join(claim["findings"])
                best = claim["lexical_evidence"]["best_evidence"]
                pointer = "; ".join(
                    f"{e['field_path']} ({e['modality']}, recall {e['claim_token_recall']:.2f})"
                    for e in best[:2]
                ) or "no lexical match"
                text = claim["text"].replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {claim['output_field']} | {claim['scene_id'] if claim['scene_id'] is not None else 'whole video'} | "
                    f"{coverage_label} | {cues} | {text[:180]} | {pointer} |"
                )
    lines += [
        "",
        "## Interpretation and limits",
        "",
        "- A low-overlap cue means the generated wording shares few tokens with the relevant textual evidence. It is not proof of hallucination.",
        "- A high overlap value means only that words overlap. It is not proof that the claim is entailed, correctly attributed, or visually confirmed.",
        "- For scene-level text, same-scene evidence is the primary scope; whole-video evidence is shown separately to help spot possible scene misattribution.",
        "- The best evidence pointers are candidates for a human to inspect, not citations proving correctness.",
        "- This screen is content-agnostic: it does not search for particular titles, brands, acronyms, outcomes, or domain facts.",
        "- The source context may omit evidence present in the actual image or waveform. This tool does not load or inspect the raw video.",
        "- Every claim remains marked for manual review; do not compare this diagnostic to a semantic pass/fail score.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local, source-aware lexical overlap screen for VCE Text LLM outputs.")
    parser.add_argument("--results-dir", type=Path, required=True,
                        help="Completed benchmark run containing llm_outputs.json")
    parser.add_argument("--source-json", type=Path, required=True,
                        help="Local source context JSON containing segments[]")
    parser.add_argument("--output-dir", type=Path,
                        help="Output directory; defaults to RESULTS_DIR/quality_grounding_audit")
    parser.add_argument("--min-token-recall", type=float, default=0.15,
                        help="Low-overlap review-cue threshold from 0 to 1; not a truth threshold (default: 0.15)")
    args = parser.parse_args()
    try:
        if not 0.0 <= args.min_token_recall <= 1.0:
            raise RuntimeError("--min-token-recall must be between 0 and 1.")
        results_dir = args.results_dir.expanduser().resolve()
        source_path = args.source_json.expanduser().resolve()
        outputs_path = results_dir / "llm_outputs.json"
        if not source_path.is_file():
            raise RuntimeError(f"Source JSON not found: {source_path}")
        if not outputs_path.is_file():
            raise RuntimeError(f"llm_outputs.json not found: {outputs_path}")
        report = build_report(
            read_json(source_path), read_json(outputs_path), args.min_token_recall,
            source_json_path=str(source_path), results_dir=str(results_dir),
        )
        out_dir = (args.output_dir or (results_dir / "quality_grounding_audit")).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "quality_grounding_audit.json"
        md_path = out_dir / "quality_grounding_audit.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(build_markdown(report), encoding="utf-8")
        print(build_markdown(report))
        print(f"\nJSON: {json_path}")
        print(f"Markdown: {md_path}")
        print("Local-only: source context and generated text are not uploaded; generated content is not modified.")
        return 0
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
