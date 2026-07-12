#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict, deque

from _common import dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace, utc_now


def validate(plan: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    experiments = plan.get("experiments", [])
    ids = [e.get("experiment_id") for e in experiments if isinstance(e, dict)]
    known = set(ids)
    if None in known or "" in known:
        errors.append("experiment_id_missing")
    if len(ids) != len(known):
        errors.append("duplicate_experiment_id")
    indegree = {eid: 0 for eid in known if eid}
    graph: dict[str, list[str]] = defaultdict(list)
    edges = plan.get("execution_dag", {}).get("edges", [])
    declared = {(e.get("from"), e.get("to")) for e in edges if isinstance(e, dict)}
    for exp in experiments:
        if not isinstance(exp, dict) or not exp.get("experiment_id"):
            continue
        eid = exp["experiment_id"]
        for dep in exp.get("depends_on", []):
            if dep not in known:
                errors.append(f"unknown_dependency:{eid}:{dep}")
                continue
            if dep == eid:
                errors.append(f"self_dependency:{eid}")
                continue
            graph[dep].append(eid)
            indegree[eid] += 1
            if (dep, eid) not in declared:
                warnings.append(f"missing_declared_edge:{dep}->{eid}")
    for source, target in declared:
        if source not in known or target not in known:
            errors.append(f"edge_unknown_node:{source}->{target}")
    queue = deque(sorted(eid for eid, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for nxt in graph.get(node, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(indegree):
        cyclic = sorted(eid for eid, degree in indegree.items() if degree > 0)
        errors.append("cycle_detected:" + ",".join(cyclic))
    groups = plan.get("execution_dag", {}).get("parallel_groups", [])
    for group in groups:
        members = group.get("experiment_ids", []) if isinstance(group, dict) else []
        for member in members:
            if member not in known:
                errors.append(f"parallel_group_unknown_experiment:{member}")
        for member in members:
            deps = next((e.get("depends_on", []) for e in experiments if e.get("experiment_id") == member), [])
            if any(dep in members for dep in deps):
                errors.append(f"parallel_group_internal_dependency:{group.get('group_id')}:{member}")
    return {
        "schema_version": "experiment_plan_dag_validation.v1",
        "generated_at": utc_now(),
        "status": "pass" if not errors else "blocked",
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "topological_order": order,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate experiment dependencies and parallel groups.")
    parser.add_argument("--workspace")
    parser.add_argument("--plan", default="external_executor/experiment_plan.json")
    parser.add_argument("--output", default="external_executor/experiment_plan_dag_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report = validate(load_json(resolve_in_workspace(ws, args.plan)))
    dump_json_atomic(resolve_in_workspace(ws, args.output), report)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
