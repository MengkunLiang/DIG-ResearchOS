#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import assert_write_allowed, dump_json_atomic, load_json, relpath, resolve_in_workspace, resolve_workspace, utc_now


def base_item(plan_item: dict) -> dict:
    return {
        "reproduction_id": plan_item.get("reproduction_id"), "baseline_id": plan_item.get("baseline_id"),
        "baseline_name": plan_item.get("baseline_name"), "candidate_id": plan_item.get("candidate_id"),
        "requirement_ids": plan_item.get("requirement_ids", []), "required": bool(plan_item.get("required")),
        "source_identity": plan_item.get("source", {}), "protocol_fingerprint": plan_item.get("protocol_fingerprint"),
        "fairness_fingerprint": plan_item.get("fairness_fingerprint"), "status": "planned",
        "technical_outcome": None, "comparability_status": "not_comparable", "attempts": [],
        "selected_attempt_id": None, "aggregate_metrics": [], "reference_comparisons": [],
        "repair_ids": [], "failure_ids": [],
        "review": {"review_id": None, "verdict": "needs_fix", "identity_fidelity": "unknown", "mechanism_fidelity": "unknown", "protocol_fidelity": "unknown", "fairness_risk": "unknown", "provenance_completeness": "insufficient", "approximation_level": "unknown", "findings": [], "required_fixes": [], "evidence_refs": [], "approved_for": "none"},
        "claim_risk_ids": [], "evidence_refs": [], "notes": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Initialize or resume baseline reproduction report envelope.")
    ap.add_argument("--workspace")
    ap.add_argument("--plan", default="external_executor/report/phase_D/baseline_reproduction_plan.json")
    ap.add_argument("--output", default="external_executor/report/phase_D/baseline_reproduction_report.json")
    args = ap.parse_args()
    ws = resolve_workspace(args.workspace)
    plan_path = resolve_in_workspace(ws, args.plan)
    output = resolve_in_workspace(ws, args.output)
    plan = load_json(plan_path)
    previous = load_json(output) if output.exists() else {}
    previous_by_id = {i.get("reproduction_id"): i for i in previous.get("items", []) if isinstance(i, dict)}
    items = []
    for p in plan.get("items", []):
        old = previous_by_id.get(p.get("reproduction_id"))
        if old and old.get("protocol_fingerprint") == p.get("protocol_fingerprint") and old.get("fairness_fingerprint") == p.get("fairness_fingerprint"):
            items.append(old)
        else:
            if old:
                old = dict(old); old["status"] = "stale"; old.setdefault("notes", []).append("Superseded by changed protocol/fairness/source identity")
            items.append(base_item(p))
    historical = previous.get("historical_items", [])
    current_ids = {x.get("reproduction_id") for x in items}
    for old in previous.get("items", []):
        if old.get("reproduction_id") not in current_ids:
            historical.append(old)
    payload = {
        "schema_version": "baseline_reproduction_report.v1", "child_skill": "baseline-reproduction",
        "status": "partial", "generated_at": utc_now(), "input_fingerprint": plan.get("input_fingerprint"),
        "iteration_id": plan.get("iteration_id"), "protocol_fingerprint": plan.get("protocol_fingerprint"),
        "fairness_fingerprint": plan.get("fairness_fingerprint"), "plan_ref": relpath(ws, plan_path),
        "items": items, "historical_items": historical, "repair_attempts": previous.get("repair_attempts", []),
        "failure_classifications": previous.get("failure_classifications", []), "baseline_risks": previous.get("baseline_risks", []),
        "claim_risks": previous.get("claim_risks", []), "artifact_refs": previous.get("artifact_refs", []),
        "reproduction_gate": {"status": "partial", "formal_comparison_ready": False, "reproduced_baseline_ids": [], "conditional_baseline_ids": [], "blocking_baseline_ids": [], "stale_baseline_ids": [], "blocking_issues": [], "next_action": "baseline_repair"},
        "notes": previous.get("notes", []),
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, payload)
    print(relpath(ws, output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
