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


def section_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "records"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def snapshot_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get("value")
        for key, entry in snapshot.get("section_digests", {}).items()
        if isinstance(entry, dict) and "value" in entry
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build bidirectional method/code/config/result/visual/claim mappings.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/final_evidence_snapshot.json")
    parser.add_argument("--method", default="external_executor/evidence_package/realized_method_package.json")
    parser.add_argument("--framework", default="external_executor/report/framework_figure_spec.json")
    parser.add_argument("--inventory", default="external_executor/report/figure_table_inventory.json")
    parser.add_argument("--output", default="external_executor/report/evidence_mapping.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    method = load_json(resolve_in_workspace(ws, args.method))
    framework = load_json(resolve_in_workspace(ws, args.framework))
    inventory = load_json(resolve_in_workspace(ws, args.inventory))
    result = snapshot_result(snapshot)

    traceability = [
        item for item in method.get("evidence_traceability", []) if isinstance(item, dict)
    ]
    claim_to_modules: dict[str, set[str]] = {}
    module_to_claims: dict[str, set[str]] = {}
    module_to_experiments: dict[str, set[str]] = {}
    for item in traceability:
        claim_id = item.get("claim_id")
        if not claim_id:
            continue
        modules = unique_strings(listify(item.get("module_ids")))
        experiments = unique_strings(listify(item.get("experiment_ids")))
        claim_to_modules.setdefault(str(claim_id), set()).update(modules)
        for module_id in modules:
            module_to_claims.setdefault(module_id, set()).add(str(claim_id))
            module_to_experiments.setdefault(module_id, set()).update(experiments)
    for item in method.get("module_attribution", {}).get("module_attributions", []):
        if not isinstance(item, dict) or not item.get("module_id"):
            continue
        module_id = str(item["module_id"])
        module_to_claims.setdefault(module_id, set()).update(unique_strings(listify(item.get("claim_ids"))))
        module_to_experiments.setdefault(module_id, set()).update(unique_strings(listify(item.get("experiment_ids"))))

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
            "claim_ids": sorted(module_to_claims.get(str(module.get("module_id")), set())),
            "experiment_ids": sorted(module_to_experiments.get(str(module.get("module_id")), set())),
            "mechanism_ids": sorted({
                str(item.get("mechanism_id"))
                for item in method.get("module_attribution", {}).get("mechanism_attributions", [])
                if isinstance(item, dict)
                and item.get("mechanism_id")
                and str(module.get("module_id")) in unique_strings(
                    listify(item.get("module_ids")) + listify(item.get("linked_module_ids"))
                )
            }),
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

    claims = section_items(result.get("claim_evidence_matrix", {}))
    by_claim = {str(item.get("claim_id")): item for item in traceability if item.get("claim_id")}
    for claim_id, trace in by_claim.items():
        if not any(str(item.get("claim_id")) == claim_id for item in claims):
            claims.append({
                "claim_id": claim_id,
                "statement": trace.get("mechanism_ref"),
                "experiment_ids": trace.get("experiment_ids", []),
                "module_ids": trace.get("module_ids", []),
                "must_not_claim": [trace.get("interpretation_boundary")] if trace.get("interpretation_boundary") else [],
                "status": "pre_T7_candidate",
            })
    claim_mappings = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        cid = claim.get("claim_id") or stable_id("CLM", claim.get("statement") or claim.get("claim"))
        visuals = [item.get("artifact_id") for item in inventory.get("items", []) if cid in item.get("claim_ids", [])]
        trace = by_claim.get(str(cid), {})
        experiments = unique_strings(
            listify(claim.get("planned_experiment_ids")) + listify(claim.get("experiment_ids"))
            + listify(trace.get("experiment_ids"))
        )
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
            "module_ids": unique_strings(
                listify(claim.get("module_ids")) + listify(claim.get("related_modules"))
                + listify(trace.get("module_ids")) + sorted(claim_to_modules.get(str(cid), set()))
            ),
            "must_not_claim": unique_strings(
                listify(claim.get("must_not_claim"))
                + ([trace.get("interpretation_boundary")] if trace.get("interpretation_boundary") else [])
            ),
            "mechanism_ref": trace.get("mechanism_ref"),
            "expected_artifacts": listify(trace.get("expected_artifacts")),
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
        if traceability and not mapping["claim_ids"]:
            warnings.append(f"module_without_claim_traceability:{mapping['module_id']}")
    for mapping in visual_mappings:
        if mapping["status"] == "ready_for_T7_audit" and mapping["kind"] != "framework_figure":
            for field in ("source_result_refs", "source_data_refs", "config_refs", "log_refs", "metric_output_refs", "plot_script_refs", "rendered_files"):
                if not nonempty(mapping.get(field)):
                    errors.append(f"ready_visual_missing_{field}:{mapping['artifact_id']}")
        if mapping["status"] == "stale":
            warnings.append(f"stale_visual_preserved:{mapping['artifact_id']}")

    graph_edges = []
    for item in module_mappings:
        for claim_id in item["claim_ids"]:
            graph_edges.append({"subject": item["module_id"], "predicate": "supportsCandidate", "object": claim_id})
        for experiment_id in item["experiment_ids"]:
            graph_edges.append({"subject": item["module_id"], "predicate": "testedBy", "object": experiment_id})
    for item in claim_mappings:
        for artifact_id in item["visual_artifact_ids"]:
            graph_edges.append({"subject": item["claim_id"], "predicate": "visualizedBy", "object": artifact_id})

    mapping = {
        "schema_version": "evidence_mapping.v1",
        "generated_at": utc_now(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "status": "complete" if not errors else "partial",
        "module_mappings": module_mappings,
        "visual_mappings": visual_mappings,
        "claim_candidate_mappings": claim_mappings,
        "evidence_graph": {"edges": graph_edges},
        "validation": {"errors": errors, "warnings": warnings},
        "mapping_fingerprint": None,
        "notes": ["Claim mappings are pre-T7 candidates and do not approve paper claims."],
    }
    mapping["mapping_fingerprint"] = canonical_json_hash({k: v for k, v in mapping.items() if k != "mapping_fingerprint"})
    dump_json_atomic(resolve_in_workspace(ws, args.output), mapping)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
