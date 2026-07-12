#!/usr/bin/env python3
"""Compare normalized baseline and ours protocol fields for fairness review."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from _common import atomic_write_json, load_json, require_authorized_output, resolve_in_workspace, utc_now, workspace_root


CRITICAL_FIELDS = {
    "dataset_id", "dataset_version", "split_fingerprint", "preprocessing_fingerprint",
    "metric_name", "metric_direction", "evaluation_fingerprint",
}
MAJOR_FIELDS = {
    "seed_policy", "repeat_count", "tuning_budget", "training_budget",
    "extra_data", "pretrained_source", "checkpoint_selection",
}
REQUIRED_FIELDS = CRITICAL_FIELDS | MAJOR_FIELDS
ALLOWABLE_DIFFERENCES = {
    "training_budget", "tuning_budget", "pretrained_source", "extra_data",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    output = resolve_in_workspace(root, args.output)
    try:
        require_authorized_output(root, output)
        payload = load_json(resolve_in_workspace(root, args.input, must_exist=True))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    baseline = payload.get("baseline")
    ours = payload.get("ours")
    if not isinstance(baseline, dict) or not isinstance(ours, dict):
        print("input requires baseline and ours objects", file=sys.stderr)
        return 2
    allowed_records = payload.get("allowed_differences", [])
    allowed: dict[str, str] = {}
    if isinstance(allowed_records, list):
        for item in allowed_records:
            if isinstance(item, dict) and item.get("field") in ALLOWABLE_DIFFERENCES and isinstance(item.get("rationale"), str) and item.get("rationale"):
                allowed[item["field"]] = item["rationale"]

    differences: list[dict[str, Any]] = []
    for field in sorted(REQUIRED_FIELDS):
        left = baseline.get(field)
        right = ours.get(field)
        if left in (None, "", []) or right in (None, "", []):
            differences.append({
                "field": field, "baseline": left, "ours": right,
                "severity": "blocking" if field in CRITICAL_FIELDS else "major",
                "status": "missing", "rationale": "Required normalized field is missing.",
            })
        elif left != right:
            is_allowed = field in allowed
            differences.append({
                "field": field, "baseline": left, "ours": right,
                "severity": "warning" if is_allowed else "blocking" if field in CRITICAL_FIELDS else "major",
                "status": "allowed_with_rationale" if is_allowed else "unresolved",
                "rationale": allowed.get(field, "Protocols differ without an allowed-difference rationale."),
            })

    if any(item["severity"] == "blocking" for item in differences):
        status = "blocked"
    elif any(item["severity"] == "major" for item in differences):
        status = "mismatch"
    elif differences:
        status = "warning"
    else:
        status = "pass"
    report = {
        "schema_version": "external_executor_protocol_comparison.v1",
        "status": status,
        "comparison_id": payload.get("comparison_id"),
        "input_fingerprint": payload.get("input_fingerprint"),
        "baseline_id": baseline.get("variant_id"),
        "ours_id": ours.get("variant_id"),
        "differences": differences,
        "created_at": utc_now(),
    }
    atomic_write_json(output, report)
    print(status)
    return 0 if status in {"pass", "warning"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
