#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import collect_known_ids, load_json, resolve_in_workspace, resolve_workspace

TOP = {"schema_version", "child_skill", "status", "generated_at", "diagnosis_id", "iteration_id", "input_fingerprint", "evidence_snapshot", "metric_summaries", "method_comparisons", "strongest_baselines", "baseline_performance", "setting_diagnostics", "anomalies", "confound_assessments", "claim_implications", "evidence_requests", "risks", "method_change_assessment", "diagnosis_gate", "artifact_refs", "notes"}
SECTION_STATUS = {"complete", "partial", "blocked", "stale", "not_started", "unavailable"}
CONFIDENCE = {"high", "medium", "low", "insufficient"}
INTERPRETATION = {"observed_fact", "descriptive_inference", "plausible_hypothesis", "unsupported"}
CLAIM_STATUS = {"supported", "weakened", "contradicted", "unresolved", "not_tested"}
GATES = {"ready_for_attribution", "partial", "blocked"}


def validate(data: dict[str, Any], snapshot: dict[str, Any], stats: dict[str, Any], result: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    missing = TOP - set(data)
    if missing: errors.append(f"missing top-level keys: {sorted(missing)}")
    if data.get("schema_version") != "result_diagnosis_report.v1": errors.append("schema_version must be result_diagnosis_report.v1")
    if data.get("child_skill") != "result-diagnosis": errors.append("child_skill mismatch")
    if data.get("status") not in {"complete", "partial", "blocked", "failed"}: errors.append("invalid status")
    if str(data.get("iteration_id")) != str(snapshot.get("iteration_id")): errors.append("iteration_id does not match snapshot")
    if data.get("input_fingerprint") != stats.get("input_fingerprint"): errors.append("input_fingerprint does not match statistics")

    sections = ("metric_summaries", "method_comparisons", "strongest_baselines", "setting_diagnostics", "anomalies", "confound_assessments", "claim_implications", "evidence_requests", "risks")
    for name in sections:
        sec = data.get(name)
        if not isinstance(sec, dict): errors.append(f"{name} must be object"); continue
        if sec.get("status") not in SECTION_STATUS: errors.append(f"invalid {name}.status")
        if not isinstance(sec.get("items", []), list): errors.append(f"{name}.items must be list")

    known = collect_known_ids(snapshot, stats, result)
    # Add IDs directly carried by the report's deterministic sections.
    for name in ("metric_summaries", "method_comparisons", "strongest_baselines", "anomalies"):
        for item in data.get(name, {}).get("items", []):
            for key in ("aggregate_id", "comparison_id", "strongest_baseline_id", "anomaly_id"):
                if item.get(key): known.add(str(item[key]))

    for name in ("setting_diagnostics", "confound_assessments", "claim_implications"):
        for index, item in enumerate(data.get(name, {}).get("items", [])):
            if item.get("confidence") not in CONFIDENCE: errors.append(f"{name}[{index}] invalid confidence")
            refs = item.get("evidence_refs", [])
            if not refs: errors.append(f"{name}[{index}] requires evidence_refs")
            unknown = [str(x) for x in refs if str(x) not in known]
            if unknown: errors.append(f"{name}[{index}] unknown evidence refs: {unknown}")
            if item.get("causal_claim") is True: errors.append(f"{name}[{index}] causal_claim is forbidden")
            if name == "setting_diagnostics" and item.get("interpretation_level") not in INTERPRETATION:
                errors.append(f"setting_diagnostics[{index}] invalid interpretation_level")
            if name == "claim_implications" and item.get("status") not in CLAIM_STATUS:
                errors.append(f"claim_implications[{index}] invalid status")

    gate = data.get("diagnosis_gate", {})
    if gate.get("status") not in GATES: errors.append("invalid diagnosis_gate.status")
    if gate.get("next_action") not in {"continue_to_module_attribution", "add_diagnostic_run", "repair_or_rerun", "human_review", "stop_and_report"}: errors.append("invalid diagnosis_gate.next_action")
    if gate.get("status") == "ready_for_attribution" and gate.get("blocking_issue_ids"):
        errors.append("ready_for_attribution cannot have blocking issues")
    if gate.get("status") == "blocked" and not gate.get("blocking_issue_ids") and data.get("evidence_snapshot", {}).get("included_run_ids"):
        warnings.append("blocked gate has no explicit blocking issue ID")
    if data.get("status") == "complete" and gate.get("status") != "ready_for_attribution":
        errors.append("complete status requires ready_for_attribution")
    performance = data.get("baseline_performance", {})
    if not isinstance(performance, dict) or not isinstance(performance.get("items"), list):
        errors.append("baseline_performance must be an object with items")
    change = data.get("method_change_assessment", {})
    if not isinstance(change, dict):
        errors.append("method_change_assessment must be an object")
    else:
        if change.get("status") not in {"needs_analysis", "complete", "blocked"}:
            errors.append("invalid method_change_assessment.status")
        if change.get("change_kind") not in {"none", "implementation_debug", "method_refinement", "human_review"}:
            errors.append("invalid method_change_assessment.change_kind")
        if change.get("status") == "complete" and change.get("change_required"):
            if not change.get("rationale") or not change.get("proposed_changes") or not change.get("evidence_refs"):
                errors.append("completed method change assessment requires rationale, proposed_changes, and evidence_refs")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate result diagnosis report.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/result_diagnosis_report.json")
    parser.add_argument("--snapshot", default="external_executor/report/diagnosis_evidence_snapshot.json")
    parser.add_argument("--statistics", default="external_executor/report/diagnosis_statistics.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    data = load_json(resolve_in_workspace(ws, args.report))
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    stats = load_json(resolve_in_workspace(ws, args.statistics))
    result = load_json(ws / "external_executor" / "result_pack.json")
    errors, warnings = validate(data, snapshot, stats, result)
    for msg in warnings: print(f"WARNING: {msg}")
    for msg in errors: print(f"ERROR: {msg}")
    print(f"validation: {len(errors)} errors, {len(warnings)} warnings")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
