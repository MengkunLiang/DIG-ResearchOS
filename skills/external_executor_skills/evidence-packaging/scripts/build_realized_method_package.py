#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    extract_refs,
    get_nested,
    listify,
    load_json,
    nonempty,
    record_id,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
    walk_dicts,
)

IMPLEMENTED = {"implemented", "active", "kept", "present", "complete", "completed", "pass"}
DROPPED = {"dropped", "removed", "disabled", "rejected"}
SUPPORT_STRONG = {"direct_ablation", "controlled_diagnostic"}
SUPPORT_WEAK = {"correlational_hint", "implementation_fact"}


def section(result: dict[str, Any], *names: str) -> Any:
    for name in names:
        if nonempty(result.get(name)):
            return result[name]
    return None


def item_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("items", "modules", "records", "implementations", "specifications", "steps", "losses"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
        return [value]
    return [item for item in listify(value) if isinstance(item, dict)]


def normalize_module(item: dict[str, Any], origin: str) -> dict[str, Any]:
    name = str(item.get("name") or item.get("module_name") or item.get("component") or item.get("module_id") or "unnamed-module")
    module_id = str(item.get("module_id") or item.get("component_id") or stable_id("MOD", name))
    raw_status = str(item.get("status") or item.get("implementation_status") or "unverified").lower()
    if raw_status in IMPLEMENTED:
        status = "implemented"
    elif raw_status in DROPPED:
        status = "dropped"
    elif item.get("added") is True:
        status = "added"
    elif item.get("modified") is True:
        status = "modified"
    else:
        status = raw_status if raw_status in {"implemented", "dropped", "added", "modified", "unverified"} else "unverified"
    return {
        "module_id": module_id,
        "name": name,
        "status": status,
        "actual_role": item.get("actual_role") or item.get("role") or item.get("intended_role"),
        "inputs": listify(item.get("inputs") or item.get("expected_input") or item.get("input")),
        "outputs": listify(item.get("outputs") or item.get("expected_output") or item.get("output")),
        "code_refs": unique_strings(listify(item.get("code_refs")) + listify(item.get("code_ref")) + listify(item.get("path"))),
        "config_keys": unique_strings(listify(item.get("config_keys")) + listify(item.get("config_key"))),
        "implementation_evidence_refs": unique_strings(listify(item.get("evidence_refs")) + extract_refs(item)),
        "empirical_support": {
            "status": "unassessed",
            "evidence_types": [],
            "evidence_refs": [],
            "confidence": None,
            "limitations": [],
        },
        "definition_status": "defined_in_implementation" if status in {"implemented", "added", "modified"} else status,
        "source_origins": [origin],
        "notes": listify(item.get("notes")),
    }


def merge_modules(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for module in modules:
        mid = module["module_id"]
        if mid not in merged:
            merged[mid] = deepcopy(module)
            continue
        target = merged[mid]
        for key in ("inputs", "outputs", "code_refs", "config_keys", "implementation_evidence_refs", "source_origins", "notes"):
            target[key] = unique_strings(target.get(key, []) + module.get(key, []))
        for key in ("actual_role",):
            if not nonempty(target.get(key)) and nonempty(module.get(key)):
                target[key] = module[key]
        if target["status"] == "unverified" and module["status"] != "unverified":
            target["status"] = module["status"]
            target["definition_status"] = module["definition_status"]
    return list(merged.values())


def attribution_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    value = section(result, "module_attributions", "module_attribution", "attribution_records")
    records = item_list(value)
    if len(records) == 1 and isinstance(value, dict):
        for key in ("items", "modules", "attributions"):
            if isinstance(value.get(key), list):
                records = [item for item in value[key] if isinstance(item, dict)]
                break
    return records


def apply_attribution(modules: list[dict[str, Any]], records: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    by_id = {m["module_id"]: m for m in modules}
    by_name = {m["name"].lower(): m for m in modules}
    supported: list[dict] = []
    unsupported: list[dict] = []
    unresolved: list[dict] = []
    for index, record in enumerate(records):
        target_id = record.get("module_id") or record.get("component_id")
        target_name = str(record.get("module_name") or record.get("name") or record.get("mechanism") or "").lower()
        module = by_id.get(str(target_id)) if target_id else None
        if module is None and target_name:
            module = by_name.get(target_name)
        evidence_type = str(record.get("evidence_type") or record.get("attribution_type") or "unsupported")
        conclusion = record.get("conclusion") or record.get("finding") or record.get("attribution")
        refs = unique_strings(listify(record.get("evidence_refs")) + extract_refs(record))
        confidence = record.get("confidence")
        status = str(record.get("status") or record.get("verdict") or "unknown").lower()
        entry = {
            "attribution_id": record.get("attribution_id") or record_id(record, f"attribution[{index}]"),
            "module_id": module.get("module_id") if module else target_id,
            "mechanism": record.get("mechanism") or (module.get("name") if module else target_name),
            "evidence_type": evidence_type,
            "conclusion": conclusion,
            "confidence": confidence,
            "evidence_refs": refs,
            "status": status,
        }
        if module:
            module["empirical_support"]["evidence_types"] = unique_strings(module["empirical_support"]["evidence_types"] + [evidence_type])
            module["empirical_support"]["evidence_refs"] = unique_strings(module["empirical_support"]["evidence_refs"] + refs)
            module["empirical_support"]["confidence"] = confidence or module["empirical_support"].get("confidence")
            if evidence_type in SUPPORT_STRONG and status not in {"unsupported", "blocked", "rejected"}:
                module["empirical_support"]["status"] = "supported"
            elif evidence_type in SUPPORT_WEAK and module["empirical_support"]["status"] == "unassessed":
                module["empirical_support"]["status"] = "definition_or_hint_only"
            elif evidence_type == "unsupported" or status in {"unsupported", "blocked", "rejected"}:
                module["empirical_support"]["status"] = "unsupported"
        if evidence_type in SUPPORT_STRONG and status not in {"unsupported", "blocked", "rejected"}:
            supported.append(entry)
        elif evidence_type == "unsupported" or status in {"unsupported", "blocked", "rejected"}:
            unsupported.append(entry)
        else:
            unresolved.append(entry)
    return supported, unsupported, unresolved


def intent_modules(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    intent = handoff.get("method_intent", {})
    output = []
    for item in listify(intent.get("candidate_modules")):
        if isinstance(item, dict):
            output.append(item)
    return output


def current_claim_boundary(result: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any]:
    explicit = section(result, "claim_boundaries", "claim_boundary")
    if isinstance(explicit, dict):
        boundary = deepcopy(explicit)
        boundary["audit_status"] = "pre_T7_only"
        return boundary
    claims = result.get("claim_evidence_matrix", {})
    items = claims.get("items", []) if isinstance(claims, dict) else listify(claims)
    supported, constrained, unsupported = [], [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or item.get("support_status") or "unknown").lower()
        rec = {"claim_id": item.get("claim_id"), "statement": item.get("statement") or item.get("claim")}
        if status in {"supported", "pass", "confirmed"}:
            supported.append(rec)
        elif status in {"unsupported", "refuted", "blocked"}:
            unsupported.append(rec)
        else:
            constrained.append(rec)
    return {
        "supported_claim_candidates": supported,
        "constrained_or_unresolved_claims": constrained,
        "unsupported_claims": unsupported,
        "must_not_claim": unique_strings(
            listify(get_nested(result, "context_alignment.confirmed_execution_scope.must_not_claim"))
            + listify(get_nested(handoff, "context_reboost.claim_boundaries"))
            + listify(get_nested(handoff, "context_reboost.must_not_claim"))
        ),
        "audit_status": "pre_T7_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a realized-method package scaffold from implementation facts and attribution evidence.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/final_evidence_snapshot.json")
    parser.add_argument("--output", default="external_executor/evidence_package/realized_method_package.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    result = load_json(ext / "result_pack.json")
    handoff = load_json(ext / "handoff_pack.json")
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))

    sources: list[tuple[str, Any]] = [
        ("result_pack.method_specification", result.get("method_specification")),
        ("result_pack.implementation_spec", result.get("implementation_spec")),
        ("result_pack.implementation_records", result.get("implementation_records")),
        ("result_pack.implementation", result.get("implementation")),
        ("result_pack.realized_method", result.get("realized_method")),
    ]
    modules: list[dict[str, Any]] = []
    for origin, value in sources:
        for item in item_list(value):
            nested = item.get("modules") or item.get("implemented_modules") or item.get("components")
            if nested:
                for module in listify(nested):
                    if isinstance(module, dict):
                        modules.append(normalize_module(module, origin))
            elif any(key in item for key in ("module_id", "module_name", "component", "component_id")):
                modules.append(normalize_module(item, origin))
    modules = merge_modules(modules)

    attributions = attribution_records(result)
    supported, unsupported, unresolved = apply_attribution(modules, attributions)
    intent = handoff.get("method_intent", {})
    intent_items = intent_modules(handoff)
    intent_ids = {str(item.get("module_id")) for item in intent_items if item.get("module_id")}
    intent_names = {str(item.get("name")) for item in intent_items if item.get("name")}
    actual_modules = [module for module in modules if module.get("status") in {"implemented", "added", "modified"}]
    actual_ids = {str(module.get("module_id")) for module in actual_modules if module.get("module_id")}
    actual_names = {str(module.get("name")) for module in actual_modules if module.get("name")}
    matched_actual = {
        str(module.get("module_id") or module.get("name"))
        for module in actual_modules
        if str(module.get("module_id")) in intent_ids or str(module.get("name")) in intent_names
    }
    unmatched_actual = [
        str(module.get("module_id") or module.get("name"))
        for module in actual_modules
        if str(module.get("module_id")) not in intent_ids and str(module.get("name")) not in intent_names
    ]
    matched_intent_ids = {
        str(item.get("module_id") or item.get("name"))
        for item in intent_items
        if str(item.get("module_id")) in actual_ids or str(item.get("name")) in actual_names
    }
    all_intent_keys = {str(item.get("module_id") or item.get("name")) for item in intent_items}
    dropped = [module for module in modules if module.get("status") == "dropped"]
    added = [module for module in actual_modules if module.get("status") == "added" or (str(module.get("module_id")) not in intent_ids and str(module.get("name")) not in intent_names)]

    method_source = section(result, "realized_method", "method_specification", "implementation_spec")
    if not isinstance(method_source, dict):
        method_source = {}
    final_name = method_source.get("final_method_name") or method_source.get("method_name") or intent.get("method_name")
    one_sentence = method_source.get("one_sentence_method") or method_source.get("summary")
    core_mechanism = method_source.get("actual_core_mechanism") or method_source.get("core_mechanism")
    flow = method_source.get("actual_algorithm_flow") or method_source.get("algorithm_flow") or method_source.get("steps") or []
    losses = method_source.get("actual_losses") or method_source.get("losses") or []

    unresolved_fields: list[str] = []
    for field, value in {
        "final_method_name": final_name,
        "one_sentence_method": one_sentence,
        "actual_core_mechanism": core_mechanism,
        "actual_algorithm_flow": flow,
        "implemented_modules": [m for m in modules if m.get("status") in {"implemented", "added", "modified"}],
    }.items():
        if not nonempty(value):
            unresolved_fields.append(field)
    for module in modules:
        if module.get("status") in {"implemented", "added", "modified"}:
            if not module.get("code_refs"):
                unresolved_fields.append(f"modules.{module['module_id']}.code_refs")
            if not module.get("config_keys"):
                unresolved_fields.append(f"modules.{module['module_id']}.config_keys")

    if not modules or not final_name:
        status = "unavailable"
    elif unresolved_fields:
        status = "partial"
    else:
        status = "complete"

    package = {
        "schema_version": "realized_method_package.v1",
        "generated_at": utc_now(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "status": status,
        "final_method_name": final_name,
        "one_sentence_method": one_sentence,
        "actual_core_mechanism": core_mechanism,
        "implemented_modules": [m for m in modules if m.get("status") in {"implemented", "added", "modified"}],
        "dropped_modules": dropped,
        "added_modules": added,
        "unverified_modules": [m for m in modules if m.get("status") == "unverified"],
        "actual_algorithm_flow": flow,
        "actual_losses": losses,
        "module_attribution": {
            "supported_mechanisms": supported,
            "unsupported_mechanisms": unsupported,
            "definition_or_hint_only": unresolved,
            "all_attribution_record_count": len(attributions),
        },
        "claim_boundary": current_claim_boundary(result, handoff),
        "delta_from_method_intent": {
            "intent_status": intent.get("status"),
            "intent_module_ids": sorted(intent_ids),
            "intent_module_names": sorted(intent_names),
            "actual_module_ids": sorted(actual_ids),
            "actual_module_names": sorted(actual_names),
            "implemented_from_intent": sorted(matched_actual),
            "intent_modules_not_verified_in_actual": sorted(all_intent_keys - matched_intent_ids),
            "actual_modules_not_in_intent": sorted(set(unmatched_actual)),
            "recorded_refinement_delta": section(result, "method_delta", "implementation_delta"),
        },
        "method_code_config_evidence_requirements": {
            "every_implemented_module_requires_code_ref": True,
            "every_implemented_module_requires_config_key": True,
            "method_definition_is_not_empirical_support": True,
            "causal_support_requires_direct_ablation_or_controlled_diagnostic": True,
        },
        "unresolved_fields": sorted(set(unresolved_fields)),
        "source_refs": [
            "external_executor/handoff_pack.json#method_intent",
            "external_executor/result_pack.json#implementation_spec",
            "external_executor/result_pack.json#implementation_records",
            "external_executor/result_pack.json#module_attribution",
            "external_executor/final_evidence_snapshot.json",
        ],
        "package_fingerprint": None,
        "notes": [],
    }
    package["package_fingerprint"] = canonical_json_hash({k: v for k, v in package.items() if k != "package_fingerprint"})
    dump_json_atomic(resolve_in_workspace(ws, args.output), package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
