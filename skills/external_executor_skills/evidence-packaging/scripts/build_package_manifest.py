#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    file_ref,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
)


def entity_for_path(ws: Path, path_value: str, role: str, snapshot_fp: str | None) -> dict[str, Any]:
    path = resolve_in_workspace(ws, path_value)
    ref = file_ref(ws, path, evidence_level=role)
    return {
        "entity_id": ref["artifact_id"],
        "entity_type": "File" if path.suffix else "Artifact",
        "role": role,
        "path": ref["path"],
        "sha256": ref["sha256"],
        "size_bytes": ref["size_bytes"],
        "exists": path.exists(),
        "snapshot_fingerprint": snapshot_fp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a lightweight Research-Object-style manifest for the evidence package.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/final_evidence_snapshot.json")
    parser.add_argument("--method", default="external_executor/evidence_package/realized_method_package.json")
    parser.add_argument("--framework", default="external_executor/report/framework_figure_spec.json")
    parser.add_argument("--inventory", default="external_executor/report/figure_table_inventory.json")
    parser.add_argument("--mapping", default="external_executor/report/evidence_mapping.json")
    parser.add_argument("--tables-report", default="external_executor/report/result_table_build_report.json")
    parser.add_argument("--figures-report", default="external_executor/report/result_figure_build_report.json")
    parser.add_argument("--output", default="external_executor/report/evidence_package_manifest.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    method = load_json(resolve_in_workspace(ws, args.method))
    framework = load_json(resolve_in_workspace(ws, args.framework))
    inventory = load_json(resolve_in_workspace(ws, args.inventory))
    mapping = load_json(resolve_in_workspace(ws, args.mapping))
    tables_report = load_json(resolve_in_workspace(ws, args.tables_report))
    figures_report = load_json(resolve_in_workspace(ws, args.figures_report))
    fp = snapshot.get("snapshot_fingerprint")

    core_paths = {
        "final_evidence_snapshot": args.snapshot,
        "realized_method_package": args.method,
        "framework_figure_spec": args.framework,
        "figure_table_inventory": args.inventory,
        "evidence_mapping": args.mapping,
        "result_table_build_report": args.tables_report,
        "result_figure_build_report": args.figures_report,
    }
    entities = [entity_for_path(ws, path, role, fp) for role, path in core_paths.items()]
    known_paths = {entity["path"] for entity in entities}

    source_paths = []
    source_paths.extend(str(value).split("#", 1)[0] for value in method.get("source_refs", []) if value)
    final_version = method.get("final_version", {})
    if final_version.get("implementation_root"):
        source_paths.append(str(final_version["implementation_root"]))
    for module in method.get("implemented_modules", []):
        source_paths.extend(str(value).split("#", 1)[0] for value in module.get("code_refs", []) if value)
    for loss in method.get("actual_losses", []):
        source_paths.extend(str(value).split("#", 1)[0] for value in loss.get("implementation_refs", []) if value)
    for index, path_value in enumerate(unique_strings(source_paths), start=1):
        if path_value in known_paths:
            continue
        entity = entity_for_path(ws, path_value, f"realized_method_source_{index}", fp)
        entities.append(entity)
        known_paths.add(entity["path"])

    for file_value in [framework.get("editable_source"), *framework.get("rendered_files", [])]:
        if isinstance(file_value, dict):
            file_value = file_value.get("path")
        if isinstance(file_value, str) and file_value not in known_paths:
            entity = entity_for_path(ws, file_value, "framework_figure_asset", fp)
            entities.append(entity)
            known_paths.add(entity["path"])
    for item in inventory.get("items", []):
        for file_value in item.get("rendered_files", []):
            if isinstance(file_value, dict):
                file_value = file_value.get("path")
            if isinstance(file_value, str) and file_value not in known_paths:
                entity = entity_for_path(ws, file_value, f"{item.get('kind')}_render", fp)
                entities.append(entity)
                known_paths.add(entity["path"])
    for item in [*tables_report.get("tables", []), *figures_report.get("figures", [])]:
        file_value = item.get("path") if isinstance(item, dict) else None
        if isinstance(file_value, str) and file_value not in known_paths:
            role = "generated_table" if file_value.startswith("external_executor/table/") else "generated_figure"
            entity = entity_for_path(ws, file_value, role, fp)
            entities.append(entity)
            known_paths.add(entity["path"])

    by_role = {entity["role"]: entity["entity_id"] for entity in entities}
    relationships = [
        {"subject": by_role.get("realized_method_package"), "predicate": "derivedFrom", "object": by_role.get("final_evidence_snapshot")},
        {"subject": by_role.get("framework_figure_spec"), "predicate": "visualizes", "object": by_role.get("realized_method_package")},
        {"subject": by_role.get("figure_table_inventory"), "predicate": "derivedFrom", "object": by_role.get("final_evidence_snapshot")},
        {"subject": by_role.get("evidence_mapping"), "predicate": "maps", "object": by_role.get("realized_method_package")},
        {"subject": by_role.get("evidence_mapping"), "predicate": "maps", "object": by_role.get("figure_table_inventory")},
    ]
    method_entity = by_role.get("realized_method_package")
    relationships.extend(
        {
            "subject": method_entity,
            "predicate": "derivedFrom",
            "object": entity["entity_id"],
        }
        for entity in entities
        if method_entity and entity["role"].startswith("realized_method_source_")
    )
    relationships = [rel for rel in relationships if rel["subject"] and rel["object"]]
    missing = [entity["path"] for entity in entities if not entity["exists"]]
    package = {
        "schema_version": "evidence_package_manifest.v1",
        "package_id": stable_id("PKG", fp or "unknown"),
        "generated_at": utc_now(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": fp,
        "status": "complete" if not missing else "partial",
        "root": {
            "entity_type": "ResearchEvidencePackage",
            "name": "ResearchOS external-executor evidence package",
            "purpose": "Pre-T7 audit package; not a paper-approved evidence set.",
            "has_part": [entity["entity_id"] for entity in entities],
        },
        "entities": entities,
        "relationships": relationships,
        "missing_entities": missing,
        "manifest_fingerprint": None,
        "notes": [
            "This manifest borrows Research Object principles of identity, aggregation, annotation, and explicit relationships.",
            "External source artifacts remain referenced in the snapshot and are not copied automatically.",
        ],
    }
    package["manifest_fingerprint"] = canonical_json_hash({k: v for k, v in package.items() if k != "manifest_fingerprint"})
    dump_json_atomic(resolve_in_workspace(ws, args.output), package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
