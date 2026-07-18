#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import (
    canonical_json_hash,
    dictify,
    dump_json_atomic,
    listify,
    load_json,
    nonempty,
    normalized_text,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    utc_now,
)

SEVERITY_ORDER = {
    "implementation_detail": 0,
    "contract_preserving_refinement": 1,
    "claim_affecting_minor": 2,
    "scope_change_major": 3,
}


def add_change(changes: list[dict[str, Any]], *, path: str, operation: str, before: Any, after: Any,
               classification: str, rationale: str, modules: list[str] | None = None,
               claims: list[str] | None = None, plan_update: bool = False, human: bool = False) -> None:
    changes.append({
        "change_id": stable_id("delta", path, operation, before, after),
        "path": path,
        "operation": operation,
        "before": before,
        "after": after,
        "classification": classification,
        "rationale": rationale,
        "authorization_refs": [],
        "affected_modules": modules or [],
        "affected_claims": claims or [],
        "affected_experiments": [],
        "fairness_effect": "review_required" if "fairness" in path or "protocol" in path else "none",
        "novelty_effect": "material" if classification == "scope_change_major" else ("possible" if classification == "claim_affecting_minor" else "none"),
        "requires_plan_update": plan_update,
        "requires_human_review": human,
    })


def text_changed(a: Any, b: Any) -> bool:
    return normalized_text(a) != normalized_text(b)


def module_map(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(x.get("module_id")): x for x in listify(spec.get("modules")) if isinstance(x, dict) and x.get("module_id")}


def list_set(value: Any) -> set[str]:
    return {normalized_text(x) for x in listify(value) if nonempty(x)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare intent/current or previous/current method specifications.")
    parser.add_argument("--workspace")
    parser.add_argument("--intent", default="external_executor/report/method_intent_contract.json")
    parser.add_argument("--previous")
    parser.add_argument("--current", default="external_executor/method_implementation_spec.json")
    parser.add_argument("--output", default="external_executor/report/method_delta.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    intent = load_json(resolve_in_workspace(ws, args.intent))
    current = load_json(resolve_in_workspace(ws, args.current))
    previous = load_json(resolve_in_workspace(ws, args.previous)) if args.previous else {}
    changes: list[dict[str, Any]] = []

    intent_contract = {
        "central_hypothesis": intent.get("central_hypothesis"),
        "contribution_type": intent.get("contribution_type"),
        "core_mechanism": intent.get("core_mechanism") or intent.get("central_mechanism_hypothesis"),
        "claim_boundary": intent.get("claim_boundary") or [],
    }
    current_contract = dictify(current.get("research_contract"))
    current_scope = dictify(current.get("scope_boundary"))

    for field in ("central_hypothesis", "contribution_type", "core_mechanism"):
        before = intent_contract.get(field)
        after = current_contract.get(field) or current_scope.get(field)
        if text_changed(before, after):
            add_change(
                changes, path=f"research_contract/{field}", operation="replace", before=before, after=after,
                classification="scope_change_major", rationale=f"{field} differs from normalized method intent",
                human=True, plan_update=True,
            )

    intent_boundary = list_set(intent_contract.get("claim_boundary"))
    current_boundary = list_set(current_contract.get("claim_boundary") or current_scope.get("claim_boundary"))
    added_boundary = current_boundary - intent_boundary
    removed_boundary = intent_boundary - current_boundary
    if added_boundary:
        add_change(changes, path="research_contract/claim_boundary", operation="add", before=sorted(intent_boundary), after=sorted(current_boundary),
                   classification="scope_change_major", rationale="claim boundary was broadened or replaced", human=True, plan_update=True)
    elif removed_boundary:
        add_change(changes, path="research_contract/claim_boundary", operation="remove", before=sorted(intent_boundary), after=sorted(current_boundary),
                   classification="claim_affecting_minor", rationale="claim boundary was narrowed", plan_update=True)

    must_ids = {str(x.get("component_id")) for x in listify(intent.get("must_preserve_components")) if isinstance(x, dict)}
    candidate_ids = {str(x.get("component_id")) for x in listify(intent.get("candidate_components")) if isinstance(x, dict)}
    curr_modules = module_map(current)
    missing_must = sorted(x for x in must_ids if x and x not in curr_modules)
    for mid in missing_must:
        add_change(changes, path=f"modules/{mid}", operation="remove", before="must_preserve_component", after=None,
                   classification="scope_change_major", rationale="must-preserve component is absent", modules=[mid], human=True, plan_update=True)
    for mid, module in curr_modules.items():
        if mid in must_ids or mid in candidate_ids:
            continue
        role = str(module.get("contribution_role") or "supporting")
        classification = "scope_change_major" if role == "core" else "contract_preserving_refinement"
        add_change(changes, path=f"modules/{mid}", operation="add", before=None, after=module.get("name"),
                   classification=classification,
                   rationale="module is not present in the normalized intent",
                   modules=[mid], human=classification == "scope_change_major", plan_update=classification != "contract_preserving_refinement")

    if previous:
        previous_contract = dictify(previous.get("research_contract"))
        previous_scope = dictify(previous.get("scope_boundary"))
        for field in ("central_hypothesis", "contribution_type", "core_mechanism"):
            before = previous_contract.get(field) or previous_scope.get(field)
            after = current_contract.get(field) or current_scope.get(field)
            if text_changed(before, after):
                add_change(changes, path=f"research_contract/{field}", operation="replace", before=before, after=after,
                           classification="scope_change_major", rationale=f"{field} changed between specification versions",
                           human=True, plan_update=True)
        for field in ("task", "benchmark"):
            before = previous_scope.get(field)
            after = current_scope.get(field)
            if text_changed(before, after):
                add_change(changes, path=f"scope_boundary/{field}", operation="replace", before=before, after=after,
                           classification="scope_change_major", rationale=f"{field} changed between specification versions",
                           human=True, plan_update=True)
        if previous.get("protocol_fingerprint") != current.get("protocol_fingerprint"):
            add_change(changes, path="protocol_fingerprint", operation="replace", before=previous.get("protocol_fingerprint"), after=current.get("protocol_fingerprint"),
                       classification="claim_affecting_minor", rationale="active experiment protocol changed", plan_update=True)

        prev_modules = module_map(previous)
        for mid in sorted(set(prev_modules) - set(curr_modules)):
            role = str(prev_modules[mid].get("contribution_role") or "supporting")
            classification = "scope_change_major" if role == "core" or mid in must_ids else "claim_affecting_minor"
            add_change(changes, path=f"modules/{mid}", operation="remove", before=prev_modules[mid].get("name"), after=None,
                       classification=classification, rationale="module removed from prior specification", modules=[mid],
                       human=classification == "scope_change_major", plan_update=True)
        for mid in sorted(set(curr_modules) - set(prev_modules)):
            role = str(curr_modules[mid].get("contribution_role") or "supporting")
            authorized = mid in candidate_ids
            classification = "contract_preserving_refinement" if authorized and role != "core" else ("scope_change_major" if role == "core" else "claim_affecting_minor")
            add_change(changes, path=f"modules/{mid}", operation="add", before=None, after=curr_modules[mid].get("name"),
                       classification=classification, rationale="module added relative to prior specification", modules=[mid],
                       human=classification == "scope_change_major", plan_update=classification in {"claim_affecting_minor", "scope_change_major"})
        for mid in sorted(set(curr_modules) & set(prev_modules)):
            before = prev_modules[mid]
            after = curr_modules[mid]
            for field in ("name", "purpose", "mechanism_ref", "contribution_role", "inputs", "outputs", "invariants", "config_keys", "ablation_switch"):
                if canonical_json_hash(before.get(field)) == canonical_json_hash(after.get(field)):
                    continue
                if field in {"mechanism_ref", "contribution_role"} and str(before.get("contribution_role")) == "core":
                    classification = "scope_change_major"
                elif field in {"inputs", "outputs", "invariants", "ablation_switch"}:
                    classification = "contract_preserving_refinement"
                else:
                    classification = "implementation_detail"
                add_change(changes, path=f"modules/{mid}/{field}", operation="modify", before=before.get(field), after=after.get(field),
                           classification=classification, rationale=f"module {field} changed", modules=[mid],
                           human=classification == "scope_change_major", plan_update=classification == "scope_change_major")

        for field in ("objectives_and_losses", "training_flow", "inference_flow", "configuration_contract", "non_contribution_engineering"):
            if canonical_json_hash(previous.get(field)) != canonical_json_hash(current.get(field)):
                classification = "contract_preserving_refinement" if field != "non_contribution_engineering" else "implementation_detail"
                add_change(changes, path=field, operation="modify", before=previous.get(field), after=current.get(field),
                           classification=classification, rationale=f"{field} changed between specification versions")

    # De-duplicate identical change IDs caused by intent and previous comparisons.
    unique: dict[str, dict[str, Any]] = {}
    for change in changes:
        unique[change["change_id"]] = change
    changes = list(unique.values())
    max_class = max((c["classification"] for c in changes), key=lambda x: SEVERITY_ORDER[x], default="implementation_detail")
    delta_level = "major" if max_class == "scope_change_major" else ("minor" if changes else "none")
    delta = {
        "schema_version": "method_delta.v1",
        "generated_at": utc_now(),
        "comparison_mode": "spec_to_spec" if previous else "intent_to_spec",
        "intent_fingerprint": intent.get("intent_fingerprint"),
        "previous_spec_id": previous.get("spec_id") if previous else None,
        "previous_spec_version": previous.get("spec_version") if previous else None,
        "previous_spec_fingerprint": previous.get("spec_fingerprint") if previous else None,
        "current_spec_id": current.get("spec_id"),
        "current_spec_version": current.get("spec_version"),
        "current_spec_fingerprint": current.get("spec_fingerprint"),
        "delta_level": delta_level,
        "maximum_classification": max_class if changes else "implementation_detail",
        "changes": changes,
        "summary": {
            "total": len(changes),
            "implementation_detail": sum(c["classification"] == "implementation_detail" for c in changes),
            "contract_preserving_refinement": sum(c["classification"] == "contract_preserving_refinement" for c in changes),
            "claim_affecting_minor": sum(c["classification"] == "claim_affecting_minor" for c in changes),
            "scope_change_major": sum(c["classification"] == "scope_change_major" for c in changes),
        },
        "requires_plan_update": any(c.get("requires_plan_update") for c in changes),
        "requires_human_review": any(c.get("requires_human_review") for c in changes),
        "affected_modules": sorted({m for c in changes for m in c.get("affected_modules", [])}),
        "affected_claims": sorted({m for c in changes for m in c.get("affected_claims", [])}),
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), delta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
