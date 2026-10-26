#!/usr/bin/env python3
"""Create an empty, private VCE quality benchmark workspace without inventing metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Local benchmark workspace root.")
    parser.add_argument(
        "--overwrite-manifest", action="store_true",
        help="Replace only manifest.jsonl. Existing transcripts/outputs are never overwritten.",
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    private = root / "benchmarks" / "private"
    refs = private / "references"
    baseline = private / "runs" / "baseline"
    candidate = private / "runs" / "candidate"
    for folder in (refs, baseline, candidate, private / "results"):
        folder.mkdir(parents=True, exist_ok=True)

    created = []
    if write_if_missing(refs / "lecture-01.txt", ""):
        created.append("references/lecture-01.txt")
    if write_if_missing(
        baseline / "lecture-01.json",
        json.dumps({"segments": [{"scene_id": 1, "audio_transcript": ""}]}, ensure_ascii=False, indent=2) + "\n",
    ):
        created.append("runs/baseline/lecture-01.json")
    manifest = private / "manifest.jsonl"
    lines = [
        {
            "case_id": "lecture-01", "run_id": "baseline-large",
            "model": "whisper large", "backend": "openai-whisper/mps", "profile": "",
            "reference_text_path": "references/lecture-01.txt",
            "hypothesis_json_path": "runs/baseline/lecture-01.json",
            "hypothesis_json_pointer": "/segments/*/audio_transcript",
            "expected_terms": [],
        },
    ]
    if manifest.exists() and not args.overwrite_manifest:
        print(f"Manifest kept unchanged: {manifest}")
        print("To refresh only the manifest use --overwrite-manifest; existing transcript files will be preserved.")
    else:
        manifest.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
        print(f"Manifest ready: {manifest}")

    print(f"Workspace: {root}")
    print("Created empty starter files (if absent): " + (", ".join(created) if created else "none; all files already existed"))
    print("The initial manifest contains only a baseline row. Add a candidate row only when a real candidate output exists.")
    print("IMPORTANT: empty files are intentional placeholders. Add a manually reviewed reference and real VCE JSON before running meaningful WER/CER.")
    print("Do not put private videos, reference transcripts, or result files into Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
