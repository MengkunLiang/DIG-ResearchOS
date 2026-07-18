#!/usr/bin/env python3
"""Validate ResearchOS external-executor controls, state, paths, and manifest."""

from __future__ import annotations

import argparse
from typing import Any

from _common import (
    emit_report, is_under_any, load_json, parse_allowed_roots,
    resolve_in_workspace, sha256_file, workspace_root,
)


REQUIRED_CONTROLS = [
    "project.yaml",
    "external_executor/AGENTS.md",
    "external_executor/handoff_pack.json",
    "external_executor/expected_outputs_schema.json",
    "external_executor/allowed_paths.txt",
]
EXECUTOR_STATUSES = {"running", "completed", "partial", "blocked", "failed"}
PHASES = {"A", "B", "C", "D", "E", "F", "done"}


def add(report: dict[str, Any], level: str, code: str, message: str) -> None:
    report[level].append({"code": code, "message": message})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", choices=("resume", "checkpoint", "final"), default="checkpoint")
    parser.add_argument("--output")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    report: dict[str, Any] = {"valid": True, "mode": args.mode, "errors": [], "warnings": [], "checks": {}}

    for rel in REQUIRED_CONTROLS:
        path = resolve_in_workspace(root, rel)
        if not path.is_file():
            add(report, "errors", "missing_control", rel)
    allowed_path = resolve_in_workspace(root, "external_executor/allowed_paths.txt")
    allowed_roots = parse_allowed_roots(root, allowed_path)
    if not allowed_roots:
        add(report, "errors", "no_allowed_paths", "allowed_paths.txt has no usable workspace paths")

    status_path = resolve_in_workspace(root, "external_executor/executor_status.json")
    manifest_path = resolve_in_workspace(root, "external_executor/report/run_manifest.json")
    result_path = resolve_in_workspace(root, "external_executor/result_pack.json")

    if not status_path.exists():
        add(report, "errors", "initialization_required", "executor_status.json is missing")
        status = {}
    else:
        try:
            status = load_json(status_path)
        except Exception as exc:
            add(report, "errors", "invalid_status_json", str(exc))
            status = {}
    if not isinstance(status, dict):
        add(report, "errors", "invalid_status_type", "executor status must be an object")
        status = {}
    if status.get("executor_status") not in EXECUTOR_STATUSES:
        add(report, "errors", "invalid_executor_status", repr(status.get("executor_status")))
    if status.get("current_phase") not in PHASES:
        add(report, "errors", "invalid_phase", repr(status.get("current_phase")))
    for key in ("completed_checkpoints", "stale_checkpoints", "active_blockers"):
        if key in status and not isinstance(status[key], list):
            add(report, "errors", "invalid_status_field", f"{key} must be an array")
    loop = status.get("iteration_loop")
    if isinstance(loop, dict):
        if loop.get("max_iterations") != 10:
            add(report, "errors", "invalid_iteration_limit", "iteration_loop.max_iterations must remain fixed at 10")
        current_iteration = loop.get("current_iteration")
        if not isinstance(current_iteration, int) or isinstance(current_iteration, bool) or not 0 <= current_iteration <= 10:
            add(report, "errors", "invalid_iteration_count", repr(current_iteration))

    if args.mode == "final" and status.get("executor_status") == "running":
        add(report, "errors", "unfinished_status", "final validation cannot leave executor_status=running")
    if status.get("executor_status") == "completed" and status.get("active_blockers"):
        add(report, "errors", "completed_with_blockers", "completed status has active blockers")

    if not manifest_path.exists():
        add(report, "errors", "missing_manifest", "external_executor/report/run_manifest.json is missing")
        manifest = {}
    else:
        try:
            manifest = load_json(manifest_path)
        except Exception as exc:
            add(report, "errors", "invalid_manifest_json", str(exc))
            manifest = {}
    artifacts = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
    if not isinstance(artifacts, list):
        add(report, "errors", "invalid_artifacts", "manifest.artifacts must be an array")
        artifacts = []

    verified = 0
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            add(report, "errors", "invalid_artifact", f"artifact[{index}] is not an object")
            continue
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str):
            add(report, "errors", "missing_artifact_path", f"artifact[{index}]")
            continue
        try:
            path = resolve_in_workspace(root, raw_path)
        except ValueError as exc:
            add(report, "errors", "artifact_path_escape", str(exc))
            continue
        if allowed_roots and not is_under_any(path, allowed_roots):
            add(report, "errors", "artifact_outside_allowed_paths", raw_path)
        if not path.is_file():
            add(report, "errors", "missing_artifact", raw_path)
            continue
        expected_hash = artifact.get("sha256")
        if not isinstance(expected_hash, str):
            add(report, "errors", "missing_checksum", raw_path)
            continue
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            add(report, "errors", "checksum_mismatch", raw_path)
            continue
        verified += 1

    if not result_path.exists():
        add(report, "errors", "missing_result_pack", "result_pack.json is missing")
    else:
        try:
            result_pack = load_json(result_path)
            result_status = result_pack.get("executor_status") if isinstance(result_pack, dict) else None
            state_status = status.get("executor_status")
            if result_status != state_status:
                add(
                    report,
                    "errors",
                    "status_mismatch",
                    f"executor_status.json={state_status!r}, result_pack.json={result_status!r}",
                )
        except Exception as exc:
            add(report, "errors", "invalid_result_pack_json", str(exc))

    report["checks"] = {
        "allowed_roots": [str(path.relative_to(root)) for path in allowed_roots],
        "manifest_artifacts": len(artifacts),
        "verified_artifacts": verified,
    }
    report["valid"] = not report["errors"]
    emit_report(report, args.output)
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
