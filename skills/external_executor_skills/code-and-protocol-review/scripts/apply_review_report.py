#!/usr/bin/env python3
"""Atomically upsert one valid report into result_pack.implementation_reviews."""

from __future__ import annotations

import argparse
import copy
import sys

from _common import atomic_write_json, load_json, require_authorized_output, resolve_in_workspace, workspace_root
from validate_review_report import validate_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--result-pack", default="external_executor/result_pack.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    try:
        report = load_json(resolve_in_workspace(root, args.report, must_exist=True))
        result_path = resolve_in_workspace(root, args.result_pack, must_exist=True)
        require_authorized_output(root, result_path)
        result_pack = load_json(result_path)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    validation = validate_report(root, report)
    if not validation["valid"]:
        print("review report is invalid", file=sys.stderr)
        for item in validation["errors"]:
            print(f"- {item['code']}: {item['message']}", file=sys.stderr)
        return 2
    if not isinstance(result_pack, dict):
        print("result pack must be an object", file=sys.stderr)
        return 2

    before = copy.deepcopy(result_pack)
    section = result_pack.get("implementation_reviews")
    if isinstance(section, list):
        items = section
        container = None
    elif isinstance(section, dict):
        items = section.get("items", [])
        if not isinstance(items, list):
            print("implementation_reviews.items must be an array", file=sys.stderr)
            return 2
        container = section
    elif section is None:
        items = []
        container = {"status": "complete", "items": []}
    else:
        print("implementation_reviews must be an array or object", file=sys.stderr)
        return 2

    by_id = {item.get("review_id"): item for item in items if isinstance(item, dict) and item.get("review_id")}
    by_id[report["review_id"]] = report
    merged = [by_id[key] for key in sorted(by_id)]
    if container is None:
        result_pack["implementation_reviews"] = merged
    else:
        container["items"] = merged
        container["status"] = "complete"
        result_pack["implementation_reviews"] = container

    for key in before:
        if key != "implementation_reviews" and result_pack.get(key) != before.get(key):
            print(f"narrow-write violation: {key}", file=sys.stderr)
            return 2
    if args.dry_run:
        print("valid report; implementation_reviews would be updated")
        return 0
    atomic_write_json(result_path, result_pack)
    print("updated result_pack.implementation_reviews")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
