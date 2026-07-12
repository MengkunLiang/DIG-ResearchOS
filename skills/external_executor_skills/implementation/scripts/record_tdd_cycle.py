#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import dump_json_atomic, load_json, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Link one valid red verification to one valid green verification.")
    parser.add_argument("--red", required=True)
    parser.add_argument("--green", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    red_path = Path(args.red).expanduser().resolve()
    green_path = Path(args.green).expanduser().resolve()
    red = load_json(red_path)
    green = load_json(green_path)
    errors = []
    for field in ("implementation_id", "verification_id", "tdd_behavior_id"):
        if red.get(field) != green.get(field):
            errors.append(f"{field} differs")
    if red.get("phase") != "red" or red.get("expectation") != "failure" or red.get("status") != "passed":
        errors.append("red record is not a passed expected-failure verification")
    if green.get("phase") not in {"green", "final"} or green.get("expectation") != "success" or green.get("status") != "passed":
        errors.append("green record is not a passed expected-success verification")
    if red.get("worktree_manifest_sha256") == green.get("worktree_manifest_sha256"):
        errors.append("red and green worktree fingerprints are identical; no implementation change is evidenced")

    payload = {
        "schema_version": "implementation_tdd_cycle.v1",
        "generated_at": utc_now(),
        "implementation_id": red.get("implementation_id"),
        "verification_id": red.get("verification_id"),
        "tdd_behavior_id": red.get("tdd_behavior_id"),
        "status": "pass" if not errors else "fail",
        "red_record": str(red_path),
        "green_record": str(green_path),
        "red_worktree_manifest_sha256": red.get("worktree_manifest_sha256"),
        "green_worktree_manifest_sha256": green.get("worktree_manifest_sha256"),
        "errors": errors,
    }
    dump_json_atomic(Path(args.output).expanduser().resolve(), payload)
    print(payload["status"])
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
