#!/usr/bin/env python3
"""Validate freshness, provenance, logs, and outcomes of review evidence."""

from __future__ import annotations

import argparse
from typing import Any

from _common import emit, load_json, parse_time, resolve_in_workspace, workspace_root


EVIDENCE_TYPES = {
    "static_inspection", "unit_test", "integration_test", "protocol_comparison",
    "config_validation", "data_integrity_check", "smoke_run", "manual_review",
}
RESULTS = {"pass", "fail", "inconclusive"}


def validate_bundle(root, snapshot: dict[str, Any], bundle: Any) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(bundle, dict) or not isinstance(bundle.get("items"), list):
        return {"valid": False, "errors": [{"code": "invalid_bundle", "message": "evidence bundle requires an items array"}], "warnings": []}
    fingerprint = snapshot.get("input_fingerprint")
    try:
        snapshot_time = parse_time(snapshot["created_at"])
    except Exception:
        snapshot_time = None
        errors.append({"code": "invalid_snapshot_time", "message": repr(snapshot.get("created_at"))})
    seen: set[str] = set()
    for index, item in enumerate(bundle["items"]):
        if not isinstance(item, dict):
            errors.append({"code": "invalid_evidence", "message": f"items[{index}]"})
            continue
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id or evidence_id in seen:
            errors.append({"code": "invalid_evidence_id", "message": f"items[{index}]"})
        else:
            seen.add(evidence_id)
        if item.get("evidence_type") not in EVIDENCE_TYPES:
            errors.append({"code": "invalid_evidence_type", "message": str(evidence_id)})
        if item.get("result") not in RESULTS:
            errors.append({"code": "invalid_evidence_result", "message": str(evidence_id)})
        if item.get("input_fingerprint") != fingerprint:
            errors.append({"code": "stale_evidence_fingerprint", "message": str(evidence_id)})
        for key in ("purpose", "command_or_check", "started_at", "finished_at", "log_ref", "scope"):
            if item.get(key) in (None, "", []):
                errors.append({"code": "missing_evidence_field", "message": f"{evidence_id}.{key}"})
        try:
            started = parse_time(item["started_at"])
            finished = parse_time(item["finished_at"])
            if started > finished:
                errors.append({"code": "invalid_evidence_time_order", "message": str(evidence_id)})
            if snapshot_time and started < snapshot_time:
                errors.append({"code": "evidence_predates_snapshot", "message": str(evidence_id)})
        except Exception:
            errors.append({"code": "invalid_evidence_time", "message": str(evidence_id)})
        log_ref = item.get("log_ref")
        if isinstance(log_ref, str):
            try:
                log_path = resolve_in_workspace(root, log_ref)
                if not log_path.is_file():
                    errors.append({"code": "missing_evidence_log", "message": str(evidence_id)})
            except ValueError as exc:
                errors.append({"code": "evidence_log_escape", "message": str(exc)})
        exit_code = item.get("exit_code")
        if not isinstance(exit_code, int):
            errors.append({"code": "invalid_exit_code", "message": str(evidence_id)})
        elif item.get("result") == "pass" and exit_code != 0:
            errors.append({"code": "pass_with_nonzero_exit", "message": str(evidence_id)})
        elif item.get("result") == "fail" and exit_code == 0:
            warnings.append({"code": "fail_with_zero_exit", "message": str(evidence_id)})
    return {"valid": not errors, "errors": errors, "warnings": warnings, "evidence_ids": sorted(seen)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    try:
        snapshot = load_json(resolve_in_workspace(root, args.snapshot, must_exist=True))
        bundle = load_json(resolve_in_workspace(root, args.evidence, must_exist=True))
    except Exception as exc:
        result = {"valid": False, "errors": [{"code": "invalid_input", "message": str(exc)}], "warnings": []}
    else:
        result = validate_bundle(root, snapshot, bundle)
    emit(result)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
