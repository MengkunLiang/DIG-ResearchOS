#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from typing import Any

from _common import (
    assert_write_allowed,
    dump_json_atomic,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    utc_now,
)

SECTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "remote_search_records": {"status": "not_started", "items": []},
    "staged_resources": {"status": "not_started", "items": []},
    "acquired_resources": {"status": "not_started", "items": []},
    "baseline_candidates": {"status": "not_started", "items": []},
    "dataset_inventory": {"status": "not_started", "items": []},
    "reimplementations": {"status": "not_started", "items": []},
    "resource_source_report": {
        "status": "not_started",
        "json_path": "external_executor/report/resource_source_report.json",
        "markdown_path": "external_executor/report/resource_source_report.md",
        "source_roots": ["resources"],
        "counts": {"byhand": 0, "Remote_acquisition": 0, "reproduction": 0},
        "categories": {"byhand": [], "Remote_acquisition": [], "reproduction": []},
    },
    "resource_reviews": {"status": "not_started", "items": []},
    "material_gaps": {"status": "not_started", "items": []},
    "resource_risks": {"status": "not_started", "items": []},
}


def load_optional(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return deepcopy(default)
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or refresh the Phase B report envelope without overwriting reviewed sections by default."
    )
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/report/resource_preparation_report.json")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing report instead of preserving child-authored sections.",
    )
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    ext = workspace / "external_executor"
    output = resolve_in_workspace(workspace, args.output)
    assert_write_allowed(workspace, output)

    report_dir = ext / "report"
    preflight = load_json(report_dir / "resource_preflight.json")
    if preflight.get("status") == "blocked":
        raise SystemExit("Resource preflight is blocked; do not initialize an acquisition report")
    matrix = load_json(ext / "resource_requirement_matrix.json")
    local_inventory = load_json(report_dir / "resource_local_inventory.json")

    mode = preflight.get("policy_snapshot", {}).get("effective_mode")
    search_default = {
        "schema_version": "resource_search_records.v1",
        "status": "not_needed" if mode == "local_only" else "not_started",
        "items": [],
    }
    search_path = report_dir / "resource_search_records.json"
    search_records = load_optional(search_path, search_default)
    if not search_path.exists():
        assert_write_allowed(workspace, search_path)
        dump_json_atomic(search_path, search_records)

    old: dict[str, Any] = {}
    if output.exists() and not args.force:
        old = load_json(output)
        if old.get("schema_version") not in (None, "resource_preparation_report.v1"):
            raise SystemExit(f"Unsupported existing report schema: {old.get('schema_version')}")

    blocking_req_ids = [
        item.get("requirement_id")
        for item in matrix.get("items", [])
        if isinstance(item, dict) and item.get("required") and item.get("blocking_if_missing")
    ]
    report: dict[str, Any] = {
        "schema_version": "resource_preparation_report.v1",
        "child_skill": "resource-and-baseline-preparation",
        "status": "partial",
        "generated_at": utc_now(),
        "input_fingerprint": preflight.get("input_fingerprint", ""),
        "policy_snapshot": preflight.get("policy_snapshot", {}),
        "resource_requirement_matrix": matrix,
        "local_inventory": local_inventory,
        "remote_search_records": search_records,
        "staged_resources": deepcopy(SECTION_DEFAULTS["staged_resources"]),
        "acquired_resources": deepcopy(SECTION_DEFAULTS["acquired_resources"]),
        "baseline_candidates": deepcopy(SECTION_DEFAULTS["baseline_candidates"]),
        "dataset_inventory": deepcopy(SECTION_DEFAULTS["dataset_inventory"]),
        "reimplementations": deepcopy(SECTION_DEFAULTS["reimplementations"]),
        "resource_source_report": deepcopy(SECTION_DEFAULTS["resource_source_report"]),
        "resource_reviews": deepcopy(SECTION_DEFAULTS["resource_reviews"]),
        "material_gaps": deepcopy(SECTION_DEFAULTS["material_gaps"]),
        "resource_risks": deepcopy(SECTION_DEFAULTS["resource_risks"]),
        "resource_readiness": {
            "status": "blocked",
            "minimum_loop_feasible": False,
            "approved_requirement_ids": [],
            "constrained_requirement_ids": [],
            "blocking_requirement_ids": blocking_req_ids,
            "claim_constraints": [],
            "blocking_issues": ["resource_review_not_completed"],
            "next_action": "stop_and_report",
        },
        "artifact_refs": [
            {
                "artifact_id": "resource-preflight",
                "path": relpath(workspace, report_dir / "resource_preflight.json"),
                "producer": "resource-and-baseline-preparation",
                "evidence_level": "resource_definition",
            },
            {
                "artifact_id": "resource-requirement-matrix",
                "path": relpath(workspace, ext / "resource_requirement_matrix.json"),
                "producer": "resource-and-baseline-preparation",
                "evidence_level": "resource_definition",
            },
            {
                "artifact_id": "resource-local-inventory",
                "path": relpath(workspace, report_dir / "resource_local_inventory.json"),
                "producer": "resource-and-baseline-preparation",
                "evidence_level": "provenance",
            },
        ],
        "notes": ["Initialized report envelope; candidate review and readiness are not yet complete."],
    }

    if old and not args.force:
        preserved = set(SECTION_DEFAULTS) | {"resource_readiness", "artifact_refs", "notes", "status"}
        for key in preserved:
            if key in old:
                report[key] = old[key]
        report["generated_at"] = utc_now()
        report["input_fingerprint"] = preflight.get("input_fingerprint", "")
        report["policy_snapshot"] = preflight.get("policy_snapshot", {})
        report["resource_requirement_matrix"] = matrix
        report["local_inventory"] = local_inventory
        report["remote_search_records"] = search_records

    dump_json_atomic(output, report)
    print(f"initialized {relpath(workspace, output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
