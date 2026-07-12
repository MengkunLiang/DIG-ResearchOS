#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    listify,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build bidirectional method/code/config/result/visual/claim mappings.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/final_evidence_snapshot.json")
    parser.add_argument("--method", default="external_executor/evidence_package/realized_method_package.json")
    parser.add_argument("--framework", default="external_executor/evidence_package/framework_figure_spec.json")
    parser.add_argument("--inventory", default="external_executor/evidence_package/figure_table_inventory.json")
    parser.add_argument("--output", default="external_executor/evidence_package/evidence_mapping.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    method = load_json(resolve_in_workspace(ws, args.method))
    framework = load_json(resolve_in_workspace(ws, args.framework))
    inventory = load_json(resolve_in_workspace(ws, args.inventory))
    result = load_json(ws / "external_executor/result_pack.json")

    module_mappings = []
    for module in method.get("implemented_modules", []):
        support = module.get("empirical_support", {})
        related_visuals = [
            item.get("artifact_id") for item in inventory.get("items", [])
            if module.get("module_id") in str(item) or module.get("name") in str(item)
        ]
        module_mappings.append({
            "mapping_id": stable_id("MAP-MOD", module.get("module_id"), snapshot.get("snapshot_fingerprint")),
            "module_id": module.get("module_id"),
            "module_name": module.get("name"),
            "definition_status": module.get("definition_status"),
            "code_refs": module.get("code_refs", []),
            "config_keys": module.get("config_keys", []),
            "implementation_evidence_refs": module.get("implementation_evidence_refs", []),
            "empirical_support_status": support.get("status"),
            "empirical_evidence_refs": support.get("evidence_refs", []),
            "evidence_types": support.get("evidence_types", []),
            "framework_node_ids": [node.get("node_id") for node in framework.get("nodes", []) if node.get("node_id") == module.get("module_id")],
            "visual_artifact_ids": unique_strings(related_visuals),
            "claim_ids": [],
        })

    visual_mappings = []
    for item in inventory.get("items", []):
        visual_mappings.append({
            "mapping_id": stable_id("MAP-VIS", item.get("artifact_id"), snapshot.get("snapshot_fingerprint")),
            "artifact_id": item.get("artifact_id"),
            "kind": item.get("kind"),
            "status": item.get("status"),
            "claim_ids": item.get("claim_ids", []),
            "source_result_refs": item.get("source_result_refs", []),
            "source_data_refs": item.get("source_data_refs", []),
            "config_refs": item.get("config_refs", []),
            "log_refs": item.get("log_refs", []),
            "metric_output_refs": item.get("metric_output_refs", []),
            "plot_script_refs": item.get("plot_script_refs", []),
            "rendered_files": item.get("rendered_files", []),
            "protocol_fingerprint": item.get("protocol_fingerprint"),
            "numeric_traceability": item.get("numeric_traceability"),
        })

    claims_value = result.get("claim_evidence_matrix", {})
    claims = claims_value.get("items", []) if isinstance(claims_value, dict) else listify(claims_value)
    claim_mappings = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        cid = claim.get("claim_id") or stable_id("CLM", claim.get("statement") or claim.get("claim"))
        visuals = [item.get("artifact_id") for item in inventory.get("items", []) if cid in item.get("claim_ids", [])]
        experiments = unique_strings(listify(claim.get("planned_experiment_ids")) + listify(claim.get("experiment_ids")))
        active_records = [record.get("record_id") for record in snapshot.get("active_formal_records", []) if cid in record.get("claim_ids", [])]
        claim_mappings.append({
            "mapping_id": stable_id("MAP-CLM", cid, snapshot.get("snapshot_fingerprint")),
            "claim_id": cid,
            "statement": claim.get("statement") or claim.get("claim"),
            "upstream_status": claim.get("status") or claim.get("support_status"),
            "audit_status": "not_audited_by_T7",
            "experiment_ids": experiments,
            "active_formal_record_ids": active_records,
            "visual_artifact_ids": visuals,
            "module_ids": unique_strings(listify(claim.get("module_ids")) + listify(claim.get("related_modules"))),
            "must_not_claim": unique_strings(listify(claim.get("must_not_claim"))),
        })

    errors: list[str] = []
    warnings: list[str] = []
    for mapping in module_mappings:
        if not mapping["code_refs"]:
            errors.append(f"module_mapping_missing_code_refs:{mapping['module_id']}")
        if not mapping["config_keys"]:
            errors.append(f"module_mapping_missing_config_keys:{mapping['module_id']}")
        if mapping["empirical_support_status"] == "supported" and not mapping["empirical_evidence_refs"]:
            errors.append(f"supported_module_missing_evidence:{mapping['module_id']}")
    for mapping in visual_mappings:
        if mapping["status"] == "ready_for_T7_audit" and mapping["kind"] != "framework_figure":
            for field in ("source_result_refs", "source_data_refs", "config_refs", "log_refs", "metric_output_refs", "plot_script_refs", "rendered_files"):
                if not nonempty(mapping.get(field)):
                    errors.append(f"ready_visual_missing_{field}:{mapping['artifact_id']}")
        if mapping["status"] == "stale":
            warnings.append(f"stale_visual_preserved:{mapping['artifact_id']}")

    mapping = {
        "schema_version": "evidence_mapping.v1",
        "generated_at": utc_now(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "status": "complete" if not errors else "partial",
        "module_mappings": module_mappings,
        "visual_mappings": visual_mappings,
        "claim_candidate_mappings": claim_mappings,
        "validation": {"errors": errors, "warnings": warnings},
        "mapping_fingerprint": None,
        "notes": ["Claim mappings are pre-T7 candidates and do not approve paper claims."],
    }
    mapping["mapping_fingerprint"] = canonical_json_hash({k: v for k, v in mapping.items() if k != "mapping_fingerprint"})
    dump_json_atomic(resolve_in_workspace(ws, args.output), mapping)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
