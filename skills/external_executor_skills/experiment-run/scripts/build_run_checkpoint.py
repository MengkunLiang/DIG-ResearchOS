#!/usr/bin/env python3
"""Build an atomic run-scoped checkpoint from a valid terminal record."""

from __future__ import annotations

import argparse
import json
import sys

from _common import atomic_write_json, load_json, now_utc, require_allowed, require_under_workspace_path, resolve_in_workspace, workspace_root
from validate_run_record import validate_record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        root = workspace_root(args.workspace)
        record = load_json(resolve_in_workspace(root, args.record, must_exist=True))
        validation = validate_record(root, record)
        if not validation["valid"]:
            print(json.dumps(validation, indent=2, sort_keys=True), file=sys.stderr)
            return 2
        output = resolve_in_workspace(root, args.output)
        require_allowed(root, output)
        require_under_workspace_path(root, output, "external_executor/raw_results", label="checkpoint output")
        complete = record["run_status"] == "completed"
        artifact_refs = [item for item in [record.get("raw_log_ref"), record.get("metric_output_ref"), record.get("config_ref")] if item]
        artifact_refs.extend(record.get("artifacts", []))
        checkpoint = {
            "schema_version": "external_executor_run_checkpoint.v1",
            "checkpoint_id": f"{record['run_id']}:terminal",
            "iteration_id": record["iteration_id"],
            "run_id": record["run_id"],
            "input_fingerprint": record["review"]["input_fingerprint"],
            "status": "complete" if complete else "partial",
            "run_record": record,
            "artifact_refs": artifact_refs,
            "manifest_entries": artifact_refs,
            "actual_budget": record["actual_budget"],
            "blocking_issues": [] if complete else [record.get("failure")],
            "recovery": record["recovery"],
            "root_updates": {
                "register_manifest_entries": True,
                "account_budget": True,
                "recommended_next_action": "result-diagnosis" if complete else "research-execution",
            },
            "created_at": now_utc(),
        }
        atomic_write_json(output, checkpoint)
        print(json.dumps({"checkpoint": output.relative_to(root).as_posix(), "status": checkpoint["status"]}, sort_keys=True))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
