#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import dump_json_atomic, listify, load_json, resolve_in_workspace, resolve_workspace


def upsert(records: list, item: dict, key: str) -> list:
    output = []
    replaced = False
    for record in records:
        if isinstance(record, dict) and record.get(key) == item.get(key):
            output.append(item)
            replaced = True
        else:
            output.append(record)
    if not replaced:
        output.append(item)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Narrowly apply method-refinement records to result_pack.json.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/method_refinement_report.json")
    parser.add_argument("--validation", default="external_executor/method_refinement_report_validation.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    report = load_json(resolve_in_workspace(ws, args.report))
    validation = load_json(resolve_in_workspace(ws, args.validation))
    if validation.get("status") != "pass":
        raise ValueError("Method refinement report validation did not pass")

    result_path = ext / "result_pack.json"
    result = load_json(result_path)
    record = report["method_refinement_record"]
    result["method_refinements"] = upsert(listify(result.get("method_refinements")), record, "refinement_id")

    request = report.get("scope_change_request")
    if isinstance(request, dict) and request.get("request_id"):
        requests = listify(result.get("scope_change_requests"))
        result["scope_change_requests"] = upsert(requests, request, "request_id")

    dump_json_atomic(result_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
