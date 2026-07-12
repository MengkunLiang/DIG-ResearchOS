#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import load_json, resolve_in_workspace, resolve_workspace

TOP_REQUIRED = {
    "schema_version", "child_skill", "status", "generated_at", "implementation_id", "iteration_id",
    "input_fingerprint", "contract_ref", "implementation_root", "base_source", "approved_changes",
    "implemented_changes", "module_mapping", "verification_records", "tdd_cycles", "patch_bundle",
    "scope_scan", "drift_assessment", "implementation_risks", "scope_change_proposals",
    "implementation_gate", "artifact_refs", "notes",
}
CHILD_STATUS = {"complete", "partial", "blocked", "failed"}
SECTION_STATUS = {"complete", "partial", "blocked", "stale", "not_applicable", "not_needed"}
GATES = {"ready_for_review", "needs_fix", "blocked"}
CONTRIBUTION = {"none", "minor", "major"}
PROTOCOL = {"none", "nonmaterial", "material"}
FAIRNESS = {"none", "controlled", "uncertain", "material"}
BASELINE_IMPACT = {"none", "adapter_only", "invalidates_selected", "invalidates_all"}
FORBIDDEN_BUILDER_FIELDS = {"review_status", "approved_for", "formal_run_ready", "protocol_approved", "fairness_approved"}


def walk_forbidden(value: Any, path: str = "") -> list[str]:
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}" if path else key
            if key in FORBIDDEN_BUILDER_FIELDS:
                if key == "review_status" and item in {None, "not_reviewed", "pending"}:
                    pass
                elif key == "approved_for" and item in {None, [], ["none"], "none"}:
                    pass
                elif item not in {None, False, "", "not_reviewed", "pending", "none"}:
                    errors.append(f"Builder-owned report asserts forbidden field {current}={item!r}")
            errors.extend(walk_forbidden(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(walk_forbidden(item, f"{path}[{index}]"))
    return errors


def validate_data(workspace: Path, data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    missing = sorted(TOP_REQUIRED - set(data))
    if missing:
        errors.append(f"missing top-level keys: {missing}")
    if data.get("schema_version") != "implementation_report.v1":
        errors.append("schema_version must be implementation_report.v1")
    if data.get("child_skill") != "implementation":
        errors.append("child_skill must be implementation")
    if data.get("status") not in CHILD_STATUS:
        errors.append(f"invalid child status: {data.get('status')!r}")
    if not data.get("implementation_id") or not data.get("iteration_id"):
        errors.append("implementation_id and iteration_id are required")

    for name in ("approved_changes", "implemented_changes", "module_mapping", "verification_records", "tdd_cycles", "implementation_risks", "scope_change_proposals"):
        section = data.get(name)
        if not isinstance(section, dict):
            errors.append(f"{name} must be an object")
            continue
        if section.get("status") not in SECTION_STATUS:
            errors.append(f"invalid {name}.status: {section.get('status')!r}")
        if not isinstance(section.get("items", []), list):
            errors.append(f"{name}.items must be a list")

    approved = data.get("approved_changes", {}).get("items", [])
    implemented = data.get("implemented_changes", {}).get("items", [])
    approved_ids = [item.get("change_id") for item in approved if isinstance(item, dict)]
    implemented_ids = [item.get("change_id") for item in implemented if isinstance(item, dict)]
    if None in approved_ids or "" in approved_ids or len(approved_ids) != len(set(approved_ids)):
        errors.append("approved change IDs must be present and unique")
    if len(implemented_ids) != len(set(implemented_ids)):
        errors.append("implemented change IDs must be unique")
    unknown = sorted(set(implemented_ids) - set(approved_ids))
    if unknown:
        errors.append(f"implemented changes are not in approved contract: {unknown}")
    missing_records = sorted(set(approved_ids) - set(implemented_ids))
    if missing_records:
        errors.append(f"approved changes lack implementation records: {missing_records}")

    drift = data.get("drift_assessment")
    if not isinstance(drift, dict):
        errors.append("drift_assessment must be an object")
    else:
        if drift.get("contribution_drift") not in CONTRIBUTION:
            errors.append("invalid contribution_drift")
        if drift.get("protocol_impact") not in PROTOCOL:
            errors.append("invalid protocol_impact")
        if drift.get("fairness_impact") not in FAIRNESS:
            errors.append("invalid fairness_impact")
        if drift.get("baseline_reproduction_impact") not in BASELINE_IMPACT:
            errors.append("invalid baseline_reproduction_impact")

    gate = data.get("implementation_gate")
    if not isinstance(gate, dict):
        errors.append("implementation_gate must be an object")
    else:
        gate_status = gate.get("status")
        if gate_status not in GATES:
            errors.append(f"invalid implementation gate: {gate_status!r}")
        if gate_status == "ready_for_review":
            if data.get("status") != "complete":
                errors.append("ready_for_review requires child status complete")
            if gate.get("blocking_issues") or gate.get("fixable_issues"):
                errors.append("ready_for_review cannot contain blocking or fixable issues")
            for flag in ("required_verifications_complete", "mapping_complete", "patch_in_scope", "fresh_final_verification"):
                if gate.get(flag) is not True:
                    errors.append(f"ready_for_review requires {flag}=true")
            if drift.get("contribution_drift") == "major" or drift.get("protocol_impact") == "material" or drift.get("fairness_impact") == "material":
                errors.append("ready_for_review conflicts with material drift")
        elif gate_status == "needs_fix":
            if not gate.get("fixable_issues"):
                errors.append("needs_fix requires fixable_issues")
            if gate.get("blocking_issues"):
                errors.append("needs_fix cannot contain blocking_issues")
        elif gate_status == "blocked":
            if not gate.get("blocking_issues"):
                errors.append("blocked requires blocking_issues")

    contract_path = resolve_in_workspace(workspace, str(data.get("contract_ref", "")))
    root = resolve_in_workspace(workspace, str(data.get("implementation_root", "")))
    if not contract_path.exists():
        errors.append(f"contract_ref missing: {contract_path}")
    if not root.exists() or not root.is_dir():
        errors.append(f"implementation_root missing: {root}")
    for ref in data.get("artifact_refs", []):
        if not isinstance(ref, dict) or not ref.get("path"):
            errors.append("artifact_ref requires path")
            continue
        path = resolve_in_workspace(workspace, ref["path"])
        if not path.exists():
            errors.append(f"artifact_ref path missing: {ref['path']}")

    errors.extend(walk_forbidden(data))
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate implementation report structure, evidence, and Builder boundary.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/implementation_report.json")
    args = parser.parse_args()
    workspace = resolve_workspace(args.workspace)
    report_path = resolve_in_workspace(workspace, args.report)
    data = load_json(report_path)
    errors, warnings = validate_data(workspace, data)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"validation: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
