#!/usr/bin/env python3
"""Validate context controls, schema majors, policy, and handoff consistency."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    atomic_write_json, emit, is_allowed_relative, load_json, major_version, parse_allowed_entries,
    resolve_in_workspace, utc_now, workspace_root,
)


REQUIRED_FILES = [
    "project.yaml",
    "external_executor/AGENTS.md",
    "external_executor/handoff_pack.json",
    "external_executor/expected_outputs_schema.json",
    "external_executor/allowed_paths.txt",
]


def default_resource_acquisition_policy() -> dict[str, Any]:
    return {
        "mode": "github_and_reimplementation",
        "network_allowed": True,
        "github_access_allowed": True,
        "dataset_download_allowed": True,
        "baseline_reimplementation_allowed": True,
        "external_code_inspection_allowed": True,
        "authenticated_resources_allowed": False,
        "license_checks_required": True,
        "checksum_required": True,
        "citation_required": True,
        "allowed_domains": [
            "github.com",
            "raw.githubusercontent.com",
            "codeload.github.com",
            "objects.githubusercontent.com",
            "huggingface.co",
            "hf.co",
            "zenodo.org",
            "figshare.com",
            "kaggle.com",
            "openml.org",
            "archive.ics.uci.edu",
        ],
        "material_absence_policy": (
            "external_executor/expr may be empty or contain only workspace guide files; "
            "check resources/ for by-hand, acquired, or reimplemented materials; continue "
            "by authorized acquisition or reimplementation into resources/ when local "
            "materials are absent."
        ),
    }


def selected_executor(root: Path) -> str:
    path = resolve_in_workspace(root, "external_executor/report/executor_selection.json")
    if not path.is_file():
        path = resolve_in_workspace(root, "external_executor/executor_selection.json")
    if not path.is_file():
        return ""
    try:
        value = load_json(path)
    except Exception:
        return ""
    return str(value.get("selected_executor") or "").strip() if isinstance(value, dict) else ""


def default_executor_capabilities(root: Path) -> dict[str, Any]:
    selected = selected_executor(root)
    real = selected in {"codex_cli", "claude_code_window", "manual"}
    return {
        "selected_executor": selected or "UNSET",
        "network_available": real,
        "github_access_available": real,
        "dataset_download_supported": real,
        "baseline_reimplementation_supported": real,
        "source": "inferred_from_executor_selection",
    }


def acquisition_policy(handoff: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    for value in (
        handoff.get("resource_acquisition_policy"),
        context.get("resource_acquisition_policy"),
        ((handoff.get("execution_contract") or {}).get("resource_acquisition_policy") if isinstance(handoff.get("execution_contract"), dict) else None),
    ):
        if isinstance(value, dict) and value:
            policy = dict(value)
            break
    else:
        policy = default_resource_acquisition_policy()
    default = default_resource_acquisition_policy()
    policy.update(
        {
            "mode": "github_and_reimplementation",
            "network_allowed": True,
            "github_access_allowed": True,
            "dataset_download_allowed": True,
            "baseline_reimplementation_allowed": True,
        }
    )
    policy.setdefault("allowed_domains", default["allowed_domains"])
    policy.setdefault("material_absence_policy", default["material_absence_policy"])
    return policy


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def names(items: Any) -> set[str]:
    result: set[str] = set()
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, str):
            result.add(item.strip())
        elif isinstance(item, dict):
            for key in ("baseline_name", "name", "baseline_id", "id"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    result.add(value.strip())
                    break
    return result


def add(report: dict[str, Any], level: str, code: str, message: str, path: str | None = None) -> None:
    record = {"code": code, "message": message}
    if path:
        record["path"] = path
    report[level].append(record)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", default="external_executor/report/preflight_context.json")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    output = resolve_in_workspace(root, args.output)
    report: dict[str, Any] = {
        "schema_version": "external_executor_context_preflight.v1",
        "status": "pass",
        "checked_at": utc_now(),
        "errors": [],
        "warnings": [],
        "checks": {},
    }

    for rel in REQUIRED_FILES:
        if not resolve_in_workspace(root, rel).is_file():
            add(report, "errors", "missing_required_file", rel, rel)

    handoff_path = resolve_in_workspace(root, "external_executor/handoff_pack.json")
    expected_path = resolve_in_workspace(root, "external_executor/expected_outputs_schema.json")
    allowed_path = resolve_in_workspace(root, "external_executor/allowed_paths.txt")
    handoff: dict[str, Any] = {}
    expected: dict[str, Any] = {}
    for label, path in (("handoff", handoff_path), ("expected_outputs", expected_path)):
        if not path.is_file():
            continue
        try:
            value = load_json(path)
            if not isinstance(value, dict):
                raise ValueError("top level must be an object")
            if label == "handoff":
                handoff = value
            else:
                expected = value
        except Exception as exc:
            add(report, "errors", f"invalid_{label}_json", str(exc), str(path.relative_to(root)))

    for label, value in (("handoff", handoff.get("schema_version")), ("expected_outputs", expected.get("schema_version"))):
        major = major_version(value)
        if major is None:
            add(report, "errors", "missing_schema_version", f"{label} schema_version is missing or unparseable")
        elif major != 1:
            add(report, "errors", "unsupported_major_schema", f"{label} major version {major} is unsupported")

    context = handoff.get("context_reboost")
    method = handoff.get("method_intent")
    if not isinstance(context, dict):
        add(report, "errors", "missing_context_reboost", "handoff.context_reboost must be an object")
        context = {}
    if not isinstance(method, dict):
        add(report, "errors", "missing_method_intent", "handoff.method_intent must be an object")
        method = {}
    if method and method.get("not_final_method_source") is not True:
        add(report, "warnings", "method_intent_finality_unclear", "method intent is not explicitly marked non-final")

    policy = acquisition_policy(handoff, context)
    if not isinstance(handoff.get("resource_acquisition_policy"), dict) and not isinstance(context.get("resource_acquisition_policy"), dict):
        add(report, "warnings", "default_acquisition_policy_applied", "Using ResearchOS default resource acquisition policy")
    mode = policy.get("mode")
    if mode not in {"local_only", "github_allowed", "github_and_reimplementation"}:
        add(report, "errors", "invalid_acquisition_mode", repr(mode))
    if mode in {"github_allowed", "github_and_reimplementation"} and policy.get("network_allowed") is not True:
        add(report, "errors", "network_policy_conflict", f"{mode} requires network_allowed=true")
    if mode == "github_and_reimplementation" and policy.get("baseline_reimplementation_allowed") is not True:
        add(report, "errors", "reimplementation_policy_conflict", "mode requires baseline_reimplementation_allowed=true")

    required_source = first_present(context.get("required_baselines"), handoff.get("required_baselines"), context.get("baseline_matrix"), handoff.get("baseline_matrix"))
    required = names(required_source)
    matrix = names(first_present(context.get("baseline_matrix"), handoff.get("baseline_matrix")))
    missing_matrix = sorted(required - matrix) if matrix else sorted(required)
    if required and missing_matrix:
        add(report, "errors", "required_baseline_not_in_matrix", ", ".join(missing_matrix))
    if not required:
        add(report, "warnings", "required_baselines_missing", "required baseline set is empty or absent")
    if not first_present(context.get("minimum_experiment_loop"), handoff.get("minimum_experiment_loop")):
        add(report, "errors", "minimum_loop_missing", "minimum_experiment_loop is required")
    if not first_present(context.get("claim_boundaries"), handoff.get("claim_boundaries")):
        add(report, "warnings", "claim_boundaries_missing", "claim boundaries are absent or empty")

    allowed_entries, allowed_errors = parse_allowed_entries(root, allowed_path)
    for message in allowed_errors:
        add(report, "errors", "invalid_allowed_path", message)
    if not allowed_entries:
        add(report, "errors", "no_allowed_paths", "no usable allowed path entry")
    output_relative = str(output.relative_to(root).as_posix())
    output_allowed = bool(allowed_entries) and is_allowed_relative(output_relative, allowed_entries)
    if allowed_entries and not output_allowed:
        add(report, "errors", "output_not_allowed", output_relative)

    capabilities_path = resolve_in_workspace(root, "external_executor/report/executor_capabilities.json")
    if not capabilities_path.is_file():
        capabilities_path = resolve_in_workspace(root, "external_executor/executor_capabilities.json")
    capabilities: dict[str, Any] = {}
    if capabilities_path.is_file():
        try:
            value = load_json(capabilities_path)
            capabilities = value if isinstance(value, dict) else {}
        except Exception as exc:
            add(report, "warnings", "invalid_capabilities", str(exc))
    else:
        capabilities = default_executor_capabilities(root)
        add(
            report,
            "warnings",
            "capabilities_inferred",
            "external_executor/report/executor_capabilities.json is absent; inferred from external_executor/report/executor_selection.json",
        )
    if policy.get("network_allowed") is True:
        if not capabilities:
            add(report, "errors", "network_capability_unconfirmed", "policy requires network but executor capabilities are not declared")
        elif capabilities.get("network_available") is not True:
            add(report, "errors", "network_capability_gap", "policy requires network but executor does not declare it available")
    if mode == "github_and_reimplementation":
        if not capabilities:
            add(report, "errors", "reimplementation_capability_unconfirmed", "reimplementation capability is not declared")
        elif capabilities.get("baseline_reimplementation_supported") is not True:
            add(report, "errors", "reimplementation_capability_gap", "executor does not support baseline reimplementation")

    report["checks"] = {
        "allowed_paths": allowed_entries,
        "acquisition_policy": policy,
        "required_baselines": sorted(required),
        "baseline_matrix_names": sorted(matrix),
        "capabilities": capabilities,
        "handoff_schema_version": handoff.get("schema_version"),
        "expected_schema_version": expected.get("schema_version"),
    }
    report["status"] = "blocked" if report["errors"] else "warning" if report["warnings"] else "pass"
    if output_allowed:
        atomic_write_json(output, report)
        print(report["status"])
    else:
        emit(report)
    return 0 if not report["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
