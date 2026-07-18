#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import dump_json_atomic, get_nested, load_json, nonempty, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate report shape and gate consistency before narrow apply.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/report/phase_C/experiment_design_report.json")
    parser.add_argument("--output", default="external_executor/report/phase_C/experiment_design_report_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report = load_json(resolve_in_workspace(ws, args.report))
    errors: list[str] = []
    if report.get("schema_version") != "experiment_design_report.v1":
        errors.append("invalid_report_schema")
    if report.get("child_skill") != "experiment-design":
        errors.append("invalid_child_skill")
    if report.get("status") not in {"complete", "partial", "blocked", "failed"}:
        errors.append("invalid_child_status")
    for field in ("claim_evidence_matrix", "experiment_plan", "validation", "recommended_next_action"):
        if not nonempty(report.get(field)):
            errors.append(f"missing_required_field:{field}")
    gate = get_nested(report, "validation.gate", default={})
    if report.get("design_readiness") != gate.get("status"):
        errors.append("design_readiness_gate_mismatch")
    if gate.get("status") in {"ready", "partial"} and get_nested(report, "validation.plan.status") != "pass":
        errors.append("ready_gate_without_plan_validation_pass")
    if gate.get("status") in {"ready", "partial"} and get_nested(report, "validation.dag.status") != "pass":
        errors.append("ready_gate_without_dag_validation_pass")
    if gate.get("status") == "blocked" and not report.get("blocking_issues"):
        errors.append("blocked_without_blocking_issues")
    validation = {
        "schema_version": "experiment_design_report_validation.v1",
        "generated_at": utc_now(),
        "status": "pass" if not errors else "blocked",
        "errors": errors,
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), validation)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
