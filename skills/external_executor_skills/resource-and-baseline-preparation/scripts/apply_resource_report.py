#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import assert_write_allowed, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace
from validate_resource_report import validate_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically apply only Phase B sections to result_pack.json.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/report/phase_B/resource_preparation_report.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    report_path = resolve_in_workspace(workspace, args.report)
    report = load_json(report_path)
    errors, warnings = validate_data(report)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit("Refusing to apply invalid report")

    result_path = workspace / "external_executor" / "result_pack.json"
    assert_write_allowed(workspace, result_path)
    result = load_json(result_path)
    resources_status = report.get("resource_readiness", {}).get("status", "blocked")
    result["resource_requirement_matrix"] = report["resource_requirement_matrix"]
    result["resources"] = {
        "status": resources_status,
        "policy_snapshot": report["policy_snapshot"],
        "local_inventory": report["local_inventory"],
        "remote_search_records": report["remote_search_records"],
        "staged_resources": report["staged_resources"],
        "acquired_resources": report["acquired_resources"],
        "reimplementations": report["reimplementations"],
        "resource_source_report": report["resource_source_report"],
        "resource_reviews": report["resource_reviews"],
        "artifact_refs": report["artifact_refs"],
    }
    result["baseline_candidates"] = report["baseline_candidates"]
    result["dataset_inventory"] = report["dataset_inventory"]
    result["material_gaps"] = report["material_gaps"]
    result["resource_risks"] = report["resource_risks"]
    result["resource_readiness"] = report["resource_readiness"]
    dump_json_atomic(result_path, result)
    print("applied Phase B sections to external_executor/result_pack.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
