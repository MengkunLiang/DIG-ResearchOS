#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    extract_refs,
    file_ref,
    get_nested,
    listify,
    load_json,
    nonempty,
    record_id,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
    walk_dicts,
)

FIG_EXTS = {".svg", ".png", ".pdf", ".jpg", ".jpeg", ".webp", ".eps"}
TABLE_EXTS = {".csv", ".tsv", ".tex", ".parquet", ".xlsx"}
PLOT_EXTS = {".py", ".r", ".jl", ".ipynb", ".m", ".qmd"}


def classify_path(value: str) -> str | None:
    suffix = Path(value).suffix.lower()
    if suffix in FIG_EXTS:
        return "figure"
    if suffix in TABLE_EXTS:
        return "table"
    return None


def artifact_kind(record: dict[str, Any], refs: list[str]) -> str | None:
    raw = str(record.get("artifact_kind") or record.get("artifact_type") or record.get("kind") or record.get("type") or "").lower()
    if "figure" in raw or "plot" in raw or "diagram" in raw:
        return "figure"
    if "table" in raw:
        return "table"
    explicit_values = unique_strings(
        listify(record.get("figure_path"))
        + listify(record.get("table_path"))
        + listify(record.get("rendered_files"))
        + listify(record.get("output_path"))
    )
    explicit_refs = extract_refs(explicit_values) + [value for value in explicit_values if isinstance(value, str)]
    kinds = {classify_path(ref) for ref in explicit_refs}
    kinds.discard(None)
    return kinds.pop() if len(kinds) == 1 else None


def source_refs(record: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values: list[Any] = []
    for key in keys:
        values.extend(listify(record.get(key)))
    return unique_strings(extract_refs(values) + [v for v in values if isinstance(v, str)])


def inventory_record(ws: Path, path: str, record: dict[str, Any], kind: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    refs = extract_refs(record)
    rendered = unique_strings(
        listify(record.get("rendered_files"))
        + listify(record.get("figure_path"))
        + listify(record.get("table_path"))
        + listify(record.get("output_path"))
        + [ref for ref in refs if classify_path(ref) == kind]
    )
    plot_scripts = source_refs(record, ("plot_script", "plot_script_ref", "plot_script_refs", "render_script", "script_ref"))
    plot_scripts += [ref for ref in refs if Path(ref).suffix.lower() in PLOT_EXTS]
    source_data = source_refs(record, ("source_data", "source_data_ref", "source_data_refs", "source_table", "source_table_ref", "structured_result_ref"))
    source_results = source_refs(record, ("source_result", "source_result_ref", "source_result_refs", "run_ref", "run_record_ref"))
    configs = source_refs(record, ("config", "config_ref", "config_refs"))
    logs = source_refs(record, ("log", "log_ref", "log_refs", "raw_log"))
    metrics = source_refs(record, ("metric_output", "metric_output_ref", "metric_output_refs", "metrics_ref"))
    claim_ids = unique_strings(listify(record.get("claim_ids")) + listify(record.get("claim_id")))
    evidence_level = str(record.get("evidence_level") or record.get("analysis_role") or record.get("run_type") or "unknown").lower()
    stale = record.get("stale") is True or str(record.get("status") or "").lower() in {"stale", "superseded", "invalid", "unusable"}
    existing_files = []
    missing_files = []
    for value in rendered:
        try:
            target = resolve_in_workspace(ws, value)
            if target.exists() and target.is_file():
                existing_files.append(file_ref(ws, target, evidence_level=evidence_level))
            else:
                missing_files.append(value)
        except Exception:
            missing_files.append(value)
    numeric_traceability = bool(source_data and source_results and metrics and plot_scripts)
    if stale:
        status = "stale"
    elif existing_files and numeric_traceability:
        status = "ready_for_T7_audit"
    elif existing_files:
        status = "partial"
    elif missing_files or rendered:
        status = "missing"
    else:
        status = "partial"
    return {
        "artifact_id": record.get("artifact_id") or record.get("figure_id") or record.get("table_id") or stable_id("VIS", path, record_id(record, path)),
        "kind": kind,
        "title": record.get("title") or record.get("name") or record.get("caption"),
        "status": status,
        "evidence_layer": record.get("evidence_layer") or record.get("role") or ("main" if evidence_level == "confirmatory" else evidence_level),
        "claim_ids": claim_ids,
        "source_record_path": path,
        "source_result_refs": source_results,
        "source_data_refs": source_data,
        "config_refs": configs,
        "log_refs": logs,
        "metric_output_refs": metrics,
        "plot_script_refs": unique_strings(plot_scripts),
        "protocol_fingerprint": record.get("protocol_fingerprint") or snapshot.get("active_protocol_fingerprint"),
        "evidence_level": evidence_level,
        "numeric_traceability": numeric_traceability,
        "editable_source": record.get("editable_source"),
        "rendered_files": existing_files,
        "missing_rendered_paths": missing_files,
        "caption_draft": record.get("caption_draft") or record.get("caption"),
        "must_not_imply": unique_strings(listify(record.get("must_not_imply"))),
        "notes": listify(record.get("notes")),
    }


def required_visuals(result: dict[str, Any], existing_claims: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    plan = result.get("experiment_plan", {})
    experiments = plan.get("experiments", []) if isinstance(plan, dict) else []
    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        role = str(exp.get("analysis_role") or "unknown")
        run_type = str(exp.get("run_type") or exp.get("experiment_kind") or "unknown")
        claims = unique_strings(listify(exp.get("claim_ids")))
        for claim in claims:
            if claim in existing_claims:
                continue
            kind = "table" if run_type in {"formal", "main_comparison", "baseline_reproduction"} else "figure"
            items.append({
                "artifact_id": stable_id("REQ-VIS", exp.get("experiment_id"), claim, kind),
                "kind": kind,
                "title": f"Evidence for {claim}",
                "status": "missing",
                "evidence_layer": "main" if role == "confirmatory" else role,
                "claim_ids": [claim],
                "source_experiment_id": exp.get("experiment_id"),
                "source_result_refs": [],
                "source_data_refs": [],
                "config_refs": [],
                "log_refs": [],
                "metric_output_refs": [],
                "plot_script_refs": [],
                "protocol_fingerprint": exp.get("protocol_fingerprint"),
                "evidence_level": role,
                "numeric_traceability": False,
                "editable_source": None,
                "rendered_files": [],
                "missing_rendered_paths": [],
                "caption_draft": None,
                "must_not_imply": ["claim support until source results and reproducible plotting path exist"],
                "notes": ["Required by experiment plan but no traceable visual artifact was detected."],
            })
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory traceable result figures and tables from the pinned evidence snapshot.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/final_evidence_snapshot.json")
    parser.add_argument("--framework", default="external_executor/evidence_package/framework_figure_spec.json")
    parser.add_argument("--output", default="external_executor/evidence_package/figure_table_inventory.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    result = load_json(ws / "external_executor/result_pack.json")
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    framework = load_json(resolve_in_workspace(ws, args.framework))
    items: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for path, record in walk_dicts(result):
        refs = extract_refs(record)
        kind = artifact_kind(record, refs)
        if not kind:
            continue
        signature = canonical_json_hash({"path": path, "refs": refs, "kind": kind})
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        items.append(inventory_record(ws, path, record, kind, snapshot))

    framework_item = {
        "artifact_id": framework.get("figure_id") or stable_id("FIG-FRAMEWORK", snapshot.get("snapshot_fingerprint")),
        "kind": "framework_figure",
        "title": "Final realized method framework",
        "status": framework.get("status"),
        "evidence_layer": "method_definition",
        "claim_ids": [],
        "source_result_refs": [],
        "source_data_refs": [],
        "config_refs": unique_strings([key for node in framework.get("nodes", []) for key in node.get("config_keys", [])]),
        "log_refs": [],
        "metric_output_refs": [],
        "plot_script_refs": [],
        "protocol_fingerprint": snapshot.get("active_protocol_fingerprint"),
        "evidence_level": "method_definition",
        "numeric_traceability": True,
        "editable_source": framework.get("editable_source"),
        "rendered_files": framework.get("rendered_files", []),
        "missing_rendered_paths": [],
        "caption_draft": framework.get("caption_draft"),
        "must_not_imply": [item.get("reason") for item in framework.get("must_not_show", [])],
        "evidence_mapping_ref": "external_executor/evidence_package/framework_figure_spec.json#evidence_mapping",
        "notes": [],
    }
    items.insert(0, framework_item)

    existing_claims = {claim for item in items if item.get("status") != "missing" for claim in item.get("claim_ids", [])}
    items.extend(required_visuals(result, existing_claims))
    status_counts = {}
    for item in items:
        status_counts[item.get("status")] = status_counts.get(item.get("status"), 0) + 1
    inventory = {
        "schema_version": "figure_table_inventory.v1",
        "generated_at": utc_now(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "status": "complete" if items and not status_counts.get("missing") and not status_counts.get("partial") else ("partial" if items else "unavailable"),
        "items": items,
        "status_counts": status_counts,
        "rules": {
            "ready_requires_source_result_config_log_metric_plot_script": True,
            "manual_number_edits_forbidden": True,
            "stale_visuals_excluded_from_active_claim_support": True,
            "framework_figure_uses_method_definition_traceability": True,
        },
        "inventory_fingerprint": None,
        "notes": [],
    }
    inventory["inventory_fingerprint"] = canonical_json_hash({k: v for k, v in inventory.items() if k != "inventory_fingerprint"})
    dump_json_atomic(resolve_in_workspace(ws, args.output), inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
