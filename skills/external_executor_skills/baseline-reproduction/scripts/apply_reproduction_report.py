#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import assert_write_allowed, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace
from validate_reproduction_report import validate


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply only baseline_reproduction to result_pack.json.")
    ap.add_argument("--workspace")
    ap.add_argument("--report", default="external_executor/baseline_reproduction_report.json")
    args = ap.parse_args()
    ws = resolve_workspace(args.workspace)
    report = load_json(resolve_in_workspace(ws, args.report))
    errors, _ = validate(report)
    if errors:
        for e in errors: print(f"ERROR: {e}")
        raise SystemExit("Refusing to apply invalid report")
    result_path = ws / "external_executor" / "result_pack.json"
    assert_write_allowed(ws, result_path)
    result = load_json(result_path)
    section = {k: v for k, v in report.items() if k not in {"child_skill"}}
    result["baseline_reproduction"] = section
    dump_json_atomic(result_path, result)
    print("applied result_pack.baseline_reproduction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
