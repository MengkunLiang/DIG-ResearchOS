#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import artifact_ref, assert_write_allowed, dump_json_atomic, load_json, relpath, resolve_in_workspace, resolve_workspace, stable_id, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize result diagnosis report from deterministic evidence.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/diagnosis_evidence_snapshot.json")
    parser.add_argument("--statistics", default="external_executor/diagnosis_statistics.json")
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
        "setting_diagnostics": {"status": "partial", "items": []},
        "anomalies": stats.get("anomalies", {"status": "complete", "items": []}),
        "confound_assessments": {"status": "partial", "items": []},
        "claim_implications": {"status": "partial", "items": []},
        "evidence_requests": {"status": "partial", "items": []},
        "risks": {"status": "complete", "items": risks},
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
