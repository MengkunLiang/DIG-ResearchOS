#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    active_iteration,
    assert_write_allowed,
    canonical_json_hash,
    dump_json_atomic,
    get_nested,
    implementation_spec,
    listify,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    tree_manifest,
    utc_now,
)

CHANGE_TYPES = {
    "module", "loss", "training_flow", "inference_flow", "adapter", "entrypoint", "config",
    "ablation_switch", "diagnostic_switch", "logging", "metric_output", "seed", "checkpoint",
    "test", "compatibility_fix", "bug_fix", "instrumentation", "other",
}


def normalize_change(item: Any, index: int) -> dict[str, Any]:
    if isinstance(item, str):
        item = {"summary": item}
    if not isinstance(item, dict):
        item = {"summary": f"change-{index}", "raw": item}
    summary = str(item.get("summary") or item.get("description") or item.get("name") or f"change-{index}")
    change_type = str(item.get("change_type") or item.get("type") or "other")
    if change_type not in CHANGE_TYPES:
        change_type = "other"
    target_paths = [str(value) for value in listify(item.get("target_paths") or item.get("paths") or item.get("code_paths")) if value]
    allowed_operations = [str(value) for value in listify(item.get("allowed_operations") or ["add", "modify"]) if value]
    return {
        "change_id": item.get("change_id") or stable_id("CHG", summary, change_type, index),
        "change_type": change_type,
        "summary": summary,
        "rationale": str(item.get("rationale") or item.get("reason") or ""),
        "target_paths": target_paths,
        "allowed_operations": allowed_operations,
        "spec_item_ids": [str(value) for value in listify(item.get("spec_item_ids") or item.get("source_item_ids")) if value],
        "module_ids": [str(value) for value in listify(item.get("module_ids") or item.get("module_id")) if value],
        "affected_experiment_ids": [str(value) for value in listify(item.get("affected_experiment_ids") or item.get("experiment_ids")) if value],
        "must_preserve": [str(value) for value in listify(item.get("must_preserve")) if value],
        "acceptance_criteria": [str(value) for value in listify(item.get("acceptance_criteria")) if value],
        "required_tests": [str(value) for value in listify(item.get("required_tests") or item.get("test_ids")) if value],
        "protocol_impact_expected": str(item.get("protocol_impact_expected") or "none"),
        "fairness_impact_expected": str(item.get("fairness_impact_expected") or "none"),
        "baseline_reproduction_impact_expected": str(item.get("baseline_reproduction_impact_expected") or "none"),
        "notes": [str(value) for value in listify(item.get("notes")) if value],
    }


def normalize_module(item: Any, index: int, changes: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(item, str):
        item = {"module_id": item, "name": item}
    if not isinstance(item, dict):
        item = {"name": f"module-{index}"}
    module_id = str(item.get("module_id") or item.get("id") or stable_id("MOD", item.get("name", index)))
    linked = [str(value) for value in listify(item.get("linked_change_ids")) if value]
    if not linked:
        linked = [change["change_id"] for change in changes if module_id in change.get("module_ids", [])]
    ablation = item.get("ablation_switch") if isinstance(item.get("ablation_switch"), dict) else {}
    if not ablation and item.get("ablation_config_key"):
        ablation = {"required": True, "config_key": item.get("ablation_config_key"), "off_semantics": item.get("ablation_off_semantics", "")}
    return {
        "module_id": module_id,
        "name": str(item.get("name") or module_id),
        "status_expected": str(item.get("status_expected") or "implemented"),
        "input_contract": item.get("input_contract") if isinstance(item.get("input_contract"), dict) else {},
        "output_contract": item.get("output_contract") if isinstance(item.get("output_contract"), dict) else {},
        "code_path_patterns": [str(value) for value in listify(item.get("code_path_patterns") or item.get("code_paths")) if value],
        "config_keys": [str(value) for value in listify(item.get("config_keys")) if value],
        "test_path_patterns": [str(value) for value in listify(item.get("test_path_patterns") or item.get("test_paths")) if value],
        "ablation_switch": {
            "required": bool(ablation.get("required", True)),
            "config_key": str(ablation.get("config_key") or ""),
            "off_semantics": str(ablation.get("off_semantics") or ""),
        },
        "diagnostic_switches": listify(item.get("diagnostic_switches")),
        "linked_change_ids": linked,
        "linked_experiment_ids": [str(value) for value in listify(item.get("linked_experiment_ids") or item.get("experiment_ids")) if value],
        "must_preserve": [str(value) for value in listify(item.get("must_preserve")) if value],
        "forbidden_shortcuts": [str(value) for value in listify(item.get("forbidden_shortcuts")) if value],
    }


def normalize_verification(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        item = {"name": f"verification-{index}", "command": item}
    command = item.get("command")
    if isinstance(command, str):
        command = []  # strings are deliberately not converted to shell argv
    return {
        "verification_id": str(item.get("verification_id") or item.get("id") or stable_id("VERIFY", item.get("name", index), index)),
        "name": str(item.get("name") or f"verification-{index}"),
        "command": [str(value) for value in (command or [])],
        "working_directory": str(item.get("working_directory") or "."),
        "verification_class": str(item.get("verification_class") or item.get("class") or "unit"),
        "mandatory": bool(item.get("mandatory", True)),
        "tdd_behavior_id": item.get("tdd_behavior_id"),
        "timeout_seconds": int(item.get("timeout_seconds", 120)),
        "allowed_environment_keys": [str(value) for value in listify(item.get("allowed_environment_keys")) if value],
        "expected_outputs": [str(value) for value in listify(item.get("expected_outputs")) if value],
        "linked_change_ids": [str(value) for value in listify(item.get("linked_change_ids")) if value],
        "linked_module_ids": [str(value) for value in listify(item.get("linked_module_ids")) if value],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile an approved implementation delta into a deterministic contract.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/implementation_change_contract.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    output = resolve_in_workspace(workspace, args.output)
    result = load_json(workspace / "external_executor" / "result_pack.json")
    preflight_path = workspace / "external_executor" / "implementation_preflight.json"
    preflight = load_json(preflight_path) if preflight_path.exists() else {}
    if preflight.get("status") == "blocked":
        raise SystemExit("Implementation preflight is blocked")

    iteration = active_iteration(result)
    spec = implementation_spec(result, iteration)
    if not iteration or not spec:
        raise SystemExit("Active iteration and implementation spec are required")

    iteration_id = str(iteration.get("iteration_id") or iteration.get("id") or stable_id("ITER", canonical_json_hash(iteration)))
    spec_id = str(spec.get("implementation_spec_id") or spec.get("spec_id") or spec.get("id") or stable_id("SPEC", canonical_json_hash(spec)))
    raw_changes = spec.get("approved_changes") or spec.get("changes") or iteration.get("approved_changes") or iteration.get("planned_changes") or []
    changes = [normalize_change(item, index) for index, item in enumerate(listify(raw_changes), 1)]
    raw_modules = spec.get("module_contracts") or spec.get("modules") or []
    modules = [normalize_module(item, index, changes) for index, item in enumerate(listify(raw_modules), 1)]
    raw_verifications = spec.get("verification_plan") or spec.get("verification_commands") or spec.get("tests") or []
    verifications = [normalize_verification(item, index) for index, item in enumerate(listify(raw_verifications), 1)]

    base_source_value = spec.get("base_source") or iteration.get("base_source")
    if isinstance(base_source_value, dict):
        source_kind = str(base_source_value.get("source_kind") or "ours")
        base_source_value = base_source_value.get("path")
    else:
        source_kind = str(spec.get("source_kind") or "ours")
    if not base_source_value:
        raise SystemExit("Implementation spec needs base_source")
    base_source = resolve_in_workspace(workspace, str(base_source_value))
    if not base_source.exists():
        raise SystemExit(f"Base source missing: {base_source}")
    base_manifest = tree_manifest(base_source)

    semantic_delta = [{key: change.get(key) for key in ("change_type", "summary", "target_paths", "module_ids")} for change in changes]
    implementation_id = str(spec.get("implementation_id") or stable_id("IMPL", iteration_id, spec_id, canonical_json_hash(semantic_delta)))
    implementation_root = workspace / "external_executor" / "expr" / "implementation" / iteration_id / implementation_id
    experiment_plan = result.get("experiment_plan", {})
    protocol_fp = get_nested(experiment_plan, "protocol_fingerprint", "protocol.fingerprint", default=None)
    fairness_fp = get_nested(experiment_plan, "fairness_fingerprint", "fairness.fingerprint", default=None)

    warnings: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    if not changes and not spec.get("authorized_noop"):
        blockers.append({"id": "no_approved_changes", "message": "No approved change and no authorized no-op."})
    for change in changes:
        if not change["target_paths"]:
            blockers.append({"id": "change_missing_target_paths", "change_id": change["change_id"]})
    if changes and not verifications:
        blockers.append({"id": "missing_verification_plan", "message": "Behavior-changing implementation needs verification."})
    for verification in verifications:
        if not verification["command"]:
            blockers.append({"id": "verification_command_not_argv", "verification_id": verification["verification_id"]})
    for module in modules:
        if module["status_expected"] in {"implemented", "modified"} and not module["code_path_patterns"]:
            warnings.append({"id": "module_code_paths_need_completion", "module_id": module["module_id"]})

    contract_input = {
        "iteration": iteration,
        "spec": spec,
        "base_manifest_sha256": base_manifest["manifest_sha256"],
        "protocol_fingerprint": protocol_fp,
        "fairness_fingerprint": fairness_fp,
    }
    status = "blocked" if blockers else ("draft" if warnings else "ready")
    contract = {
        "schema_version": "implementation_change_contract.v1",
        "status": status,
        "implementation_id": implementation_id,
        "iteration_id": iteration_id,
        "implementation_spec_id": spec_id,
        "generated_at": utc_now(),
        "input_fingerprint": canonical_json_hash(contract_input),
        "base_source": {
            "path": relpath(workspace, base_source),
            "source_kind": source_kind,
            "fingerprint": base_manifest["manifest_sha256"],
            "read_only": True,
        },
        "implementation_root": relpath(workspace, implementation_root),
        "protocol_fingerprint": protocol_fp,
        "fairness_fingerprint": fairness_fp,
        "approved_changes": changes,
        "module_contracts": modules,
        "verification_plan": verifications,
        "authorized_noop": bool(spec.get("authorized_noop", False)),
        "protected_paths": [str(value) for value in listify(spec.get("protected_paths") or iteration.get("protected_paths")) if value],
        "allowed_dependency_changes": listify(spec.get("allowed_dependency_changes")),
        "forbidden_changes": [str(value) for value in listify(spec.get("forbidden_changes") or [
            "change_core_mechanism", "change_task_or_benchmark", "change_dataset_split", "change_primary_metric",
            "drop_required_baseline", "add_unapproved_data_or_pretraining", "broaden_network_or_install_authority",
        ]) if value],
        "affected_experiment_ids": sorted({str(value) for change in changes for value in change.get("affected_experiment_ids", [])}),
        "baseline_reproduction_dependencies": listify(spec.get("baseline_reproduction_dependencies")),
        "source_refs": [
            "external_executor/result_pack.json#iteration_plans",
            "external_executor/result_pack.json#implementation_spec",
            "external_executor/result_pack.json#experiment_plan",
        ],
        "warnings": warnings,
        "blocking_issues": blockers,
    }
    assert_write_allowed(workspace, output)
    assert_write_allowed(workspace, implementation_root)
    dump_json_atomic(output, contract)
    print(f"{status}: wrote {relpath(workspace, output)}")
    return 2 if blockers else (1 if warnings else 0)


if __name__ == "__main__":
    raise SystemExit(main())
