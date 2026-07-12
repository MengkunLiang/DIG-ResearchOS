#!/usr/bin/env python3
"""Validate review-report structure, evidence, verdict, and approval invariants."""

from __future__ import annotations

import argparse
import re
from typing import Any

from _common import emit, load_json, resolve_in_workspace, workspace_root
from validate_verification_evidence import validate_bundle


REQUIRED_TOP = {
    "schema_version", "review_id", "iteration_id", "reviewed_at",
    "input_fingerprint", "requested_approval_level", "review_scope", "axes",
    "findings", "verification_evidence", "review_status", "approved_for",
    "required_fixes", "repair_owners", "confidence", "contribution_drift",
    "recommended_next_action",
}
AXES = {
    "spec_alignment", "code_correctness", "protocol_fairness", "data_integrity",
    "reproducibility", "security_and_paths", "contribution_drift",
}
AXIS_STATUSES = {"pass", "warning", "fail", "blocked", "not_applicable"}
SEVERITIES = {"info", "warning", "major", "blocking"}
FINDING_STATUSES = {"open", "fixed_and_verified", "accepted_constraint", "false_positive", "deferred_by_human"}
REPAIR_OWNERS = {"baseline-reproduction", "method-refinement", "implementation", "experiment-design", "research-execution"}
REVIEW_STATUSES = {"pass", "needs_fix", "blocked"}
LEVELS = {"none": 0, "smoke": 1, "small_scale": 2, "formal": 3}
CONFIDENCE = {"high", "medium", "low"}
ACTIONS = {"experiment-run", "baseline-reproduction", "method-refinement", "implementation", "experiment-design", "human_review"}


def add(errors: list[dict[str, str]], code: str, message: str) -> None:
    errors.append({"code": code, "message": message})


def ref_exists(root, ref: str, evidence_ids: set[str]) -> bool:
    if ref in evidence_ids:
        return True
    try:
        return resolve_in_workspace(root, ref).is_file()
    except ValueError:
        return False


def validate_report(root, report: Any) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(report, dict):
        return {"valid": False, "errors": [{"code": "invalid_type", "message": "report must be an object"}], "warnings": []}
    for key in sorted(REQUIRED_TOP - set(report)):
        add(errors, "missing_top_key", key)
    for key in ("review_id", "iteration_id", "reviewed_at"):
        if not isinstance(report.get(key), str) or not report.get(key):
            add(errors, "invalid_identity_field", key)
    fingerprint = report.get("input_fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        add(errors, "invalid_fingerprint", repr(fingerprint))

    scope = report.get("review_scope")
    if not isinstance(scope, dict):
        add(errors, "invalid_review_scope", "review_scope must be an object")
        scope = {}
    for key in ("snapshot_ref", "changed_paths", "expanded_paths", "expansion_reasons", "implementation_spec_refs", "affected_experiments", "protocol_fingerprint", "baseline_refs"):
        if key not in scope:
            add(errors, "missing_scope_field", key)
    if not isinstance(scope.get("changed_paths"), list) or not scope.get("changed_paths"):
        add(errors, "empty_changed_paths", "review scope must pin changed paths")

    snapshot: dict[str, Any] = {}
    snapshot_ref = scope.get("snapshot_ref")
    if isinstance(snapshot_ref, str):
        try:
            snapshot = load_json(resolve_in_workspace(root, snapshot_ref, must_exist=True))
            if snapshot.get("input_fingerprint") != fingerprint:
                add(errors, "snapshot_fingerprint_mismatch", snapshot_ref)
            if snapshot.get("iteration_id") != report.get("iteration_id"):
                add(errors, "snapshot_iteration_mismatch", snapshot_ref)
        except Exception as exc:
            add(errors, "invalid_snapshot", str(exc))
    else:
        add(errors, "invalid_snapshot_ref", repr(snapshot_ref))

    evidence = report.get("verification_evidence")
    evidence_result = validate_bundle(root, snapshot, {"items": evidence}) if isinstance(evidence, list) and snapshot else {
        "valid": False, "errors": [{"code": "invalid_evidence", "message": "verification_evidence must be an array and snapshot must be valid"}], "warnings": [], "evidence_ids": []
    }
    errors.extend(evidence_result.get("errors", []))
    warnings.extend(evidence_result.get("warnings", []))
    evidence_ids = set(evidence_result.get("evidence_ids", []))

    axes = report.get("axes")
    if not isinstance(axes, dict):
        add(errors, "invalid_axes", "axes must be an object")
        axes = {}
    for axis in sorted(AXES - set(axes)):
        add(errors, "missing_axis", axis)
    for axis in AXES:
        value = axes.get(axis)
        if not isinstance(value, dict):
            continue
        if value.get("status") not in AXIS_STATUSES:
            add(errors, "invalid_axis_status", axis)
        if value.get("confidence") not in CONFIDENCE:
            add(errors, "invalid_axis_confidence", axis)
        refs = value.get("evidence_refs")
        if not isinstance(refs, list):
            add(errors, "invalid_axis_evidence", axis)
        else:
            for ref in refs:
                if not isinstance(ref, str) or not ref_exists(root, ref, evidence_ids):
                    add(errors, "unknown_axis_evidence", f"{axis}: {ref}")
        if not isinstance(value.get("findings"), list):
            add(errors, "invalid_axis_findings", axis)
        if value.get("status") == "not_applicable" and not value.get("reason"):
            add(errors, "missing_not_applicable_reason", axis)

    findings = report.get("findings")
    if not isinstance(findings, list):
        add(errors, "invalid_findings", "findings must be an array")
        findings = []
    seen: set[str] = set()
    open_major = False
    open_blocking = False
    formal_warning = False
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            add(errors, "invalid_finding", f"findings[{index}]")
            continue
        finding_id = finding.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id or finding_id in seen:
            add(errors, "invalid_finding_id", f"findings[{index}]")
        else:
            seen.add(finding_id)
        if finding.get("axis") not in AXES:
            add(errors, "invalid_finding_axis", str(finding_id))
        if finding.get("severity") not in SEVERITIES:
            add(errors, "invalid_finding_severity", str(finding_id))
        if finding.get("status") not in FINDING_STATUSES:
            add(errors, "invalid_finding_status", str(finding_id))
        if finding.get("repair_owner") not in REPAIR_OWNERS:
            add(errors, "invalid_repair_owner", str(finding_id))
        for key in ("category", "message", "required_fix"):
            if not isinstance(finding.get(key), str):
                add(errors, "invalid_finding_field", f"{finding_id}.{key}")
        for key in ("locations", "evidence_refs", "blocks_run_levels"):
            if not isinstance(finding.get(key), list):
                add(errors, "invalid_finding_field", f"{finding_id}.{key}")
        for ref in finding.get("evidence_refs", []):
            if not isinstance(ref, str) or not ref_exists(root, ref, evidence_ids):
                add(errors, "unknown_finding_evidence", f"{finding_id}: {ref}")
        if not isinstance(finding.get("impact"), dict):
            add(errors, "invalid_finding_impact", str(finding_id))
        if finding.get("status") == "open":
            open_major |= finding.get("severity") == "major"
            open_blocking |= finding.get("severity") == "blocking"
            formal_warning |= finding.get("severity") == "warning" and "formal" in finding.get("blocks_run_levels", [])

    requested = report.get("requested_approval_level")
    approved = report.get("approved_for")
    if requested not in {"smoke", "small_scale", "formal"}:
        add(errors, "invalid_requested_level", repr(requested))
    if approved not in LEVELS:
        add(errors, "invalid_approved_level", repr(approved))
    elif requested in LEVELS and LEVELS[approved] > LEVELS[requested]:
        add(errors, "approval_exceeds_request", f"{approved} > {requested}")
    review_status = report.get("review_status")
    if review_status not in REVIEW_STATUSES:
        add(errors, "invalid_review_status", repr(review_status))
    if report.get("contribution_drift") not in {"none", "minor", "major"}:
        add(errors, "invalid_contribution_drift", repr(report.get("contribution_drift")))
    if not isinstance(report.get("required_fixes"), list) or not isinstance(report.get("repair_owners"), list):
        add(errors, "invalid_fix_fields", "required_fixes and repair_owners must be arrays")
    for owner in report.get("repair_owners", []):
        if owner not in REPAIR_OWNERS:
            add(errors, "invalid_report_repair_owner", str(owner))
    if report.get("recommended_next_action") not in ACTIONS:
        add(errors, "invalid_next_action", repr(report.get("recommended_next_action")))

    confidence = report.get("confidence")
    if not isinstance(confidence, dict) or confidence.get("overall") not in CONFIDENCE:
        add(errors, "invalid_confidence", "overall confidence is required")
    else:
        by_axis = confidence.get("by_axis")
        if not isinstance(by_axis, dict) or any(by_axis.get(axis) not in CONFIDENCE for axis in AXES):
            add(errors, "invalid_axis_confidence", "all axes require confidence")

    passed_evidence_types = {
        item.get("evidence_type") for item in evidence if isinstance(item, dict) and item.get("result") == "pass"
    } if isinstance(evidence, list) else set()
    if review_status == "pass":
        if open_major or open_blocking:
            add(errors, "pass_with_open_severe_finding", "pass cannot contain open major/blocking findings")
        if approved != requested:
            add(errors, "pass_below_requested_level", f"approved={approved}, requested={requested}")
        if approved == "none":
            add(errors, "pass_without_approval", "pass requires an approved run level")
        if not passed_evidence_types:
            add(errors, "pass_without_fresh_evidence", "pass requires at least one passing verification record")
    elif review_status == "needs_fix":
        if not report.get("required_fixes"):
            add(errors, "needs_fix_without_fixes", "needs_fix requires required_fixes")
        if not open_major and approved == requested:
            add(errors, "needs_fix_without_gate_gap", "needs_fix requires an open major finding or lower approval")
    elif review_status == "blocked":
        if not open_blocking:
            add(errors, "blocked_without_blocking_finding", "blocked requires an open blocking finding")
        if approved != "none":
            add(errors, "blocked_with_approval", str(approved))
        if report.get("recommended_next_action") != "human_review":
            add(errors, "blocked_without_human_review", repr(report.get("recommended_next_action")))

    if approved == "formal":
        if any(isinstance(axes.get(axis), dict) and axes[axis].get("status") != "pass" for axis in AXES):
            add(errors, "formal_axis_not_pass", "all seven axes must pass")
        if formal_warning:
            add(errors, "formal_with_blocking_warning", "an open warning blocks formal approval")
        if report.get("contribution_drift") == "major":
            add(errors, "formal_with_major_drift", "major contribution drift requires human review")
        if not scope.get("protocol_fingerprint"):
            add(errors, "formal_without_protocol_fingerprint", "formal approval requires protocol fingerprint")
        required_types = {"protocol_comparison", "config_validation", "data_integrity_check"}
        if not (passed_evidence_types & {"unit_test", "integration_test"}):
            add(errors, "formal_missing_test_evidence", "unit or integration test evidence is required")
        for evidence_type in sorted(required_types - passed_evidence_types):
            add(errors, "formal_missing_evidence_type", evidence_type)
    elif approved == "small_scale" and review_status == "pass":
        if not (passed_evidence_types & {"unit_test", "integration_test"}):
            add(errors, "small_scale_missing_test_evidence", "unit or integration test evidence is required")
        if "config_validation" not in passed_evidence_types:
            add(errors, "small_scale_missing_config_evidence", "config validation evidence is required")
    elif approved == "smoke" and review_status == "pass":
        if not (passed_evidence_types & {"static_inspection", "unit_test", "integration_test", "smoke_run", "config_validation"}):
            add(errors, "smoke_missing_evidence", "smoke approval requires relevant fresh evidence")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    try:
        report = load_json(resolve_in_workspace(root, args.report, must_exist=True))
    except Exception as exc:
        result = {"valid": False, "errors": [{"code": "invalid_report", "message": str(exc)}], "warnings": []}
    else:
        result = validate_report(root, report)
    emit(result)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
