#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import (
    canonical_json_hash,
    component_id,
    dictify,
    dump_json_atomic,
    first_nonempty,
    flatten_source_refs,
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


def normalize_component(raw: Any, *, required_default: bool, prefix: str) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"name": raw}
    raw = dictify(raw)
    name = str(raw.get("name") or raw.get("module_name") or raw.get("component") or raw.get("id") or "Unnamed component")
    cid = str(raw.get("component_id") or raw.get("module_id") or stable_id(prefix, name))
    invariants = unique_strings(listify(raw.get("invariants") or raw.get("must_preserve") or raw.get("constraints")))
    return {
        "component_id": cid,
        "name": name,
        "role": str(raw.get("role") or raw.get("intended_role") or raw.get("purpose") or ""),
        "mechanism_ref": str(raw.get("mechanism_ref") or raw.get("mechanism") or raw.get("related_claim") or ""),
        "intended_input": raw.get("expected_input") or raw.get("input") or raw.get("inputs") or "",
        "intended_output": raw.get("expected_output") or raw.get("output") or raw.get("outputs") or "",
        "invariants": invariants,
        "required": bool(raw.get("required", required_default)),
        "planned_ablation": raw.get("planned_ablation") or raw.get("ablation") or "",
        "source_refs": flatten_source_refs(raw.get("source_refs"), raw.get("source_ref")),
    }


def normalize_flow(raw: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, item in enumerate(listify(raw), start=1):
        if isinstance(item, str):
            item = {"description": item}
        item = dictify(item)
        output.append({
            "step": item.get("step") if isinstance(item.get("step"), int) else index,
            "description": str(item.get("description") or item.get("name") or ""),
            "related_component": str(item.get("related_module") or item.get("component_id") or item.get("module_id") or ""),
            "inputs": listify(item.get("inputs")),
            "outputs": listify(item.get("outputs")),
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize method_intent and confirmed scope into a stable contract.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/report/method_intent_contract.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    handoff = load_json(ext / "handoff_pack.json")
    result = load_json(ext / "result_pack.json")
    preflight = load_json(ext / "report" / "method_refinement_preflight.json", default={})

    intent = dictify(handoff.get("method_intent"))
    scope = dictify(get_nested(result, "context_alignment.confirmed_execution_scope", default={}))
    reboost = dictify(handoff.get("context_reboost"))
    mechanism_block = dictify(reboost.get("method_mechanism"))

    central_hypothesis = first_nonempty(
        scope.get("central_hypothesis"),
        reboost.get("central_hypothesis"),
        intent.get("central_hypothesis"),
        intent.get("central_mechanism_hypothesis"),
        default="",
    )
    contribution_type = first_nonempty(
        scope.get("contribution_type"),
        reboost.get("contribution_type"),
        intent.get("contribution_type"),
        default="",
    )
    core_mechanism = first_nonempty(
        intent.get("central_mechanism_hypothesis"),
        intent.get("actual_core_mechanism"),
        mechanism_block.get("core_mechanism"),
        scope.get("core_mechanism"),
        default="",
    )

    must_raw = first_nonempty(
        intent.get("must_preserve_components"),
        mechanism_block.get("must_preserve_components"),
        scope.get("must_preserve_components"),
        default=[],
    )
    candidate_raw = first_nonempty(
        intent.get("candidate_modules"),
        intent.get("candidate_components"),
        mechanism_block.get("candidate_components"),
        default=[],
    )
    must_components = [normalize_component(x, required_default=True, prefix="M") for x in listify(must_raw)]
    candidate_components = [normalize_component(x, required_default=False, prefix="C") for x in listify(candidate_raw)]

    # If no explicit must-preserve list exists, promote candidate modules marked required.
    if not must_components:
        promoted = [x for x in candidate_components if x.get("required")]
        if promoted:
            must_components = promoted
            candidate_components = [x for x in candidate_components if x not in promoted]

    expected_flow = normalize_flow(first_nonempty(intent.get("expected_algorithm_flow"), scope.get("expected_algorithm_flow"), default=[]))
    ablations = []
    for index, item in enumerate(listify(intent.get("mechanism_to_ablation_plan")), start=1):
        if isinstance(item, str):
            item = {"mechanism": item}
        item = dictify(item)
        ablations.append({
            "mapping_id": str(item.get("mapping_id") or stable_id("MAP", item.get("mechanism") or index)),
            "mechanism": str(item.get("mechanism") or item.get("mechanism_ref") or ""),
            "planned_test": str(item.get("planned_test") or item.get("test") or ""),
            "expected_if_supported": str(item.get("expected_observation_if_supported") or item.get("expected_if_supported") or ""),
            "expected_if_not_supported": str(item.get("expected_observation_if_not_supported") or item.get("expected_if_not_supported") or ""),
            "related_claim": str(item.get("related_claim") or item.get("claim_id") or ""),
            "source_refs": flatten_source_refs(item.get("source_refs")),
        })

    allowed_refinements = unique_strings(
        listify(intent.get("allowed_refinements")) + listify(mechanism_block.get("allowed_refinements"))
    )
    forbidden = unique_strings(
        listify(intent.get("forbidden_silent_changes"))
        + listify(mechanism_block.get("forbidden_scope_changes"))
        + [
            "replace_core_mechanism",
            "change_task_or_benchmark",
            "change_contribution_type_without_review",
            "drop_required_baseline",
            "broaden_claim_boundary_without_review",
        ]
    )
    claim_boundary = listify(first_nonempty(
        scope.get("claim_boundaries"),
        scope.get("claim_boundary"),
        reboost.get("claim_boundaries"),
        default=[],
    ))
    must_not_claim = listify(first_nonempty(
        scope.get("must_not_claim"),
        reboost.get("must_not_claim"),
        reboost.get("claim_boundaries"),
        default=[],
    ))

    unknowns: list[dict[str, Any]] = []
    if not nonempty(contribution_type):
        unknowns.append({"field": "contribution_type", "class": "scope_sensitive", "reason": "not explicit"})
    if not must_components:
        unknowns.append({"field": "must_preserve_components", "class": "scope_sensitive", "reason": "not explicit"})
    if not expected_flow:
        unknowns.append({"field": "expected_algorithm_flow", "class": "implementation_detail", "reason": "not explicit"})
    for comp in must_components + candidate_components:
        if not nonempty(comp.get("intended_input")) or not nonempty(comp.get("intended_output")):
            unknowns.append({
                "field": f"component:{comp['component_id']}:interface",
                "class": "implementation_detail",
                "reason": "input or output not explicit",
            })

    contract_core = {
        "central_hypothesis": central_hypothesis,
        "contribution_type": contribution_type,
        "central_mechanism_hypothesis": str(intent.get("central_mechanism_hypothesis") or core_mechanism),
        "core_mechanism": core_mechanism,
        "must_preserve_components": must_components,
        "candidate_components": candidate_components,
        "expected_algorithm_flow": expected_flow,
        "allowed_refinements": allowed_refinements,
        "forbidden_silent_changes": forbidden,
        "mechanism_to_ablation_plan": ablations,
        "claim_boundary": claim_boundary,
        "must_not_claim": must_not_claim,
        "unknowns": unknowns,
    }
    blocking_unknowns = [x for x in unknowns if x.get("class") == "scope_sensitive"]
    contract = {
        "schema_version": "method_intent_contract.v1",
        "generated_at": utc_now(),
        "status": "blocked" if blocking_unknowns else ("partial" if unknowns else "complete"),
        "source_status": intent.get("status"),
        "not_final_method_source": intent.get("not_final_method_source") is True,
        "input_fingerprint": preflight.get("input_fingerprint"),
        **contract_core,
        "source_refs": [
            "external_executor/handoff_pack.json#method_intent",
            "external_executor/handoff_pack.json#context_reboost",
            "external_executor/result_pack.json#context_alignment.confirmed_execution_scope",
        ],
        "intent_fingerprint": canonical_json_hash(contract_core),
        "blocking_issues": [f"unresolved_scope_sensitive_unknown:{x['field']}" for x in blocking_unknowns],
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), contract)
    return 0 if not blocking_unknowns else 1


if __name__ == "__main__":
    raise SystemExit(main())
