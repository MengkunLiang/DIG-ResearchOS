#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    assert_write_allowed, canonical_hash, current_diagnosis, dump_json_atomic,
    get_nested, listify, load_json, metric_direction, normalize_state_map, relpath,
    resolve_in_workspace, resolve_workspace, section_items, stable_id, utc_now,
)


def module_records(result: dict[str, Any], handoff: dict[str, Any]) -> list[dict[str, Any]]:
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
    for impl in section_items(result.get("implementations")):
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
    plan_metrics = {str(x.get("name")): x for x in listify(plan.get("metrics")) if isinstance(x, dict) and x.get("name")}
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("metric") or "")
        if not name:
            continue
        direction = metric_direction(item.get("direction")) or metric_direction(plan_metrics.get(name, {}).get("direction"))
        out.append({"metric_name": name, "value": item.get("value"), "direction": direction, "aggregation": item.get("aggregation", plan_metrics.get(name, {}).get("aggregation", "mean")), "unit": item.get("unit"), "source_ref": item.get("source_ref")})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Pin module attribution evidence snapshot.")
    parser.add_argument("--workspace")
    parser.add_argument("--iteration-id", required=True)
    parser.add_argument("--output", default="external_executor/module_attribution_snapshot.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    result = load_json(ext / "result_pack.json")
    handoff = load_json(ext / "handoff_pack.json")
    diagnosis = current_diagnosis(result, args.iteration_id)
    if not diagnosis:
        raise SystemExit(f"No current diagnosis for {args.iteration_id}")
    modules = module_records(result, handoff)
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
        eligible = status in {"completed", "complete", "success", "succeeded"} and bool(metric_records(run, plan))
        reason = None
        if not eligible:
            reason = "nonterminal_or_missing_metric"
        elif run_type not in {"ablation", "diagnostic", "formal", "small_scale"} and not explicit:
            reason = "not_attribution_relevant"
        evidence_id = stable_id("RUN", run_id, run.get("protocol_fingerprint"), run.get("seed"))
        record = {
            "evidence_id": evidence_id, "run_id": run_id, "iteration_id": args.iteration_id,
            "experiment_id": run.get("experiment_id"), "claim_ids": listify(run.get("claim_ids") or run.get("claim_id")),
            "method_id": str(run.get("method_id") or run.get("variant", {}).get("method_id") or run.get("baseline_id") or "unknown"),
            "variant_id": str(run.get("variant_id") or run.get("variant", {}).get("variant_id") or run.get("name") or run_id),
            "reference_variant_id": run.get("reference_variant_id"), "run_type": run_type,
            "analysis_role": run.get("analysis_role"), "status": status,
            "module_states": state_map, "intervention": intervention,
            "setting": run.get("setting", "default"), "subset": run.get("subset", "all"),
            "dataset": run.get("dataset"), "dataset_version": run.get("dataset_version"), "split": run.get("split"),
            "preprocessing_fingerprint": run.get("preprocessing_fingerprint"),
            "protocol_fingerprint": run.get("protocol_fingerprint", plan.get("protocol_fingerprint")),
            "fairness_fingerprint": run.get("fairness_fingerprint"), "seed": run.get("seed"), "repeat": run.get("repeat"),
            "metrics": metric_records(run, plan), "artifact_refs": listify(run.get("artifact_refs")),
            "eligible": eligible and reason is None, "exclusion_reason": reason,
        }
        runs.append(record)
        (included if record["eligible"] else excluded).append(run_id)
    payload = {
        "schema_version": "module_attribution_snapshot.v1", "generated_at": utc_now(),
        "status": "complete" if included else "partial", "iteration_id": args.iteration_id,
        "diagnosis_id": diagnosis.get("diagnosis_id"), "diagnosis_gate": diagnosis.get("diagnosis_gate"),
        "diagnosis_input_fingerprint": diagnosis.get("input_fingerprint"),
        "modules": modules, "mechanisms": mechanisms, "runs": runs,
        "included_run_ids": included, "excluded_run_ids": excluded,
        "diagnosis_anomalies": diagnosis.get("anomalies", {}), "diagnosis_confounds": diagnosis.get("confound_assessments", {}),
        "diagnosis_evidence_requests": diagnosis.get("evidence_requests", {}),
    }
    payload["input_fingerprint"] = canonical_hash({k: payload[k] for k in ("iteration_id", "diagnosis_id", "diagnosis_input_fingerprint", "modules", "mechanisms", "runs")})
    assert_write_allowed(ws, output)
    dump_json_atomic(output, payload)
    print(f"wrote {len(included)} included runs to {relpath(ws, output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
