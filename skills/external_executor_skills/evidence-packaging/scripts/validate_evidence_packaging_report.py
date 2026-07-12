#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import dump_json_atomic, load_json, nonempty, resolve_in_workspace, resolve_workspace, utc_now

VALID_CHILD_STATUS = {"complete", "partial", "blocked", "failed"}
VALID_READINESS = {"ready", "partial", "blocked"}
VALID_METHOD = {"complete", "partial", "unavailable"}
VALID_FRAMEWORK = {"ready_for_T7_audit", "missing", "blocked"}
VALID_VISUAL = {"ready_for_T7_audit", "partial", "missing", "blocked", "stale"}


def validate(report: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if report.get("schema_version") != "evidence_packaging_report.v1":
        errors.append("invalid_report_schema")
    if report.get("child_skill") != "evidence-packaging":
        errors.append("invalid_child_skill")
    if report.get("status") not in VALID_CHILD_STATUS:
        errors.append("invalid_child_status")
    if report.get("packaging_readiness") not in VALID_READINESS:
        errors.append("invalid_packaging_readiness")
    for key in ("snapshot_id", "snapshot_fingerprint", "realized_method_package", "framework_figure", "figure_table_inventory", "evidence_mapping", "package_manifest", "validation", "recommended_next_action"):
        if not nonempty(report.get(key)):
            errors.append(f"missing_required_field:{key}")

    expected_fp = report.get("snapshot_fingerprint")
    components = {
        "method": report.get("realized_method_package", {}),
        "framework": report.get("framework_figure", {}),
        "inventory": report.get("figure_table_inventory", {}),
        "mapping": report.get("evidence_mapping", {}),
        "manifest": report.get("package_manifest", {}),
    }
    for name, component in components.items():
        if component.get("snapshot_fingerprint") != expected_fp:
            errors.append(f"component_snapshot_mismatch:{name}")

    method = components["method"]
    if method.get("status") not in VALID_METHOD:
        errors.append("invalid_method_status")
    for module in method.get("implemented_modules", []):
        mid = module.get("module_id")
        if not module.get("code_refs"):
            errors.append(f"implemented_module_missing_code_refs:{mid}")
        if not module.get("config_keys"):
            errors.append(f"implemented_module_missing_config_keys:{mid}")
        support = module.get("empirical_support", {})
        if support.get("status") == "supported" and not support.get("evidence_refs"):
            errors.append(f"supported_module_missing_evidence:{mid}")
    if method.get("claim_boundary", {}).get("audit_status") != "pre_T7_only":
        errors.append("claim_boundary_must_remain_pre_T7")

    framework = components["framework"]
    if framework.get("status") not in VALID_FRAMEWORK:
        errors.append("invalid_framework_status")
    if framework.get("status") == "ready_for_T7_audit":
        if not framework.get("nodes"):
            errors.append("ready_framework_without_nodes")
        if not framework.get("editable_source"):
            errors.append("ready_framework_without_editable_source")
        if not framework.get("rendered_files"):
            errors.append("ready_framework_without_render")
        forbidden = set()
        for item in framework.get("must_not_show", []):
            reason = str(item.get("reason") or "").lower()
            action = str(item.get("action") or "").lower()
            if action in {"hide", "exclude", "omit"} or any(token in reason for token in ("dropped", "not_in_final", "unimplemented", "removed")):
                if item.get("item"):
                    forbidden.add(str(item.get("item")))
        for node in framework.get("nodes", []):
            if str(node.get("node_id")) in forbidden or str(node.get("label")) in forbidden:
                errors.append(f"framework_contains_forbidden_node:{node.get('node_id')}")

    inventory = components["inventory"]
    for item in inventory.get("items", []):
        status = item.get("status")
        if status not in VALID_VISUAL:
            errors.append(f"invalid_visual_status:{item.get('artifact_id')}:{status}")
        if status == "ready_for_T7_audit" and item.get("kind") != "framework_figure":
            for field in ("source_result_refs", "source_data_refs", "config_refs", "log_refs", "metric_output_refs", "plot_script_refs", "rendered_files"):
                if not item.get(field):
                    errors.append(f"ready_visual_missing_{field}:{item.get('artifact_id')}")
            if not item.get("numeric_traceability"):
                errors.append(f"ready_visual_not_numeric_traceable:{item.get('artifact_id')}")
        if status == "stale" and item.get("claim_ids"):
            warnings.append(f"stale_visual_retains_claim_links_but_must_not_support_them:{item.get('artifact_id')}")

    mapping = components["mapping"]
    if mapping.get("validation", {}).get("errors"):
        errors.extend([f"mapping:{value}" for value in mapping["validation"]["errors"]])
    if report.get("claim_approval") != "not_performed":
        errors.append("evidence_packaging_must_not_approve_claims")
    if report.get("handoff_semantics") != "pre_T7_audit_only":
        errors.append("invalid_handoff_semantics")

    gate = report.get("validation", {}).get("gate", {})
    if gate.get("status") != report.get("packaging_readiness"):
        errors.append("gate_readiness_mismatch")
    if report.get("packaging_readiness") == "ready" and report.get("status") != "complete":
        errors.append("ready_package_requires_complete_child_status")
    if report.get("packaging_readiness") == "partial" and report.get("status") not in {"partial", "complete"}:
        errors.append("partial_package_status_mismatch")
    if report.get("packaging_readiness") == "blocked" and not report.get("blocking_issues"):
        errors.append("blocked_package_without_blocking_issues")
    return sorted(set(errors)), sorted(set(warnings))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the complete F1-F3 evidence package before narrow apply.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/evidence_packaging_report.json")
    parser.add_argument("--output", default="external_executor/evidence_packaging_report_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report = load_json(resolve_in_workspace(ws, args.report))
    errors, warnings = validate(report)
    validation = {
        "schema_version": "evidence_packaging_report_validation.v1",
        "generated_at": utc_now(),
        "status": "pass" if not errors else "blocked",
        "errors": errors,
        "warnings": warnings,
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), validation)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
