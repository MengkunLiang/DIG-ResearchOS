#!/usr/bin/env python3
"""Validate checkpoint or final external-executor result-pack contracts."""

from __future__ import annotations

import argparse
from typing import Any

from _common import emit_report, load_json, relative_to_workspace, resolve_in_workspace, section_status, workspace_root


FALLBACK_REQUIRED = [
    "schema_version", "executor_status", "context_alignment",
    "resource_requirement_matrix", "resources", "baseline_candidates",
    "dataset_inventory", "material_gaps", "resource_risks", "resource_readiness",
    "baseline_reproduction", "claim_evidence_matrix", "experiment_plan",
    "experiment_runs", "implementation_reviews", "result_diagnoses",
    "module_attributions", "iteration_decisions", "realized_method_package",
    "final_framework_figure", "figure_table_inventory", "writer_handoff",
]
EXECUTOR_STATUSES = {"running", "completed", "partial", "blocked", "failed"}
RUN_STATUSES = {"planned", "running", "completed", "failed", "cancelled", "stale", "unusable"}
RUN_TYPES = {"smoke", "small_scale", "formal", "ablation", "robustness", "diagnostic", "efficiency"}


def required_keys(expected: dict[str, Any]) -> list[str]:
    candidates = [expected.get("required"), expected.get("required_fields")]
    for parent in ("result_pack", "result_pack_schema"):
        value = expected.get(parent)
        if isinstance(value, dict):
            candidates.append(value.get("required"))
    for candidate in candidates:
        if isinstance(candidate, list) and all(isinstance(item, str) for item in candidate):
            return candidate
    return FALLBACK_REQUIRED


def runs_from(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "runs", "records"):
            items = value.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def has_any(run: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(run.get(key) not in (None, "", [], {}) for key in keys)


def referenced_path(run: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = run.get(key)
        if isinstance(value, dict) and isinstance(value.get("path"), str):
            return value["path"]
        if key.endswith("_path") and isinstance(value, str) and value:
            return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", choices=("checkpoint", "final"), default="checkpoint")
    parser.add_argument("--output")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    result_path = resolve_in_workspace(root, "external_executor/result_pack.json")
    expected_path = resolve_in_workspace(root, "external_executor/expected_outputs_schema.json")
    report: dict[str, Any] = {"valid": True, "mode": args.mode, "errors": [], "warnings": [], "checks": {}}

    try:
        result = load_json(result_path)
    except Exception as exc:
        report["errors"].append({"code": "invalid_result_pack", "message": str(exc)})
        result = {}
    try:
        expected = load_json(expected_path)
    except Exception as exc:
        report["errors"].append({"code": "invalid_expected_schema", "message": str(exc)})
        expected = {}
    manifest_paths: set[str] = set()
    try:
        manifest = load_json(resolve_in_workspace(root, "external_executor/run_manifest.json"))
        for artifact in manifest.get("artifacts", []):
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
                manifest_paths.add(artifact["path"])
    except Exception as exc:
        report["warnings"].append({"code": "manifest_unavailable", "message": str(exc)})

    if not isinstance(result, dict):
        report["errors"].append({"code": "invalid_result_type", "message": "result pack must be an object"})
        result = {}
    missing = [key for key in required_keys(expected) if key not in result]
    for key in missing:
        report["errors"].append({"code": "missing_required_key", "message": key})

    status = result.get("executor_status")
    if status not in EXECUTOR_STATUSES:
        report["errors"].append({"code": "invalid_executor_status", "message": repr(status)})
    if args.mode == "final" and status == "running":
        report["errors"].append({
            "code": "unfinished_status",
            "message": "final validation cannot leave executor_status=running",
        })

    runs = runs_from(result.get("experiment_runs"))
    formal_checked = 0
    provenance_groups = {
        "config": ("config_path", "config_ref", "config"),
        "raw_log": ("raw_log_path", "raw_log_ref", "raw_log"),
        "metric_output": ("metric_output_path", "metric_output_ref", "metric_output"),
        "split": ("dataset_split", "split", "split_ref"),
        "seed": ("seed", "seeds", "repeat_index"),
        "code": ("code_version_or_patch_id", "code_version", "code_ref", "patch_id"),
        "protocol": ("protocol_fingerprint", "protocol_ref"),
    }
    for index, run in enumerate(runs):
        if run.get("run_type") and run.get("run_type") not in RUN_TYPES:
            report["errors"].append({"code": "invalid_run_type", "message": f"run[{index}]"})
        if run.get("status") and run.get("status") not in RUN_STATUSES:
            report["errors"].append({"code": "invalid_run_status", "message": f"run[{index}]"})
        if args.mode == "final" and run.get("run_type") in {"formal", "ablation", "robustness", "efficiency"} and run.get("status") == "completed":
            formal_checked += 1
            for label, keys in provenance_groups.items():
                if not has_any(run, keys):
                    report["errors"].append({
                        "code": "formal_provenance_missing",
                        "message": f"run[{index}] missing {label}",
                    })
                    continue
                if label in {"config", "raw_log", "metric_output"}:
                    raw_path = referenced_path(run, keys)
                    if raw_path:
                        try:
                            path = resolve_in_workspace(root, raw_path)
                        except ValueError as exc:
                            report["errors"].append({"code": "formal_path_escape", "message": str(exc)})
                            continue
                        if not path.is_file():
                            report["errors"].append({"code": "formal_artifact_missing", "message": raw_path})
                            continue
                        relative = relative_to_workspace(root, path)
                        if manifest_paths and relative not in manifest_paths:
                            report["errors"].append({"code": "formal_artifact_unregistered", "message": relative})

    if args.mode == "final" and status == "completed":
        for section in ("realized_method_package", "writer_handoff"):
            value = result.get(section)
            if value in (None, {}, []):
                report["errors"].append({"code": "completed_missing_section", "message": section})
                continue
            if section_status(value) in {"not_started", "blocked", "unavailable", "stale"}:
                report["errors"].append({"code": "completed_invalid_section", "message": section})

    report["checks"] = {
        "required_keys": len(required_keys(expected)),
        "missing_keys": len(missing),
        "runs": len(runs),
        "formal_runs_checked": formal_checked,
    }
    report["valid"] = not report["errors"]
    emit_report(report, args.output)
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
