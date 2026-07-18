#!/usr/bin/env python3
"""Resolve the one final method lineage used by evidence packaging."""
from __future__ import annotations

import json
from typing import Any


def section_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "records", "implementations", "reports"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
        return [value] if value else []
    return []


def _select_current(
    value: Any,
    *,
    iteration_id: str | None,
    id_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    items = section_items(value)
    if not items:
        return None
    if isinstance(value, dict) and iteration_id:
        current = value.get("current_by_iteration")
        current_id = current.get(iteration_id) if isinstance(current, dict) else None
        if current_id:
            for item in items:
                if any(item.get(key) == current_id for key in id_keys):
                    return item
    if iteration_id:
        matched = [item for item in items if item.get("iteration_id") == iteration_id]
        if matched:
            return matched[-1]
    return items[-1]


def _contains_implementation(record: dict[str, Any], implementation_id: str | None) -> bool:
    if not implementation_id:
        return False
    if record.get("implementation_id") == implementation_id:
        return True
    return implementation_id in json.dumps(record, ensure_ascii=False, sort_keys=True)


def resolve_final_sources(result: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    implementations = result.get("implementations")
    implementation_items = section_items(implementations)
    active_id = implementations.get("active_implementation_id") if isinstance(implementations, dict) else None
    active_implementation = None
    if active_id:
        active_implementation = next(
            (item for item in implementation_items if item.get("implementation_id") == active_id),
            None,
        )
        if active_implementation is None:
            errors.append("active_implementation_id_not_found")
    elif len(implementation_items) == 1:
        active_implementation = implementation_items[0]
        active_id = active_implementation.get("implementation_id")
        warnings.append("active_implementation_id_inferred_from_single_item")
    elif implementation_items:
        errors.append("active_implementation_id_missing_or_ambiguous")
    else:
        errors.append("implementation_records_missing")

    iteration_id = active_implementation.get("iteration_id") if active_implementation else None
    if not iteration_id:
        decision = _select_current(
            result.get("iteration_decisions"), iteration_id=None, id_keys=("decision_id",)
        )
        iteration_id = decision.get("iteration_id") if decision else None
        if iteration_id:
            warnings.append("final_iteration_inferred_from_iteration_decision")
    if not iteration_id:
        errors.append("final_iteration_id_missing")

    method_fingerprint = active_implementation.get("method_spec_fingerprint") if active_implementation else None
    refinements = section_items(result.get("method_refinements"))
    matching_refinements = [
        item for item in refinements
        if method_fingerprint and item.get("spec_fingerprint") == method_fingerprint
    ]
    if matching_refinements:
        method_refinement = matching_refinements[-1]
    else:
        ready = [item for item in refinements if item.get("status") in {"ready", "complete", "completed"}]
        method_refinement = ready[-1] if ready else (refinements[-1] if refinements else None)
        if method_refinement and method_fingerprint:
            errors.append("active_implementation_method_spec_not_found")
    if method_refinement is None:
        errors.append("method_refinement_record_missing")

    reviews = section_items(result.get("implementation_reviews"))
    matching_reviews = [item for item in reviews if _contains_implementation(item, active_id)]
    if not matching_reviews and iteration_id:
        matching_reviews = [item for item in reviews if item.get("iteration_id") == iteration_id]
    review = matching_reviews[-1] if matching_reviews else None
    if review is None:
        warnings.append("final_implementation_review_missing")

    decision = _select_current(
        result.get("iteration_decisions"), iteration_id=iteration_id, id_keys=("decision_id",)
    )
    diagnosis = _select_current(
        result.get("result_diagnoses") or result.get("result_diagnosis"),
        iteration_id=iteration_id,
        id_keys=("diagnosis_id",),
    )
    attribution = _select_current(
        result.get("module_attributions") or result.get("module_attribution"),
        iteration_id=iteration_id,
        id_keys=("attribution_id",),
    )

    runs = section_items(result.get("experiment_runs") or result.get("runs"))
    implementation_runs = [item for item in runs if _contains_implementation(item, active_id)]
    if runs and not implementation_runs:
        warnings.append("no_experiment_run_bound_to_active_implementation")

    return {
        "final_iteration_id": iteration_id,
        "active_implementation_id": active_id,
        "active_implementation": active_implementation,
        "method_refinement": method_refinement,
        "implementation_review": review,
        "iteration_decision": decision,
        "result_diagnosis": diagnosis,
        "module_attribution": attribution,
        "active_implementation_runs": implementation_runs,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }
