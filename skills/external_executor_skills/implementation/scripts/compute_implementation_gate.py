#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import dump_json_atomic, load_json, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive the implementation gate deterministically.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()

    path = Path(args.report).expanduser().resolve()
    report = load_json(path)
    blocking: list[dict[str, Any]] = []
    fixable: list[dict[str, Any]] = []

    contract_ready = report.get("approved_changes", {}).get("status") == "complete"
    if not contract_ready:
        blocking.append({"id": "change_contract_not_ready"})

    implemented = report.get("implemented_changes", {}).get("items", [])
    incomplete_changes = [item.get("change_id") for item in implemented if item.get("status") not in {"implemented", "not_applicable"}]
    if incomplete_changes:
        fixable.append({"id": "approved_changes_incomplete", "change_ids": incomplete_changes})

    patch = report.get("patch_bundle", {})
    changed_files = patch.get("changed_files", [])
    authorized_noop = bool(report.get("authorized_noop", False))
    if patch.get("status") != "complete":
        fixable.append({"id": "patch_bundle_incomplete"})
    elif not changed_files and not authorized_noop:
        fixable.append({"id": "empty_patch_without_authorized_noop"})

    scope_status = report.get("scope_scan", {}).get("status")
    if scope_status == "blocked":
        blocking.append({"id": "scope_scan_blocked"})
    elif scope_status != "pass":
        fixable.append({"id": "scope_scan_not_pass", "status": scope_status})

    mapping = report.get("module_mapping", {})
    mapping_complete = mapping.get("validation_status") == "pass" or mapping.get("status") == "complete" and not mapping.get("items")
    if not mapping_complete:
        fixable.append({"id": "module_mapping_incomplete"})

    verification_records = report.get("verification_records", {}).get("items", [])
    mandatory_ids = set()
    for change in report.get("approved_changes", {}).get("items", []):
        mandatory_ids.update(str(value) for value in change.get("required_tests", []) if value)
    # When the report includes declared verification metadata, honor mandatory flags as well.
    mandatory_ids.update(str(item.get("verification_id")) for item in verification_records if item.get("mandatory") is True and item.get("verification_id"))
    passed_success = {str(item.get("verification_id")) for item in verification_records if item.get("status") == "passed" and item.get("expectation") == "success" and item.get("phase") in {"green", "final"}}
    missing_mandatory = sorted(mandatory_ids - passed_success)
    if missing_mandatory:
        fixable.append({"id": "mandatory_verification_missing_or_failed", "verification_ids": missing_mandatory})

    final_fp = report.get("final_worktree_fingerprint")
    fresh_final = any(
        item.get("status") == "passed"
        and item.get("expectation") == "success"
        and item.get("phase") == "final"
        and item.get("worktree_manifest_sha256") == final_fp
        for item in verification_records
    )
    if verification_records and not fresh_final:
        fixable.append({"id": "fresh_final_verification_missing"})
    elif not verification_records and report.get("approved_changes", {}).get("items"):
        fixable.append({"id": "no_verification_records"})

    drift = report.get("drift_assessment", {})
    if drift.get("contribution_drift") == "major":
        blocking.append({"id": "major_contribution_drift"})
    if drift.get("protocol_impact") == "material":
        blocking.append({"id": "material_protocol_impact"})
    if drift.get("fairness_impact") == "material":
        blocking.append({"id": "material_fairness_impact"})
    if report.get("scope_change_proposals", {}).get("items"):
        blocking.append({"id": "scope_change_proposed"})

    risks = report.get("implementation_risks", {}).get("items", [])
    for risk in risks:
        if risk.get("severity") in {"blocking", "critical"} and risk.get("status") not in {"resolved", "accepted_by_root"}:
            blocking.append({"id": "unresolved_blocking_risk", "risk_id": risk.get("risk_id")})

    if blocking:
        gate = "blocked"
        next_action = "human_review"
        child_status = "blocked"
    elif fixable:
        gate = "needs_fix"
        next_action = "repair_implementation"
        child_status = "partial"
    else:
        gate = "ready_for_review"
        next_action = "continue_to_code_and_protocol_review"
        child_status = "complete"

    report["status"] = child_status
    report["generated_at"] = utc_now()
    report["implementation_gate"] = {
        "status": gate,
        "required_verifications_complete": not any(item["id"] in {"mandatory_verification_missing_or_failed", "no_verification_records"} for item in fixable),
        "mapping_complete": mapping_complete,
        "patch_in_scope": scope_status == "pass",
        "fresh_final_verification": fresh_final,
        "blocking_issues": blocking,
        "fixable_issues": fixable,
        "next_action": next_action,
    }
    if args.write_back:
        dump_json_atomic(path, report)
    print(f"{gate}: blocking={len(blocking)} fixable={len(fixable)}")
    return 2 if blocking else (1 if fixable else 0)


if __name__ == "__main__":
    raise SystemExit(main())
