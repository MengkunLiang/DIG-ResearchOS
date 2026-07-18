#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    get_nested,
    listify,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    unique_strings,
    utc_now,
)


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return deepcopy(value)
    return {}


def normalize_metrics(benchmark: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
    raw_primary = benchmark.get("primary_metrics", benchmark.get("primary_metric", benchmark.get("metric")))
    raw_secondary = benchmark.get("secondary_metrics", scope.get("secondary_metrics"))
    directions = benchmark.get("metric_directions", benchmark.get("metric_direction", {}))
    if isinstance(directions, str):
        directions = {str(m): directions for m in listify(raw_primary)}
    return {
        "primary": unique_strings(listify(raw_primary)),
        "secondary": unique_strings(listify(raw_secondary)),
        "directions": directions if isinstance(directions, dict) else {},
        "aggregation": benchmark.get("aggregation", benchmark.get("metric_aggregation")),
        "units": benchmark.get("metric_units", {}),
        "selection_rule": benchmark.get("model_selection_rule") or scope.get("model_selection_rule"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a versioned protocol snapshot from confirmed scope and approved resources.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/report/phase_C/protocol_snapshot.json")
    parser.add_argument("--overrides", help="Optional JSON file containing authorized protocol completions")
    parser.add_argument("--force-version", type=int)
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    handoff = load_json(ext / "handoff_pack.json")
    result = load_json(ext / "result_pack.json")
    scope = get_nested(result, "context_alignment.confirmed_execution_scope", default={})
    benchmark = first_dict(
        scope.get("benchmark_protocol_summary"),
        scope.get("benchmark_protocol"),
        get_nested(handoff, "context_reboost.benchmark_protocol"),
    )
    approved_baselines = []
    for item in get_nested(result, "baseline_candidates.items", default=listify(result.get("baseline_candidates"))):
        if not isinstance(item, dict):
            continue
        approved_baselines.append({
            "baseline_id": item.get("baseline_id") or item.get("candidate_id") or item.get("name"),
            "name": item.get("name") or item.get("baseline_name") or item.get("candidate_id"),
            "resource_ref": item.get("artifact_ref") or item.get("path") or item.get("candidate_id"),
            "config_ref": item.get("config_ref") or item.get("default_config"),
            "identity": item.get("identity") or item.get("source_class"),
            "approximation_level": item.get("approximation_level", "unknown"),
            "fairness_constraints": item.get("fairness_constraints", []),
        })
    if not approved_baselines:
        for item in listify(scope.get("required_baselines")):
            if isinstance(item, str):
                approved_baselines.append({"baseline_id": item, "name": item, "resource_ref": None, "config_ref": None})
            elif isinstance(item, dict):
                approved_baselines.append({
                    "baseline_id": item.get("baseline_id") or item.get("name"),
                    "name": item.get("name"),
                    "resource_ref": item.get("resource_ref"),
                    "config_ref": item.get("config_ref"),
                    "fairness_constraints": item.get("fairness_constraints", []),
                })

    budget = first_dict(scope.get("iteration_budget"), get_nested(handoff, "context_reboost.iteration_budget"))
    seed_policy = first_dict(benchmark.get("seed_policy"), scope.get("seed_policy"), get_nested(handoff, "context_reboost.seed_policy"))
    statistics = first_dict(benchmark.get("statistics"), benchmark.get("uncertainty_strategy"), scope.get("statistics"))
    snapshot_core = {
        "benchmark": {
            "name": benchmark.get("benchmark") or benchmark.get("name"),
            "task": benchmark.get("task") or scope.get("task"),
            "version": benchmark.get("benchmark_version") or benchmark.get("version"),
            "protocol_ref": benchmark.get("protocol_ref") or benchmark.get("reference"),
        },
        "dataset": {
            "name": benchmark.get("dataset") or benchmark.get("dataset_name"),
            "version": benchmark.get("dataset_version"),
            "split": benchmark.get("split") or benchmark.get("dataset_split"),
            "split_ref": benchmark.get("split_ref"),
            "preprocessing": benchmark.get("preprocessing") or scope.get("preprocessing"),
            "data_resource_refs": [
                item.get("artifact_ref") or item.get("path") or item.get("dataset_id")
                for item in get_nested(result, "dataset_inventory.items", default=listify(result.get("dataset_inventory")))
                if isinstance(item, dict)
            ],
        },
        "metrics": normalize_metrics(benchmark, scope),
        "baselines": approved_baselines,
        "ours": {
            "config_policy": scope.get("ours_config_policy") or benchmark.get("ours_config_policy"),
            "initialization": benchmark.get("initialization"),
            "training_budget_rule": benchmark.get("training_budget_rule") or scope.get("training_budget_rule"),
        },
        "evaluation": {
            "script_refs": unique_strings(listify(benchmark.get("evaluation_script")) + listify(benchmark.get("evaluation_scripts"))),
            "statistics": statistics,
            "uncertainty_strategy": benchmark.get("uncertainty_strategy") or statistics.get("uncertainty_strategy"),
            "missing_run_policy": benchmark.get("missing_run_policy", "report_and_do_not_impute"),
            "failed_run_policy": benchmark.get("failed_run_policy", "retain_and_exclude_from_confirmatory_aggregation_only_by_predeclared_rule"),
        },
        "seeds_and_repeats": {
            "seeds": listify(seed_policy.get("seeds", benchmark.get("seeds"))),
            "seed_count": seed_policy.get("seed_count", benchmark.get("seed_count")),
            "repeats": seed_policy.get("repeats", benchmark.get("repeats")),
            "selection_policy": seed_policy.get("selection_policy", "predeclared_not_result_selected"),
        },
        "hyperparameters": {
            "search_policy": benchmark.get("hyperparameter_search_policy") or scope.get("hyperparameter_search_policy"),
            "tuning_split": benchmark.get("tuning_split"),
            "tuning_budget": benchmark.get("tuning_budget"),
            "fixed_parameters": benchmark.get("fixed_parameters", {}),
            "fairness_rule": benchmark.get("hyperparameter_fairness_rule") or scope.get("hyperparameter_fairness_rule"),
        },
        "compute_budget": {
            "max_refinement_rounds": budget.get("max_rounds") or budget.get("max_refinement_rounds"),
            "max_total_runs": budget.get("max_total_runs") or budget.get("total_runs") or budget.get("max_runs"),
            "max_trials": budget.get("max_trials") or budget.get("total_trials"),
            "max_wall_clock_hours": budget.get("max_wall_clock_hours") or budget.get("wall_clock_hours"),
            "max_gpu_hours": budget.get("max_gpu_hours") or budget.get("gpu_hours"),
            "max_cost": budget.get("max_cost") or budget.get("cost_budget"),
            "currency": budget.get("currency"),
        },
        "early_stop": {
            "conditions": unique_strings(listify(budget.get("stop_conditions")) + listify(scope.get("stop_conditions"))),
            "experiment_level_rules": benchmark.get("early_stop_rules", []),
        },
        "reporting": {
            "primary_table_policy": benchmark.get("primary_table_policy"),
            "multiple_comparison_policy": benchmark.get("multiple_comparison_policy"),
            "effect_size_policy": benchmark.get("effect_size_policy"),
            "confidence_interval_policy": benchmark.get("confidence_interval_policy"),
        },
    }

    if args.overrides:
        overrides = load_json(resolve_in_workspace(ws, args.overrides))
        if not isinstance(overrides, dict):
            raise ValueError("overrides must contain a JSON object")
        # Authorized completions replace only top-level protocol components.
        for key, value in overrides.items():
            if key in snapshot_core:
                snapshot_core[key] = value

    unresolved: list[str] = []
    required_paths = {
        "dataset.name": get_nested(snapshot_core, "dataset.name"),
        "dataset.split": get_nested(snapshot_core, "dataset.split"),
        "metrics.primary": get_nested(snapshot_core, "metrics.primary"),
        "metrics.aggregation": get_nested(snapshot_core, "metrics.aggregation"),
        "seeds_and_repeats.seed_count_or_seeds": get_nested(snapshot_core, "seeds_and_repeats.seeds") or get_nested(snapshot_core, "seeds_and_repeats.seed_count"),
        "compute_budget.max_total_runs": get_nested(snapshot_core, "compute_budget.max_total_runs"),
    }
    for field, value in required_paths.items():
        if not nonempty(value):
            unresolved.append(field)
    primary = get_nested(snapshot_core, "metrics.primary", default=[])
    directions = get_nested(snapshot_core, "metrics.directions", default={})
    for metric in primary:
        if not isinstance(directions, dict) or not directions.get(metric):
            unresolved.append(f"metrics.directions.{metric}")

    old_snapshot = None
    old_plan = result.get("experiment_plan")
    if isinstance(old_plan, dict):
        old_snapshot = old_plan.get("protocol_snapshot")
    if output.exists():
        old_snapshot = load_json(output)
    old_core = old_snapshot.get("protocol") if isinstance(old_snapshot, dict) else None
    changed = old_core is not None and canonical_json_hash(old_core) != canonical_json_hash(snapshot_core)
    old_version = old_snapshot.get("protocol_version", 0) if isinstance(old_snapshot, dict) else 0
    version = args.force_version or (old_version + 1 if changed else max(1, old_version))

    snapshot = {
        "schema_version": "experiment_protocol.v1",
        "protocol_version": version,
        "generated_at": utc_now(),
        "status": "complete" if not unresolved else "needs_completion",
        "protocol": snapshot_core,
        "unresolved_fields": sorted(set(unresolved)),
        "source_refs": [
            "result_pack.json#context_alignment.confirmed_execution_scope",
            "result_pack.json#resource_readiness",
            "result_pack.json#baseline_candidates",
            "result_pack.json#dataset_inventory",
            "handoff_pack.json#context_reboost",
        ],
        "input_fingerprint": canonical_json_hash({"scope": scope, "resources": result.get("resource_readiness"), "baselines": approved_baselines}),
        "supersedes_protocol_version": old_version if changed and old_version else None,
        "change_detected": changed,
    }
    dump_json_atomic(output, snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
