#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import (
    dictify,
    dump_json_atomic,
    extract_protocol_fingerprint,
    listify,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the method implementation specification contract.")
    parser.add_argument("--workspace")
    parser.add_argument("--spec", default="external_executor/method_implementation_spec.json")
    parser.add_argument("--output", default="external_executor/method_implementation_spec_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    spec = load_json(resolve_in_workspace(ws, args.spec))
    intent = load_json(ext / "method_intent_contract.json")
    result = load_json(ext / "result_pack.json")

    errors: list[str] = []
    warnings: list[str] = []

    if spec.get("schema_version") != "method_implementation_spec.v1":
        errors.append("invalid_spec_schema")
    for field in (
        "spec_id", "spec_version", "iteration_id", "intent_fingerprint", "protocol_fingerprint",
        "plan_version", "research_contract", "modules", "training_flow", "inference_flow",
        "data_and_protocol_interfaces", "baseline_and_fairness_constraints", "configuration_contract",
        "logging_and_provenance_contract", "acceptance_checks", "scope_boundary", "change_log",
    ):
        if not nonempty(spec.get(field)):
            errors.append(f"missing_required_field:{field}")

    if spec.get("intent_fingerprint") != intent.get("intent_fingerprint"):
        errors.append("intent_fingerprint_mismatch")
    active_fp = extract_protocol_fingerprint(result)
    if not active_fp or spec.get("protocol_fingerprint") != active_fp:
        errors.append("protocol_fingerprint_mismatch")

    research = dictify(spec.get("research_contract"))
    for field in ("central_hypothesis", "contribution_type", "core_mechanism"):
        if not nonempty(research.get(field)):
            errors.append(f"missing_research_contract_field:{field}")

    modules = [x for x in listify(spec.get("modules")) if isinstance(x, dict)]
    ids = [str(x.get("module_id") or "") for x in modules]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_module_id")
    if any(not x for x in ids):
        errors.append("module_missing_id")

    must_ids = {str(x.get("component_id")) for x in listify(intent.get("must_preserve_components")) if isinstance(x, dict)}
    missing = sorted(must_ids - set(ids))
    errors.extend(f"missing_must_preserve_module:{mid}" for mid in missing)

    config_keys: list[str] = []
    core_ids: set[str] = set()
    for module in modules:
        mid = str(module.get("module_id") or "unknown")
        role = str(module.get("contribution_role") or "")
        if role not in {"core", "supporting", "engineering", "diagnostic"}:
            warnings.append(f"unknown_contribution_role:{mid}:{role}")
        if role == "core":
            core_ids.add(mid)
        for field in ("name", "purpose", "inputs", "outputs", "invariants", "code_targets", "config_keys", "tests", "failure_modes"):
            if not nonempty(module.get(field)):
                if field == "failure_modes":
                    warnings.append(f"module_missing_failure_modes:{mid}")
                else:
                    errors.append(f"module_missing_field:{mid}:{field}")
        if role in {"core", "supporting"}:
            ablation = dictify(module.get("ablation_switch"))
            if not nonempty(ablation.get("config_key")) and not nonempty(module.get("diagnostic_hooks")):
                errors.append(f"module_missing_control:{mid}")
        for item in listify(module.get("config_keys")):
            if not isinstance(item, dict) or not nonempty(item.get("key")):
                errors.append(f"invalid_config_key_record:{mid}")
                continue
            config_keys.append(str(item.get("key")))
            if not nonempty(item.get("type")) or "default" not in item:
                errors.append(f"incomplete_config_key_record:{mid}:{item.get('key')}")
    if len(config_keys) != len(set(config_keys)):
        errors.append("duplicate_config_key")

    for flow_name in ("training_flow", "inference_flow"):
        flow = [x for x in listify(spec.get(flow_name)) if isinstance(x, dict)]
        steps = [x.get("step") for x in flow]
        if any(not isinstance(x, int) for x in steps):
            errors.append(f"invalid_flow_step:{flow_name}")
        if steps != sorted(steps):
            errors.append(f"unordered_flow:{flow_name}")
        for item in flow:
            unknown_modules = set(str(x) for x in listify(item.get("module_ids"))) - set(ids)
            for mid in sorted(unknown_modules):
                errors.append(f"flow_unknown_module:{flow_name}:{mid}")

    trace = [x for x in listify(spec.get("evidence_traceability") or spec.get("ablation_and_diagnostic_controls")) if isinstance(x, dict)]
    if core_ids and not trace:
        errors.append("missing_evidence_traceability")
    traced_modules = {str(mid) for row in trace for mid in listify(row.get("module_ids"))}
    for mid in sorted(core_ids - traced_modules):
        warnings.append(f"core_module_not_in_traceability:{mid}")
    for row in trace:
        if not nonempty(row.get("mechanism_ref")):
            errors.append("traceability_missing_mechanism")
        if not nonempty(row.get("module_ids")):
            errors.append(f"traceability_missing_modules:{row.get('mechanism_ref')}")
        if not nonempty(row.get("ablation_switch_or_diagnostic")):
            errors.append(f"traceability_missing_control:{row.get('mechanism_ref')}")
        if not nonempty(row.get("experiment_ids")):
            warnings.append(f"traceability_missing_experiment_binding:{row.get('mechanism_ref')}")

    protocol_iface = dictify(spec.get("data_and_protocol_interfaces"))
    for field in ("protocol_fingerprint", "dataset", "split", "primary_metric", "metric_direction"):
        if not nonempty(protocol_iface.get(field)):
            errors.append(f"protocol_interface_missing:{field}")

    fairness = dictify(spec.get("baseline_and_fairness_constraints"))
    for field in ("same_data_split", "same_metric_implementation", "non_contribution_tricks_must_be_controlled"):
        if fairness.get(field) is not True:
            errors.append(f"fairness_constraint_not_enforced:{field}")

    unresolved = [x for x in listify(spec.get("unresolved_design_decisions")) if isinstance(x, dict)]
    for item in unresolved:
        cls = item.get("class")
        if cls == "scope_sensitive":
            errors.append(f"unresolved_scope_sensitive_decision:{item.get('field')}")
        elif cls == "evidence_sensitive":
            warnings.append(f"unresolved_evidence_sensitive_decision:{item.get('field')}")

    status = "blocked" if errors else ("needs_fix" if warnings else "pass")
    validation = {
        "schema_version": "method_implementation_spec_validation.v1",
        "generated_at": utc_now(),
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "module_count": len(modules),
            "core_module_count": len(core_ids),
            "config_key_count": len(config_keys),
            "traceability_row_count": len(trace),
            "unresolved_decision_count": len(unresolved),
        },
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), validation)
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
