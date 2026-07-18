#!/usr/bin/env python3
"""Validate one immutable ResearchOS experiment run request."""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Any

from _common import error, is_under_workspace_path, load_json, require_allowed, resolve_in_workspace, sha256_file, workspace_root

RUN_TYPES = {"smoke", "small_scale", "formal", "ablation", "robustness", "diagnostic", "efficiency"}
LEVELS = {"none": 0, "smoke": 1, "small_scale": 2, "formal": 3}
ROLES = {"confirmatory", "diagnostic", "exploratory"}
METHOD_ROLES = {"ours", "baseline"}
DATA_KINDS = {"real", "toy", "synthetic", "dry_run"}
DEPENDENCY_KINDS = {"code", "config", "dataset", "resource", "metric", "evaluator", "checkpoint"}
SECRET_PATTERN = re.compile(r"(secret|token|password|passwd|api[_-]?key|credential|private[_-]?key)", re.I)
SHELL_NAMES = {"sh", "bash", "dash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}
ABLATION_FIELDS = {
    "variant_id", "reference_variant_id", "pair_id", "target_module_ids", "module_states",
    "intervention", "preprocessing_fingerprint", "fairness_fingerprint", "metric_directions",
}


def _experiment_entry(plan: Any, experiment_id: str) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    for key in ("experiments", "items"):
        items = plan.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("experiment_id") == experiment_id:
                    return item
    return None


def _finite_nonnegative(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _compare_declared(entry: dict[str, Any], request: dict[str, Any], errors: list[dict[str, str]]) -> None:
    for field in ("run_type", "analysis_role", "protocol_fingerprint", "seed", "repeat_index"):
        if field in entry and entry[field] != request.get(field):
            errors.append(error("plan_mismatch", f"{field}: plan={entry[field]!r}, request={request.get(field)!r}"))
    plan_dataset = entry.get("dataset")
    request_dataset = request.get("dataset")
    if isinstance(request_dataset, dict):
        expected_dataset = plan_dataset.get("id") if isinstance(plan_dataset, dict) else plan_dataset
        expected_version = plan_dataset.get("version") if isinstance(plan_dataset, dict) else entry.get("dataset_version")
        expected_split = plan_dataset.get("split") if isinstance(plan_dataset, dict) else entry.get("split")
        for field, expected in (("id", expected_dataset), ("version", expected_version), ("split", expected_split)):
            if expected not in (None, "") and expected != request_dataset.get(field):
                errors.append(error("plan_dataset_mismatch", field))
    for field in ("preprocessing_fingerprint", "fairness_fingerprint", "setting", "subset"):
        if entry.get(field) not in (None, "") and entry.get(field) != request.get(field):
            errors.append(error("plan_mismatch", f"{field}: plan={entry.get(field)!r}, request={request.get(field)!r}"))
    if entry.get("metric_directions") and entry.get("metric_directions") != request.get("metric_directions"):
        errors.append(error("plan_mismatch", "metric_directions"))
    if request.get("run_type") != "ablation":
        return
    contract = entry.get("attribution_contract")
    if not isinstance(contract, dict):
        errors.append(error("ablation_contract_missing", str(entry.get("experiment_id"))))
        return
    variants = {
        str(item.get("variant_id")): item
        for item in contract.get("variant_contracts", [])
        if isinstance(item, dict) and item.get("variant_id")
    }
    variant = variants.get(str(request.get("variant_id")))
    if variant is None:
        errors.append(error("ablation_variant_not_declared", str(request.get("variant_id"))))
        return
    for field in ("reference_variant_id", "module_states"):
        if variant.get(field) != request.get(field):
            errors.append(error("ablation_variant_mismatch", field))
    if sorted(str(x) for x in request.get("target_module_ids", [])) != sorted(str(x) for x in contract.get("target_module_ids", [])):
        errors.append(error("ablation_target_modules_mismatch", str(request.get("target_module_ids"))))
    planned_intervention = variant.get("intervention", {})
    requested_intervention = request.get("intervention", {})
    for field in ("type", "controlled", "module_ids", "action", "replacements"):
        if field in planned_intervention and planned_intervention.get(field) != requested_intervention.get(field):
            errors.append(error("ablation_intervention_mismatch", field))


def validate_request(root: Path, request: Any) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(request, dict):
        return {"valid": False, "errors": [error("invalid_type", "request must be an object")], "warnings": []}
    required = {
        "schema_version", "run_id", "experiment_id", "iteration_id", "run_type", "execution_level",
        "analysis_role", "method_id", "method_role", "implementation_id", "command", "cwd", "timeout_seconds", "experiment_plan_ref", "iteration_plan_ref",
        "review_ref", "review_id", "input_fingerprint", "protocol_fingerprint", "config_ref", "raw_log_path",
        "metric_output_path", "run_record_path", "checkpoint_path", "declared_outputs", "dependencies",
        "dataset", "seed", "repeat_index", "resources", "budget", "environment", "isolation", "data_kind",
    }
    for key in sorted(required - request.keys()):
        errors.append(error("missing_field", key))
    for key in ("run_id", "experiment_id", "iteration_id", "method_id", "implementation_id", "review_id", "input_fingerprint", "protocol_fingerprint"):
        if not isinstance(request.get(key), str) or not request.get(key).strip():
            errors.append(error("invalid_identity", key))
    if request.get("schema_version") != "external_executor_run_request.v1":
        errors.append(error("unsupported_schema", str(request.get("schema_version"))))
    if request.get("run_type") not in RUN_TYPES:
        errors.append(error("invalid_run_type", str(request.get("run_type"))))
    level = request.get("execution_level")
    if level not in LEVELS or level == "none":
        errors.append(error("invalid_execution_level", str(level)))
    if request.get("analysis_role") not in ROLES:
        errors.append(error("invalid_analysis_role", str(request.get("analysis_role"))))
    if request.get("method_role") not in METHOD_ROLES:
        errors.append(error("invalid_method_role", str(request.get("method_role"))))
    if request.get("data_kind") not in DATA_KINDS:
        errors.append(error("invalid_data_kind", str(request.get("data_kind"))))
    if request.get("run_type") == "ablation":
        for key in sorted(ABLATION_FIELDS - request.keys()):
            errors.append(error("missing_ablation_field", key))
        for key in ("variant_id", "reference_variant_id", "pair_id", "preprocessing_fingerprint", "fairness_fingerprint"):
            if not isinstance(request.get(key), str) or not request.get(key).strip():
                errors.append(error("invalid_ablation_identity", key))
        target_ids = request.get("target_module_ids")
        states = request.get("module_states")
        if not isinstance(target_ids, list) or not target_ids or not all(isinstance(x, str) and x for x in target_ids):
            errors.append(error("invalid_target_module_ids", str(target_ids)))
        if not isinstance(states, dict) or set(states) != set(target_ids or []) or not all(isinstance(x, bool) for x in states.values()):
            errors.append(error("invalid_module_states", str(states)))
        intervention = request.get("intervention")
        if not isinstance(intervention, dict) or not isinstance(intervention.get("controlled"), bool) or not intervention.get("type"):
            errors.append(error("invalid_intervention", str(intervention)))
        directions = request.get("metric_directions")
        if not isinstance(directions, dict) or not directions or not all(value in {"higher_is_better", "lower_is_better"} for value in directions.values()):
            errors.append(error("invalid_metric_directions", str(directions)))
    expected_level = {"smoke": "smoke", "small_scale": "small_scale", "formal": "formal"}.get(request.get("run_type"))
    if expected_level and level != expected_level:
        errors.append(error("run_type_level_mismatch", f"{request.get('run_type')} requires {expected_level}"))
    command = request.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        errors.append(error("invalid_command", "command must be a non-empty string array"))
    elif Path(command[0]).name.lower() in SHELL_NAMES and any(item.lower() in {"-c", "/c", "-command"} for item in command[1:]):
        errors.append(error("shell_command_forbidden", "execute a reviewed argument vector without a shell command string"))
    timeout = request.get("timeout_seconds")
    if not _finite_nonnegative(timeout) or timeout <= 0:
        errors.append(error("invalid_timeout", str(timeout)))

    paths = ["experiment_plan_ref", "iteration_plan_ref", "review_ref", "config_ref"]
    outputs = ["raw_log_path", "metric_output_path", "run_record_path", "checkpoint_path"]
    resolved: dict[str, Path] = {}
    for field in paths:
        try:
            resolved[field] = resolve_in_workspace(root, str(request.get(field, "")), must_exist=True)
            require_allowed(root, resolved[field])
            if not resolved[field].is_file():
                raise ValueError("must be a regular file")
            if field == "config_ref" and not is_under_workspace_path(root, resolved[field], "external_executor/expr"):
                errors.append(error("config_ref_not_under_expr", str(request.get(field))))
        except Exception as exc:
            errors.append(error("invalid_input_path", f"{field}: {exc}"))
    for field in outputs:
        try:
            resolved[field] = resolve_in_workspace(root, str(request.get(field, "")))
            require_allowed(root, resolved[field])
            if not is_under_workspace_path(root, resolved[field], "external_executor/raw_results"):
                errors.append(error("output_not_under_raw_results", f"{field}: {request.get(field)}"))
        except Exception as exc:
            errors.append(error("invalid_output_path", f"{field}: {exc}"))
    try:
        cwd = resolve_in_workspace(root, str(request.get("cwd", "")), must_exist=True)
        require_allowed(root, cwd)
        if not is_under_workspace_path(root, cwd, "external_executor/expr"):
            errors.append(error("cwd_not_under_expr", str(request.get("cwd"))))
        if not cwd.is_dir():
            errors.append(error("invalid_cwd", "cwd must be a directory"))
    except Exception as exc:
        errors.append(error("invalid_cwd", str(exc)))
    output_values = [str(request.get(field, "")) for field in outputs]
    if len(set(output_values)) != len(output_values):
        errors.append(error("output_path_collision", "log, metric, record, and checkpoint paths must be distinct"))
    declared_outputs = request.get("declared_outputs")
    if not isinstance(declared_outputs, list) or not all(isinstance(item, str) and item for item in declared_outputs):
        errors.append(error("invalid_declared_outputs", "declared_outputs must be a string array"))
    else:
        for value in declared_outputs:
            try:
                declared_path = resolve_in_workspace(root, value)
                require_allowed(root, declared_path)
                if not is_under_workspace_path(root, declared_path, "external_executor/raw_results"):
                    raise ValueError("declared output must be under external_executor/raw_results")
                for field in outputs:
                    core_path = resolved.get(field)
                    if core_path is not None and (declared_path == core_path or declared_path in core_path.parents):
                        raise ValueError(f"declared output overlaps {field}")
            except Exception as exc:
                errors.append(error("invalid_declared_output", f"{value}: {exc}"))

    dependencies = request.get("dependencies")
    kinds: set[str] = set()
    if not isinstance(dependencies, list) or not dependencies:
        errors.append(error("invalid_dependencies", "dependencies must be a non-empty array"))
    else:
        for index, item in enumerate(dependencies):
            if not isinstance(item, dict):
                errors.append(error("invalid_dependency", str(index)))
                continue
            kind = item.get("kind")
            kinds.add(str(kind))
            if kind not in DEPENDENCY_KINDS:
                errors.append(error("invalid_dependency_kind", f"{index}: {kind}"))
            try:
                path = resolve_in_workspace(root, str(item.get("path", "")), must_exist=True)
                require_allowed(root, path)
                if kind in {"code", "config"} and not is_under_workspace_path(root, path, "external_executor/expr"):
                    errors.append(error(f"{kind}_dependency_not_under_expr", str(item.get("path"))))
                if kind in {"dataset", "resource"} and not is_under_workspace_path(root, path, "resources"):
                    errors.append(error(f"{kind}_dependency_not_under_resource", str(item.get("path"))))
                if not path.is_file():
                    raise ValueError("dependency must be a regular file")
                expected = item.get("sha256")
                actual = sha256_file(path)
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    errors.append(error("dependency_checksum_missing", str(item.get("path"))))
                elif expected != actual:
                    errors.append(error("dependency_checksum_mismatch", str(item.get("path"))))
            except Exception as exc:
                errors.append(error("invalid_dependency_path", f"{index}: {exc}"))
    if level == "formal":
        if "code" not in kinds:
            errors.append(error("formal_missing_code_dependency", "formal runs require code provenance"))
        if not ({"dataset", "resource"} & kinds):
            errors.append(error("formal_missing_data_dependency", "formal runs require dataset/resource provenance"))
        if not ({"metric", "evaluator"} & kinds):
            errors.append(error("formal_missing_evaluator_dependency", "formal runs require metric/evaluator provenance"))
        dataset = request.get("dataset")
        if not isinstance(dataset, dict) or not all(dataset.get(field) not in (None, "") for field in ("id", "version", "split")):
            errors.append(error("formal_dataset_identity_incomplete", str(dataset)))
        if not isinstance(request.get("seed"), int) or isinstance(request.get("seed"), bool):
            errors.append(error("invalid_seed", str(request.get("seed"))))
        if not isinstance(request.get("repeat_index"), int) or isinstance(request.get("repeat_index"), bool) or request.get("repeat_index") < 0:
            errors.append(error("invalid_repeat_index", str(request.get("repeat_index"))))
        resources = request.get("resources")
        if not isinstance(resources, dict) or not isinstance(resources.get("gpu_count"), int) or isinstance(resources.get("gpu_count"), bool) or resources.get("gpu_count") < 0:
            errors.append(error("invalid_gpu_count", str(resources)))

    budget = request.get("budget")
    if not isinstance(budget, dict):
        errors.append(error("invalid_budget", "budget must be an object"))
    else:
        remaining = budget.get("remaining")
        estimated = budget.get("estimated")
        if not isinstance(remaining, dict) or not isinstance(estimated, dict):
            errors.append(error("invalid_budget", "remaining and estimated must be objects"))
        else:
            for field in ("runs", "wall_clock_seconds", "gpu_hours", "cost"):
                if not _finite_nonnegative(remaining.get(field)) or not _finite_nonnegative(estimated.get(field)):
                    errors.append(error("invalid_budget_value", field))
                elif estimated[field] > remaining[field]:
                    errors.append(error("budget_exceeded", field))
            if estimated.get("runs") != 1:
                errors.append(error("invalid_run_reservation", "estimated.runs must equal 1"))
            if _finite_nonnegative(timeout) and _finite_nonnegative(remaining.get("wall_clock_seconds")) and timeout > remaining["wall_clock_seconds"]:
                errors.append(error("timeout_exceeds_budget", "timeout exceeds remaining wall-clock budget"))

    environment = request.get("environment")
    if not isinstance(environment, dict):
        errors.append(error("invalid_environment", "environment must be an object"))
    else:
        allowed_env = environment.get("allowed_env", [])
        overrides = environment.get("overrides", {})
        if not isinstance(allowed_env, list) or not all(isinstance(item, str) and item for item in allowed_env):
            errors.append(error("invalid_allowed_env", "allowed_env must be a string array"))
        if not isinstance(overrides, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in overrides.items()):
            errors.append(error("invalid_environment_overrides", "overrides must be a string map"))
        else:
            for key, value in overrides.items():
                if SECRET_PATTERN.search(key) or SECRET_PATTERN.search(value):
                    errors.append(error("secret_in_environment_override", key))
    isolation = request.get("isolation")
    if not isinstance(isolation, dict):
        errors.append(error("invalid_isolation", "isolation must be an object"))
    elif level == "formal":
        if isolation.get("filesystem") != "enforced" or not isolation.get("evidence_ref"):
            errors.append(error("formal_filesystem_isolation_missing", "formal runs require enforced filesystem isolation evidence"))
        else:
            try:
                evidence_path = resolve_in_workspace(root, str(isolation.get("evidence_ref")), must_exist=True)
                require_allowed(root, evidence_path)
                if not evidence_path.is_file():
                    raise ValueError("isolation evidence must be a regular file")
            except Exception as exc:
                errors.append(error("invalid_isolation_evidence", str(exc)))
        network_required = bool(environment.get("network_required")) if isinstance(environment, dict) else False
        network = isolation.get("network")
        if network_required and network != "authorized":
            errors.append(error("formal_network_authority_missing", "network-required formal run must be authorized"))
        if not network_required and network != "enforced":
            errors.append(error("formal_network_isolation_missing", "formal runs without network authority require enforced network isolation"))

    if "experiment_plan_ref" in resolved:
        try:
            plan = load_json(resolved["experiment_plan_ref"])
            entry = _experiment_entry(plan, str(request.get("experiment_id", "")))
            if entry is None:
                errors.append(error("experiment_not_in_plan", str(request.get("experiment_id"))))
            else:
                _compare_declared(entry, request, errors)
        except Exception as exc:
            errors.append(error("invalid_experiment_plan", str(exc)))
    if "iteration_plan_ref" in resolved:
        try:
            iteration = load_json(resolved["iteration_plan_ref"])
            if isinstance(iteration, dict) and iteration.get("iteration_id") != request.get("iteration_id"):
                errors.append(error("iteration_mismatch", str(iteration.get("iteration_id"))))
            planned = iteration.get("runs_to_execute", []) if isinstance(iteration, dict) else []
            if isinstance(planned, list) and planned:
                planned_ids = set()
                for item in planned:
                    if isinstance(item, str):
                        planned_ids.add(item)
                    elif isinstance(item, dict):
                        planned_ids.update(str(item[key]) for key in ("run_id", "experiment_id") if item.get(key))
                if request.get("run_id") not in planned_ids and request.get("experiment_id") not in planned_ids:
                    errors.append(error("run_not_in_iteration_plan", str(request.get("run_id"))))
        except Exception as exc:
            errors.append(error("invalid_iteration_plan", str(exc)))
    if "review_ref" in resolved:
        try:
            review = load_json(resolved["review_ref"])
            if not isinstance(review, dict):
                raise ValueError("review must be an object")
            if review.get("review_id") != request.get("review_id"):
                errors.append(error("review_id_mismatch", str(review.get("review_id"))))
            if review.get("review_status") != "pass":
                errors.append(error("review_not_passed", str(review.get("review_status"))))
            approved = review.get("approved_for")
            if approved not in LEVELS or LEVELS.get(approved, 0) < LEVELS.get(level, 99):
                errors.append(error("approval_level_insufficient", f"approved_for={approved}, requested={level}"))
            if level == "formal" and approved != "formal":
                errors.append(error("formal_not_explicitly_approved", str(approved)))
            if review.get("input_fingerprint") != request.get("input_fingerprint"):
                errors.append(error("review_input_fingerprint_mismatch", "review and request differ"))
            review_protocol = review.get("review_scope", {}).get("protocol_fingerprint")
            if review_protocol != request.get("protocol_fingerprint"):
                errors.append(error("review_protocol_fingerprint_mismatch", str(review_protocol)))
        except Exception as exc:
            errors.append(error("invalid_review", str(exc)))
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    try:
        root = workspace_root(args.workspace)
        request = load_json(resolve_in_workspace(root, args.request, must_exist=True))
        result = validate_request(root, request)
    except Exception as exc:
        result = {"valid": False, "errors": [error("invalid_input", str(exc))], "warnings": []}
    import json
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
