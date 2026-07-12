#!/usr/bin/env python3
"""Narrowly upsert one validated run checkpoint into result_pack.experiment_runs."""

from __future__ import annotations

import argparse
import copy
import sys

from _common import atomic_write_json, load_json, require_allowed, resolve_in_workspace, workspace_root
from validate_run_record import validate_record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--result-pack", default="external_executor/result_pack.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        root = workspace_root(args.workspace)
        checkpoint = load_json(resolve_in_workspace(root, args.checkpoint, must_exist=True))
        result_path = resolve_in_workspace(root, args.result_pack, must_exist=True)
        require_allowed(root, result_path)
        result_pack = load_json(result_path)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != "external_executor_run_checkpoint.v1":
        print("invalid checkpoint schema", file=sys.stderr)
        return 2
    record = checkpoint.get("run_record")
    validation = validate_record(root, record)
    if not validation["valid"]:
        print("checkpoint contains an invalid run record", file=sys.stderr)
        return 2
    if checkpoint.get("run_id") != record.get("run_id") or checkpoint.get("iteration_id") != record.get("iteration_id"):
        print("checkpoint identity does not match run record", file=sys.stderr)
        return 2
    if not isinstance(result_pack, dict):
        print("result pack must be an object", file=sys.stderr)
        return 2
    before = copy.deepcopy(result_pack)
    section = result_pack.get("experiment_runs")
    if isinstance(section, list):
        items = section
        container = None
    elif isinstance(section, dict):
        items = section.get("items", [])
        if not isinstance(items, list):
            print("experiment_runs.items must be an array", file=sys.stderr)
            return 2
        container = section
    elif section is None:
        items = []
        container = {"status": "partial", "items": [], "blocking_issues": []}
    else:
        print("experiment_runs must be an array or section object", file=sys.stderr)
        return 2
    by_id = {item.get("run_id"): item for item in items if isinstance(item, dict) and item.get("run_id")}
    by_id[record["run_id"]] = record
    merged = [by_id[key] for key in sorted(by_id)]
    if container is None:
        result_pack["experiment_runs"] = merged
    else:
        previous_status = container.get("status")
        container["items"] = merged
        container["status"] = "complete" if previous_status == "complete" and all(item.get("run_status") == "completed" for item in merged) else "partial"
        container["blocking_issues"] = [item.get("failure") for item in merged if item.get("run_status") != "completed" and item.get("failure")]
        result_pack["experiment_runs"] = container
    for key in before:
        if key != "experiment_runs" and result_pack.get(key) != before.get(key):
            print(f"narrow-write violation: {key}", file=sys.stderr)
            return 2
    if args.dry_run:
        print("valid checkpoint; experiment_runs would be updated")
        return 0
    atomic_write_json(result_path, result_pack)
    print("updated result_pack.experiment_runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
