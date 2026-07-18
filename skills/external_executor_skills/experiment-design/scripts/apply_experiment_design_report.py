#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import assert_write_allowed, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically update only experiment-design-owned result-pack sections.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/report/experiment_design_report.json")
    parser.add_argument("--validation", default="external_executor/report/experiment_design_report_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    result_path = ext / "result_pack.json"
    assert_write_allowed(ws, result_path)
    validation = load_json(resolve_in_workspace(ws, args.validation))
    if validation.get("status") != "pass":
        raise ValueError("experiment design report validation did not pass")
    report = load_json(resolve_in_workspace(ws, args.report))
    result = load_json(result_path)
    # Narrow ownership: these are the only two Phase C sections applied by this child.
    result["claim_evidence_matrix"] = report["claim_evidence_matrix"]
    result["experiment_plan"] = report["experiment_plan"]
    dump_json_atomic(result_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
