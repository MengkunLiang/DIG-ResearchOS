#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import dump_json_atomic, load_json, utc_now


def main() -> int:
    ap = argparse.ArgumentParser(description="Compute deterministic baseline reproduction gate.")
    ap.add_argument("--report", required=True)
    ap.add_argument("--write-back", action="store_true")
    args = ap.parse_args()
    path = Path(args.report).expanduser().resolve()
    report = load_json(path)
    reproduced, conditional, blocking, stale = [], [], [], []
    issues = []
    useful = False
    for item in report.get("items", []):
        bid = item.get("baseline_id")
        status = item.get("status")
        review = item.get("review", {})
        approval = review.get("approved_for")
        if status == "stale":
            stale.append(bid)
            if item.get("required"):
                conditional.append(bid)
            continue
        if status == "reproduced" and review.get("verdict") == "pass" and approval == "formal_review_candidate":
            reproduced.append(bid); useful = True
        elif status == "blocked":
            blocking.append(bid); issues.append({"baseline_id": bid, "reason": "blocked reproduction item"})
        elif status in {"partially_reproduced", "executable_only", "failed", "unavailable", "running", "planned"}:
            conditional.append(bid)
            useful = useful or status in {"partially_reproduced", "executable_only"}
            if item.get("required") and status == "unavailable" and item.get("review", {}).get("verdict") == "blocked":
                blocking.append(bid); issues.append({"baseline_id": bid, "reason": "required baseline unavailable and reviewer blocked"})
        elif item.get("required"):
            conditional.append(bid)
    blocking = sorted(set(x for x in blocking if x))
    reproduced = sorted(set(x for x in reproduced if x))
    conditional = sorted(set(x for x in conditional if x) - set(blocking) - set(reproduced))
    stale = sorted(set(x for x in stale if x))
    required_ids = {i.get("baseline_id") for i in report.get("items", []) if i.get("required")}
    all_required_reproduced = required_ids and required_ids.issubset(set(reproduced))
    if blocking:
        gate = "blocked"; next_action = "human_review"
    elif all_required_reproduced and not stale:
        gate = "pass"; next_action = "continue_to_method_refinement"
    else:
        gate = "partial"; next_action = "baseline_repair" if not useful or conditional or stale else "continue_to_implementation"
    report["reproduction_gate"] = {
        "status": gate, "formal_comparison_ready": gate == "pass",
        "reproduced_baseline_ids": reproduced, "conditional_baseline_ids": conditional,
        "blocking_baseline_ids": blocking, "stale_baseline_ids": stale,
        "blocking_issues": issues, "next_action": next_action, "computed_at": utc_now(),
        "computation": "deterministic_baseline_reproduction_gate_v1",
    }
    if report.get("status") != "failed":
        report["status"] = "blocked" if gate == "blocked" else ("complete" if all(i.get("status") not in {"planned", "running"} for i in report.get("items", [])) else "partial")
    if args.write_back:
        dump_json_atomic(path, report)
    print(f"{gate}: reproduced={len(reproduced)} conditional={len(conditional)} blocking={len(blocking)} stale={len(stale)}")
    return 2 if gate == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
