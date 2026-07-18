#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
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
)
from _method_sources import section_items

IMPLEMENTED = {
    "implemented", "active", "kept", "present", "complete", "completed", "pass",
    "diagnostic_only", "interface_only", "adapter_only",
}
DROPPED = {"dropped", "removed", "disabled", "rejected"}
SUPPORT_STRONG = {"direct_ablation", "controlled_diagnostic"}
SUPPORT_WEAK = {"correlational_hint", "implementation_fact"}


def snapshot_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, entry in snapshot.get("section_digests", {}).items():
        if isinstance(entry, dict) and "value" in entry:
            result[key] = deepcopy(entry["value"])
    return result


def config_key_strings(value: Any) -> list[str]:
    output: list[str] = []
    for item in listify(value):
        if isinstance(item, dict):
            key = item.get("key") or item.get("config_key") or item.get("path")
            if key:
                output.append(str(key))
        elif item is not None:
            output.append(str(item))
    return unique_strings(output)


def implementation_worktree(workspace: Path, implementation: dict[str, Any]) -> Path | None:
    root_value = implementation.get("implementation_root")
    if not root_value:
        return None
    root = resolve_in_workspace(workspace, str(root_value))
    worktree = root / "worktree"
    return worktree if worktree.is_dir() else root


def qualify_code_refs(workspace: Path, implementation: dict[str, Any], values: Any) -> list[str]:
    root = implementation_worktree(workspace, implementation)
    output: list[str] = []
    for value in listify(values):
        if isinstance(value, dict):
            value = value.get("path") or value.get("code_ref") or value.get("symbol")
        if not value:
            continue
        text = str(value)
        if "#" in text:
            path_part, fragment = text.split("#", 1)
        else:
            path_part, fragment = text, None
        if path_part.startswith("external_executor/"):
            rel = path_part
        elif root is not None:
            rel = root.joinpath(path_part).resolve(strict=False).relative_to(workspace.resolve()).as_posix()
        else:
            rel = path_part
        output.append(f"{rel}#{fragment}" if fragment else rel)
    return unique_strings(output)


def normalize_module(
    item: dict[str, Any],
    origin: str,
    *,
    workspace: Path,
    implementation: dict[str, Any],
) -> dict[str, Any]:
    name = str(
        item.get("name") or item.get("module_name") or item.get("component")
        or item.get("module_id") or "unnamed-module"
    )
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
        status = raw_status if raw_status in {"implemented", "dropped", "added", "modified"} else "unverified"
    raw_refs = (
        listify(item.get("code_refs")) + listify(item.get("code_ref"))
        + listify(item.get("code_paths")) + listify(item.get("code_path"))
        + listify(item.get("path"))
    )
    limitations = unique_strings(listify(item.get("limitations")) + listify(item.get("known_limitations")))
    return {
        "module_id": module_id,
        "name": name,
        "status": status,
        "actual_role": item.get("actual_role") or item.get("role") or item.get("intended_role") or item.get("description"),
        "inputs": listify(item.get("inputs") or item.get("expected_input") or item.get("input")),
        "outputs": listify(item.get("outputs") or item.get("expected_output") or item.get("output")),
        "code_refs": qualify_code_refs(workspace, implementation, raw_refs),
        "config_keys": config_key_strings(item.get("config_keys") or item.get("config_key")),
        "implementation_evidence_refs": unique_strings(listify(item.get("evidence_refs")) + extract_refs(item)),
        "public_interfaces": unique_strings(listify(item.get("public_interfaces")) + listify(item.get("symbols"))),
        "ablation_switch": item.get("ablation_switch"),
        "diagnostic_switches": listify(item.get("diagnostic_switches")),
        "affected_experiment_ids": unique_strings(listify(item.get("affected_experiment_ids"))),
        "empirical_support": {
            "status": "unassessed",
            "evidence_types": [],
            "evidence_refs": [],
            "confidence": None,
            "limitations": limitations,
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
        for key in (
            "inputs", "outputs", "code_refs", "config_keys", "implementation_evidence_refs",
            "public_interfaces", "diagnostic_switches", "affected_experiment_ids", "source_origins", "notes",
        ):
            target[key] = unique_strings(target.get(key, []) + module.get(key, []))
        target["empirical_support"]["limitations"] = unique_strings(
            target["empirical_support"].get("limitations", [])
            + module.get("empirical_support", {}).get("limitations", [])
        )
        if target.get("name") in {None, "", mid} and module.get("name") not in {None, "", mid}:
            target["name"] = module["name"]
        for key in ("actual_role", "ablation_switch"):
            if not nonempty(target.get(key)) and nonempty(module.get(key)):
                target[key] = module[key]
        if target["status"] == "unverified" and module["status"] != "unverified":
            target["status"] = module["status"]
            target["definition_status"] = module["definition_status"]
    return list(merged.values())


def attribution_items(report: dict[str, Any] | None, name: str) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    return section_items(report.get(name))


def apply_attribution(
    modules: list[dict[str, Any]], records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {module["module_id"]: module for module in modules}
    supported: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        module_id = record.get("module_id") or record.get("component_id")
        module = by_id.get(str(module_id)) if module_id else None
        evidence_type = str(record.get("evidence_type") or record.get("attribution_type") or "unsupported")
        empirical = str(record.get("empirical_status") or record.get("status") or record.get("verdict") or "unknown").lower()
        causal = str(record.get("causal_status") or "unknown").lower()
        refs = unique_strings(listify(record.get("evidence_refs")) + extract_refs(record))
        limitations = unique_strings(listify(record.get("limitations")))
        entry = {
            "attribution_id": record.get("attribution_id") or record_id(record, f"attribution[{index}]"),
            "module_id": module.get("module_id") if module else module_id,
            "mechanism": record.get("mechanism") or record.get("conclusion") or (module.get("name") if module else None),
            "evidence_type": evidence_type,
            "empirical_status": empirical,
            "causal_status": causal,
            "conclusion": record.get("conclusion") or record.get("finding") or record.get("summary"),
            "confidence": record.get("confidence"),
            "evidence_refs": refs,
            "claim_ids": unique_strings(listify(record.get("claim_ids"))),
            "experiment_ids": unique_strings(listify(record.get("experiment_ids"))),
            "confound_ids": unique_strings(listify(record.get("confound_ids"))),
            "limitations": limitations,
        }
        if module:
            support = module["empirical_support"]
            support["evidence_types"] = unique_strings(support["evidence_types"] + [evidence_type])
            support["evidence_refs"] = unique_strings(support["evidence_refs"] + refs)
            support["confidence"] = record.get("confidence") or support.get("confidence")
            support["limitations"] = unique_strings(support.get("limitations", []) + limitations)
            if evidence_type in SUPPORT_STRONG and empirical not in {"unsupported", "harmful"}:
                support["status"] = "supported"
            elif evidence_type in SUPPORT_WEAK and support["status"] == "unassessed":
                support["status"] = "definition_or_hint_only"
            elif evidence_type == "unsupported" or empirical == "unsupported":
                support["status"] = "unsupported"
        if evidence_type in SUPPORT_STRONG and empirical not in {"unsupported", "harmful"}:
            supported.append(entry)
        elif evidence_type == "unsupported" or empirical == "unsupported":
            unsupported.append(entry)
        else:
            unresolved.append(entry)
    return supported, unsupported, unresolved


def classify_mechanisms(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    supported: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get("status") or "unresolved").lower()
        evidence_type = str(record.get("evidence_type") or "unsupported").lower()
        if status == "supported" and evidence_type in SUPPORT_STRONG:
            supported.append(deepcopy(record))
        elif status in {"contradicted", "unsupported"}:
            unsupported.append(deepcopy(record))
        else:
            unresolved.append(deepcopy(record))
    return supported, unsupported, unresolved


def current_claim_boundary(result: dict[str, Any], handoff_intent: dict[str, Any]) -> dict[str, Any]:
    explicit = result.get("claim_boundary") or result.get("claim_boundaries")
    if isinstance(explicit, dict):
        boundary = deepcopy(explicit)
        boundary["audit_status"] = "pre_T7_only"
        return boundary
    claims = result.get("claim_evidence_matrix", {})
    items = section_items(claims)
    supported: list[dict[str, Any]] = []
    constrained: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for item in items:
        status = str(item.get("status") or item.get("support_status") or "unknown").lower()
        record = {"claim_id": item.get("claim_id"), "statement": item.get("statement") or item.get("claim")}
        if status in {"supported", "pass", "confirmed"}:
            supported.append(record)
        elif status in {"unsupported", "refuted", "blocked"}:
            unsupported.append(record)
        else:
            constrained.append(record)
    contract = handoff_intent.get("research_contract", {}) if isinstance(handoff_intent, dict) else {}
    return {
        "supported_claim_candidates": supported,
        "constrained_or_unresolved_claims": constrained,
        "unsupported_claims": unsupported,
        "must_not_claim": unique_strings(
            listify(handoff_intent.get("must_not_claim")) + listify(contract.get("must_not_claim"))
        ),
        "setting_subset_boundaries": [],
        "attribution_limitations": [],
        "audit_status": "pre_T7_only",
    }


def flow_with_phase(value: Any, phase: str, implemented_ids: set[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(listify(value)):
        if not isinstance(raw, dict):
            continue
        item = deepcopy(raw)
        module_ids = unique_strings(listify(item.get("module_ids")) + listify(item.get("module_id")))
        item.setdefault("step_id", stable_id("FLOW", phase, item.get("step") or index + 1, item.get("name")))
        item["phase"] = phase
        item["module_ids"] = module_ids
        item["realization_status"] = (
            "implementation_mapped" if module_ids and set(module_ids).issubset(implemented_ids) else "unverified_from_spec"
        )
        item["definition_source"] = "selected_method_spec"
        output.append(item)
    return output


def objective_records(
    workspace: Path, implementation: dict[str, Any], method_spec: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    output: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(listify(method_spec.get("objectives_and_losses") or method_spec.get("actual_losses") or method_spec.get("losses"))):
        if not isinstance(raw, dict):
            continue
        item = deepcopy(raw)
        item.setdefault("objective_id", stable_id("LOSS", item.get("name") or index))
        targets = qualify_code_refs(
            workspace,
            implementation,
            item.get("implementation_refs") or item.get("implementation_ref") or item.get("implementation_target"),
        )
        item["implementation_refs"] = targets
        existing = []
        for ref in targets:
            path = resolve_in_workspace(workspace, ref.split("#", 1)[0])
            if path.exists():
                existing.append(ref)
        item["implementation_validation"] = "verified" if existing else "unverified"
        if not existing:
            errors.append(f"loss_implementation_unverified:{item['objective_id']}")
        output.append(item)
    return output, errors


def known_spec_config_keys(method_spec: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for module in section_items(method_spec.get("modules")):
        keys.update(config_key_strings(module.get("config_keys")))
    contract = method_spec.get("configuration_contract")
    if isinstance(contract, dict):
        for value in contract.values():
            if isinstance(value, list):
                keys.update(config_key_strings(value))
    elif isinstance(contract, list):
        keys.update(config_key_strings(contract))
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one final-version realized method package from a pinned snapshot.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/phase_F/final_evidence_snapshot.json")
    parser.add_argument("--output", default="external_executor/evidence_package/realized_method_package.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    snapshot = load_json(resolve_in_workspace(workspace, args.snapshot))
    result = snapshot_result(snapshot)
    selection = snapshot.get("final_source_selection", {})
    method_spec_record = snapshot.get("selected_method_spec", {})
    method_spec = method_spec_record.get("value", {}) if isinstance(method_spec_record, dict) else {}
    handoff_intent = snapshot.get("handoff_method_intent", {})
    implementation = selection.get("active_implementation") if isinstance(selection, dict) else None
    implementation = implementation if isinstance(implementation, dict) else {}
    attribution_report = selection.get("module_attribution") if isinstance(selection, dict) else None
    review = selection.get("implementation_review") if isinstance(selection, dict) else None

    module_candidates: list[dict[str, Any]] = []
    for item in section_items(implementation.get("module_mapping")):
        module_candidates.append(normalize_module(
            item,
            "active_implementation.module_mapping",
            workspace=workspace,
            implementation=implementation,
        ))
    for item in section_items(method_spec.get("modules")):
        module_candidates.append(normalize_module(
            item,
            "selected_method_spec.modules",
            workspace=workspace,
            implementation=implementation,
        ))
    modules = merge_modules(module_candidates)
    implemented_modules = [item for item in modules if item.get("status") in {"implemented", "added", "modified"}]
    implemented_ids = {str(item.get("module_id")) for item in implemented_modules}

    module_records = attribution_items(attribution_report, "module_attributions")
    supported, unsupported, hint_only = apply_attribution(modules, module_records)
    mechanism_records = attribution_items(attribution_report, "mechanism_attributions")
    supported_mechanisms, unsupported_mechanisms, unresolved_mechanisms = classify_mechanisms(mechanism_records)

    research_contract = method_spec.get("research_contract", {}) if isinstance(method_spec.get("research_contract"), dict) else {}
    primary_module = next((item for item in method_spec.get("modules", []) if item.get("module_id") in {"M1", "ours"}), None)
    primary_module = primary_module or (method_spec.get("modules", [None])[0] if method_spec.get("modules") else None)
    final_name = (
        method_spec.get("final_method_name") or method_spec.get("method_name")
        or research_contract.get("method_name") or (primary_module or {}).get("name")
        or handoff_intent.get("method_name")
    )
    one_sentence = (
        method_spec.get("one_sentence_method") or method_spec.get("one_sentence_summary")
        or method_spec.get("summary")
    )
    core_mechanism = (
        method_spec.get("actual_core_mechanism") or method_spec.get("core_mechanism")
        or research_contract.get("core_mechanism")
    )

    training_flow = flow_with_phase(method_spec.get("training_flow"), "training", implemented_ids)
    inference_flow = flow_with_phase(method_spec.get("inference_flow"), "inference", implemented_ids)
    algorithm_flow = training_flow + inference_flow
    losses, loss_errors = objective_records(workspace, implementation, method_spec)

    source_errors = list(selection.get("errors", [])) if isinstance(selection, dict) else ["final_source_selection_missing"]
    source_warnings = list(selection.get("warnings", [])) if isinstance(selection, dict) else []
    root = implementation_worktree(workspace, implementation)
    if root is None or not root.is_dir():
        source_errors.append("active_implementation_root_missing")
    else:
        implementation_anchor = workspace / "external_executor" / "expr" / "implementation"
        try:
            root.resolve().relative_to(implementation_anchor.resolve())
        except ValueError:
            source_errors.append("active_implementation_root_outside_expr_implementation")
    if not implementation.get("final_worktree_fingerprint"):
        source_errors.append("final_worktree_fingerprint_missing")
    review_status = review.get("review_status") if isinstance(review, dict) else None
    if review_status != "pass":
        source_errors.append("final_implementation_review_not_passed")
    module_mapping_section = implementation.get("module_mapping")
    if not isinstance(module_mapping_section, dict) or module_mapping_section.get("status") != "complete":
        source_errors.append("active_module_mapping_incomplete")
    if not selection.get("active_implementation_runs"):
        source_errors.append("no_experiment_run_bound_to_active_implementation")

    known_config = known_spec_config_keys(method_spec)
    for module in implemented_modules:
        for ref in module.get("code_refs", []):
            path = resolve_in_workspace(workspace, ref.split("#", 1)[0])
            if not path.exists():
                source_errors.append(f"module_code_ref_missing:{module['module_id']}:{ref}")
            elif root is not None:
                try:
                    path.resolve().relative_to(root.resolve())
                except ValueError:
                    source_errors.append(f"module_code_ref_outside_active_worktree:{module['module_id']}:{ref}")
        unknown_keys = sorted(set(module.get("config_keys", [])) - known_config) if known_config else []
        if unknown_keys:
            source_errors.append(f"module_config_keys_not_in_spec:{module['module_id']}:{','.join(unknown_keys)}")
    source_errors.extend(loss_errors)

    protocols = {
        str(value) for value in (
            snapshot.get("active_protocol_fingerprint"),
            implementation.get("protocol_fingerprint"),
            method_spec.get("protocol_fingerprint"),
            get_nested(review or {}, "review_scope.protocol_fingerprint"),
        ) if value
    }
    if len(protocols) > 1:
        source_errors.append("method_implementation_review_protocol_mismatch")

    refinement = selection.get("method_refinement", {}) if isinstance(selection, dict) else {}
    spec_identity = {
        "spec_id": method_spec.get("spec_id") or refinement.get("spec_id"),
        "spec_version": method_spec.get("spec_version") or refinement.get("spec_version"),
        "spec_fingerprint": method_spec.get("spec_fingerprint") or refinement.get("spec_fingerprint"),
        "spec_ref": method_spec_record.get("path") if isinstance(method_spec_record, dict) else None,
        "source_sha256": method_spec_record.get("sha256") if isinstance(method_spec_record, dict) else None,
    }
    final_version = {
        "iteration_id": selection.get("final_iteration_id"),
        "implementation_id": implementation.get("implementation_id"),
        "implementation_root": implementation.get("implementation_root"),
        "final_worktree_fingerprint": implementation.get("final_worktree_fingerprint"),
        "method_spec": spec_identity,
        "review_id": review.get("review_id") if isinstance(review, dict) else None,
        "review_status": review_status,
        "approved_for": review.get("approved_for") if isinstance(review, dict) else None,
        "protocol_fingerprint": next(iter(protocols)) if len(protocols) == 1 else None,
        "bound_experiment_run_ids": [
            item.get("run_id") for item in selection.get("active_implementation_runs", []) if item.get("run_id")
        ],
    }

    intent_modules = section_items(handoff_intent.get("candidate_modules"))
    intent_ids = {str(item.get("module_id")) for item in intent_modules if item.get("module_id")}
    actual_ids = {str(item.get("module_id")) for item in implemented_modules if item.get("module_id")}

    unresolved_fields: list[str] = []
    required = {
        "final_method_name": final_name,
        "one_sentence_method": one_sentence,
        "actual_core_mechanism": core_mechanism,
        "training_flow": training_flow,
        "inference_flow": inference_flow,
        "actual_losses": losses,
        "implemented_modules": implemented_modules,
        "final_version.implementation_id": final_version["implementation_id"],
        "final_version.final_worktree_fingerprint": final_version["final_worktree_fingerprint"],
        "final_version.method_spec.spec_fingerprint": spec_identity["spec_fingerprint"],
    }
    unresolved_fields.extend(key for key, value in required.items() if not nonempty(value))
    for module in implemented_modules:
        if not module.get("code_refs"):
            unresolved_fields.append(f"modules.{module['module_id']}.code_refs")
        if not module.get("config_keys"):
            unresolved_fields.append(f"modules.{module['module_id']}.config_keys")

    incoherent_source = any(
        error in {"active_implementation_root_missing", "active_implementation_root_outside_expr_implementation"}
        or error.startswith("module_code_ref_missing:")
        or error.startswith("module_code_ref_outside_active_worktree:")
        for error in source_errors
    )
    if not implemented_modules or not final_name or not implementation or incoherent_source:
        status = "unavailable"
    elif unresolved_fields or source_errors:
        status = "partial"
    else:
        status = "complete"

    full_attribution = {
        "selected_attribution_id": attribution_report.get("attribution_id") if isinstance(attribution_report, dict) else None,
        "selected_iteration_id": attribution_report.get("iteration_id") if isinstance(attribution_report, dict) else None,
        "supported_modules": supported,
        "unsupported_modules": unsupported,
        "definition_or_hint_only": hint_only,
        "all_attribution_record_count": len(module_records),
        "supported_mechanisms": supported_mechanisms,
        "unsupported_mechanisms": unsupported_mechanisms,
        "unresolved_mechanisms": unresolved_mechanisms,
        "module_attributions": deepcopy(module_records),
        "mechanism_attributions": deepcopy(mechanism_records),
        "interaction_effects": deepcopy(attribution_items(attribution_report, "interaction_effects")),
        "baseline_module_attributions": deepcopy(attribution_items(attribution_report, "baseline_module_attributions")),
        "confounds": deepcopy(attribution_items(attribution_report, "confounds")),
        "recommendations": deepcopy(attribution_items(attribution_report, "recommendations")),
        "unsupported_questions": deepcopy(attribution_items(attribution_report, "unsupported_questions")),
        "risks": deepcopy(attribution_items(attribution_report, "risks")),
        "attribution_gate": deepcopy(attribution_report.get("attribution_gate", {})) if isinstance(attribution_report, dict) else {},
    }

    package = {
        "schema_version": "realized_method_package.v1",
        "generated_at": utc_now(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "status": status,
        "final_version": final_version,
        "final_method_name": final_name,
        "one_sentence_method": one_sentence,
        "actual_core_mechanism": core_mechanism,
        "system_boundary": deepcopy(method_spec.get("system_boundary", {})),
        "data_and_protocol_interfaces": deepcopy(method_spec.get("data_and_protocol_interfaces", {})),
        "symbol_table": deepcopy(method_spec.get("symbol_table", [])),
        "pseudocode": deepcopy(method_spec.get("pseudocode", [])),
        "implemented_modules": implemented_modules,
        "dropped_modules": [item for item in modules if item.get("status") == "dropped"],
        "added_modules": [item for item in implemented_modules if item.get("status") == "added" or item.get("module_id") not in intent_ids],
        "unverified_modules": [item for item in modules if item.get("status") == "unverified"],
        "actual_algorithm_flow": algorithm_flow,
        "training_flow": training_flow,
        "inference_flow": inference_flow,
        "actual_losses": losses,
        "configuration_contract": deepcopy(method_spec.get("configuration_contract", {})),
        "ablation_and_diagnostic_controls": deepcopy(method_spec.get("ablation_and_diagnostic_controls", {})),
        "implementation_change_history": deepcopy(method_spec.get("change_log", [])),
        "method_evolution": {
            "method_refinements": deepcopy(section_items(result.get("method_refinements"))),
            "implementation_versions": [
                {
                    "implementation_id": item.get("implementation_id"),
                    "iteration_id": item.get("iteration_id"),
                    "implementation_root": item.get("implementation_root"),
                    "final_worktree_fingerprint": item.get("final_worktree_fingerprint"),
                    "method_spec_fingerprint": item.get("method_spec_fingerprint"),
                    "status": item.get("status"),
                    "selected_final": item.get("implementation_id") == implementation.get("implementation_id"),
                }
                for item in section_items(result.get("implementations"))
            ],
            "iteration_decisions": deepcopy(section_items(result.get("iteration_decisions"))),
        },
        "execution_binding": {
            "active_implementation_run_count": len(selection.get("active_implementation_runs", [])),
            "runs": [
                {
                    "run_id": item.get("run_id"),
                    "experiment_id": item.get("experiment_id"),
                    "run_type": item.get("run_type"),
                    "status": item.get("run_status") or item.get("status"),
                    "protocol_fingerprint": item.get("protocol_fingerprint"),
                    "claim_ids": listify(item.get("claim_ids")),
                    "config_ref": item.get("config_ref"),
                    "metric_output_ref": item.get("metric_output_ref"),
                    "raw_log_ref": item.get("raw_log_ref"),
                }
                for item in selection.get("active_implementation_runs", [])
            ],
            "selected_result_diagnosis_id": (
                selection.get("result_diagnosis", {}).get("diagnosis_id")
                if isinstance(selection.get("result_diagnosis"), dict) else None
            ),
        },
        "evidence_traceability": deepcopy(method_spec.get("evidence_traceability", [])),
        "reproducibility_requirements": {
            "logging_and_provenance_contract": deepcopy(method_spec.get("logging_and_provenance_contract", {})),
            "acceptance_checks": deepcopy(method_spec.get("acceptance_checks", [])),
            "baseline_and_fairness_constraints": deepcopy(method_spec.get("baseline_and_fairness_constraints", {})),
        },
        "scope_boundary": deepcopy(method_spec.get("scope_boundary", {})),
        "non_contribution_engineering": deepcopy(method_spec.get("non_contribution_engineering", [])),
        "module_attribution": full_attribution,
        "claim_boundary": current_claim_boundary(result, handoff_intent),
        "delta_from_method_intent": {
            "intent_status": handoff_intent.get("status"),
            "intent_module_ids": sorted(intent_ids),
            "actual_module_ids": sorted(actual_ids),
            "implemented_from_intent": sorted(intent_ids & actual_ids),
            "intent_modules_not_verified_in_actual": sorted(intent_ids - actual_ids),
            "actual_modules_not_in_intent": sorted(actual_ids - intent_ids),
            "selected_refinement": deepcopy(refinement),
            "change_log": deepcopy(method_spec.get("change_log", [])),
        },
        "source_validation": {
            "status": "pass" if not source_errors else "blocked" if incoherent_source else "partial",
            "errors": sorted(set(source_errors)),
            "warnings": sorted(set(source_warnings)),
            "active_implementation_only": True,
            "built_from_pinned_snapshot_values": True,
            "code_paths_checked": True,
            "config_keys_checked_against_method_spec": bool(known_config),
            "protocol_consistent": len(protocols) == 1,
        },
        "method_code_config_evidence_requirements": {
            "every_implemented_module_requires_code_ref": True,
            "every_implemented_module_requires_config_key": True,
            "method_definition_is_not_empirical_support": True,
            "causal_support_requires_direct_ablation_or_controlled_diagnostic": True,
            "complete_requires_final_version_and_verified_loss_implementation": True,
        },
        "unresolved_fields": sorted(set(unresolved_fields)),
        "source_refs": unique_strings([
            args.snapshot,
            method_spec_record.get("path") if isinstance(method_spec_record, dict) else None,
            "external_executor/result_pack.json#implementations.active_implementation_id",
            "external_executor/result_pack.json#method_refinements",
            "external_executor/result_pack.json#implementation_reviews",
            "external_executor/result_pack.json#result_diagnoses",
            "external_executor/result_pack.json#module_attributions",
        ]),
        "package_fingerprint": None,
        "notes": [],
    }
    package["package_fingerprint"] = canonical_json_hash({key: value for key, value in package.items() if key != "package_fingerprint"})
    dump_json_atomic(resolve_in_workspace(workspace, args.output), package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
