#!/usr/bin/env python3
"""Create minimal external-executor state envelopes without overwriting existing files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import atomic_write_json, load_json, resolve_in_workspace, utc_now, workspace_root


FALLBACK_REQUIRED = [
    "schema_version", "executor_status", "context_alignment",
    "resource_requirement_matrix", "resources", "baseline_candidates",
    "dataset_inventory", "material_gaps", "resource_risks", "resource_readiness",
    "baseline_reproduction", "claim_evidence_matrix", "experiment_plan",
    "experiment_runs", "implementation_reviews", "result_diagnoses",
    "module_attributions", "iteration_decisions", "realized_method_package",
    "framework_figure", "figure_table_inventory",
]


def required_keys(expected: dict) -> list[str]:
    candidates = [
        expected.get("required"),
        expected.get("required_fields"),
        expected.get("result_pack", {}).get("required") if isinstance(expected.get("result_pack"), dict) else None,
        expected.get("result_pack_schema", {}).get("required") if isinstance(expected.get("result_pack_schema"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, list) and all(isinstance(item, str) for item in candidate):
            return candidate
    return FALLBACK_REQUIRED


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--force", action="store_true", help="Replace existing envelopes")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    external = resolve_in_workspace(root, "external_executor")
    expected_path = external / "expected_outputs_schema.json"
    if not expected_path.exists():
        print(f"missing expected schema: {expected_path}", file=sys.stderr)
        return 2
    try:
        expected = load_json(expected_path)
    except Exception as exc:
        print(f"invalid expected schema: {exc}", file=sys.stderr)
        return 2

    now = utc_now()
    status = {
        "schema_version": "external_executor_status.v1",
        "executor_status": "running",
        "current_phase": "A",
        "current_step": "A1",
        "iteration_id": None,
        "completed_checkpoints": [],
        "stale_checkpoints": [],
        "active_blockers": [],
        "budget": {},
        "iteration_loop": {"current_iteration": 0, "max_iterations": 10, "last_decision_id": None, "outcome": "not_started"},
        "input_fingerprint": None,
        "next_action": "context-alignment",
        "updated_at": now,
    }
    manifest = {
        "schema_version": "external_executor_manifest.v1",
        "input_fingerprint": None,
        "artifacts": [],
        "checkpoints": [],
        "updated_at": now,
    }
    result_pack: dict = {}
    for key in required_keys(expected):
        if key == "schema_version":
            result_pack[key] = "external_executor_result.v1"
        elif key == "executor_status":
            result_pack[key] = "running"
        else:
            result_pack[key] = {"status": "not_started", "items": [], "blocking_issues": []}
    result_pack.setdefault("iteration_plans", {"status": "not_started", "items": [], "active_iteration_id": None})

    payloads = {
        external / "executor_status.json": status,
        external / "report" / "run_manifest.json": manifest,
        external / "result_pack.json": result_pack,
    }
    for path, payload in payloads.items():
        if path.exists() and not args.force:
            continue
        atomic_write_json(path, payload)
    print("initialized external executor envelopes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
