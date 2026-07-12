#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import canonical_json_hash, dump_json_atomic, get_nested, load_json, resolve_in_workspace, resolve_workspace, utc_now

MATERIAL_PATHS = {
    "benchmark.name", "benchmark.task", "benchmark.version",
    "dataset.name", "dataset.version", "dataset.split", "dataset.preprocessing",
    "metrics.primary", "metrics.directions", "metrics.aggregation",
    "baselines", "evaluation.script_refs", "evaluation.statistics", "evaluation.uncertainty_strategy",
    "seeds_and_repeats", "hyperparameters.search_policy", "hyperparameters.tuning_split",
    "hyperparameters.tuning_budget", "hyperparameters.fairness_rule",
}


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            out.update(flatten(value, path))
        return out
    return {prefix: data}


def material(path: str) -> bool:
    return any(path == root or path.startswith(root + ".") for root in MATERIAL_PATHS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare protocol snapshots and classify evidence invalidation impact.")
    parser.add_argument("--workspace")
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--plan")
    parser.add_argument("--output", default="external_executor/protocol_change_impact.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    old = load_json(resolve_in_workspace(ws, args.old))
    new = load_json(resolve_in_workspace(ws, args.new))
    old_flat = flatten(old.get("protocol", old))
    new_flat = flatten(new.get("protocol", new))
    changes = []
    for path in sorted(set(old_flat) | set(new_flat)):
        before = old_flat.get(path)
        after = new_flat.get(path)
        if canonical_json_hash(before) == canonical_json_hash(after):
            continue
        changes.append({"field": path, "before": before, "after": after, "impact": "material" if material(path) else "nonmaterial"})
    material_changes = [c for c in changes if c["impact"] == "material"]
    affected = []
    if args.plan:
        plan = load_json(resolve_in_workspace(ws, args.plan))
        affected = [e.get("experiment_id") for e in plan.get("experiments", []) if e.get("run_type") != "smoke"]
    report = {
        "schema_version": "protocol_change_impact.v1",
        "generated_at": utc_now(),
        "old_protocol_version": old.get("protocol_version"),
        "new_protocol_version": new.get("protocol_version"),
        "changes": changes,
        "material_change": bool(material_changes),
        "requires_new_protocol_version": bool(changes),
        "requires_result_invalidation_or_downgrade": bool(material_changes),
        "affected_experiment_ids": affected if material_changes else [],
        "required_action": "version_and_mark_affected_results_stale" if material_changes else ("record_nonmaterial_change" if changes else "none"),
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
