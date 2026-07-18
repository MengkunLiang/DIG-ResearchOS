#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import (
    active_iteration_id, assert_write_allowed, canonical_hash, dump_json_atomic,
    load_json, metric_direction, relpath, resolve_in_workspace, resolve_workspace,
    schema_major, section_items, utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate result-diagnosis prerequisites.")
    parser.add_argument("--workspace")
    parser.add_argument("--iteration-id")
    parser.add_argument("--output", default="external_executor/report/phase_E/result_diagnosis_preflight.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    blockers, warnings = [], []
    required = [ext/"AGENTS.md", ext/"allowed_paths.txt", ext/"result_pack.json", ext/"expected_outputs_schema.json"]
    for path in required:
        if not path.exists():
            blockers.append({"id": f"missing_{path.name}", "message": f"Missing {relpath(ws, path)}"})
    result = {}
    expected = {}
    try:
        result = load_json(ext/"result_pack.json")
    except Exception as exc:
        blockers.append({"id": "malformed_result_pack", "message": str(exc)})
    try:
        expected = load_json(ext/"expected_outputs_schema.json")
    except Exception as exc:
        blockers.append({"id": "malformed_expected_schema", "message": str(exc)})
    major = schema_major(result.get("schema_version")) if result else None
    if major not in (None, 1):
        blockers.append({"id": "unsupported_result_pack_major", "message": str(result.get("schema_version"))})

    alignment = result.get("context_alignment", {}) if isinstance(result, dict) else {}
    if alignment.get("status") not in {"pass", "mismatch"}:
        blockers.append({"id": "context_alignment_blocking", "message": repr(alignment.get("status"))})
    plan = result.get("experiment_plan", {}) if isinstance(result, dict) else {}
    if not isinstance(plan, dict) or plan.get("status") in {None, "not_started", "blocked", "stale"}:
        blockers.append({"id": "experiment_plan_unavailable", "message": "experiment_plan must be current and non-blocking"})
    protocol_fp = plan.get("protocol_fingerprint") or result.get("protocol_fingerprint")
    if not protocol_fp:
        blockers.append({"id": "missing_protocol_fingerprint", "message": "No protocol fingerprint found"})

    iteration_id = args.iteration_id or active_iteration_id(result)
    if not iteration_id:
        blockers.append({"id": "missing_iteration_id", "message": "Cannot identify iteration"})
    runs = section_items(result.get("experiment_runs"))
    selected = [r for r in runs if str(r.get("iteration_id")) == str(iteration_id)] if iteration_id else []
    terminal = [r for r in selected if (r.get("run_status") or r.get("status")) in {"completed", "failed", "cancelled", "stale", "unusable"}]
    if not selected:
        blockers.append({"id": "missing_iteration_runs", "message": f"No run records for iteration {iteration_id}"})
    elif not terminal:
        blockers.append({"id": "no_terminal_runs", "message": "No terminal run records are available"})

    directions = set()
    for run in selected:
        metrics = run.get("metrics") or run.get("metric_output")
        if isinstance(metrics, dict):
            for name, spec in metrics.items():
                if isinstance(spec, dict):
                    direction = metric_direction(spec.get("direction"))
                    if direction: directions.add((str(name), direction))
    if not directions:
        plan_metrics = plan.get("metrics") or plan.get("primary_metrics") or []
        for item in plan_metrics if isinstance(plan_metrics, list) else [plan_metrics]:
            if isinstance(item, dict) and item.get("name") and metric_direction(item.get("direction")):
                directions.add((str(item["name"]), metric_direction(item.get("direction"))))
    if not directions:
        warnings.append({"id": "metric_direction_not_preflight_confirmed", "message": "Metric direction must be recovered during snapshot/normalization"})

    targets = [
        output,
        ext/"report"/"phase_E"/"diagnosis_evidence_snapshot.json",
        ext/"report"/"phase_E"/"diagnosis_statistics.json",
        ext/"result_diagnosis_report.json",
        ext/"result_diagnosis",
    ]
    for target in targets:
        try:
            assert_write_allowed(ws, target)
        except Exception as exc:
            blockers.append({"id": "write_not_allowed", "message": f"{target}: {exc}"})

    payload = {
        "schema_version": "result_diagnosis_preflight.v1",
        "generated_at": utc_now(),
        "status": "blocked" if blockers else ("warning" if warnings else "pass"),
        "iteration_id": iteration_id,
        "run_count": len(selected),
        "terminal_run_count": len(terminal),
        "protocol_fingerprint": protocol_fp,
        "metric_directions_seen": [{"name": n, "direction": d} for n, d in sorted(directions)],
        "input_fingerprint": canonical_hash({"iteration_id": iteration_id, "runs": selected, "plan": plan, "claims": result.get("claim_evidence_matrix"), "reviews": result.get("implementation_reviews"), "baseline_reproduction": result.get("baseline_reproduction"), "expected": expected}),
        "blockers": blockers,
        "warnings": warnings,
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, payload)
    print(f"{payload['status']}: wrote {relpath(ws, output)}")
    return 2 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
