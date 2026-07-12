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


def step_module_id(step: Any) -> str | None:
    if isinstance(step, dict):
        for key in ("module_id", "related_module", "component_id"):
            if nonempty(step.get(key)):
                return str(step[key])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an evidence-bound framework-figure specification from one realized-method package.")
    parser.add_argument("--workspace")
    parser.add_argument("--method", default="external_executor/evidence_package/realized_method_package.json")
    parser.add_argument("--output", default="external_executor/evidence_package/framework_figure_spec.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    method = load_json(resolve_in_workspace(ws, args.method))
    snapshot_id = method.get("snapshot_id")
    snapshot_fp = method.get("snapshot_fingerprint")
    modules = method.get("implemented_modules", [])
    dropped = method.get("dropped_modules", [])
    unsupported = method.get("module_attribution", {}).get("unsupported_mechanisms", [])
    hint_only = method.get("module_attribution", {}).get("definition_or_hint_only", [])
    flow = method.get("actual_algorithm_flow", [])

    nodes: list[dict[str, Any]] = []
    for module in modules:
        mid = str(module.get("module_id") or stable_id("MOD", module.get("name")))
        support = module.get("empirical_support", {})
        nodes.append({
            "node_id": mid,
            "label": module.get("name") or mid,
            "node_type": "implemented_module",
            "role": module.get("actual_role"),
            "definition_status": module.get("definition_status"),
            "empirical_support_status": support.get("status"),
            "code_refs": module.get("code_refs", []),
            "config_keys": module.get("config_keys", []),
            "evidence_refs": unique_strings(module.get("implementation_evidence_refs", []) + support.get("evidence_refs", [])),
            "visual_emphasis": "primary" if support.get("status") == "supported" else "neutral",
            "must_not_imply": (
                [] if support.get("status") == "supported"
                else ["empirically validated contribution", "causal performance mechanism"]
            ),
        })

    node_ids = {node["node_id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    explicit_edges = []
    for step in flow:
        if isinstance(step, dict) and isinstance(step.get("edges"), list):
            explicit_edges.extend(step["edges"])
    for edge in explicit_edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source") or edge.get("from")
        target = edge.get("target") or edge.get("to")
        if source in node_ids and target in node_ids:
            edges.append({
                "edge_id": edge.get("edge_id") or stable_id("EDGE", source, target, edge.get("label")),
                "source": source,
                "target": target,
                "label": edge.get("label") or edge.get("relation"),
                "edge_type": edge.get("edge_type", "data_or_control_flow"),
                "evidence_refs": unique_strings(listify(edge.get("evidence_refs"))),
            })

    if not edges and flow:
        ordered_ids = [step_module_id(step) for step in flow]
        ordered_ids = [mid for mid in ordered_ids if mid in node_ids]
        for source, target in zip(ordered_ids, ordered_ids[1:]):
            edges.append({
                "edge_id": stable_id("EDGE", source, target),
                "source": source,
                "target": target,
                "label": None,
                "edge_type": "declared_algorithm_order",
                "evidence_refs": ["external_executor/evidence_package/realized_method_package.json#actual_algorithm_flow"],
            })

    panels = []
    if nodes:
        panels.append({
            "panel_id": "A",
            "title": "Realized method architecture",
            "purpose": "Show only the implemented modules and their actual algorithmic relationships.",
            "node_ids": [node["node_id"] for node in nodes],
            "edge_ids": [edge["edge_id"] for edge in edges],
            "claim_role": "method_definition",
        })
    supported = method.get("module_attribution", {}).get("supported_mechanisms", [])
    if supported:
        panels.append({
            "panel_id": "B",
            "title": "Evidence-supported mechanisms",
            "purpose": "Distinguish controlled empirical support from implementation-only facts.",
            "attribution_ids": [item.get("attribution_id") for item in supported],
            "claim_role": "mechanism_evidence",
        })

    must_not_show = []
    for module in dropped:
        must_not_show.append({
            "item": module.get("name") or module.get("module_id"),
            "reason": "dropped_or_not_in_final_implementation",
            "source_refs": module.get("source_origins", []),
        })
    for item in unsupported:
        must_not_show.append({
            "item": item.get("mechanism") or item.get("module_id"),
            "reason": "unsupported_mechanism_must_not_be_highlighted_as_validated",
            "source_refs": item.get("evidence_refs", []),
        })
    for item in hint_only:
        must_not_show.append({
            "item": item.get("mechanism") or item.get("module_id"),
            "reason": "hint_or_implementation_fact_must_not_be_shown_as_causal_support",
            "source_refs": item.get("evidence_refs", []),
        })

    unresolved = []
    if method.get("status") != "complete":
        unresolved.append("realized_method_not_complete")
    if not nodes:
        unresolved.append("no_implemented_module_nodes")
    if len(nodes) > 1 and not edges:
        unresolved.append("module_relationships_missing")
    for node in nodes:
        if not node.get("code_refs"):
            unresolved.append(f"node_without_code_ref:{node['node_id']}")
        if not node.get("config_keys"):
            unresolved.append(f"node_without_config_key:{node['node_id']}")

    if unresolved and not nodes:
        status = "missing"
    elif unresolved:
        status = "blocked"
    else:
        status = "ready_for_T7_audit"

    caption = None
    if nodes:
        caption = (
            f"Overview of {method.get('final_method_name') or 'the realized method'}. "
            "The diagram reflects the implemented code and configuration; empirical support is shown only for mechanisms with controlled evidence."
        )

    spec = {
        "schema_version": "framework_figure_spec.v1",
        "generated_at": utc_now(),
        "snapshot_id": snapshot_id,
        "snapshot_fingerprint": snapshot_fp,
        "status": status,
        "figure_id": stable_id("FIG-FRAMEWORK", snapshot_fp or "unknown"),
        "main_message": method.get("one_sentence_method"),
        "panels": panels,
        "nodes": nodes,
        "edges": edges,
        "caption_draft": caption,
        "editable_source": None,
        "rendered_files": [],
        "must_not_show": must_not_show,
        "evidence_mapping": {
            "realized_method_ref": "external_executor/evidence_package/realized_method_package.json",
            "node_count": len(nodes),
            "edge_count": len(edges),
            "supported_attribution_refs": [item.get("attribution_id") for item in supported],
        },
        "unresolved_fields": sorted(set(unresolved)),
        "spec_fingerprint": None,
        "notes": [],
    }
    spec["spec_fingerprint"] = canonical_json_hash({k: v for k, v in spec.items() if k != "spec_fingerprint"})
    dump_json_atomic(resolve_in_workspace(ws, args.output), spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
