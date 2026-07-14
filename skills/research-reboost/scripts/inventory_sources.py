#!/usr/bin/env python3
"""Inventory ResearchOS Pre-T5 inputs without interpreting their semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    path: str
    category: str
    requirement: str
    purpose: str
    precedence: int


REQUIRED_SOURCES = (
    SourceSpec("SRC_PROJECT", "project.yaml", "project", "required", "Project identity, task, and user constraints", 1),
    SourceSpec("SRC_SYNTHESIS", "literature/synthesis.md", "literature_synthesis", "required", "Field synthesis, method families, and research gap", 5),
    SourceSpec("SRC_SYNTHESIS_WORKBENCH", "literature/synthesis_workbench.json", "synthesis_workbench", "required", "Structured literature evidence and confidence", 5),
    SourceSpec("SRC_DOMAIN_MAP", "literature/domain_map.json", "domain_map", "required", "Domain and bridge relationships", 5),
    SourceSpec("SRC_COMPARISON_TABLE", "literature/comparison_table.csv", "comparison_table", "required", "Comparable methods, modules, datasets, and metrics", 5),
    SourceSpec("SRC_HYPOTHESES", "ideation/hypotheses.md", "hypotheses", "required", "Central hypothesis and mechanism candidates", 3),
    SourceSpec("SRC_EXP_PLAN", "ideation/exp_plan.yaml", "experiment_plan", "required", "Planned datasets, metrics, protocols, and ablations", 2),
    SourceSpec("SRC_IDEA_SCORECARD", "ideation/idea_scorecard.yaml", "idea_scorecard", "required", "Idea quality, feasibility, and weaknesses", 3),
    SourceSpec("SRC_RISKS", "ideation/risks.md", "risks", "required", "Scientific and execution risks", 3),
    SourceSpec("SRC_NOVELTY", "ideation/novelty_audit.md", "novelty_audit", "required", "Nearest work, required baselines, novelty and claim boundaries", 2),
)

OPTIONAL_PATHS = (
    ("literature/deep_read_notes", "paper_note", "Detailed paper evidence"),
    ("literature/bridge_notes", "bridge_paper_note", "Cross-domain paper evidence"),
    ("literature/shallow_read_notes", "abstract_note", "Abstract-only retrieval hints"),
    ("resources", "resource", "Existing local resources"),
    ("user_seeds/seed_external_resources.jsonl", "user_seed", "User-provided external resource hints"),
    ("user_seeds/bridge_domains.yaml", "user_seed", "User-provided bridge-domain hints"),
    ("ideation/hypothesis_brief.yaml", "pre_novelty_context", "Selected Candidate lineage and T4.5 search scope; not execution authority"),
    ("ideation/selected/t45_search_targets.json", "pre_novelty_context", "Targeted novelty-search context; not execution authority"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def availability(path: Path) -> str:
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "unreadable"
    if not os.access(path, os.R_OK):
        return "unreadable"
    return "available"


def source_record(root: Path, spec: SourceSpec) -> dict:
    full_path = root / spec.path
    state = availability(full_path)
    result = {
        "source_id": spec.source_id,
        "path": spec.path,
        "category": spec.category,
        "requirement": spec.requirement,
        "availability": state,
        "used": False,
        "purpose": spec.purpose,
        "precedence": spec.precedence,
        "used_for": [],
    }
    if state == "available":
        result["content_sha256"] = sha256_file(full_path)
    else:
        result["omission_reason"] = f"Source is {state} at inventory time"
    return result


def iter_optional_files(root: Path) -> Iterable[tuple[Path, str, str]]:
    for relative, category, purpose in OPTIONAL_PATHS:
        target = root / relative
        if target.is_file():
            yield target, category, purpose
        elif target.is_dir():
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    yield path, category, purpose


def optional_record(root: Path, path: Path, index: int, category: str, purpose: str) -> dict:
    relative = path.relative_to(root).as_posix()
    return {
        "source_id": f"SRC_OPTIONAL_{index:04d}",
        "path": relative,
        "category": category,
        "requirement": "optional_backtrack",
        "availability": "available",
        "used": False,
        "purpose": purpose,
        "precedence": 6,
        "used_for": [],
        "content_sha256": sha256_file(path),
    }


def build_inventory(root: Path, include_optional: bool) -> dict:
    sources = [source_record(root, spec) for spec in REQUIRED_SOURCES]
    if include_optional:
        for index, (path, category, purpose) in enumerate(iter_optional_files(root), start=1):
            sources.append(optional_record(root, path, index, category, purpose))
    required = [entry for entry in sources if entry["requirement"] == "required"]
    available = sum(entry["availability"] == "available" for entry in required)
    return {
        "inventory_version": "researchos_pre_t5_inventory.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project_root": str(root),
        "required_source_coverage": available / len(required),
        "ready_for_semantic_reboost": available == len(required),
        "sources": sources,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="ResearchOS project root")
    parser.add_argument("--output", type=Path, help="Write JSON inventory here; stdout when omitted")
    parser.add_argument("--no-optional", action="store_true", help="Do not enumerate optional backtracking files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    if not root.is_dir():
        print(f"error: project root is not a directory: {root}", file=sys.stderr)
        return 2
    payload = build_inventory(root, include_optional=not args.no_optional)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if payload["ready_for_semantic_reboost"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
