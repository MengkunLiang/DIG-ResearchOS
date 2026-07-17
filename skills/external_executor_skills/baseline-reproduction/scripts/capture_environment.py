#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from _common import SECRET_NAME_RE, canonical_hash, dump_json_atomic, find_workspace, is_within, tree_manifest, utc_now


def git_info(path: Path) -> dict:
    def run(*args: str) -> str | None:
        try:
            return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return None
    commit = run("rev-parse", "HEAD")
    if not commit:
        return {"is_git_repository": False}
    return {"is_git_repository": True, "commit": commit, "tree": run("rev-parse", "HEAD^{tree}"), "describe": run("describe", "--always", "--dirty", "--tags"), "dirty": bool(run("status", "--porcelain")), "remote": run("remote", "get-url", "origin")}


def memory_info() -> dict:
    path = Path("/proc/meminfo")
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k in {"MemTotal", "MemAvailable", "SwapTotal"}:
                out[k] = v.strip()
    return out


def gpu_info() -> list[dict]:
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=index,name,uuid,driver_version,memory.total", "--format=csv,noheader,nounits"], check=True, capture_output=True, text=True, timeout=10)
        rows = []
        for line in p.stdout.splitlines():
            vals = [x.strip() for x in line.split(",")]
            if len(vals) >= 5:
                rows.append(dict(zip(["index", "name", "uuid", "driver_version", "memory_total_mb"], vals[:5])))
        return rows
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture non-secret execution environment.")
    ap.add_argument("--path", required=True)
    ap.add_argument("--source")
    ap.add_argument("--env-name", action="append", default=[])
    ap.add_argument("--probe-gpu", action="store_true")
    args = ap.parse_args()
    output = Path(args.path).expanduser().resolve()
    source = Path(args.source).expanduser().resolve() if args.source else None
    workspace = find_workspace(output)
    if not is_within(output, workspace / "external_executor" / "raw_results"):
        raise SystemExit("Environment records must be written under external_executor/raw_results")
    if source and not is_within(source, workspace / "external_executor" / "expr"):
        raise SystemExit("Environment source must be a deployment under external_executor/expr")
    packages = sorted([{"name": d.metadata.get("Name") or d.name, "version": d.version} for d in importlib.metadata.distributions()], key=lambda x: x["name"].lower())
    env_names = sorted(set(args.env_name))
    env_record = []
    for name in env_names:
        env_record.append({"name": name, "present": name in os.environ, "value": "<redacted>" if name in os.environ else None, "secret_like": bool(SECRET_NAME_RE.search(name))})
    data = {
        "schema_version": "baseline_environment.v1",
        "captured_at": utc_now(),
        "platform": {"system": platform.system(), "release": platform.release(), "version": platform.version(), "machine": platform.machine(), "processor": platform.processor(), "python_implementation": platform.python_implementation(), "python_version": platform.python_version(), "python_executable": sys.executable},
        "hardware": {"cpu_count": os.cpu_count(), "memory": memory_info(), "gpus": gpu_info() if args.probe_gpu else []},
        "packages": packages,
        "declared_environment": env_record,
        "source_git": git_info(source) if source and source.exists() else {},
        "source_manifest_sha256": tree_manifest(source)["manifest_sha256"] if source and source.exists() else None,
    }
    data["environment_fingerprint"] = canonical_hash(data)
    dump_json_atomic(output, data)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
