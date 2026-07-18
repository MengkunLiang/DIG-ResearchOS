#!/usr/bin/env python3
"""Inventory ResearchOS Pre-T5 inputs without interpreting their semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, replace
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
    fallback_paths: tuple[str, ...] = ()


REQUIRED_SOURCES = (
    SourceSpec("SRC_PROJECT", "project.yaml", "project", "required", "Project identity, task, and user constraints", 1),
    SourceSpec("SRC_SYNTHESIS", "literature/synthesis.md", "literature_synthesis", "required", "Field synthesis, method families, and research gap", 5),
    SourceSpec("SRC_SYNTHESIS_WORKBENCH", "literature/synthesis_workbench.json", "synthesis_workbench", "required", "Structured literature evidence and confidence", 5),
    SourceSpec("SRC_DOMAIN_MAP", "literature/domain_map.json", "domain_map", "required", "Domain and bridge relationships", 5),
    SourceSpec("SRC_COMPARISON_TABLE", "literature/comparison_table.csv", "comparison_table", "required", "Comparable methods, modules, datasets, and metrics", 5),
    SourceSpec("SRC_HYPOTHESES", "ideation/hypotheses.md", "hypotheses", "required", "Central hypothesis and mechanism candidates", 3),
    SourceSpec("SRC_EXP_PLAN", "ideation/exp_plan.yaml", "experiment_plan", "required", "Planned datasets, metrics, protocols, and ablations", 2),
    SourceSpec(
        "SRC_IDEA_SCORECARD",
        "ideation/selected/selected_candidate.json",
        "idea_scorecard",
        "required",
        "Selected Candidate dossier: research rationale, feasibility, and weaknesses",
        3,
        ("ideation/idea_scorecard.yaml",),
    ),
    SourceSpec(
        "SRC_RISKS",
        "ideation/kill_criteria.yaml",
        "risks",
        "required",
        "Scientific, execution, and stopping risks",
        3,
        ("ideation/risks.md",),
    ),
    SourceSpec("SRC_NOVELTY", "ideation/novelty_audit.md", "novelty_audit", "required", "Nearest work, required baselines, novelty and claim boundaries", 2),
)

# Current T4.5 writes these files after the audit passes.  They enrich the
# execution contract but remain optional here so older resumable workspaces do
# not become blocked solely because they predate the dossier format.
CONTEXT_SOURCES = (
    SourceSpec(
        "SRC_RESEARCH_DOSSIER",
        "ideation/research_dossier.json",
        "research_dossier",
        "optional_backtrack",
        "Post-novelty research problem, contribution intent, conditional implications, and evidence status",
        3,
    ),
    SourceSpec(
        "SRC_VALIDATION_MAP",
        "ideation/validation_map.yaml",
        "validation_map",
        "optional_backtrack",
        "Hypothesis tests, controls, prerequisites, and evidence boundaries",
        3,
    ),
    SourceSpec(
        "SRC_CONTRIBUTION_MAP",
        "ideation/contribution_hypothesis_map.yaml",
        "contribution_hypothesis_map",
        "optional_backtrack",
        "Contribution-to-hypothesis traceability and interpretation limits",
        3,
    ),
    SourceSpec(
        "SRC_RESEARCH_PROPOSAL",
        "ideation/proposal/research_proposal.md",
        "research_proposal",
        "optional_backtrack",
        "Post-novelty comprehensive proposal for planning context; never empirical evidence",
        3,
    ),
    SourceSpec(
        "SRC_PROPOSAL_MANIFEST",
        "ideation/proposal/proposal_manifest.json",
        "research_proposal",
        "optional_backtrack",
        "Proposal provenance, T5 transfer boundary, and section-to-source traceability",
        3,
    ),
)

OPTIONAL_PATHS = (
    ("literature/deep_read_notes", "paper_note", "Detailed paper evidence"),
    ("literature/bridge_notes", "bridge_paper_note", "Cross-domain paper evidence"),
    ("literature/cross_domain_catalogs", "cross_domain_catalog", "Cross-domain retrieval context; not direct claim evidence"),
    ("literature/shallow_read_notes", "abstract_note", "Abstract-only retrieval hints"),
    (
        "literature/resource_catalog.jsonl",
        "resource",
        "Paper-associated code, data, benchmark, model, project, and supplement discovery records; not execution-ready resources",
    ),
    (
        "literature/resource_catalog_summary.json",
        "resource",
        "Summary of paper-associated resource discovery coverage and T5 acquisition boundary",
    ),
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


def resolve_required_source(root: Path, spec: SourceSpec) -> SourceSpec:
    """Prefer current T4.5 artifacts while keeping legacy workspaces resumable."""

    for candidate in (spec.path, *spec.fallback_paths):
        if (root / candidate).exists():
            return replace(spec, path=candidate)
    return spec


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
    sources = [source_record(root, resolve_required_source(root, spec)) for spec in REQUIRED_SOURCES]
    sources.extend(source_record(root, spec) for spec in CONTEXT_SOURCES)
    if include_optional:
        for index, (path, category, purpose) in enumerate(iter_optional_files(root), start=1):
            sources.append(optional_record(root, path, index, category, purpose))
    required = [entry for entry in sources if entry["requirement"] == "required"]
    available = sum(entry["availability"] == "available" for entry in required)
    return {
        "inventory_version": "researchos_pre_t5_inventory.v2",
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
