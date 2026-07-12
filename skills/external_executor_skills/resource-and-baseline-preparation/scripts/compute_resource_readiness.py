#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import assert_write_allowed, dump_json_atomic, is_within, load_json, resolve_workspace, utc_now

SUITABLE_APPROVALS = {
    "baseline_implementation": {"baseline_reproduction", "formal_comparison"},
    "dataset": {"dataset_use", "experiment_design", "formal_comparison"},
    "dataset_split": {"dataset_use", "experiment_design", "formal_comparison"},
    "benchmark_definition": {"experiment_design", "formal_comparison"},
    "metric_implementation": {"metric_use", "experiment_design", "formal_comparison"},
    "evaluation_protocol": {"metric_use", "experiment_design", "formal_comparison"},
    "preprocessing": {"preprocessing_use", "experiment_design", "formal_comparison"},
    "checkpoint": {"checkpoint_use", "smoke_preparation", "formal_comparison"},
    "environment": {"smoke_preparation", "baseline_reproduction", "formal_comparison"},
    "reference_material": {"static_inspection", "experiment_design"},
    "adapter_interface": {"smoke_preparation", "baseline_reproduction", "formal_comparison"},
    "other": {"static_inspection", "experiment_design"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive the Phase B readiness gate from requirements and reviews.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    path = Path(args.report).expanduser().resolve()
    if not is_within(path, workspace):
        raise SystemExit(f"Report path is outside workspace: {path}")
    report = load_json(path)
    requirements = report.get("resource_requirement_matrix", {}).get("items", [])
    reviews = report.get("resource_reviews", {}).get("items", [])
    review_by_req = {}
    for review in reviews:
        for req_id in review.get("requirement_ids", []):
            review_by_req.setdefault(req_id, []).append(review)

    approved = []
    constrained = []
    blocking = []
    derived_issues = []
    for req in requirements:
        req_id = req.get("requirement_id")
        if not req_id:
            continue
        suitable = SUITABLE_APPROVALS.get(req.get("resource_type"), {"static_inspection"})
        passing = []
        for review in review_by_req.get(req_id, []):
            approvals = set(review.get("approved_for", []))
            if review.get("verdict") == "pass" and approvals & suitable:
                passing.append(review)
        if passing:
            approved.append(req_id)
            if any(r.get("approximation_level") in {"minor", "material", "unknown"} or r.get("fairness_risk") in {"medium", "high"} or r.get("license_risk") in {"medium", "high"} for r in passing):
                constrained.append(req_id)
            continue
        if req.get("required") and (req.get("minimum_loop_dependency") or req.get("missing_blocks_execution")):
            blocking.append(req_id)
            derived_issues.append({"requirement_id": req_id, "reason": "no passing review with suitable approval"})
        else:
            constrained.append(req_id)

    claim_constraints = list(report.get("resource_readiness", {}).get("claim_constraints", []))
    if blocking:
        status = "blocked"
        feasible = False
        next_action = "human_review" if any(req.get("replacement", {}).get("requires_review") for req in requirements if req.get("requirement_id") in blocking) else "stop_and_report"
    elif constrained or claim_constraints:
        status = "partial"
        feasible = True
        next_action = "continue_with_constraints"
    else:
        status = "ready"
        feasible = True
        next_action = "continue_to_experiment_design"

    readiness = {
        "status": status,
        "minimum_loop_feasible": feasible,
        "approved_requirement_ids": sorted(set(approved)),
        "constrained_requirement_ids": sorted(set(constrained) - set(blocking)),
        "blocking_requirement_ids": sorted(set(blocking)),
        "claim_constraints": claim_constraints,
        "blocking_issues": derived_issues,
        "next_action": next_action,
        "computed_at": utc_now(),
        "computation": "deterministic_requirement_review_gate_v1",
    }
    report["resource_readiness"] = readiness
    if args.write_back:
        assert_write_allowed(workspace, path)
        dump_json_atomic(path, report)
    print(f"{status}: approved={len(approved)} constrained={len(constrained)} blocking={len(blocking)}")
    return 2 if status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
