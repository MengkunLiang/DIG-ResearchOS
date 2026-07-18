#!/usr/bin/env python3
"""Validate run provenance, status, evidence use, and artifact integrity."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from _common import canonical_sha256, error, expand_regular_files, load_json, resolve_in_workspace, sha256_file, workspace_root
from validate_run_request import ABLATION_FIELDS, RUN_TYPES, ROLES, validate_request

STATUSES = {"completed", "failed", "cancelled", "unusable", "stale"}
EVIDENCE_USES = {"engineering_only", "diagnostic_only", "pre_audit_candidate", "none"}


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_ref(root: Path, ref: Any, label: str, errors: list[dict[str, str]]) -> None:
    if not isinstance(ref, dict):
        errors.append(error("missing_artifact_ref", label))
        return
    try:
        path = resolve_in_workspace(root, str(ref.get("path", "")), must_exist=True)
        if not path.is_file():
            raise ValueError("not a regular file")
        if sha256_file(path) != ref.get("sha256"):
            errors.append(error("artifact_checksum_mismatch", label))
        if path.stat().st_size != ref.get("size_bytes"):
            errors.append(error("artifact_size_mismatch", label))
    except Exception as exc:
        errors.append(error("invalid_artifact_ref", f"{label}: {exc}"))


def validate_record(root: Path, record: Any) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(record, dict):
        return {"valid": False, "errors": [error("invalid_type", "record must be an object")], "warnings": []}
    required = {
        "schema_version", "run_id", "experiment_id", "iteration_id", "request_ref", "request_fingerprint",
        "run_type", "execution_level", "analysis_role", "method_id", "method_role", "implementation_id",
        "run_status", "evidence_level", "evidence_use",
        "review", "protocol_fingerprint", "command", "cwd", "config_ref", "dataset", "data_kind", "seed",
        "repeat_index", "dependencies", "environment", "hardware", "started_at", "finished_at",
        "duration_seconds", "exit", "raw_log_ref", "metric_output_ref", "metrics", "artifacts", "actual_budget",
        "failure", "recovery", "created_at", "claim_ids", "variant_id", "reference_variant_id", "pair_id",
        "target_module_ids", "module_states", "intervention", "dataset_version", "split",
        "preprocessing_fingerprint", "fairness_fingerprint", "setting", "subset", "metric_directions",
    }
    for key in sorted(required - record.keys()):
        errors.append(error("missing_field", key))
    if record.get("schema_version") != "external_executor_experiment_run.v1":
        errors.append(error("unsupported_schema", str(record.get("schema_version"))))
    if record.get("run_type") not in RUN_TYPES:
        errors.append(error("invalid_run_type", str(record.get("run_type"))))
    if record.get("execution_level") not in {"smoke", "small_scale", "formal"}:
        errors.append(error("invalid_execution_level", str(record.get("execution_level"))))
    if record.get("analysis_role") not in ROLES:
        errors.append(error("invalid_analysis_role", str(record.get("analysis_role"))))
    status = record.get("run_status")
    if status not in STATUSES:
        errors.append(error("invalid_run_status", str(status)))
    if record.get("evidence_use") not in EVIDENCE_USES:
        errors.append(error("invalid_evidence_use", str(record.get("evidence_use"))))
    if record.get("run_type") == "ablation":
        for key in sorted(ABLATION_FIELDS):
            if key not in record:
                errors.append(error("missing_ablation_record_field", key))
        target_ids = record.get("target_module_ids")
        states = record.get("module_states")
        if not isinstance(target_ids, list) or not target_ids or not isinstance(states, dict) or set(states) != set(target_ids):
            errors.append(error("invalid_ablation_record_states", str(states)))
        if not record.get("pair_id") or not record.get("reference_variant_id"):
            errors.append(error("invalid_ablation_pair_identity", str(record.get("pair_id"))))
    try:
        request = load_json(resolve_in_workspace(root, str(record.get("request_ref", "")), must_exist=True))
        if canonical_sha256(request) != record.get("request_fingerprint"):
            errors.append(error("request_fingerprint_mismatch", str(record.get("run_id"))))
        request_validation = validate_request(root, request)
        for item in request_validation["errors"]:
            errors.append(error("request_no_longer_valid", f"{item['code']}: {item['message']}"))
        for field in (
            "run_id", "experiment_id", "iteration_id", "run_type", "execution_level", "analysis_role", "method_id",
            "method_role", "implementation_id", "protocol_fingerprint", "command", "cwd", "seed", "repeat_index",
            "dataset", "data_kind", "claim_ids", "variant_id", "reference_variant_id", "pair_id",
            "target_module_ids", "module_states", "intervention", "preprocessing_fingerprint",
            "fairness_fingerprint", "setting", "subset", "metric_directions",
        ):
            if request.get(field) != record.get(field):
                errors.append(error("request_record_mismatch", field))
        expected_paths = {
            "config_ref": request.get("config_ref"),
            "raw_log_ref": request.get("raw_log_path"),
            "metric_output_ref": request.get("metric_output_path"),
        }
        for ref_field, expected_path in expected_paths.items():
            ref = record.get(ref_field)
            if isinstance(ref, dict) and ref.get("path") != expected_path:
                errors.append(error("request_record_path_mismatch", ref_field))
        request_dependencies = {(item.get("kind"), item.get("path")) for item in request.get("dependencies", []) if isinstance(item, dict)}
        record_dependencies = {(item.get("kind"), item.get("path")) for item in record.get("dependencies", []) if isinstance(item, dict)}
        if request_dependencies != record_dependencies:
            errors.append(error("request_record_dependency_mismatch", "dependency identities differ"))
        if status == "completed":
            expected_outputs = {path.relative_to(root).as_posix() for path in expand_regular_files(root, request.get("declared_outputs", []))}
            recorded_outputs = {item.get("path") for item in record.get("artifacts", []) if isinstance(item, dict)}
            if not expected_outputs <= recorded_outputs:
                errors.append(error("declared_output_missing_from_record", ", ".join(sorted(expected_outputs - recorded_outputs))))
        review = load_json(resolve_in_workspace(root, str(request.get("review_ref", "")), must_exist=True))
        record_review = record.get("review", {})
        for field, expected in (
            ("review_ref", request.get("review_ref")),
            ("review_id", review.get("review_id")),
            ("approved_for", review.get("approved_for")),
            ("input_fingerprint", review.get("input_fingerprint")),
        ):
            if not isinstance(record_review, dict) or record_review.get(field) != expected:
                errors.append(error("review_record_mismatch", field))
    except Exception as exc:
        request = {}
        errors.append(error("invalid_request_ref", str(exc)))
    started = _time(record.get("started_at"))
    finished = _time(record.get("finished_at"))
    if not started or not finished or finished < started:
        errors.append(error("invalid_timestamps", "started_at and finished_at must be ordered"))
    duration = record.get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0:
        errors.append(error("invalid_duration", str(duration)))
    _check_ref(root, record.get("raw_log_ref"), "raw_log_ref", errors)
    _check_ref(root, record.get("config_ref"), "config_ref", errors)
    dependencies = record.get("dependencies")
    kinds = set()
    if not isinstance(dependencies, list) or not dependencies:
        errors.append(error("invalid_dependencies", "dependencies must be non-empty"))
    else:
        for index, item in enumerate(dependencies):
            if not isinstance(item, dict):
                errors.append(error("invalid_dependency", str(index)))
                continue
            kinds.add(item.get("kind"))
            _check_ref(root, item, f"dependency[{index}]", errors)
    for index, item in enumerate(record.get("artifacts", [])) if isinstance(record.get("artifacts"), list) else []:
        _check_ref(root, item, f"artifacts[{index}]", errors)
    actual = record.get("actual_budget")
    if not isinstance(actual, dict) or actual.get("runs") != 1:
        errors.append(error("invalid_actual_budget", "one attempt must consume one run"))
    exit_info = record.get("exit")
    if not isinstance(exit_info, dict):
        errors.append(error("invalid_exit", "exit must be an object"))
        exit_info = {}
    if status == "completed":
        if exit_info.get("exit_code") != 0 or exit_info.get("timed_out") is not False:
            errors.append(error("completed_with_bad_exit", str(exit_info)))
        if record.get("failure") is not None:
            errors.append(error("completed_with_failure", str(record.get("failure"))))
        _check_ref(root, record.get("metric_output_ref"), "metric_output_ref", errors)
        if not isinstance(record.get("metrics"), dict):
            errors.append(error("invalid_metrics", "completed metrics must be an object"))
        if record.get("evidence_use") == "none":
            errors.append(error("completed_without_evidence_use", "completed record cannot use none"))
    else:
        if not isinstance(record.get("failure"), dict) and status != "stale":
            errors.append(error("missing_failure", status))
        recovery = record.get("recovery")
        if not isinstance(recovery, dict) or not recovery.get("requires_new_run_id"):
            errors.append(error("missing_recovery", status))
        if record.get("evidence_use") != "none":
            errors.append(error("terminal_failure_has_evidence_use", str(record.get("evidence_use"))))
    formal_candidate = record.get("evidence_use") == "pre_audit_candidate"
    if formal_candidate:
        review = record.get("review", {})
        dataset = record.get("dataset", {})
        env = record.get("environment", {})
        hardware = record.get("hardware", {})
        if status != "completed" or record.get("execution_level") != "formal" or record.get("analysis_role") != "confirmatory" or record.get("data_kind") != "real":
            errors.append(error("invalid_pre_audit_candidate", "candidate must be completed formal confirmatory real-data run"))
        if review.get("approved_for") != "formal":
            errors.append(error("formal_approval_missing", str(review.get("approved_for"))))
        if not ({"code"} <= kinds and {"dataset", "resource"} & kinds and {"metric", "evaluator"} & kinds):
            errors.append(error("formal_provenance_incomplete", "dependency kinds"))
        if not isinstance(dataset, dict) or not all(dataset.get(key) not in (None, "") for key in ("id", "version", "split")):
            errors.append(error("formal_dataset_identity_incomplete", str(dataset)))
        if not env.get("environment_fingerprint") or not hardware.get("hardware_fingerprint"):
            errors.append(error("formal_environment_identity_incomplete", "environment/hardware fingerprint"))
    if record.get("evidence_use") == "pre_audit_candidate" and (record.get("execution_level") != "formal" or record.get("data_kind") != "real"):
        errors.append(error("evidence_promotion", "non-formal or non-real run cannot be a pre-audit candidate"))
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--record", required=True)
    args = parser.parse_args()
    try:
        root = workspace_root(args.workspace)
        record = load_json(resolve_in_workspace(root, args.record, must_exist=True))
        result = validate_record(root, record)
    except Exception as exc:
        result = {"valid": False, "errors": [error("invalid_input", str(exc))], "warnings": []}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
