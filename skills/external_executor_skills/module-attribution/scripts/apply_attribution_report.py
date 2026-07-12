#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import assert_write_allowed, collect_known_ids, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace, utc_now
from validate_attribution_report import validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply only module_attributions to result_pack.json.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/module_attribution_report.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report_path = resolve_in_workspace(ws, args.report)
    report = load_json(report_path)
    snapshot = load_json(ws / "external_executor/module_attribution_snapshot.json")
    facts = load_json(ws / "external_executor/module_attribution_facts.json")
    result_path = ws / "external_executor/result_pack.json"
    result = load_json(result_path)
    errors = validate(report, collect_known_ids(snapshot, facts, result))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit("Refusing to apply invalid attribution report")
    section = result.get("module_attributions")
    if not isinstance(section, dict):
        section = {"status": "not_started", "items": [], "current_by_iteration": {}}
    items = section.get("items", []) if isinstance(section.get("items"), list) else []
    items = [x for x in items if x.get("attribution_id") != report.get("attribution_id")]
    items.append(report)
    current = section.get("current_by_iteration", {}) if isinstance(section.get("current_by_iteration"), dict) else {}
    current[str(report.get("iteration_id"))] = report.get("attribution_id")
    result["module_attributions"] = {
        "status": report.get("attribution_gate", {}).get("status", "partial"),
        "items": items, "current_by_iteration": current, "updated_at": utc_now(),
    }
    assert_write_allowed(ws, result_path)
    dump_json_atomic(result_path, result)
    print("applied result_pack.module_attributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
