#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import (
    active_iteration_plan,
    canonical_json_hash,
    dictify,
    dump_json_atomic,
    extract_plan_version,
    extract_protocol_fingerprint,
    first_nonempty,
    get_nested,
    listify,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
)


def io_record(value: Any, fallback_name: str) -> list[dict[str, Any]]:
    values = listify(value)
    output: list[dict[str, Any]] = []
    for index, item in enumerate(values, start=1):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or f"{fallback_name}_{index}")
            output.append({
                "name": name,
                "type_or_shape": str(item.get("type_or_shape") or item.get("type") or item.get("shape") or "project_defined"),
                "semantics": str(item.get("semantics") or item.get("description") or item.get("meaning") or name),
            })
        elif nonempty(item):
            output.append({"name": f"{fallback_name}_{index}", "type_or_shape": "project_defined", "semantics": str(item)})
    return output


def normalize_module(comp: dict[str, Any], *, contribution_role: str, status: str) -> dict[str, Any]:
    module_id = str(comp.get("component_id") or comp.get("module_id") or stable_id("M", comp.get("name") or "module"))
    inputs = io_record(comp.get("intended_input") or comp.get("inputs"), "input")
    outputs = io_record(comp.get("intended_output") or comp.get("outputs"), "output")
    invariants = unique_strings(listify(comp.get("invariants")))
    planned_ablation = comp.get("planned_ablation")
    switch_key = f"method.modules.{module_id}.enabled"
    return {
        "module_id": module_id,
        "name": str(comp.get("name") or module_id),
        "contribution_role": contribution_role,
        "status": status,
        "purpose": str(comp.get("role") or comp.get("purpose") or "Implement the approved component role."),
        "mechanism_ref": str(comp.get("mechanism_ref") or ""),
        "inputs": inputs,
        "outputs": outputs,
        "invariants": invariants,
        "implementation_notes": [],
        "code_targets": [f"external_executor/expr/implementation/src/{module_id.lower()}.py"],
        "config_keys": [
            {
                "key": switch_key,
                "type": "boolean",
                "default": True,
                "allowed_values_or_range": [True, False],
                "owner_module": module_id,
                "is_ablation_switch": True,
                "is_claim_sensitive": contribution_role == "core",
                "source": "method_intent",
            }
        ],
        "ablation_switch": {
            "config_key": switch_key,
            "control_action": "disable_or_replace",
            "planned_test": planned_ablation or "",
            "required": contribution_role == "core",
        },
        "diagnostic_hooks": [],
        "tests": [
            {"test_id": f"TEST-{module_id}-interface", "kind": "interface", "assertion": "inputs and outputs satisfy the declared contract"},
            {"test_id": f"TEST-{module_id}-switch", "kind": "ablation_control", "assertion": "ablation switch changes only the intended component"},
        ],
        "failure_modes": [
            {"failure_id": f"FAIL-{module_id}-invalid-interface", "symptom": "declared input/output contract is violated", "handling": "fail fast and preserve logs"}
        ],
        "evidence_links": [],
        "source_refs": comp.get("source_refs") or ["external_executor/method_intent_contract.json"],
    }


def experiment_ids_for(plan: dict[str, Any], *, claim: str = "", mechanism: str = "") -> list[str]:
    output: list[str] = []
    for exp in listify(plan.get("experiments")):
        if not isinstance(exp, dict):
            continue
        claims = [str(x) for x in listify(exp.get("claim_ids") or exp.get("claims"))]
        mech = str(exp.get("mechanism_ref") or get_nested(exp, "intervention.mechanism_ref", default="") or "")
        if claim and claim not in claims and claim != str(exp.get("claim_id") or ""):
            continue
        if mechanism and mechanism.lower() not in mech.lower() and mech.lower() not in mechanism.lower():
            continue
        eid = str(exp.get("experiment_id") or exp.get("id") or "")
        if eid:
            output.append(eid)
    return unique_strings(output)


def previous_version(result: dict[str, Any], previous: dict[str, Any]) -> int:
    if previous:
        value = previous.get("spec_version")
        if isinstance(value, int):
            return value
    records = result.get("method_refinements")
    versions = [x.get("spec_version") for x in listify(records) if isinstance(x, dict) and isinstance(x.get("spec_version"), int)]
    return max(versions, default=0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a versioned implementation specification from method intent and active plan.")
    parser.add_argument("--workspace")
    parser.add_argument("--intent", default="external_executor/method_intent_contract.json")
    parser.add_argument("--previous")
    parser.add_argument("--output", default="external_executor/method_implementation_spec.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    intent = load_json(resolve_in_workspace(ws, args.intent))
    result = load_json(ext / "result_pack.json")
    preflight = load_json(ext / "method_refinement_preflight.json", default={})
    plan = dictify(result.get("experiment_plan"))
    iteration = active_iteration_plan(result)
    previous: dict[str, Any] = {}
    if args.previous:
        previous = load_json(resolve_in_workspace(ws, args.previous))

    iteration_id = str(iteration.get("iteration_id") or iteration.get("id") or "iteration-unknown")
    spec_version = previous_version(result, previous) + 1
    spec_id = stable_id("method-spec", iteration_id, spec_version, intent.get("intent_fingerprint"))

    modules: list[dict[str, Any]] = []
    for comp in listify(intent.get("must_preserve_components")):
        if isinstance(comp, dict):
            modules.append(normalize_module(comp, contribution_role="core", status="retained" if previous else "planned"))
    for comp in listify(intent.get("candidate_components")):
        if isinstance(comp, dict):
            modules.append(normalize_module(comp, contribution_role="supporting", status="retained" if previous else "planned"))

    # Preserve previous module detail when IDs match, while retaining the current intent contract.
    previous_modules = {str(x.get("module_id")): x for x in listify(previous.get("modules")) if isinstance(x, dict)}
    for index, module in enumerate(modules):
        old = previous_modules.get(module["module_id"])
        if old:
            merged = dict(old)
            merged.update({k: v for k, v in module.items() if k in {"name", "contribution_role", "purpose", "mechanism_ref", "source_refs"} and nonempty(v)})
            merged["status"] = "retained"
            modules[index] = merged

    core_mechanism = str(intent.get("core_mechanism") or intent.get("central_mechanism_hypothesis") or "")
    protocol_fp = extract_protocol_fingerprint(result)
    protocol = dictify(plan.get("protocol_snapshot"))
    if "protocol" in protocol and isinstance(protocol["protocol"], dict):
        protocol = protocol["protocol"]

    required_baselines = get_nested(result, "context_alignment.confirmed_execution_scope.required_baselines", default=[])
    if not required_baselines:
        required_baselines = get_nested(result, "resource_readiness.required_baselines", default=[])
    approved_baselines = []
    for item in listify(result.get("baseline_candidates", {}).get("items") if isinstance(result.get("baseline_candidates"), dict) else result.get("baseline_candidates")):
        if isinstance(item, dict):
            approved_baselines.append({
                "baseline_id": item.get("baseline_id") or item.get("candidate_id"),
                "name": item.get("name"),
                "resource_ref": item.get("path") or item.get("artifact_ref"),
                "approval": item.get("approved_for") or "resource_prepared",
            })

    traceability = []
    for mapping in listify(intent.get("mechanism_to_ablation_plan")):
        if not isinstance(mapping, dict):
            continue
        mechanism = str(mapping.get("mechanism") or "")
        claim = str(mapping.get("related_claim") or "")
        module_ids = [m["module_id"] for m in modules if mechanism and (mechanism.lower() in str(m.get("mechanism_ref") or "").lower() or mechanism.lower() in str(m.get("purpose") or "").lower())]
        if not module_ids and modules:
            module_ids = [m["module_id"] for m in modules if m.get("contribution_role") == "core"]
        config_keys = [c["key"] for m in modules if m["module_id"] in module_ids for c in listify(m.get("config_keys")) if isinstance(c, dict) and c.get("key")]
        traceability.append({
            "claim_id": claim,
            "mechanism_ref": mechanism,
            "module_ids": module_ids,
            "config_keys": config_keys,
            "ablation_switch_or_diagnostic": str(mapping.get("planned_test") or ""),
            "experiment_ids": experiment_ids_for(plan, claim=claim, mechanism=mechanism),
            "expected_artifacts": [],
            "interpretation_boundary": {
                "supported": mapping.get("expected_if_supported"),
                "not_supported": mapping.get("expected_if_not_supported"),
            },
        })

    expected_flow = listify(intent.get("expected_algorithm_flow"))
    training_flow = []
    if expected_flow:
        for item in expected_flow:
            if not isinstance(item, dict):
                continue
            related = str(item.get("related_component") or "")
            training_flow.append({
                "step": item.get("step"),
                "name": f"step-{item.get('step')}",
                "description": item.get("description"),
                "module_ids": [related] if related else [],
                "inputs": item.get("inputs") or [],
                "outputs": item.get("outputs") or [],
                "state_updates": [],
                "config_dependencies": [],
                "failure_handling": [],
            })
    else:
        for index, module in enumerate(modules, start=1):
            training_flow.append({
                "step": index,
                "name": module["name"],
                "description": f"Apply {module['name']} according to its declared contract.",
                "module_ids": [module["module_id"]],
                "inputs": [x.get("name") for x in module.get("inputs", [])],
                "outputs": [x.get("name") for x in module.get("outputs", [])],
                "state_updates": [],
                "config_dependencies": [x.get("key") for x in module.get("config_keys", []) if isinstance(x, dict)],
                "failure_handling": [],
            })

    spec_core = {
        "spec_id": spec_id,
        "spec_version": spec_version,
        "iteration_id": iteration_id,
        "input_fingerprint": preflight.get("input_fingerprint"),
        "intent_fingerprint": intent.get("intent_fingerprint"),
        "protocol_fingerprint": protocol_fp,
        "plan_version": extract_plan_version(result),
        "trigger": {
            "reason": iteration.get("trigger") or iteration.get("reason"),
            "approved_changes": iteration.get("approved_changes") or iteration.get("planned_changes") or [],
            "decision_ref": iteration.get("decision_ref") or iteration.get("iteration_decision_id"),
            "evidence_refs": iteration.get("evidence_refs") or [],
        },
        "research_contract": {
            "central_hypothesis": intent.get("central_hypothesis"),
            "contribution_type": intent.get("contribution_type"),
            "core_mechanism": core_mechanism,
            "must_preserve_component_ids": [x.get("component_id") for x in listify(intent.get("must_preserve_components")) if isinstance(x, dict)],
            "claim_boundary": intent.get("claim_boundary") or [],
            "must_not_claim": intent.get("must_not_claim") or [],
            "allowed_refinements": intent.get("allowed_refinements") or [],
            "forbidden_silent_changes": intent.get("forbidden_silent_changes") or [],
        },
        "system_boundary": {
            "inputs": unique_strings([x.get("name") for m in modules for x in m.get("inputs", []) if isinstance(x, dict)]),
            "outputs": unique_strings([x.get("name") for m in modules for x in m.get("outputs", []) if isinstance(x, dict)]),
            "non_goals": listify(intent.get("must_not_claim")),
            "runtime_constraints": get_nested(result, "context_alignment.confirmed_execution_scope.executor_capabilities", default={}),
            "allowed_paths_ref": "external_executor/allowed_paths.txt",
            "deployment_root": "external_executor/expr/",
            "raw_results_root": "external_executor/raw_results/",
        },
        "modules": modules,
        "objectives_and_losses": previous.get("objectives_and_losses", []),
        "training_flow": training_flow,
        "inference_flow": previous.get("inference_flow", training_flow),
        "data_and_protocol_interfaces": {
            "protocol_fingerprint": protocol_fp,
            "dataset": protocol.get("dataset") or get_nested(protocol, "dataset.name", default=""),
            "dataset_version": protocol.get("dataset_version") or get_nested(protocol, "dataset.version", default=""),
            "split": protocol.get("split") or get_nested(protocol, "dataset.split", default=""),
            "preprocessing": protocol.get("preprocessing") or get_nested(protocol, "dataset.preprocessing", default=""),
            "primary_metric": protocol.get("primary_metric") or get_nested(protocol, "metrics.primary.name", default=""),
            "metric_direction": protocol.get("metric_direction") or get_nested(protocol, "metrics.primary.direction", default=""),
            "aggregation": protocol.get("aggregation") or get_nested(protocol, "metrics.primary.aggregation", default=""),
            "seed_policy": protocol.get("seed_policy") or get_nested(protocol, "randomness", default={}),
            "evaluation_entry_point": protocol.get("evaluation_script") or get_nested(protocol, "evaluation.entry_point", default=""),
        },
        "baseline_and_fairness_constraints": {
            "required_baselines": required_baselines,
            "approved_baselines": approved_baselines,
            "same_data_split": True,
            "same_metric_implementation": True,
            "tuning_fairness": protocol.get("hyperparameter_fairness_rule") or get_nested(protocol, "tuning.fairness_rule", default="same opportunity or published fixed config"),
            "compute_fairness": protocol.get("compute_fairness") or get_nested(protocol, "budget.compute_fairness", default="record and compare"),
            "non_contribution_tricks_must_be_controlled": True,
        },
        "configuration_contract": {
            "config_root": "method",
            "keys": [c for m in modules for c in m.get("config_keys", []) if isinstance(c, dict)],
            "unknown_key_policy": "error",
            "snapshot_required": True,
        },
        "logging_and_provenance_contract": {
            "required": ["resolved config", "seed/repeat", "protocol fingerprint", "code/patch reference", "raw log", "metric output", "checkpoint metadata"],
            "structured_run_record_required": True,
            "silent_fallback_forbidden": True,
            "raw_results_root": "external_executor/raw_results/",
        },
        "ablation_and_diagnostic_controls": traceability,
        "acceptance_checks": [
            {"check_id": "AC-intent", "assertion": "research contract matches the normalized intent"},
            {"check_id": "AC-interfaces", "assertion": "core module interfaces and invariants are testable"},
            {"check_id": "AC-ablation", "assertion": "claim-relevant mechanisms expose controlled tests"},
            {"check_id": "AC-protocol", "assertion": "dataset, metric, split, seed, and evaluation bind to the active protocol"},
            {"check_id": "AC-logging", "assertion": "formal provenance can be emitted without manual reconstruction"},
        ],
        "non_contribution_engineering": previous.get("non_contribution_engineering", []),
        "unresolved_design_decisions": listify(intent.get("unknowns")),
        "scope_boundary": {
            "task": protocol.get("task") or get_nested(protocol, "benchmark.task", default=""),
            "benchmark": protocol.get("benchmark") or get_nested(protocol, "benchmark.name", default=""),
            "central_hypothesis": intent.get("central_hypothesis"),
            "contribution_type": intent.get("contribution_type"),
            "core_mechanism": core_mechanism,
            "claim_boundary": intent.get("claim_boundary") or [],
        },
        "evidence_traceability": traceability,
        "source_refs": [
            "external_executor/method_intent_contract.json",
            "external_executor/result_pack.json#experiment_plan",
            "external_executor/result_pack.json#current_iteration_plan|iteration_plans",
        ],
    }
    if previous:
        spec_core["source_refs"].append(args.previous)

    change_log = list(previous.get("change_log", [])) if previous else []
    change_log.append({
        "change_id": stable_id("change", spec_id, spec_version),
        "spec_version": spec_version,
        "iteration_id": iteration_id,
        "trigger": spec_core["trigger"],
        "summary": "Initial implementation specification" if not previous else "Refined implementation specification",
        "declared_classification": "contract_preserving_refinement",
        "authorization_refs": listify(spec_core["trigger"].get("decision_ref")) + listify(spec_core["trigger"].get("evidence_refs")),
    })

    spec = {
        "schema_version": "method_implementation_spec.v1",
        "generated_at": utc_now(),
        "status": "needs_review",
        **spec_core,
        "change_log": change_log,
        "spec_content_fingerprint": canonical_json_hash(spec_core),
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
