#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import dump_json_atomic, get_nested, load_json, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute the Phase C readiness gate from deterministic validations.")
    parser.add_argument("--workspace")
    parser.add_argument("--plan", default="external_executor/experiment_plan.json")
    parser.add_argument("--plan-validation", default="external_executor/report/experiment_plan_validation.json")
    parser.add_argument("--dag-validation", default="external_executor/report/experiment_plan_dag_validation.json")
    parser.add_argument("--output", default="external_executor/report/experiment_design_gate.json")
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    plan_path = resolve_in_workspace(ws, args.plan)
    plan = load_json(plan_path)
    result = load_json(ws / "external_executor/result_pack.json")
    plan_validation = load_json(resolve_in_workspace(ws, args.plan_validation))
    dag_validation = load_json(resolve_in_workspace(ws, args.dag_validation))
    blockers = list(plan_validation.get("errors", [])) + list(dag_validation.get("errors", []))
    constraints = list(plan_validation.get("warnings", [])) + list(dag_validation.get("warnings", []))

    readiness = result.get("resource_readiness", {})
    if readiness.get("status") == "blocked" or readiness.get("minimum_loop_feasible") is False:
        blockers.append("resource_readiness_blocks_minimum_loop")
    constraints += [f"resource_constraint:{x}" for x in readiness.get("claim_constraints", [])]
    unsupported_required = [
        c.get("claim_id") for c in get_nested(plan, "claim_evidence_matrix.items", default=[])
        if c.get("required") and c.get("status") == "unsupported"
    ]
    if unsupported_required:
        blockers.append("required_claims_unsupported:" + ",".join(unsupported_required))

    blockers = sorted(set(blockers))
    constraints = sorted(set(constraints))
    if blockers:
        status = "blocked"
        next_action = "return_to_experiment_design_or_human_review"
        review_status = "needs_fix"
    elif constraints or readiness.get("status") == "partial":
        status = "partial"
        next_action = "continue_to_phase_d_with_constraints"
        review_status = "pass"
    else:
        status = "ready"
        next_action = "continue_to_phase_d"
        review_status = "pass"

    gate = {
        "schema_version": "experiment_design_gate.v1",
        "generated_at": utc_now(),
        "status": status,
        "minimum_loop_planned": not any("minimum_loop" in x for x in blockers),
        "protocol_locked": not any("protocol" in x for x in blockers),
        "dag_valid": dag_validation.get("status") == "pass",
        "plan_review_status": review_status,
        "blocking_issues": blockers,
        "constraints": constraints,
        "next_action": next_action,
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), gate)
    if args.write_back:
        plan["plan_review"] = {
            "status": review_status,
            "findings": constraints,
            "required_fixes": blockers,
            "approved_for_phase_d": status in {"ready", "partial"},
            "evidence_refs": [args.plan_validation, args.dag_validation],
        }
        plan["design_gate"] = gate
        plan["status"] = "complete" if status in {"ready", "partial"} else "needs_fix"
        dump_json_atomic(plan_path, plan)
    return 0 if status in {"ready", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
