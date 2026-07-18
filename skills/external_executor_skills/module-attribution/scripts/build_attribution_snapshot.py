#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    assert_write_allowed, canonical_hash, current_diagnosis, current_implementation, dump_json_atomic,
    get_nested, listify, load_json, metric_direction, normalize_state_map, relpath,
    resolve_in_workspace, resolve_workspace, section_items, stable_id, utc_now,
)


def module_records(result: dict[str, Any], handoff: dict[str, Any], iteration_id: str) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    intent = handoff.get("method_intent", {}) if isinstance(handoff, dict) else {}
    for idx, item in enumerate(intent.get("candidate_modules", []) if isinstance(intent, dict) else [], 1):
        if not isinstance(item, dict):
            continue
        mid = str(item.get("module_id") or stable_id("MOD", item.get("name", idx)))
        records[mid] = {
            "module_id": mid, "owner_method_id": "ours", "name": item.get("name", mid),
            "module_kind": item.get("module_kind", "core"), "intended_role": item.get("intended_role", item.get("why_it_may_help", "")),
            "inputs": listify(item.get("expected_input")), "outputs": listify(item.get("expected_output")),
            "code_paths": [], "config_keys": [], "ablation_switches": listify(item.get("planned_ablation")),
            "diagnostic_switches": [], "mechanism_ids": listify(item.get("mechanism_id") or item.get("related_claim")),
            "implementation_status": "declared_only", "source_refs": ["external_executor/handoff_pack.json#method_intent"], "notes": [],
        }
    implementation = current_implementation(result, iteration_id)
    for impl in [implementation] if implementation else []:
        for item in impl.get("module_mappings", {}).get("items", []) if isinstance(impl.get("module_mappings"), dict) else listify(impl.get("module_mappings")):
            if not isinstance(item, dict):
                continue
            mid = str(item.get("module_id") or stable_id("MOD", item.get("name", "module")))
            base = records.get(mid, {"module_id": mid, "owner_method_id": item.get("owner_method_id", "ours"), "name": item.get("name", mid), "module_kind": item.get("module_kind", "other"), "intended_role": item.get("purpose", ""), "inputs": [], "outputs": [], "mechanism_ids": [], "source_refs": [], "notes": []})
            base.update({
                "owner_method_id": item.get("owner_method_id", base.get("owner_method_id", "ours")),
                "code_paths": listify(item.get("code_paths") or item.get("code_path")),
                "config_keys": listify(item.get("config_keys")),
                "ablation_switches": listify(item.get("ablation_switch") or item.get("ablation_switches")),
                "diagnostic_switches": listify(item.get("diagnostic_switches")),
                "inputs": listify(item.get("inputs") or item.get("input")) or base.get("inputs", []),
                "outputs": listify(item.get("outputs") or item.get("output")) or base.get("outputs", []),
                "mechanism_ids": listify(item.get("mechanism_ids")) or base.get("mechanism_ids", []),
                "implementation_status": "implemented",
            })
            base.setdefault("source_refs", []).append(f"result_pack.implementations#{impl.get('implementation_id', 'unknown')}")
            records[mid] = base
    for brep in section_items(result.get("baseline_reproduction")):
        owner = str(brep.get("baseline_id") or brep.get("method_id") or brep.get("name") or "baseline")
        for item in listify(brep.get("module_mappings") or brep.get("modules")):
            if not isinstance(item, dict):
                continue
            mid = str(item.get("module_id") or stable_id("MOD", owner, item.get("name", "component")))
            records[mid] = {
                "module_id": mid, "owner_method_id": owner, "name": item.get("name", mid),
                "module_kind": item.get("module_kind", "baseline_component"), "intended_role": item.get("purpose", ""),
                "inputs": listify(item.get("inputs")), "outputs": listify(item.get("outputs")),
                "code_paths": listify(item.get("code_paths")), "config_keys": listify(item.get("config_keys")),
                "ablation_switches": listify(item.get("ablation_switches")), "diagnostic_switches": listify(item.get("diagnostic_switches")),
                "mechanism_ids": listify(item.get("mechanism_ids")), "implementation_status": item.get("implementation_status", "implemented"),
                "source_refs": [f"result_pack.baseline_reproduction#{brep.get('reproduction_id', owner)}"], "notes": [],
            }
    return list(records.values())


def mechanism_records(result: dict[str, Any], handoff: dict[str, Any], modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    intent = handoff.get("method_intent", {}) if isinstance(handoff, dict) else {}
    plans = intent.get("mechanism_to_ablation_plan", []) if isinstance(intent, dict) else []
    for idx, item in enumerate(plans, 1):
        if not isinstance(item, dict):
            continue
        mech_id = str(item.get("mechanism_id") or stable_id("MECH", item.get("mechanism", idx)))
        out[mech_id] = {
            "mechanism_id": mech_id, "name": item.get("mechanism", mech_id), "hypothesis": item.get("mechanism", ""),
            "linked_module_ids": listify(item.get("module_ids") or item.get("related_module")),
            "predicted_observations": listify(item.get("expected_observation_if_supported")),
            "falsifying_observations": listify(item.get("expected_observation_if_not_supported")),
            "planned_experiment_ids": listify(item.get("planned_test")), "claim_ids": listify(item.get("claim_ids")),
            "source_refs": ["external_executor/handoff_pack.json#method_intent.mechanism_to_ablation_plan"],
        }
    for module in modules:
        for mech in module.get("mechanism_ids", []):
            if not mech:
                continue
            mech_id = str(mech)
            out.setdefault(mech_id, {"mechanism_id": mech_id, "name": mech_id, "hypothesis": "", "linked_module_ids": [], "predicted_observations": [], "falsifying_observations": [], "planned_experiment_ids": [], "claim_ids": [], "source_refs": []})
            if module["module_id"] not in out[mech_id]["linked_module_ids"]:
                out[mech_id]["linked_module_ids"].append(module["module_id"])
    return list(out.values())


def experiment_entry(plan: dict[str, Any], experiment_id: Any) -> dict[str, Any]:
    for item in section_items(plan.get("experiments")) or section_items(plan):
        if str(item.get("experiment_id") or item.get("id")) == str(experiment_id):
            return item
    return {}


def metric_records(run: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = run.get("metrics") or run.get("metric_output") or run.get("results") or {}
    if isinstance(metrics, list):
        items = metrics
    elif isinstance(metrics, dict):
        items = []
        for name, value in metrics.items():
            if isinstance(value, dict):
                items.append({"name": name, **value})
            else:
                items.append({"name": name, "value": value})
    else:
        items = []
    entry = experiment_entry(plan, run.get("experiment_id"))
    declared_metrics = listify(entry.get("metrics")) or listify(get_nested(plan, "protocol_snapshot.protocol.metrics.primary", default=[]))
    plan_metrics: dict[str, dict[str, Any]] = {}
    for metric in declared_metrics:
        if isinstance(metric, dict) and (metric.get("name") or metric.get("metric")):
            plan_metrics[str(metric.get("name") or metric.get("metric"))] = metric
        elif isinstance(metric, str):
            plan_metrics[metric] = {"name": metric}
    directions = (
        run.get("metric_directions")
        or entry.get("metric_directions")
        or get_nested(plan, "protocol_snapshot.protocol.metrics.directions", default={})
        or {}
    )
    aggregation = get_nested(plan, "protocol_snapshot.protocol.metrics.aggregation", default={})
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("metric") or "")
        if not name:
            continue
        direction = (
            metric_direction(item.get("direction"))
            or metric_direction(directions.get(name) if isinstance(directions, dict) else None)
            or metric_direction(plan_metrics.get(name, {}).get("direction"))
        )
        declared_aggregation = aggregation.get(name) if isinstance(aggregation, dict) else aggregation
        out.append({
            "metric_name": name,
            "value": item.get("value"),
            "direction": direction,
            "aggregation": item.get("aggregation", plan_metrics.get(name, {}).get("aggregation", declared_aggregation or "mean")),
            "unit": item.get("unit", plan_metrics.get(name, {}).get("unit")),
            "source_ref": item.get("source_ref"),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Pin module attribution evidence snapshot.")
    parser.add_argument("--workspace")
    parser.add_argument("--iteration-id", required=True)
    parser.add_argument("--output", default="external_executor/report/phase_E/module_attribution_snapshot.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    result = load_json(ext / "result_pack.json")
    handoff = load_json(ext / "handoff_pack.json")
    diagnosis = current_diagnosis(result, args.iteration_id)
    if not diagnosis:
        raise SystemExit(f"No current diagnosis for {args.iteration_id}")
    implementation = current_implementation(result, args.iteration_id)
    active_implementation_id = implementation.get("implementation_id") if implementation else None
    modules = module_records(result, handoff, args.iteration_id)
    mechanisms = mechanism_records(result, handoff, modules)
    plan = result.get("experiment_plan", {}) if isinstance(result.get("experiment_plan"), dict) else {}
    runs = []
    included = []
    excluded = []
    for run in section_items(result.get("experiment_runs")):
        if str(run.get("iteration_id")) != str(args.iteration_id):
            continue
        run_id = str(run.get("run_id") or stable_id("RUN", args.iteration_id, len(runs)))
        status = str(run.get("run_status") or run.get("status") or "").lower()
        run_type = str(run.get("run_type", "")).lower()
        state_map = normalize_state_map(run.get("module_states"))
        for mid in listify(run.get("disabled_modules") or run.get("removed_modules")):
            state_map[str(mid)] = False
        for mid in listify(run.get("enabled_modules") or run.get("added_modules")):
            state_map[str(mid)] = True
        intervention = run.get("intervention", {}) if isinstance(run.get("intervention"), dict) else {}
        explicit = bool(state_map or intervention or run.get("reference_variant_id"))
        metrics = metric_records(run, plan)
        eligible = status in {"completed", "complete", "success", "succeeded"} and bool(metrics)
        reason = None
        if not eligible:
            reason = "nonterminal_or_missing_metric"
        elif active_implementation_id and run.get("implementation_id") and str(run.get("implementation_id")) != str(active_implementation_id):
            reason = "nonfinal_implementation"
        elif run_type == "ablation" and not (
            state_map
            and run.get("pair_id")
            and run.get("reference_variant_id")
            and intervention.get("controlled") is True
        ):
            reason = "incomplete_ablation_identity"
        elif any(not metric.get("direction") for metric in metrics):
            reason = "missing_metric_direction"
        elif run_type not in {"ablation", "diagnostic", "formal", "small_scale"} and not explicit:
            reason = "not_attribution_relevant"
        dataset = run.get("dataset")
        if isinstance(dataset, dict):
            dataset_id = dataset.get("id") or dataset.get("name")
            dataset_version = dataset.get("version") or run.get("dataset_version")
            split = dataset.get("split") or run.get("split")
        else:
            dataset_id = dataset
            dataset_version = run.get("dataset_version")
            split = run.get("split")
        evidence_id = stable_id("RUN", run_id, run.get("protocol_fingerprint"), run.get("seed"))
        record = {
            "evidence_id": evidence_id, "run_id": run_id, "iteration_id": args.iteration_id,
            "experiment_id": run.get("experiment_id"), "claim_ids": listify(run.get("claim_ids") or run.get("claim_id")),
            "method_id": str(run.get("method_id") or run.get("variant", {}).get("method_id") or run.get("baseline_id") or "unknown"),
            "variant_id": str(run.get("variant_id") or run.get("variant", {}).get("variant_id") or run.get("name") or run_id),
            "reference_variant_id": run.get("reference_variant_id"), "run_type": run_type,
            "pair_id": run.get("pair_id"), "target_module_ids": listify(run.get("target_module_ids")),
            "implementation_id": run.get("implementation_id"),
            "analysis_role": run.get("analysis_role"), "status": status,
            "module_states": state_map, "intervention": intervention,
            "setting": run.get("setting", "default"), "subset": run.get("subset", "all"),
            "dataset": dataset_id, "dataset_version": dataset_version, "split": split,
            "preprocessing_fingerprint": run.get("preprocessing_fingerprint"),
            "protocol_fingerprint": run.get("protocol_fingerprint", plan.get("protocol_fingerprint")),
            "fairness_fingerprint": run.get("fairness_fingerprint"), "seed": run.get("seed"),
            "repeat": run.get("repeat_index", run.get("repeat")),
            "metrics": metrics,
            "artifact_refs": (
                listify(run.get("artifacts"))
                + listify(run.get("artifact_refs"))
                + listify(run.get("raw_log_ref"))
                + listify(run.get("metric_output_ref"))
            ),
            "eligible": eligible and reason is None, "exclusion_reason": reason,
        }
        runs.append(record)
        (included if record["eligible"] else excluded).append(run_id)
    payload = {
        "schema_version": "module_attribution_snapshot.v1", "generated_at": utc_now(),
        "status": "complete" if included else "partial", "iteration_id": args.iteration_id,
        "implementation_id": active_implementation_id,
        "diagnosis_id": diagnosis.get("diagnosis_id"), "diagnosis_gate": diagnosis.get("diagnosis_gate"),
        "diagnosis_input_fingerprint": diagnosis.get("input_fingerprint"),
        "modules": modules, "mechanisms": mechanisms, "runs": runs,
        "included_run_ids": included, "excluded_run_ids": excluded,
        "diagnosis_anomalies": diagnosis.get("anomalies", {}), "diagnosis_confounds": diagnosis.get("confound_assessments", {}),
        "diagnosis_evidence_requests": diagnosis.get("evidence_requests", {}),
        "iteration_history": [
            {
                "iteration_id": item.get("iteration_id"),
                "diagnosis_id": item.get("diagnosis_id"),
                "status": item.get("status"),
                "method_change_assessment": item.get("method_change_assessment"),
            }
            for item in section_items(result.get("result_diagnoses"))
        ],
        "implementation_history": [
            {
                "implementation_id": item.get("implementation_id"),
                "iteration_id": item.get("iteration_id"),
                "status": item.get("status"),
                "implementation_root": item.get("implementation_root"),
            }
            for item in section_items(result.get("implementations"))
        ],
    }
    payload["input_fingerprint"] = canonical_hash({
        key: payload[key]
        for key in (
            "iteration_id", "implementation_id", "diagnosis_id", "diagnosis_input_fingerprint",
            "modules", "mechanisms", "runs", "iteration_history", "implementation_history",
        )
    })
    assert_write_allowed(ws, output)
    dump_json_atomic(output, payload)
    print(f"wrote {len(included)} included runs to {relpath(ws, output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
