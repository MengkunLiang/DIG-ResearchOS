#!/usr/bin/env python3
"""Validate the context-alignment report and its gate invariants."""

from __future__ import annotations

import argparse
import re
from typing import Any

from _common import emit, load_json, resolve_in_workspace, workspace_root


REQUIRED_TOP = {
    "schema_version", "status", "alignment_fingerprint", "checked_at",
    "source_files_checked", "axes", "mismatches", "assumptions",
    "blocking_issues", "confirmed_execution_scope", "field_provenance",
    "confidence", "next_action",
}
REQUIRED_AXES = {
    "control_plane", "research_semantics", "experiment_contract",
    "claim_boundary", "capability_fit",
}
REQUIRED_SCOPE = {
    "project_goal", "central_hypothesis", "core_mechanism",
    "must_preserve_components", "candidate_components", "allowed_refinements",
    "forbidden_scope_changes", "required_baselines", "replacement_constraints",
    "benchmark_protocol", "minimum_experiment_loop", "claim_boundaries",
    "must_not_claim", "writer_handoff_contract", "resource_acquisition_policy",
    "allowed_paths", "forbidden_paths", "iteration_budget", "stop_conditions",
    "output_schema_version",
}
CRITICAL_NONEMPTY = {
    "project_goal", "central_hypothesis", "core_mechanism", "required_baselines",
    "benchmark_protocol", "minimum_experiment_loop", "claim_boundaries",
    "resource_acquisition_policy", "allowed_paths", "stop_conditions",
    "output_schema_version",
}
STATUSES = {"pass", "mismatch", "blocked"}
SEVERITIES = {"info", "warning", "material", "blocking"}
RESOLUTIONS = {
    "confirmed_same", "accepted_compiled_value", "accepted_stricter_control",
    "recorded_constraint", "requires_human_review", "unresolved",
}
ACTIONS = {"continue_to_phase_b", "continue_with_constraints", "human_review", "stop_and_report"}
CONFIDENCE = {"high", "medium", "low"}
SOURCE_STATUSES = {"checked", "missing", "unreadable", "not_needed"}
AXIS_STATUSES = {"pass", "mismatch", "blocked", "unknown"}
DERIVATIONS = {"direct", "compiled_and_confirmed", "compiled_with_constraint"}


def issue(errors: list[dict[str, str]], code: str, message: str) -> None:
    errors.append({"code": code, "message": message})


def validate_report(report: Any, root=None) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(report, dict):
        return {"valid": False, "errors": [{"code": "invalid_type", "message": "report must be an object"}], "warnings": []}

    for key in sorted(REQUIRED_TOP - set(report)):
        issue(errors, "missing_top_key", key)
    status = report.get("status")
    if status not in STATUSES:
        issue(errors, "invalid_status", repr(status))
    fingerprint = report.get("alignment_fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        issue(errors, "invalid_fingerprint", repr(fingerprint))
    if not isinstance(report.get("checked_at"), str) or not report.get("checked_at"):
        issue(errors, "invalid_checked_at", "checked_at must be a non-empty RFC3339 string")

    sources = report.get("source_files_checked")
    if not isinstance(sources, list):
        issue(errors, "invalid_sources", "source_files_checked must be an array")
        sources = []
    source_paths = {
        item.get("path") for item in sources
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for index, item in enumerate(sources):
        if not isinstance(item, dict):
            issue(errors, "invalid_source_record", f"source_files_checked[{index}]")
            continue
        if not isinstance(item.get("path"), str) or not item.get("path"):
            issue(errors, "invalid_source_path", f"source_files_checked[{index}]")
        if item.get("status") not in SOURCE_STATUSES:
            issue(errors, "invalid_source_status", f"source_files_checked[{index}]")
        if item.get("status") == "checked":
            if not isinstance(item.get("sha256"), str):
                issue(errors, "missing_source_checksum", f"source_files_checked[{index}]")
            if not isinstance(item.get("fields_used"), list) or not item.get("fields_used"):
                issue(errors, "missing_fields_used", f"source_files_checked[{index}]")
    if root is not None:
        for path in source_paths:
            try:
                resolve_in_workspace(root, path)
            except ValueError as exc:
                issue(errors, "source_path_escape", str(exc))
        inventory_path = resolve_in_workspace(root, "external_executor/context_source_inventory.json")
        if not inventory_path.is_file():
            issue(errors, "missing_source_inventory", "context_source_inventory.json is required")
            inventory_sources = {}
        else:
            try:
                inventory = load_json(inventory_path)
                inventory_sources = {
                    item.get("path"): item for item in inventory.get("sources", [])
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                }
                if report.get("alignment_fingerprint") != inventory.get("alignment_fingerprint"):
                    issue(errors, "alignment_fingerprint_mismatch", "report fingerprint does not match source inventory")
            except Exception as exc:
                issue(errors, "invalid_source_inventory", str(exc))
                inventory_sources = {}
        for item in sources:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            path = item["path"]
            if path not in inventory_sources:
                issue(errors, "source_not_in_inventory", path)
                continue
            expected_hash = inventory_sources[path].get("sha256")
            if item.get("status") == "checked" and expected_hash and item.get("sha256") != expected_hash:
                issue(errors, "source_checksum_mismatch", path)

    axes = report.get("axes")
    if not isinstance(axes, dict):
        issue(errors, "invalid_axes", "axes must be an object")
        axes = {}
    for axis in sorted(REQUIRED_AXES - set(axes)):
        issue(errors, "missing_axis", axis)
    for axis, value in axes.items():
        if axis in REQUIRED_AXES and not isinstance(value, dict):
            issue(errors, "invalid_axis", axis)
            continue
        if axis in REQUIRED_AXES:
            if value.get("status") not in AXIS_STATUSES:
                issue(errors, "invalid_axis_status", axis)
            for key in ("fields_checked", "findings", "evidence_refs"):
                if not isinstance(value.get(key), list):
                    issue(errors, "invalid_axis_field", f"{axis}.{key}")
            if value.get("confidence") not in CONFIDENCE:
                issue(errors, "invalid_axis_confidence", axis)

    mismatches = report.get("mismatches")
    if not isinstance(mismatches, list):
        issue(errors, "invalid_mismatches", "mismatches must be an array")
        mismatches = []
    blocking_mismatch = False
    for index, item in enumerate(mismatches):
        if not isinstance(item, dict):
            issue(errors, "invalid_mismatch", f"mismatches[{index}]")
            continue
        if item.get("axis") not in REQUIRED_AXES:
            issue(errors, "invalid_mismatch_axis", f"mismatches[{index}]")
        if item.get("severity") not in SEVERITIES:
            issue(errors, "invalid_mismatch_severity", f"mismatches[{index}]")
        resolution = item.get("resolution_status")
        if resolution not in RESOLUTIONS:
            issue(errors, "invalid_resolution", f"mismatches[{index}]")
        refs = item.get("source_refs", [])
        if not isinstance(refs, list):
            issue(errors, "invalid_source_refs", f"mismatches[{index}]")
        else:
            for ref in refs:
                if ref not in source_paths:
                    issue(errors, "unknown_source_ref", f"mismatches[{index}]: {ref}")
        if item.get("severity") in {"material", "blocking"} and resolution in {"requires_human_review", "unresolved"}:
            blocking_mismatch = True
        if item.get("requires_human_review") is True:
            blocking_mismatch = True
        for key in ("mismatch_id", "field", "resolution"):
            if not isinstance(item.get(key), str):
                issue(errors, "invalid_mismatch_field", f"mismatches[{index}].{key}")
        for key in ("compared_values", "downstream_constraints"):
            if not isinstance(item.get(key), list):
                issue(errors, "invalid_mismatch_field", f"mismatches[{index}].{key}")
        if not isinstance(item.get("impact"), dict):
            issue(errors, "invalid_mismatch_impact", f"mismatches[{index}]")
        if not isinstance(item.get("requires_human_review"), bool):
            issue(errors, "invalid_human_review_flag", f"mismatches[{index}]")

    blockers = report.get("blocking_issues")
    if not isinstance(blockers, list):
        issue(errors, "invalid_blockers", "blocking_issues must be an array")
        blockers = []
    for index, item in enumerate(blockers):
        if not isinstance(item, dict):
            issue(errors, "invalid_blocker", f"blocking_issues[{index}]")
            continue
        for key in ("blocker_id", "type", "message", "required_resolution"):
            if not isinstance(item.get(key), str) or not item.get(key):
                issue(errors, "invalid_blocker_field", f"blocking_issues[{index}].{key}")
        if not isinstance(item.get("source_refs"), list):
            issue(errors, "invalid_blocker_refs", f"blocking_issues[{index}]")

    scope = report.get("confirmed_execution_scope")
    if not isinstance(scope, dict):
        issue(errors, "invalid_scope", "confirmed_execution_scope must be an object")
        scope = {}
    for key in sorted(REQUIRED_SCOPE - set(scope)):
        issue(errors, "missing_scope_field", key)
    if status in {"pass", "mismatch"}:
        for key in sorted(CRITICAL_NONEMPTY):
            if scope.get(key) in (None, "", [], {}):
                issue(errors, "empty_critical_scope_field", key)

    provenance = report.get("field_provenance")
    if not isinstance(provenance, dict):
        issue(errors, "invalid_provenance", "field_provenance must be an object")
        provenance = {}
    for key in sorted(REQUIRED_SCOPE):
        if key not in provenance:
            issue(errors, "missing_field_provenance", key)
            continue
        value = provenance[key]
        refs = value.get("source_refs", []) if isinstance(value, dict) else []
        if not refs:
            issue(errors, "empty_field_provenance", key)
        for ref in refs:
            if ref not in source_paths:
                issue(errors, "unknown_provenance_ref", f"{key}: {ref}")
        if not isinstance(value, dict) or value.get("derivation") not in DERIVATIONS:
            issue(errors, "invalid_provenance_derivation", key)

    if not isinstance(report.get("assumptions"), list):
        issue(errors, "invalid_assumptions", "assumptions must be an array")

    confidence = report.get("confidence")
    if not isinstance(confidence, dict) or confidence.get("overall") not in CONFIDENCE:
        issue(errors, "invalid_confidence", "overall confidence is required")
    else:
        by_axis = confidence.get("by_axis")
        if not isinstance(by_axis, dict):
            issue(errors, "invalid_axis_confidence", "by_axis must be an object")
        else:
            for axis in REQUIRED_AXES:
                if by_axis.get(axis) not in CONFIDENCE:
                    issue(errors, "invalid_axis_confidence", axis)

    action = report.get("next_action")
    if action not in ACTIONS:
        issue(errors, "invalid_next_action", repr(action))
    if status == "pass":
        if mismatches:
            issue(errors, "pass_with_mismatches", "pass requires an empty mismatch list")
        if blockers:
            issue(errors, "pass_with_blockers", "pass requires no blockers")
        if action != "continue_to_phase_b":
            issue(errors, "pass_action_mismatch", repr(action))
    elif status == "mismatch":
        if not mismatches:
            issue(errors, "mismatch_without_records", "mismatch requires at least one record")
        if blockers or blocking_mismatch:
            issue(errors, "mismatch_is_blocking", "blocking issues require status=blocked")
        if action != "continue_with_constraints":
            issue(errors, "mismatch_action_mismatch", repr(action))
    elif status == "blocked":
        if not blockers and not blocking_mismatch:
            issue(errors, "blocked_without_reason", "blocked requires a blocker or unresolved material mismatch")
        if action not in {"human_review", "stop_and_report"}:
            issue(errors, "blocked_action_mismatch", repr(action))

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", default="external_executor/context_alignment_report.json")
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    path = resolve_in_workspace(root, args.report)
    try:
        report = load_json(path)
    except Exception as exc:
        result = {"valid": False, "errors": [{"code": "invalid_report_json", "message": str(exc)}], "warnings": []}
    else:
        result = validate_report(report, root)
    emit(result)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
