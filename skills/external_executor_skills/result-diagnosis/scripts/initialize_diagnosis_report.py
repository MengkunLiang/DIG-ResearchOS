#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import artifact_ref, assert_write_allowed, dump_json_atomic, load_json, relpath, resolve_in_workspace, resolve_workspace, stable_id, utc_now


def baseline_id(value):
    if isinstance(value, dict):
        return value.get("baseline_id") or value.get("candidate_id") or value.get("id") or value.get("name")
    return value


def summarize_baseline_performance(snapshot: dict, stats: dict) -> dict:
    reproduction = snapshot.get("baseline_reproduction") if isinstance(snapshot.get("baseline_reproduction"), dict) else {}
    reproduced = [
        str(item.get("baseline_id") or item.get("candidate_id"))
        for item in reproduction.get("items", [])
        if isinstance(item, dict)
        and item.get("required", True)
        and item.get("status") in {"reproduced", "partially_reproduced"}
        and (item.get("baseline_id") or item.get("candidate_id"))
    ]
    declared = [str(baseline_id(item)) for item in snapshot.get("required_baselines", []) if baseline_id(item)]
    required = sorted(set(reproduced or declared))
    comparisons = stats.get("method_comparisons", {}).get("items", [])
    by_baseline = {}
    for item in comparisons:
        if not isinstance(item, dict) or not item.get("baseline_method_id"):
            continue
        bid = str(item["baseline_method_id"])
        entry = by_baseline.setdefault(bid, {"baseline_id": bid, "wins": 0, "ties": 0, "losses": 0, "comparison_ids": []})
        outcome = item.get("numeric_outcome")
        if outcome == "win":
            entry["wins"] += 1
        elif outcome == "loss":
            entry["losses"] += 1
        else:
            entry["ties"] += 1
        if item.get("comparison_id"):
            entry["comparison_ids"].append(item["comparison_id"])

    if not required:
        required = sorted(by_baseline)
    items = []
    for bid in required:
        entry = by_baseline.get(bid, {"baseline_id": bid, "wins": 0, "ties": 0, "losses": 0, "comparison_ids": []})
        total = entry["wins"] + entry["ties"] + entry["losses"]
        entry["status"] = "beaten" if total and entry["wins"] == total else "not_beaten" if total else "missing"
        items.append(entry)
    beaten = [item["baseline_id"] for item in items if item["status"] == "beaten"]
    missing = [item["baseline_id"] for item in items if item["status"] == "missing"]
    lost_to = [item["baseline_id"] for item in items if item["losses"] > 0]
    total_required = len(items)
    return {
        "status": "complete" if total_required and not missing else "partial",
        "required_baseline_ids": required,
        "items": items,
        "beaten_baseline_ids": beaten,
        "missing_baseline_ids": missing,
        "lost_to_baseline_ids": lost_to,
        "all_required_baselines_beaten": bool(total_required) and len(beaten) == total_required,
        "majority_baselines_beaten": bool(total_required) and len(beaten) > total_required / 2,
        "worse_than_majority": bool(total_required) and len(lost_to) > total_required / 2,
        "comparison_surface_count": sum(item["wins"] + item["ties"] + item["losses"] for item in items),
        "evidence_refs": [ref for item in items for ref in item["comparison_ids"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize result diagnosis report from deterministic evidence.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/phase_E/diagnosis_evidence_snapshot.json")
    parser.add_argument("--statistics", default="external_executor/report/phase_E/diagnosis_statistics.json")
    parser.add_argument("--output", default="external_executor/result_diagnosis_report.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    snapshot_path = resolve_in_workspace(ws, args.snapshot)
    stats_path = resolve_in_workspace(ws, args.statistics)
    output = resolve_in_workspace(ws, args.output)
    snapshot = load_json(snapshot_path)
    stats = load_json(stats_path)
    iteration_id = str(snapshot.get("iteration_id"))
    diagnosis_id = stable_id("DIAG", iteration_id, stats.get("input_fingerprint"))
    anomaly_items = stats.get("anomalies", {}).get("items", [])
    baseline_performance = summarize_baseline_performance(snapshot, stats)
    ours_runs = [run for run in snapshot.get("runs", []) if run.get("method_role") == "ours"]
    failed_ours = [run for run in ours_runs if run.get("status") != "completed"]
    change_required = bool(failed_ours) or not baseline_performance["all_required_baselines_beaten"]
    change_kind = "implementation_debug" if failed_ours else "method_refinement" if change_required else "none"
    risks = []
    for item in anomaly_items:
        if item.get("severity") in {"material", "blocking"}:
            risks.append({"risk_id": stable_id("RISK", item.get("anomaly_id")), "category": item.get("category"), "severity": item.get("severity"), "summary": item.get("description"), "evidence_refs": [item.get("anomaly_id")], "status": "open"})
    report = {
        "schema_version": "result_diagnosis_report.v1",
        "child_skill": "result-diagnosis", "status": "partial", "generated_at": utc_now(),
        "diagnosis_id": diagnosis_id, "iteration_id": iteration_id,
        "input_fingerprint": stats.get("input_fingerprint"),
        "evidence_snapshot": {"status": snapshot.get("status"), "ref": relpath(ws, snapshot_path), "included_run_ids": snapshot.get("included_run_ids", []), "excluded_run_ids": snapshot.get("excluded_run_ids", [])},
        "metric_summaries": stats.get("metric_summaries", {"status": "partial", "items": []}),
        "method_comparisons": stats.get("method_comparisons", {"status": "partial", "items": []}),
        "strongest_baselines": stats.get("strongest_baselines", {"status": "partial", "items": []}),
        "baseline_performance": baseline_performance,
        "setting_diagnostics": {"status": "partial", "items": []},
        "anomalies": stats.get("anomalies", {"status": "complete", "items": []}),
        "confound_assessments": {"status": "partial", "items": []},
        "claim_implications": {"status": "partial", "items": []},
        "evidence_requests": {"status": "partial", "items": []},
        "risks": {"status": "complete", "items": risks},
        "method_change_assessment": {
            "status": "needs_analysis" if change_required else "complete",
            "change_required": change_required,
            "change_kind": change_kind,
            "rationale": "" if change_required else "All required baselines are beaten on every comparable surface.",
            "failure_or_underperformance_causes": [],
            "proposed_changes": [],
            "must_preserve": [],
            "prior_iteration_lessons": [],
            "evidence_refs": [run.get("evidence_id") for run in failed_ours] + baseline_performance["evidence_refs"],
        },
        "diagnosis_gate": {"status": "partial", "evidence_sufficiency": "limited", "material_anomaly_ids": [x.get("anomaly_id") for x in anomaly_items if x.get("severity") == "material"], "blocking_issue_ids": [x.get("anomaly_id") for x in anomaly_items if x.get("severity") == "blocking"], "claim_counts": {}, "next_action": "add_diagnostic_run"},
        "artifact_refs": [artifact_ref(ws, snapshot_path), artifact_ref(ws, stats_path)],
        "notes": ["Complete setting_diagnostics, confound_assessments, claim_implications, evidence_requests, then recompute the gate."],
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, report)
    print(f"initialized {diagnosis_id} -> {relpath(ws, output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
