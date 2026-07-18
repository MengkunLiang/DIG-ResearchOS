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
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    schema_major,
    utc_now,
)


def section_status(value: Any) -> str | None:
    return value.get("status") if isinstance(value, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate implementation prerequisites and authority.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/report/implementation_preflight.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    ext = workspace / "external_executor"
    output = resolve_in_workspace(workspace, args.output)
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    controls = {
        "agents": ext / "AGENTS.md",
        "allowed_paths": ext / "allowed_paths.txt",
        "handoff": ext / "handoff_pack.json",
        "expected_schema": ext / "expected_outputs_schema.json",
        "result_pack": ext / "result_pack.json",
    }
    for name, path in controls.items():
        if not path.exists():
            issues.append({"id": f"missing_{name}", "severity": "blocking", "message": relpath(workspace, path)})

    result: dict[str, Any] = {}
    handoff: dict[str, Any] = {}
    expected: dict[str, Any] = {}
    for name, target in (("result_pack", result), ("handoff", handoff), ("expected_schema", expected)):
        path = controls[name]
        if not path.exists():
            continue
        try:
            parsed = load_json(path)
            if not isinstance(parsed, dict):
                raise ValueError("root must be an object")
            target.update(parsed)
        except Exception as exc:
            issues.append({"id": f"malformed_{name}", "severity": "blocking", "message": str(exc)})

    for name, value in (("result_pack", result.get("schema_version")), ("handoff", handoff.get("schema_version")), ("expected_schema", expected.get("schema_version"))):
        major = schema_major(value)
        if value and major not in {None, 1}:
            issues.append({"id": f"unsupported_{name}_schema", "severity": "blocking", "message": str(value)})

    alignment = result.get("context_alignment")
    if not isinstance(alignment, dict) or alignment.get("status") not in {"pass", "mismatch"}:
        issues.append({"id": "context_alignment_not_ready", "severity": "blocking", "message": repr(section_status(alignment))})

    readiness = result.get("resource_readiness")
    readiness_status = section_status(readiness)
    if readiness_status not in {"ready", "partial"}:
        issues.append({"id": "resource_readiness_not_feasible", "severity": "blocking", "message": repr(readiness_status)})
    elif readiness_status == "partial":
        warnings.append({"id": "resource_constraints_propagate", "message": "Implementation must preserve Phase B constraints."})

    experiment_plan = result.get("experiment_plan")
    if not isinstance(experiment_plan, dict) or experiment_plan.get("status") not in {"complete", "approved", "ready", "partial"}:
        issues.append({"id": "experiment_plan_missing_or_unready", "severity": "blocking", "message": repr(section_status(experiment_plan))})
        experiment_plan = {}

    protocol_fp = get_nested(experiment_plan, "protocol_fingerprint", "protocol.fingerprint", default=None)
    fairness_fp = get_nested(experiment_plan, "fairness_fingerprint", "fairness.fingerprint", default=None)
    if not protocol_fp:
        issues.append({"id": "missing_protocol_fingerprint", "severity": "blocking", "message": "Experiment plan lacks protocol fingerprint."})
    if not fairness_fp:
        issues.append({"id": "missing_fairness_fingerprint", "severity": "blocking", "message": "Experiment plan lacks fairness fingerprint."})

    iteration = active_iteration(result)
    if not iteration:
        issues.append({"id": "missing_active_iteration_plan", "severity": "blocking", "message": "No active root-owned iteration plan."})
        iteration = {}
    else:
        action = iteration.get("implementation_required", iteration.get("requires_implementation", True))
        approved = iteration.get("status") in {"active", "approved", "planned", "running"} or iteration.get("approved") is True
        if action is False:
            issues.append({"id": "iteration_does_not_authorize_implementation", "severity": "blocking", "message": "Active iteration says implementation is not required."})
        if not approved:
            issues.append({"id": "iteration_not_approved", "severity": "blocking", "message": repr(iteration.get("status"))})

    spec = implementation_spec(result, iteration, workspace)
    if not isinstance(spec, dict):
        issues.append({"id": "missing_implementation_spec", "severity": "blocking", "message": "No implementation specification or approved delta found."})
        spec = {}
    elif spec.get("status") in {"blocked", "stale", "draft"}:
        issues.append({"id": "implementation_spec_not_ready", "severity": "blocking", "message": repr(spec.get("status"))})

    scope_requests = result.get("scope_change_requests", {})
    requests = scope_requests.get("items", []) if isinstance(scope_requests, dict) else scope_requests if isinstance(scope_requests, list) else []
    unresolved = [item for item in requests if isinstance(item, dict) and item.get("status") in {"proposed", "pending", "human_review", "blocked"} and item.get("contribution_drift") == "major"]
    if unresolved:
        issues.append({"id": "unresolved_major_scope_change", "severity": "blocking", "message": f"{len(unresolved)} unresolved request(s)"})

    base_source_value = spec.get("base_source") or iteration.get("base_source") or get_nested(result, "implementation_base.path", default=None)
    if isinstance(base_source_value, dict):
        base_source_value = base_source_value.get("path")
    base_source = resolve_in_workspace(workspace, str(base_source_value)) if base_source_value else None
    if not base_source_value or not base_source or not base_source.exists():
        issues.append({"id": "base_source_missing", "severity": "blocking", "message": repr(base_source_value)})
    elif not (base_source.is_dir() or base_source.is_file()):
        issues.append({"id": "base_source_invalid", "severity": "blocking", "message": str(base_source)})
    if iteration.get("copy_previous_method") is True and base_source is not None:
        implementation_root = ext / "expr" / "implementation"
        try:
            base_source.relative_to(implementation_root)
        except ValueError:
            issues.append({"id": "later_iteration_must_copy_previous_implementation", "severity": "blocking", "message": relpath(workspace, base_source)})
        if base_source.name != "worktree":
            issues.append({"id": "previous_implementation_worktree_required", "severity": "blocking", "message": relpath(workspace, base_source)})

    write_targets = [
        output,
        ext / "report" / "implementation_change_contract.json",
        ext / "report" / "implementation_report.json",
        ext / "expr" / "implementation",
        ext / "result_pack.json",
    ]
    for path in write_targets:
        try:
            assert_write_allowed(workspace, path)
        except Exception as exc:
            issues.append({"id": "write_path_not_allowed", "severity": "blocking", "message": f"{path}: {exc}"})

    fingerprint_data = {
        "alignment": alignment,
        "resource_readiness": readiness,
        "experiment_plan": experiment_plan,
        "iteration": iteration,
        "implementation_spec": spec,
        "allowed_paths": controls["allowed_paths"].read_text(encoding="utf-8", errors="replace") if controls["allowed_paths"].exists() else None,
    }
    status = "blocked" if issues else ("warning" if warnings else "pass")
    payload = {
        "schema_version": "implementation_preflight.v1",
        "generated_at": utc_now(),
        "status": status,
        "input_fingerprint": canonical_json_hash(fingerprint_data),
        "iteration_id": iteration.get("iteration_id") or iteration.get("id"),
        "implementation_spec_id": spec.get("implementation_spec_id") or spec.get("spec_id") or spec.get("id"),
        "base_source": relpath(workspace, base_source) if base_source and base_source.exists() else base_source_value,
        "protocol_fingerprint": protocol_fp,
        "fairness_fingerprint": fairness_fp,
        "issues": issues,
        "warnings": warnings,
        "checked_files": [relpath(workspace, path) for path in controls.values() if path.exists()],
    }
    assert_write_allowed(workspace, output)
    dump_json_atomic(output, payload)
    print(f"{status}: wrote {relpath(workspace, output)}")
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
