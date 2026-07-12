#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import assert_write_allowed, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace
from validate_implementation_report import validate_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically apply only the implementation-owned result-pack section.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/implementation_report.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    report_path = resolve_in_workspace(workspace, args.report)
    report = load_json(report_path)
    errors, _ = validate_data(workspace, report)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit("Refusing to apply invalid implementation report")

    result_path = workspace / "external_executor" / "result_pack.json"
    assert_write_allowed(workspace, result_path)
    result = load_json(result_path)
    existing = result.get("implementations")
    if not isinstance(existing, dict):
        existing = {"status": "not_started", "active_implementation_id": None, "items": []}
    items = existing.get("items", []) if isinstance(existing.get("items"), list) else []
    filtered = [item for item in items if not isinstance(item, dict) or item.get("implementation_id") != report["implementation_id"]]
    filtered.append(report)
    gate = report.get("implementation_gate", {}).get("status")
    status = "complete" if gate == "ready_for_review" else "blocked" if gate == "blocked" else "partial"
    result["implementations"] = {
        "status": status,
        "active_implementation_id": report["implementation_id"],
        "items": filtered,
    }
    dump_json_atomic(result_path, result)
    print("applied result_pack.implementations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
