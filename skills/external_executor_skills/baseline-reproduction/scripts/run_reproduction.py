#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from _common import assert_write_allowed, dump_json_atomic, is_within, load_json, relpath, resolve_in_workspace, resolve_workspace, safe_env, sha256_file, stable_id, tree_manifest, utc_now

DEFAULT_EXECUTABLES = {"python", "python3", Path(sys.executable).name, "torchrun", "accelerate"}


def resource_preexec(memory_mb: int | None, cpu_seconds: int | None):
    def apply() -> None:
        os.setsid()
        if memory_mb:
            limit = int(memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        if cpu_seconds:
            resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_seconds), int(cpu_seconds)))
    return apply


def find_attempt(ws: Path, item: dict, attempt: int) -> Path:
    base = ws / "external_executor" / "expr" / "baselines"
    matches = list(base.glob(f"*/{item['reproduction_id']}/attempt-{attempt}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one attempt directory, found {len(matches)}")
    return matches[0].resolve()


def result_dir_for(ws: Path, attempt_dir: Path, fragment: dict) -> Path:
    value = fragment.get("result_dir")
    if value:
        return resolve_in_workspace(ws, str(value))
    rel = attempt_dir.relative_to(ws / "external_executor" / "expr" / "baselines")
    return (ws / "external_executor" / "raw_results" / "baseline_reproduction" / rel).resolve(strict=False)


def evidence_dir_for(ws: Path, attempt_dir: Path, fragment: dict) -> Path:
    value = fragment.get("evidence_dir")
    if value:
        return resolve_in_workspace(ws, str(value))
    rel = attempt_dir.relative_to(ws / "external_executor" / "expr" / "baselines")
    return (ws / "external_executor" / "report" / "baseline_reproduction" / rel).resolve(strict=False)


def stage_output(ws: Path, source: Path, target_root: Path, rel: str) -> dict:
    target = (target_root / rel).resolve(strict=False)
    if not is_within(target, target_root):
        return {"path": rel, "exists": False, "type": None, "reason": "target_escape"}
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        shutil.copy2(source, target)
        return {"path": relpath(ws, target), "sha256": sha256_file(target), "size_bytes": target.stat().st_size}
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=False)
        return {"path": relpath(ws, target), "type": "directory"}
    return {"path": rel, "exists": False, "type": None}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one authorized baseline reproduction command.")
    ap.add_argument("--workspace")
    ap.add_argument("--plan", default="external_executor/report/baseline_reproduction_plan.json")
    ap.add_argument("--reproduction-id", required=True)
    ap.add_argument("--attempt", type=int, required=True)
    ap.add_argument("--allow-executable", action="append", default=[])
    args = ap.parse_args()
    ws = resolve_workspace(args.workspace)
    plan = load_json(resolve_in_workspace(ws, args.plan))
    item = next((x for x in plan.get("items", []) if x.get("reproduction_id") == args.reproduction_id), None)
    if not item:
        raise SystemExit("Unknown reproduction ID")
    execution = item.get("execution", {})
    if not execution.get("authorized"):
        raise SystemExit("Plan item execution.authorized is false")
    argv = execution.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise SystemExit("execution.argv must be a non-empty string array")
    attempt_dir = find_attempt(ws, item, args.attempt)
    assert_write_allowed(ws, attempt_dir)
    fragment = load_json(attempt_dir / "plan_fragment.json")
    result_dir = result_dir_for(ws, attempt_dir, fragment)
    evidence_dir = evidence_dir_for(ws, attempt_dir, fragment)
    if not is_within(result_dir, ws / "external_executor" / "raw_results"):
        raise SystemExit(f"Result directory must be under external_executor/raw_results: {result_dir}")
    if not is_within(evidence_dir, ws / "external_executor" / "report"):
        raise SystemExit(f"Evidence directory must be under external_executor/report: {evidence_dir}")
    assert_write_allowed(ws, result_dir)
    assert_write_allowed(ws, evidence_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "outputs").mkdir(exist_ok=True)
    work_rel = execution.get("working_directory", ".")
    workdir = (attempt_dir / "source" / work_rel).resolve(strict=False)
    if not is_within(workdir, (attempt_dir / "source").resolve()) or not workdir.exists():
        raise SystemExit(f"Invalid working directory: {workdir}")

    executable = argv[0]
    exe_name = Path(executable).name
    allowed = set(execution.get("allowed_executables", [])) | DEFAULT_EXECUTABLES | set(args.allow_executable)
    resolved_exe = shutil.which(executable) if not Path(executable).is_absolute() else executable
    inside_attempt = False
    if resolved_exe:
        try:
            inside_attempt = Path(resolved_exe).resolve().is_relative_to(attempt_dir)
        except AttributeError:
            inside_attempt = is_within(Path(resolved_exe).resolve(), attempt_dir)
    if exe_name not in allowed and executable not in allowed and not inside_attempt:
        raise SystemExit(f"Executable not allowed: {executable}; allowed={sorted(allowed)}")

    env, redacted = safe_env(os.environ, execution.get("allowed_env_names", []), execution.get("env_overrides", {}))
    env["RESEARCHOS_ATTEMPT_DIR"] = str(attempt_dir)
    env["RESEARCHOS_DEPLOYMENT_DIR"] = str(attempt_dir)
    env["RESEARCHOS_RAW_RESULTS_DIR"] = str(result_dir)
    env["RESEARCHOS_EVIDENCE_DIR"] = str(evidence_dir)
    env["RESEARCHOS_OUTPUT_DIR"] = str(result_dir / "outputs")
    stdout_path = result_dir / "stdout.log"
    stderr_path = result_dir / "stderr.log"
    record_path = evidence_dir / "run_record.json"
    if record_path.exists():
        raise SystemExit("run_record.json already exists; create a new attempt instead of overwriting")

    start_wall = time.time()
    start_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    started_at = utc_now()
    status = "failed"
    exit_code = None
    termination_signal = None
    timed_out = False
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        memory_limit = execution.get("memory_limit_mb")
        cpu_limit = execution.get("cpu_time_limit_seconds")
        popen_kwargs = {
            "cwd": workdir,
            "env": env,
            "stdout": out,
            "stderr": err,
            "stdin": subprocess.DEVNULL,
        }
        if memory_limit or cpu_limit:
            popen_kwargs["preexec_fn"] = resource_preexec(memory_limit, cpu_limit)
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **popen_kwargs)
        try:
            exit_code = proc.wait(timeout=int(execution.get("timeout_seconds", 3600)))
            status = "completed" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired:
            timed_out = True
            status = "timed_out"
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
                proc.wait()
            exit_code = proc.returncode
        if exit_code is not None and exit_code < 0:
            termination_signal = -exit_code

    end_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    finished_at = utc_now()
    expected_checks = []
    produced = []
    for rel in execution.get("expected_outputs", []):
        raw_output = (result_dir / "outputs" / rel).resolve(strict=False)
        direct_raw_output = (result_dir / rel).resolve(strict=False)
        work_output = (workdir / rel).resolve(strict=False)
        valid_raw = is_within(raw_output, result_dir) and raw_output.exists()
        valid_direct_raw = is_within(direct_raw_output, result_dir) and direct_raw_output.exists()
        valid_work = is_within(work_output, workdir) and work_output.exists()
        valid = valid_raw or valid_direct_raw or valid_work
        stored_under_raw = bool(valid_raw or valid_direct_raw)
        staged = None
        if not valid_raw and not valid_direct_raw and valid_work:
            staged = stage_output(ws, work_output, result_dir / "outputs", rel)
            stored_under_raw = True
        expected_checks.append({
            "path": rel,
            "exists": bool(valid),
            "type": "directory" if valid and (raw_output if valid_raw else direct_raw_output if valid_direct_raw else work_output).is_dir() else "file" if valid else None,
            "stored_under_raw_results": stored_under_raw,
        })
        if valid_raw and raw_output.is_file():
            produced.append({"path": relpath(ws, raw_output), "sha256": sha256_file(raw_output), "size_bytes": raw_output.stat().st_size})
        elif valid_direct_raw and direct_raw_output.is_file():
            produced.append({"path": relpath(ws, direct_raw_output), "sha256": sha256_file(direct_raw_output), "size_bytes": direct_raw_output.stat().st_size})
        elif staged:
            staged["staged_from"] = relpath(ws, work_output)
            produced.append(staged)
    provenance = load_json(attempt_dir / "attempt_provenance.json")
    executed_source_manifest = tree_manifest(attempt_dir / "source")
    executed_config_manifest = tree_manifest(attempt_dir / "configs")
    run_id = stable_id("RUN", item["reproduction_id"], args.attempt, started_at)
    record = {
        "schema_version": "baseline_run_record.v1",
        "run_id": run_id,
        "reproduction_id": item["reproduction_id"],
        "baseline_id": item.get("baseline_id"),
        "candidate_id": item.get("candidate_id"),
        "attempt": args.attempt,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(time.time() - start_wall, 6),
        "argv": argv,
        "command_display": " ".join(subprocess.list2cmdline([x]) for x in argv),
        "working_directory": relpath(ws, workdir),
        "deployment_dir": relpath(ws, attempt_dir),
        "result_dir": relpath(ws, result_dir),
        "evidence_dir": relpath(ws, evidence_dir),
        "exit_code": exit_code,
        "termination_signal": termination_signal,
        "timed_out": timed_out,
        "timeout_seconds": int(execution.get("timeout_seconds", 3600)),
        "resource_limits": {"memory_limit_mb": execution.get("memory_limit_mb"), "cpu_time_limit_seconds": execution.get("cpu_time_limit_seconds")},
        "resource_usage": {"user_cpu_seconds": round(end_cpu.ru_utime - start_cpu.ru_utime, 6), "system_cpu_seconds": round(end_cpu.ru_stime - start_cpu.ru_stime, 6), "max_rss": end_cpu.ru_maxrss},
        "protocol_fingerprint": item.get("protocol_fingerprint"),
        "fairness_fingerprint": item.get("fairness_fingerprint"),
        "source_manifest_sha256": executed_source_manifest["manifest_sha256"],
        "config_manifest_sha256": executed_config_manifest["manifest_sha256"],
        "prepared_source_manifest_sha256": provenance.get("source_manifest_sha256"),
        "prepared_config_manifest_sha256": provenance.get("config_manifest_sha256"),
        "dataset": item.get("dataset", {}),
        "seeds": item.get("seeds", []),
        "repeats": item.get("repeats", 1),
        "stdout_path": relpath(ws, stdout_path),
        "stderr_path": relpath(ws, stderr_path),
        "environment_path": relpath(ws, evidence_dir / "environment.json") if (evidence_dir / "environment.json").exists() else None,
        "expected_outputs": execution.get("expected_outputs", []),
        "output_checks": expected_checks,
        "produced_artifacts": produced,
        "redacted_environment_names": redacted,
        "repository_content_executed": True,
        "network_isolation_enforced": False,
        "notes": ["The runner sanitizes environment variables but does not itself provide OS-level network isolation."],
    }
    dump_json_atomic(record_path, record)
    print(f"{status}: {relpath(ws, record_path)}")
    return 0 if status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
