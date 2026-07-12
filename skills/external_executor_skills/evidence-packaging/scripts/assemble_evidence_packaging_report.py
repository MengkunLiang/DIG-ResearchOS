#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import canonical_json_hash, dump_json_atomic, file_ref, load_json, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble the durable evidence-packaging child report.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/evidence_packaging_report.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    paths = {
        "preflight": "external_executor/evidence_packaging_preflight.json",
        "snapshot": "external_executor/final_evidence_snapshot.json",
        "snapshot_validation": "external_executor/final_evidence_snapshot_validation.json",
        "realized_method_package": "external_executor/evidence_package/realized_method_package.json",
        "framework_figure": "external_executor/evidence_package/framework_figure_spec.json",
        "figure_table_inventory": "external_executor/evidence_package/figure_table_inventory.json",
        "evidence_mapping": "external_executor/evidence_package/evidence_mapping.json",
        "package_manifest": "external_executor/evidence_package/evidence_package_manifest.json",
        "packaging_gate": "external_executor/evidence_packaging_gate.json",
    }
    data = {key: load_json(resolve_in_workspace(ws, path)) for key, path in paths.items()}
    gate = data["packaging_gate"]
    child_status = "complete" if gate.get("status") == "ready" else ("partial" if gate.get("status") == "partial" else "blocked")
    report = {
        "schema_version": "evidence_packaging_report.v1",
        "child_skill": "evidence-packaging",
        "generated_at": utc_now(),
        "status": child_status,
        "packaging_readiness": gate.get("status"),
        "snapshot_id": data["snapshot"].get("snapshot_id"),
        "snapshot_fingerprint": data["snapshot"].get("snapshot_fingerprint"),
        "realized_method_package": data["realized_method_package"],
        "framework_figure": data["framework_figure"],
        "figure_table_inventory": data["figure_table_inventory"],
        "evidence_mapping": data["evidence_mapping"],
        "package_manifest": data["package_manifest"],
        "validation": {
            "preflight": data["preflight"],
            "snapshot": data["snapshot_validation"],
            "gate": gate,
        },
        "artifact_refs": [file_ref(ws, resolve_in_workspace(ws, path), evidence_level="evidence_package") for path in paths.values()],
        "blocking_issues": gate.get("blocking_issues", []),
        "constraints": gate.get("constraints", []),
        "recommended_next_action": gate.get("next_action"),
        "handoff_semantics": "pre_T7_audit_only",
        "claim_approval": "not_performed",
        "report_fingerprint": None,
        "notes": [],
    }
    report["report_fingerprint"] = canonical_json_hash({k: v for k, v in report.items() if k != "report_fingerprint"})
    dump_json_atomic(resolve_in_workspace(ws, args.output), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
