#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from _common import (
    artifact_ref,
    dump_json_atomic,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    safe_environment,
    tree_manifest,
    utc_now,
)

ALLOWED_CLASSES = {"unit", "interface", "config", "import", "shape", "serialization", "type", "lint", "build", "integration"}
ALLOWED_EXECUTABLES = {"python", "python3", "pytest", "ruff", "mypy", "pyright", "unittest", "cargo", "go", "npm", "node", "tsc"}
FORBIDDEN_TOKENS = {
    "pip", "conda", "apt", "apt-get", "brew", "curl", "wget", "git", "docker", "podman", "kubectl",
    "torchrun", "deepspeed", "accelerate", "sbatch", "srun", "mpirun", "make", "cmake",
}


def find_verification(contract: dict[str, Any], verification_id: str) -> dict[str, Any]:
    for item in contract.get("verification_plan", []):
        if item.get("verification_id") == verification_id:
            return item
    raise KeyError(f"Unknown verification ID: {verification_id}")


def validate_command(item: dict[str, Any]) -> None:
    command = item.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(value, str) and value for value in command):
        raise ValueError("Verification command must be a non-empty argv array")
    executable = Path(command[0]).name
    if executable not in ALLOWED_EXECUTABLES:
        raise ValueError(f"Executable is not allowed for implementation verification: {executable}")
    lower_tokens = {Path(token).name.lower() for token in command}
    if lower_tokens & FORBIDDEN_TOKENS:
        raise ValueError(f"Forbidden command token(s): {sorted(lower_tokens & FORBIDDEN_TOKENS)}")
    if item.get("verification_class") not in ALLOWED_CLASSES:
        raise ValueError(f"Unsupported verification class: {item.get('verification_class')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one declared bounded implementation verification.")
    parser.add_argument("--workspace")
    parser.add_argument("--contract", default="external_executor/implementation_change_contract.json")
    parser.add_argument("--verification-id", required=True)
    parser.add_argument("--phase", choices=["red", "green", "final"], required=True)
    parser.add_argument("--expect", choices=["failure", "success"], required=True)
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    contract_path = resolve_in_workspace(workspace, args.contract)
    contract = load_json(contract_path)
    item = find_verification(contract, args.verification_id)
    validate_command(item)

    root = resolve_in_workspace(workspace, contract["implementation_root"])
    worktree = root / "worktree"
    if not worktree.exists():
        raise SystemExit("Worktree does not exist; run prepare_worktree.py")
    working_directory = (worktree / str(item.get("working_directory") or ".")).resolve(strict=False)
    if not working_directory.exists() or not working_directory.is_dir():
        raise SystemExit(f"Invalid verification working directory: {working_directory}")
    try:
        working_directory.relative_to(worktree.resolve())
    except ValueError:
        raise SystemExit("Verification working directory escapes worktree")

    verification_dir = root / "verification" / item["verification_id"]
    verification_dir.mkdir(parents=True, exist_ok=True)
    record_path = verification_dir / f"{args.phase}.json"
    stdout_path = verification_dir / f"{args.phase}.stdout.log"
    stderr_path = verification_dir / f"{args.phase}.stderr.log"
    if record_path.exists():
        raise SystemExit(f"Verification record already exists: {record_path}")

    env = safe_environment(item.get("allowed_environment_keys", []))
    env["HOME"] = str(verification_dir / "home")
    env["TMPDIR"] = str(verification_dir / "tmp")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    command = item["command"]
    started_at = utc_now()
    start = time.monotonic()
    exit_code: int | None = None
    timed_out = False
    error: str | None = None
    with stdout_path.open("wb") as stdout_fh, stderr_path.open("wb") as stderr_fh:
        try:
            proc = subprocess.Popen(
                command,
                cwd=working_directory,
                env=env,
                stdout=stdout_fh,
                stderr=stderr_fh,
                shell=False,
                start_new_session=True,
            )
            try:
                exit_code = proc.wait(timeout=max(1, int(item.get("timeout_seconds", 120))))
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                exit_code = proc.returncode
        except Exception as exc:
            error = str(exc)
    duration = time.monotonic() - start
    manifest = tree_manifest(worktree)

    missing_outputs = []
    output_artifacts = []
    for expected in item.get("expected_outputs", []):
        path = (worktree / expected).resolve(strict=False)
        try:
            path.relative_to(worktree.resolve())
        except ValueError:
            missing_outputs.append({"path": expected, "reason": "path_escape"})
            continue
        if not path.exists() or not path.is_file():
            missing_outputs.append({"path": expected, "reason": "missing"})
        else:
            output_artifacts.append(artifact_ref(workspace, path, level="verification"))

    if error:
        raw_status = "error"
    elif timed_out:
        raw_status = "timed_out"
    elif exit_code == 0 and not missing_outputs:
        raw_status = "success"
    else:
        raw_status = "failure"
    expectation_met = raw_status == args.expect
    status = "passed" if expectation_met else ("timed_out" if timed_out else "failed")
    record = {
        "schema_version": "implementation_verification.v1",
        "implementation_id": contract["implementation_id"],
        "iteration_id": contract["iteration_id"],
        "verification_id": item["verification_id"],
        "name": item.get("name"),
        "verification_class": item.get("verification_class"),
        "mandatory": bool(item.get("mandatory", True)),
        "tdd_behavior_id": item.get("tdd_behavior_id"),
        "phase": args.phase,
        "expectation": args.expect,
        "status": status,
        "raw_outcome": raw_status,
        "expectation_met": expectation_met,
        "command": command,
        "working_directory": relpath(workspace, working_directory),
        "started_at": started_at,
        "ended_at": utc_now(),
        "duration_seconds": round(duration, 6),
        "exit_code": exit_code,
        "stdout_path": relpath(workspace, stdout_path),
        "stderr_path": relpath(workspace, stderr_path),
        "worktree_manifest_sha256": manifest["manifest_sha256"],
        "expected_outputs": item.get("expected_outputs", []),
        "missing_outputs": missing_outputs,
        "output_artifacts": output_artifacts,
        "environment_keys": sorted(env.keys()),
        "error": error,
        "notes": [],
    }
    dump_json_atomic(record_path, record)
    print(f"{status}: {relpath(workspace, record_path)}")
    return 0 if expectation_met else 2


if __name__ == "__main__":
    raise SystemExit(main())
