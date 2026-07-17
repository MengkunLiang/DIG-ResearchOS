#!/usr/bin/env python3
"""Execute one approved immutable experiment attempt without a shell."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from _common import (
    artifact_ref,
    atomic_write_json,
    canonical_sha256,
    expand_regular_files,
    load_json,
    now_utc,
    relative_path,
    require_allowed,
    resolve_in_workspace,
    sha256_file,
    workspace_root,
)
from capture_environment import snapshot
from validate_run_request import SECRET_PATTERN, validate_request

SAFE_BASE_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP")


def _environment(request: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    spec = request["environment"]
    child: dict[str, str] = {}
    recorded: dict[str, Any] = {"passed": {}, "injected_secret_names": []}
    for name in SAFE_BASE_ENV:
        if name in os.environ:
            child[name] = os.environ[name]
            recorded["passed"][name] = os.environ[name]
    for name in spec.get("allowed_env", []):
        if name not in os.environ:
            continue
        child[name] = os.environ[name]
        if SECRET_PATTERN.search(name):
            recorded["injected_secret_names"].append(name)
        else:
            recorded["passed"][name] = os.environ[name]
    for name, value in spec.get("overrides", {}).items():
        child[name] = value
        recorded["passed"][name] = value
    return child, recorded


def _dependency_refs(root: Path, request: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for item in request["dependencies"]:
        path = resolve_in_workspace(root, item["path"], must_exist=True)
        refs.append({**item, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    return refs


def _evidence_use(request: dict[str, Any], status: str) -> tuple[str, str]:
    if status != "completed":
        return "unsupported", "none"
    if request["data_kind"] != "real" or request["execution_level"] == "smoke":
        return "raw_result", "engineering_only"
    if request["execution_level"] == "formal" and request["analysis_role"] == "confirmatory":
        return "raw_result", "pre_audit_candidate"
    return "diagnostic_hint", "diagnostic_only"


def _existing_reusable(root: Path, record_path: Path, request_fingerprint: str) -> bool:
    from validate_run_record import validate_record

    record = load_json(record_path)
    return (
        isinstance(record, dict)
        and record.get("run_status") == "completed"
        and record.get("request_fingerprint") == request_fingerprint
        and validate_record(root, record)["valid"]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--reuse-valid", action="store_true")
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    request_path = resolve_in_workspace(root, args.request, must_exist=True)
    request = load_json(request_path)
    validation = validate_request(root, request)
    if not validation["valid"]:
        print(json.dumps(validation, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    request_fingerprint = canonical_sha256(request)
    record_path = resolve_in_workspace(root, request["run_record_path"])
    require_allowed(root, record_path)
    if record_path.exists():
        if args.reuse_valid and _existing_reusable(root, record_path, request_fingerprint):
            print(json.dumps({"run_id": request["run_id"], "status": "reused"}, sort_keys=True))
            return 0
        print("run record already exists; preserve it and use a new run ID", file=sys.stderr)
        return 2
    log_path = resolve_in_workspace(root, request["raw_log_path"])
    metric_path = resolve_in_workspace(root, request["metric_output_path"])
    config_path = resolve_in_workspace(root, request["config_ref"], must_exist=True)
    for path in (log_path, metric_path, record_path):
        require_allowed(root, path)
        path.parent.mkdir(parents=True, exist_ok=True)
    environment_snapshot = snapshot()
    child_env, environment_pass = _environment(request)
    child_env["RESEARCHOS_RAW_RESULTS_DIR"] = str(record_path.parent)
    child_env["RESEARCHOS_OUTPUT_DIR"] = str(metric_path.parent)
    started_at = now_utc()
    started = time.monotonic()
    base_record: dict[str, Any] = {
        "schema_version": "external_executor_experiment_run.v1",
        "run_id": request["run_id"],
        "experiment_id": request["experiment_id"],
        "iteration_id": request["iteration_id"],
        "request_ref": relative_path(root, request_path),
        "request_fingerprint": request_fingerprint,
        "run_type": request["run_type"],
        "execution_level": request["execution_level"],
        "analysis_role": request["analysis_role"],
        "run_status": "running",
        "evidence_level": "unsupported",
        "evidence_use": "none",
        "review": {
            "review_ref": request["review_ref"],
            "review_id": request["review_id"],
            "approved_for": load_json(resolve_in_workspace(root, request["review_ref"], must_exist=True)).get("approved_for"),
            "input_fingerprint": request["input_fingerprint"],
        },
        "protocol_fingerprint": request["protocol_fingerprint"],
        "command": request["command"],
        "cwd": request["cwd"],
        "config_ref": artifact_ref(root, config_path),
        "dataset": request["dataset"],
        "data_kind": request["data_kind"],
        "seed": request["seed"],
        "repeat_index": request["repeat_index"],
        "dependencies": _dependency_refs(root, request),
        "environment": {**environment_snapshot, "process_environment": environment_pass},
        "hardware": environment_snapshot["hardware"],
        "started_at": started_at,
        "finished_at": None,
        "duration_seconds": None,
        "exit": {"exit_code": None, "signal": None, "timed_out": False},
        "raw_log_ref": None,
        "metric_output_ref": None,
        "metrics": {},
        "artifacts": [],
        "actual_budget": {"runs": 1, "wall_clock_seconds": 0.0, "gpu_hours": 0.0, "cost": None},
        "failure": None,
        "recovery": {},
        "created_at": started_at,
    }
    atomic_write_json(record_path, base_record)
    status = "failed"
    failure: dict[str, Any] | None = None
    exit_code: int | None = None
    terminating_signal: int | None = None
    timed_out = False
    process: subprocess.Popen[bytes] | None = None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb") as log_handle:
            process = subprocess.Popen(
                request["command"], cwd=resolve_in_workspace(root, request["cwd"], must_exist=True),
                env=child_env, stdout=log_handle, stderr=subprocess.STDOUT, shell=False, start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=float(request["timeout_seconds"]))
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    exit_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    exit_code = process.wait()
                failure = {"category": "timeout", "message": "declared timeout expired"}
            except KeyboardInterrupt:
                os.killpg(process.pid, signal.SIGTERM)
                exit_code = process.wait()
                status = "cancelled"
                failure = {"category": "operator_cancelled", "message": "runner received keyboard interrupt"}
        if status != "cancelled":
            if exit_code == 0 and not timed_out:
                status = "completed"
            else:
                status = "failed"
                if failure is None:
                    failure = {"category": "nonzero_exit", "message": f"process exited {exit_code}"}
        if exit_code is not None and exit_code < 0:
            terminating_signal = -exit_code
    except FileNotFoundError as exc:
        failure = {"category": "launch_error", "message": str(exc)}
    except Exception as exc:
        failure = {"category": "environment_issue", "message": f"{type(exc).__name__}: {exc}"}

    finished_at = now_utc()
    duration = max(0.0, time.monotonic() - started)
    metrics: Any = {}
    metric_ref = None
    if metric_path.is_file():
        try:
            metrics = load_json(metric_path)
            if not isinstance(metrics, dict):
                raise ValueError("metric output must be a JSON object")
            metric_ref = artifact_ref(root, metric_path)
        except Exception as exc:
            status = "unusable"
            failure = {"category": "invalid_metric_output", "message": str(exc)}
    elif status == "completed":
        status = "unusable"
        failure = {"category": "missing_output", "message": "metric output is missing"}
    artifact_refs = []
    missing_outputs = []
    for value in request["declared_outputs"]:
        path = resolve_in_workspace(root, value)
        if not path.exists():
            missing_outputs.append(value)
            continue
        try:
            artifact_refs.extend(artifact_ref(root, item) for item in expand_regular_files(root, [value]))
        except Exception as exc:
            missing_outputs.append(f"{value}: {exc}")
    if missing_outputs and status == "completed":
        status = "unusable"
        failure = {"category": "missing_output", "message": "; ".join(missing_outputs)}
    evidence_level, evidence_use = _evidence_use(request, status)
    gpu_count = request.get("resources", {}).get("gpu_count", 0)
    actual_budget = {
        "runs": 1,
        "wall_clock_seconds": round(duration, 6),
        "gpu_hours": round(duration * float(gpu_count) / 3600.0, 9),
        "cost": None,
    }
    recovery = {
        "recoverable": status in {"failed", "cancelled", "unusable"},
        "requires_new_run_id": status != "completed",
        "requires_review": failure is not None and failure.get("category") in {"protocol_mismatch", "dependency_changed"},
        "requires_plan_change": False,
        "required_authority": [],
        "preserved_artifacts": [relative_path(root, log_path)] if log_path.exists() else [],
        "recommended_next_action": "research-execution" if status != "completed" else "result-diagnosis",
    }
    record = {
        **base_record,
        "run_status": status,
        "evidence_level": evidence_level,
        "evidence_use": evidence_use,
        "finished_at": finished_at,
        "duration_seconds": round(duration, 6),
        "exit": {"exit_code": exit_code, "signal": terminating_signal, "timed_out": timed_out},
        "raw_log_ref": artifact_ref(root, log_path) if log_path.is_file() else None,
        "metric_output_ref": metric_ref,
        "metrics": metrics,
        "artifacts": artifact_refs,
        "actual_budget": actual_budget,
        "failure": failure,
        "recovery": recovery,
    }
    atomic_write_json(record_path, record)
    print(json.dumps({"run_id": request["run_id"], "run_status": status, "record": relative_path(root, record_path)}, sort_keys=True))
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
