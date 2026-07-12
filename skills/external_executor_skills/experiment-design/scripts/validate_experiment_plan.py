#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import dump_json_atomic, get_nested, load_json, nonempty, numeric, resolve_in_workspace, resolve_workspace, utc_now

ROLES = {"confirmatory", "diagnostic", "exploratory"}
RUN_TYPES = {"smoke", "small_scale", "formal", "ablation", "robustness", "diagnostic", "efficiency"}


def add(errors: list[str], condition: bool, message: str) -> None:
    if condition:
        errors.append(message)


def validate(plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    claims = plan.get("claim_evidence_matrix", {}).get("items", [])
    experiments = plan.get("experiments", [])
    claim_ids = {c.get("claim_id") for c in claims if isinstance(c, dict)}
    exp_ids = {e.get("experiment_id") for e in experiments if isinstance(e, dict)}

    add(errors, plan.get("schema_version") != "experiment_plan.v1", "unsupported_or_missing_plan_schema")
    add(errors, not isinstance(plan.get("plan_version"), int), "plan_version_missing")
    fingerprint = get_nested(plan, "protocol_fingerprint.fingerprint") or get_nested(plan, "protocol_snapshot.protocol_fingerprint")
    add(errors, not nonempty(fingerprint), "protocol_fingerprint_missing")

    unresolved = get_nested(plan, "protocol_snapshot.unresolved_fields", default=[])
    if unresolved:
        errors.append("protocol_unresolved_fields:" + ",".join(str(x) for x in unresolved))

    for claim in claims:
        if not isinstance(claim, dict):
            errors.append("malformed_claim_entry")
            continue
        cid = claim.get("claim_id")
        mapped = [e for e in experiments if cid in e.get("claim_ids", [])]
        if claim.get("required") and claim.get("status") != "unsupported" and not mapped:
            errors.append(f"required_claim_without_experiment:{cid}")
        if claim.get("required") and claim.get("status") == "unsupported" and not nonempty(claim.get("unsupported_reason")):
            errors.append(f"unsupported_required_claim_without_reason:{cid}")
        for eid in claim.get("planned_experiment_ids", []):
            if eid not in exp_ids:
                warnings.append(f"claim_references_unknown_experiment:{cid}:{eid}")

    for exp in experiments:
        if not isinstance(exp, dict):
            errors.append("malformed_experiment_entry")
            continue
        eid = exp.get("experiment_id", "missing")
        if exp.get("analysis_role") not in ROLES:
            errors.append(f"invalid_analysis_role:{eid}:{exp.get('analysis_role')}")
        if exp.get("run_type") not in RUN_TYPES:
            errors.append(f"invalid_run_type:{eid}:{exp.get('run_type')}")
        for cid in exp.get("claim_ids", []):
            if cid not in claim_ids:
                errors.append(f"experiment_references_unknown_claim:{eid}:{cid}")
        if exp.get("analysis_role") == "confirmatory":
            for field in ("decision_rule", "interpretation_if_positive", "interpretation_if_negative", "interpretation_if_inconclusive"):
                if not nonempty(exp.get(field)):
                    errors.append(f"confirmatory_missing_{field}:{eid}")
        if exp.get("run_type") in {"formal", "ablation", "robustness", "diagnostic", "efficiency"}:
            required_fields = ("dataset", "split", "metrics", "protocol_fingerprint", "expected_artifacts", "preconditions")
            for field in required_fields:
                if not nonempty(exp.get(field)):
                    errors.append(f"run_missing_{field}:{eid}")
            if not (nonempty(exp.get("seeds")) or nonempty(exp.get("seed_count")) or nonempty(exp.get("repeats"))):
                errors.append(f"run_missing_seed_or_repeat_policy:{eid}")
        if exp.get("experiment_kind") == "baseline_reproduction" and not nonempty(exp.get("baseline_refs")):
            errors.append(f"baseline_reproduction_missing_resource_ref:{eid}")
        if exp.get("run_type") == "ablation":
            if not nonempty(exp.get("mechanism_ref")):
                errors.append(f"ablation_missing_mechanism_ref:{eid}")
            if not nonempty(exp.get("variants")) or len(exp.get("variants", [])) < 2:
                errors.append(f"ablation_missing_controlled_variants:{eid}")
        for dep in exp.get("depends_on", []):
            if dep not in exp_ids:
                errors.append(f"unknown_dependency:{eid}:{dep}")
        if not nonempty(exp.get("reviewer_question")) and exp.get("experiment_kind") not in {"baseline_reproduction", "ours_smoke"}:
            warnings.append(f"reviewer_question_missing:{eid}")
        if not nonempty(exp.get("estimated_cost")):
            warnings.append(f"estimated_cost_missing:{eid}")

    max_runs = numeric(get_nested(plan, "budget.max_total_runs"))
    estimated_runs = numeric(get_nested(plan, "estimated_budget.total_runs"))
    if max_runs is None:
        errors.append("max_total_runs_budget_missing")
    if estimated_runs is None:
        warnings.append("estimated_total_runs_missing")
    elif max_runs is not None and estimated_runs > max_runs:
        errors.append(f"estimated_runs_exceed_budget:{estimated_runs}>{max_runs}")

    risk_refs = set(get_nested(plan, "resource_constraints.risk_refs", default=[]))
    propagated = {ref for exp in experiments for ref in exp.get("risk_refs", [])}
    missing_risks = sorted(risk_refs - propagated)
    if missing_risks:
        errors.append("resource_risks_not_propagated:" + ",".join(missing_risks))

    if plan.get("unexpanded_minimum_loop_items"):
        errors.append("minimum_loop_items_not_expanded:" + ",".join(plan["unexpanded_minimum_loop_items"]))

    required_baseline_exps = [e for e in experiments if e.get("experiment_kind") == "baseline_reproduction"]
    if not required_baseline_exps:
        errors.append("no_baseline_reproduction_planned")
    if not any(e.get("experiment_kind") == "main_comparison" for e in experiments):
        errors.append("no_main_comparison_planned")
    if not any(e.get("run_type") == "smoke" for e in experiments):
        errors.append("no_ours_smoke_planned")

    return {
        "schema_version": "experiment_plan_validation.v1",
        "generated_at": utc_now(),
        "status": "pass" if not errors else "needs_fix",
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "summary": {
            "claim_count": len(claims),
            "experiment_count": len(experiments),
            "confirmatory_count": sum(1 for e in experiments if e.get("analysis_role") == "confirmatory"),
            "formal_or_ablation_count": sum(1 for e in experiments if e.get("run_type") in {"formal", "ablation"}),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Phase C plan contract and anti-post-hoc requirements.")
    parser.add_argument("--workspace")
    parser.add_argument("--plan", default="external_executor/experiment_plan.json")
    parser.add_argument("--output", default="external_executor/experiment_plan_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report = validate(load_json(resolve_in_workspace(ws, args.plan)))
    dump_json_atomic(resolve_in_workspace(ws, args.output), report)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
