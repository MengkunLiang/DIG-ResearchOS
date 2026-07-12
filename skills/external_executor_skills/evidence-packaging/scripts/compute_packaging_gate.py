#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import dump_json_atomic, load_json, nonempty, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute Phase F1-F3 readiness from snapshot, method, visuals, mappings, and manifest.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot-validation", default="external_executor/final_evidence_snapshot_validation.json")
    parser.add_argument("--method", default="external_executor/evidence_package/realized_method_package.json")
    parser.add_argument("--framework", default="external_executor/evidence_package/framework_figure_spec.json")
    parser.add_argument("--inventory", default="external_executor/evidence_package/figure_table_inventory.json")
    parser.add_argument("--mapping", default="external_executor/evidence_package/evidence_mapping.json")
    parser.add_argument("--manifest", default="external_executor/evidence_package/evidence_package_manifest.json")
    parser.add_argument("--output", default="external_executor/evidence_packaging_gate.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    snapshot_validation = load_json(resolve_in_workspace(ws, args.snapshot_validation))
    method = load_json(resolve_in_workspace(ws, args.method))
    framework = load_json(resolve_in_workspace(ws, args.framework))
    inventory = load_json(resolve_in_workspace(ws, args.inventory))
    mapping = load_json(resolve_in_workspace(ws, args.mapping))
    manifest = load_json(resolve_in_workspace(ws, args.manifest))

    fps = {
        "method": method.get("snapshot_fingerprint"),
        "framework": framework.get("snapshot_fingerprint"),
        "inventory": inventory.get("snapshot_fingerprint"),
        "mapping": mapping.get("snapshot_fingerprint"),
        "manifest": manifest.get("snapshot_fingerprint"),
        "snapshot_validation": snapshot_validation.get("snapshot_fingerprint"),
    }
    nonnull = {value for value in fps.values() if value}
    blockers: list[str] = []
    constraints: list[str] = []
    if len(nonnull) != 1 or any(not value for value in fps.values()):
        blockers.append("snapshot_fingerprint_mismatch_or_missing")
    if snapshot_validation.get("status") != "pass":
        blockers.extend([f"snapshot:{error}" for error in snapshot_validation.get("errors", [])] or ["snapshot_validation_failed"])
    constraints.extend([f"snapshot:{warning}" for warning in snapshot_validation.get("warnings", [])])

    if method.get("status") == "unavailable":
        blockers.append("realized_method_unavailable")
    elif method.get("status") == "partial":
        constraints.extend([f"method_unresolved:{field}" for field in method.get("unresolved_fields", [])])
    elif method.get("status") != "complete":
        blockers.append(f"invalid_realized_method_status:{method.get('status')}")

    if framework.get("status") == "missing":
        constraints.append("framework_figure_missing")
    elif framework.get("status") == "blocked":
        constraints.extend([f"framework:{field}" for field in framework.get("unresolved_fields", [])])
    elif framework.get("status") == "ready_for_T7_audit":
        if not framework.get("editable_source") or not framework.get("rendered_files"):
            constraints.append("framework_figure_not_rendered_or_not_editable")
    else:
        blockers.append(f"invalid_framework_status:{framework.get('status')}")

    main_missing = []
    ready_visuals = 0
    for item in inventory.get("items", []):
        if item.get("status") == "ready_for_T7_audit":
            ready_visuals += 1
        if item.get("evidence_layer") == "main" and item.get("status") in {"missing", "partial", "blocked"}:
            main_missing.append(item.get("artifact_id"))
        if item.get("status") == "ready_for_T7_audit" and item.get("kind") != "framework_figure" and not item.get("numeric_traceability"):
            blockers.append(f"ready_visual_without_numeric_traceability:{item.get('artifact_id')}")
    if main_missing:
        constraints.append("main_visuals_incomplete:" + ",".join(str(x) for x in main_missing))
    if ready_visuals == 0:
        constraints.append("no_ready_visual_artifacts")

    mapping_errors = mapping.get("validation", {}).get("errors", [])
    if mapping_errors:
        blockers.extend([f"mapping:{error}" for error in mapping_errors])
    constraints.extend([f"mapping:{warning}" for warning in mapping.get("validation", {}).get("warnings", [])])
    if manifest.get("missing_entities"):
        constraints.extend([f"manifest_missing:{path}" for path in manifest.get("missing_entities", [])])

    blockers = sorted(set(blockers))
    constraints = sorted(set(constraints))
    useful = method.get("status") in {"complete", "partial"} or ready_visuals > 0
    if blockers:
        status = "blocked"
        next_action = "repair_package_or_return_to_root"
    elif constraints:
        status = "partial"
        next_action = "continue_to_writer_handoff_with_constraints"
    else:
        status = "ready"
        next_action = "continue_to_writer_handoff"

    gate = {
        "schema_version": "evidence_packaging_gate.v1",
        "generated_at": utc_now(),
        "status": status,
        "useful_partial_evidence_present": useful,
        "single_snapshot_enforced": len(nonnull) == 1,
        "snapshot_fingerprint": next(iter(nonnull)) if len(nonnull) == 1 else None,
        "blocking_issues": blockers,
        "constraints": constraints,
        "next_action": next_action,
        "claim_approval": "not_performed",
        "t7_audit_required": True,
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), gate)
    return 0 if status in {"ready", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
