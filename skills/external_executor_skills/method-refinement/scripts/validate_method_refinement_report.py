#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import dump_json_atomic, load_json, nonempty, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate report, review, drift, and approval consistency.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/method_refinement_report.json")
    parser.add_argument("--output", default="external_executor/method_refinement_report_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report = load_json(resolve_in_workspace(ws, args.report))
    errors: list[str] = []

    if report.get("schema_version") != "method_refinement_report.v1":
        errors.append("invalid_report_schema")
    if report.get("child_skill") != "method-refinement":
        errors.append("invalid_child_skill")
    if report.get("status") not in {"complete", "partial", "blocked", "failed"}:
        errors.append("invalid_child_status")
    if report.get("refinement_status") not in {"ready", "needs_fix", "blocked"}:
        errors.append("invalid_refinement_status")
    for field in (
        "refinement_id", "method_refinement_record", "method_intent_contract", "method_implementation_spec",
        "method_spec_fingerprint", "method_delta", "scope_assessment", "spec_validation", "review",
        "artifact_refs", "recommended_next_action",
    ):
        if not nonempty(report.get(field)):
            errors.append(f"missing_required_field:{field}")

    review = report.get("review") or {}
    scope = report.get("scope_assessment") or {}
    record = report.get("method_refinement_record") or {}
    if report.get("refinement_status") != review.get("refinement_status"):
        errors.append("refinement_status_review_mismatch")
    if record.get("status") != report.get("refinement_status"):
        errors.append("record_status_mismatch")
    if record.get("refinement_id") != report.get("refinement_id"):
        errors.append("refinement_id_mismatch")
    if report.get("refinement_status") == "ready":
        if review.get("review_status") != "pass" or review.get("approved_for") != "implementation":
            errors.append("ready_without_passing_review")
        if scope.get("drift_level") == "major" or scope.get("requires_human_review"):
            errors.append("ready_with_major_scope_drift")
        if report.get("status") != "complete":
            errors.append("ready_child_status_not_complete")
    if report.get("refinement_status") == "needs_fix" and report.get("status") != "partial":
        errors.append("needs_fix_child_status_not_partial")
    if report.get("refinement_status") == "blocked":
        if report.get("status") != "blocked":
            errors.append("blocked_child_status_mismatch")
        if not report.get("blocking_issues"):
            errors.append("blocked_without_blocking_issues")
    if scope.get("requires_human_review") and not report.get("scope_change_request"):
        errors.append("human_review_without_scope_change_request")
    if review.get("approved_for") == "implementation" and report.get("refinement_status") != "ready":
        errors.append("implementation_approval_on_nonready_report")

    validation = {
        "schema_version": "method_refinement_report_validation.v1",
        "generated_at": utc_now(),
        "status": "pass" if not errors else "blocked",
        "errors": errors,
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), validation)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
