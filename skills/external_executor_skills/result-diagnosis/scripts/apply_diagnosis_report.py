#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import assert_write_allowed, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace
from validate_diagnosis_report import validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Narrowly apply one diagnosis to result_pack.result_diagnoses.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/result_diagnosis_report.json")
    parser.add_argument("--snapshot", default="external_executor/diagnosis_evidence_snapshot.json")
    parser.add_argument("--statistics", default="external_executor/diagnosis_statistics.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report = load_json(resolve_in_workspace(ws, args.report))
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    stats = load_json(resolve_in_workspace(ws, args.statistics))
    result_path = ws / "external_executor" / "result_pack.json"
    result = load_json(result_path)
    errors, _ = validate(report, snapshot, stats, result)
    if errors:
        for msg in errors: print(f"ERROR: {msg}")
        raise SystemExit("Refusing to apply invalid diagnosis report")
    section = result.get("result_diagnoses")
    if not isinstance(section, dict):
        section = {"status": "not_started", "items": [], "current_by_iteration": {}}
    items = section.get("items", []) if isinstance(section.get("items"), list) else []
    did = report["diagnosis_id"]
    items = [x for x in items if not (isinstance(x, dict) and x.get("diagnosis_id") == did)] + [report]
    current = section.get("current_by_iteration", {}) if isinstance(section.get("current_by_iteration"), dict) else {}
    current[str(report["iteration_id"])] = did
    section = {"status": report["status"], "items": items, "current_by_iteration": current}
    result["result_diagnoses"] = section
    assert_write_allowed(ws, result_path)
    dump_json_atomic(result_path, result)
    print(f"applied diagnosis {did} to result_pack.result_diagnoses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
