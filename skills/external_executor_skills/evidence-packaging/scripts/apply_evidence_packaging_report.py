#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import assert_write_allowed, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically apply only evidence-packaging-owned sections to result_pack.json.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/evidence_packaging_report.json")
    parser.add_argument("--validation", default="external_executor/evidence_packaging_report_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    validation = load_json(resolve_in_workspace(ws, args.validation))
    if validation.get("status") != "pass":
        raise ValueError("Evidence packaging report validation did not pass")
    report = load_json(resolve_in_workspace(ws, args.report))
    result_path = ext / "result_pack.json"
    assert_write_allowed(ws, result_path)
    result = load_json(result_path)
    result["realized_method_package"] = report["realized_method_package"]
    result["framework_figure"] = report["framework_figure"]
    result["figure_table_inventory"] = report["figure_table_inventory"]
    result["evidence_mapping"] = report["evidence_mapping"]
    result["evidence_packaging"] = {
        "status": report["status"],
        "readiness": report["packaging_readiness"],
        "snapshot_id": report["snapshot_id"],
        "snapshot_fingerprint": report["snapshot_fingerprint"],
        "package_manifest": report["package_manifest"],
        "artifact_refs": report["artifact_refs"],
        "blocking_issues": report["blocking_issues"],
        "constraints": report["constraints"],
        "recommended_next_action": report["recommended_next_action"],
        "handoff_semantics": "pre_T7_audit_only",
    }
    dump_json_atomic(result_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
