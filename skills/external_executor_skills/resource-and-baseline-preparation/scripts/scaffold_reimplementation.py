#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import (
    assert_write_allowed,
    dump_json_atomic,
    load_json,
    relpath,
    resolve_workspace,
    slugify,
    utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a provenance-first baseline reimplementation package.")
    parser.add_argument("--workspace")
    parser.add_argument("--requirement-id", required=True)
    parser.add_argument("--baseline-name", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    preflight = load_json(workspace / "external_executor" / "resource_preflight.json")
    policy = preflight.get("policy_snapshot", {})
    if policy.get("effective_mode") != "github_and_reimplementation" or not policy.get("effective_reimplementation_allowed"):
        raise SystemExit("Policy does not authorize baseline reimplementation")
    matrix = load_json(workspace / "external_executor" / "resource_requirement_matrix.json")
    requirement = next((i for i in matrix.get("items", []) if i.get("requirement_id") == args.requirement_id), None)
    if not requirement:
        raise SystemExit(f"Unknown requirement ID: {args.requirement_id}")
    if requirement.get("resource_type") != "baseline_implementation":
        raise SystemExit("Reimplementation scaffold is only for baseline_implementation requirements")

    dest = workspace / "external_executor" / "workdir" / "resources" / "reimplementations" / slugify(args.baseline_name)
    assert_write_allowed(workspace, dest)
    if dest.exists() and not args.force:
        raise SystemExit(f"Destination exists: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    for sub in ("src", "configs", "tests"):
        (dest / sub).mkdir(exist_ok=True)
        (dest / sub / ".gitkeep").touch()

    spec = f"""# Reimplementation Specification: {args.baseline_name}\n\n## Status\n\n`spec_draft`\n\n## Requirement\n\n- Requirement ID: `{args.requirement_id}`\n- Baseline identity: {args.baseline_name}\n- Allowed label: `executor_reimplementation` or `approximate_reproduction`\n\n## Authoritative Sources\n\n""" + "\n".join(f"- {source}" for source in args.source) + """\n\n## Defining Mechanism\n\nDescribe the source-grounded mechanism. Do not infer claim-critical details silently.\n\n## Inputs, Outputs, and Shapes\n\n## Objective and Losses\n\n## Training Procedure\n\n## Inference Procedure\n\n## Dataset, Split, and Preprocessing\n\n## Metric and Evaluation Protocol\n\n## Explicit Hyperparameters\n\n## Unspecified Details and Assumptions\n\nEvery assumption must also appear in `assumptions.json`.\n\n## Fairness Controls\n\n## Validation Plan\n\nInclude mechanism tests, interface tests, and later baseline-reproduction expectations.\n\n## Known Fidelity Risks\n\n## Stop Conditions\n\nState what missing evidence would make this package unavailable rather than approximate.\n"""
    (dest / "REIMPLEMENTATION_SPEC.md").write_text(spec, encoding="utf-8")
    (dest / "README.md").write_text(
        f"# {args.baseline_name} reimplementation\n\nThis package is not official and contains no reproduced experimental result.\n",
        encoding="utf-8",
    )
    provenance = {
        "schema_version": "baseline_reimplementation_provenance.v1",
        "created_at": utc_now(),
        "requirement_id": args.requirement_id,
        "baseline_name": args.baseline_name,
        "implementation_label": "executor_reimplementation",
        "official": False,
        "source_refs": args.source,
        "search_exhaustion_evidence_refs": [],
        "package_path": relpath(workspace, dest),
        "status": "spec_draft",
    }
    dump_json_atomic(dest / "provenance.json", provenance)
    dump_json_atomic(dest / "assumptions.json", {"schema_version": "baseline_reimplementation_assumptions.v1", "items": []})
    dump_json_atomic(dest / "paper_to_code_map.json", {"schema_version": "paper_to_code_map.v1", "items": []})
    print(relpath(workspace, dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
