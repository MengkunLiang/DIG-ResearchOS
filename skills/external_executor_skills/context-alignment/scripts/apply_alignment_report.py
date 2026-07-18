#!/usr/bin/env python3
"""Atomically update only result_pack.context_alignment with a valid report."""

from __future__ import annotations

import argparse
import copy
import sys

from _common import atomic_write_json, is_allowed_relative, load_json, parse_allowed_entries, relative_path, resolve_in_workspace, workspace_root
from validate_alignment_report import validate_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", default="external_executor/report/phase_A/context_alignment_report.json")
    parser.add_argument("--result-pack", default="external_executor/result_pack.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    report_path = resolve_in_workspace(root, args.report, must_exist=True)
    result_path = resolve_in_workspace(root, args.result_pack, must_exist=True)
    allowed_entries, allowed_errors = parse_allowed_entries(
        root, resolve_in_workspace(root, "external_executor/allowed_paths.txt")
    )
    if allowed_errors or not allowed_entries or not is_allowed_relative(relative_path(root, result_path), allowed_entries):
        print("result-pack path is not authorized by allowed_paths.txt", file=sys.stderr)
        return 2
    try:
        report = load_json(report_path)
        result_pack = load_json(result_path)
    except Exception as exc:
        print(f"unable to load input: {exc}", file=sys.stderr)
        return 2
    validation = validate_report(report, root)
    if not validation["valid"]:
        print("alignment report is invalid", file=sys.stderr)
        for item in validation["errors"]:
            print(f"- {item['code']}: {item['message']}", file=sys.stderr)
        return 2
    if not isinstance(result_pack, dict):
        print("result pack must be an object", file=sys.stderr)
        return 2

    before = copy.deepcopy(result_pack)
    result_pack["context_alignment"] = report
    for key in before:
        if key != "context_alignment" and result_pack.get(key) != before.get(key):
            print(f"narrow-write violation: {key}", file=sys.stderr)
            return 2
    if args.dry_run:
        print("valid report; result_pack.context_alignment would be updated")
        return 0
    atomic_write_json(result_path, result_pack)
    print("updated result_pack.context_alignment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
