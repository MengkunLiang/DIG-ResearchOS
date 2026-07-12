#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import load_json, resolve_in_workspace, resolve_workspace

TOP = {"schema_version", "child_skill", "status", "generated_at", "input_fingerprint", "iteration_id", "protocol_fingerprint", "fairness_fingerprint", "plan_ref", "items", "repair_attempts", "failure_classifications", "baseline_risks", "claim_risks", "artifact_refs", "reproduction_gate", "notes"}
ITEM_STATUS = {"planned", "running", "reproduced", "partially_reproduced", "executable_only", "failed", "unavailable", "blocked", "stale"}
OUTCOMES = {None, "reproduced_within_tolerance", "reproduced_directionally", "partially_reproduced", "executable_only", "failed", "unavailable", "blocked"}
COMPARABILITY = {"formal_review_candidate", "conditional_comparison_only", "smoke_only", "not_comparable"}
CHILD = {"complete", "partial", "blocked", "failed"}


def validate(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    missing = sorted(TOP - set(data))
    if missing: errors.append(f"missing top-level keys: {missing}")
    if data.get("schema_version") != "baseline_reproduction_report.v1": errors.append("invalid schema_version")
    if data.get("child_skill") != "baseline-reproduction": errors.append("child_skill mismatch")
    if data.get("status") not in CHILD: errors.append("invalid child status")
    if not isinstance(data.get("items"), list): errors.append("items must be list"); return errors, warnings
    ids, baselines = set(), set()
    for item in data.get("items", []):
        rid = item.get("reproduction_id")
        if not rid: errors.append("item missing reproduction_id")
        elif rid in ids: errors.append(f"duplicate reproduction_id: {rid}")
        ids.add(rid)
        bid = item.get("baseline_id")
        if not bid: errors.append(f"item {rid} missing baseline_id")
        baselines.add(bid)
        if item.get("status") not in ITEM_STATUS: errors.append(f"invalid item status for {rid}: {item.get('status')}")
        if item.get("technical_outcome") not in OUTCOMES: errors.append(f"invalid technical outcome for {rid}")
        if item.get("comparability_status") not in COMPARABILITY: errors.append(f"invalid comparability for {rid}")
        review = item.get("review")
        if not isinstance(review, dict): errors.append(f"item {rid} missing review"); continue
        if review.get("verdict") not in {"pass", "needs_fix", "blocked"}: errors.append(f"invalid review verdict for {rid}")
        if review.get("approved_for") not in {"formal_review_candidate", "conditional_comparison_only", "smoke_only", "none"}: errors.append(f"invalid approval for {rid}")
        if review.get("approved_for") == "formal_review_candidate" and review.get("verdict") != "pass": errors.append(f"formal review candidate without pass: {rid}")
        if item.get("status") == "reproduced" and not (review.get("verdict") == "pass" and item.get("comparability_status") == "formal_review_candidate"):
            errors.append(f"reproduced item lacks passing formal-review-candidate evidence: {rid}")
        if item.get("comparability_status") == "formal_review_candidate" and not item.get("attempts"):
            errors.append(f"formal review candidate has no attempts: {rid}")
        if item.get("selected_attempt_id") and item.get("selected_attempt_id") not in {a.get("attempt_id") or a.get("run_id") for a in item.get("attempts", []) if isinstance(a, dict)}:
            errors.append(f"selected attempt not present: {rid}")
    gate = data.get("reproduction_gate")
    if not isinstance(gate, dict): errors.append("reproduction_gate must be object"); return errors, warnings
    if gate.get("status") not in {"pass", "partial", "blocked"}: errors.append("invalid gate status")
    for field in ("reproduced_baseline_ids", "conditional_baseline_ids", "blocking_baseline_ids", "stale_baseline_ids"):
        if not isinstance(gate.get(field), list): errors.append(f"{field} must be list")
        else:
            unknown = set(gate[field]) - baselines
            if unknown: errors.append(f"unknown baseline IDs in {field}: {sorted(unknown)}")
    if gate.get("status") == "pass":
        if not gate.get("formal_comparison_ready"): errors.append("pass requires formal_comparison_ready")
        if gate.get("conditional_baseline_ids") or gate.get("blocking_baseline_ids") or gate.get("stale_baseline_ids"): errors.append("pass cannot have conditional/blocking/stale IDs")
        required = {i.get("baseline_id") for i in data.get("items", []) if i.get("required")}
        if not required.issubset(set(gate.get("reproduced_baseline_ids", []))): errors.append("pass does not cover all required baselines")
    if gate.get("status") == "partial" and gate.get("formal_comparison_ready"): errors.append("partial cannot be formal_comparison_ready")
    if gate.get("status") == "blocked" and not (gate.get("blocking_baseline_ids") or gate.get("blocking_issues")): errors.append("blocked needs blockers")
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate baseline reproduction report.")
    ap.add_argument("--workspace")
    ap.add_argument("--report", default="external_executor/baseline_reproduction_report.json")
    args = ap.parse_args()
    ws = resolve_workspace(args.workspace)
    data = load_json(resolve_in_workspace(ws, args.report))
    errors, warnings = validate(data)
    for x in warnings: print(f"WARNING: {x}")
    for x in errors: print(f"ERROR: {x}")
    print(f"validation: {len(errors)} errors, {len(warnings)} warnings")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
