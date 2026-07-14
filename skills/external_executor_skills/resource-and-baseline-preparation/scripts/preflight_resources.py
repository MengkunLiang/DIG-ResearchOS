#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import (
    assert_write_allowed,
    canonical_json_hash,
    dump_json_atomic,
    get_nested,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    schema_major,
    utc_now,
)

SUPPORTED_HANDOFF_MAJOR = 1
SUPPORTED_RESULT_MAJOR = 1
VALID_MODES = {"local_only", "github_allowed", "github_and_reimplementation"}


def default_resource_acquisition_policy() -> dict:
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
            "external_executor/expr may contain only README/checklist scaffolding; "
            "continue by authorized acquisition or reimplementation when local materials are absent."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase B resource preparation prerequisites.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/resource_preflight.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    ext = workspace / "external_executor"
    output = resolve_in_workspace(workspace, args.output)

    issues = []
    warnings = []
    controls = {
        "agents": ext / "AGENTS.md",
        "allowed_paths": ext / "allowed_paths.txt",
        "handoff": ext / "handoff_pack.json",
        "result_pack": ext / "result_pack.json",
        "expected_schema": ext / "expected_outputs_schema.json",
    }
    for name, path in controls.items():
        if not path.exists():
            issues.append({"id": f"missing_{name}", "severity": "blocking", "message": f"Missing {relpath(workspace, path)}"})

    handoff = {}
    result_pack = {}
    expected_schema = {}
    if controls["handoff"].exists():
        try:
            handoff = load_json(controls["handoff"])
        except Exception as exc:
            issues.append({"id": "malformed_handoff", "severity": "blocking", "message": str(exc)})
    if controls["result_pack"].exists():
        try:
            result_pack = load_json(controls["result_pack"])
        except Exception as exc:
            issues.append({"id": "malformed_result_pack", "severity": "blocking", "message": str(exc)})
    if controls["expected_schema"].exists():
        try:
            expected_schema = load_json(controls["expected_schema"])
        except Exception as exc:
            issues.append({"id": "malformed_expected_schema", "severity": "blocking", "message": str(exc)})

    handoff_major = schema_major(handoff.get("schema_version"))
    result_major = schema_major(result_pack.get("schema_version"))
    if handoff and handoff_major not in (None, SUPPORTED_HANDOFF_MAJOR):
        issues.append({"id": "unsupported_handoff_major", "severity": "blocking", "message": str(handoff.get("schema_version"))})
    if result_pack and result_major not in (None, SUPPORTED_RESULT_MAJOR):
        issues.append({"id": "unsupported_result_major", "severity": "blocking", "message": str(result_pack.get("schema_version"))})

    alignment = result_pack.get("context_alignment") if isinstance(result_pack, dict) else None
    if not isinstance(alignment, dict):
        issues.append({"id": "missing_context_alignment", "severity": "blocking", "message": "result_pack.context_alignment is required"})
        alignment = {}
    alignment_status = alignment.get("status")
    if alignment_status not in {"pass", "mismatch"}:
        issues.append({"id": "blocking_context_alignment", "severity": "blocking", "message": f"alignment status is {alignment_status!r}"})
    scope = alignment.get("confirmed_execution_scope")
    if not isinstance(scope, dict):
        issues.append({"id": "missing_confirmed_execution_scope", "severity": "blocking", "message": "context_alignment.confirmed_execution_scope is required"})
        scope = {}

    policy = get_nested(scope, "resource_acquisition_policy", default=None)
    if not isinstance(policy, dict):
        policy = get_nested(handoff, "resource_acquisition_policy", "context_reboost.resource_acquisition_policy", default=None)
    if not isinstance(policy, dict):
        warnings.append({"id": "default_acquisition_policy_applied", "message": "No resource_acquisition_policy found; using ResearchOS default"})
        policy = default_resource_acquisition_policy()
    default_policy = default_resource_acquisition_policy()
    policy.setdefault("allowed_domains", default_policy["allowed_domains"])
    policy.setdefault("material_absence_policy", default_policy["material_absence_policy"])

    mode = policy.get("mode")
    if mode not in VALID_MODES:
        issues.append({"id": "invalid_acquisition_mode", "severity": "blocking", "message": repr(mode)})
    network_allowed = bool(policy.get("network_allowed", False))
    dataset_download_allowed = bool(policy.get("dataset_download_allowed", False))
    reimplementation_allowed = bool(policy.get("baseline_reimplementation_allowed", False))
    allowed_domains = policy.get("allowed_domains", [])
    if not isinstance(allowed_domains, list):
        issues.append({"id": "invalid_allowed_domains", "severity": "blocking", "message": "allowed_domains must be a list"})
        allowed_domains = []

    if mode == "local_only" and (network_allowed or dataset_download_allowed or reimplementation_allowed):
        warnings.append({"id": "stricter_mode_overrides_flags", "message": "local_only disables network, dataset download, and reimplementation flags"})
    if mode in {"github_allowed", "github_and_reimplementation"} and network_allowed and not allowed_domains:
        issues.append({"id": "network_without_domains", "severity": "blocking", "message": "network is allowed but allowed_domains is empty"})
    if mode != "github_and_reimplementation" and reimplementation_allowed:
        warnings.append({"id": "mode_disables_reimplementation", "message": "baseline_reimplementation_allowed is ignored outside github_and_reimplementation"})

    required_axes = {
        "required_baselines": get_nested(scope, "required_baselines", default=None),
        "benchmark_protocol_summary": get_nested(scope, "benchmark_protocol_summary", "benchmark_protocol", default=None),
        "minimum_experiment_loop": get_nested(scope, "minimum_experiment_loop", default=None),
        "claim_boundaries": get_nested(scope, "claim_boundaries", "claim_boundary", default=None),
    }
    for axis, value in required_axes.items():
        if value in (None, "", []):
            issues.append({"id": f"missing_{axis}", "severity": "blocking", "message": f"confirmed scope lacks {axis}"})

    write_targets = [
        output,
        ext / "resource_requirement_matrix.json",
        ext / "resource_local_inventory.json",
        ext / "resource_search_records.json",
        ext / "resource_preparation_report.json",
        ext / "workdir" / "resources",
    ]
    for target in write_targets:
        try:
            assert_write_allowed(workspace, target)
        except Exception as exc:
            issues.append({"id": "write_path_not_allowed", "severity": "blocking", "message": f"{target}: {exc}"})

    fingerprint_payload = {
        "handoff": handoff,
        "context_alignment": alignment,
        "expected_outputs_schema": expected_schema,
        "allowed_paths_text": controls["allowed_paths"].read_text(encoding="utf-8", errors="replace") if controls["allowed_paths"].exists() else None,
    }
    status = "blocked" if issues else ("warning" if warnings else "pass")
    report = {
        "schema_version": "resource_preflight.v1",
        "generated_at": utc_now(),
        "workspace": str(workspace),
        "status": status,
        "input_fingerprint": canonical_json_hash(fingerprint_payload),
        "alignment_status": alignment_status,
        "policy_snapshot": {
            **policy,
            "effective_mode": mode,
            "effective_network_allowed": network_allowed and mode != "local_only",
            "effective_dataset_download_allowed": dataset_download_allowed and network_allowed and mode != "local_only",
            "effective_reimplementation_allowed": reimplementation_allowed and mode == "github_and_reimplementation",
            "allowed_domains": allowed_domains,
        },
        "confirmed_scope_present": bool(scope),
        "issues": issues,
        "warnings": warnings,
        "checked_files": [relpath(workspace, path) for path in controls.values() if path.exists()],
    }
    assert_write_allowed(workspace, output)
    dump_json_atomic(output, report)
    print(f"{status}: wrote {relpath(workspace, output)}")
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
