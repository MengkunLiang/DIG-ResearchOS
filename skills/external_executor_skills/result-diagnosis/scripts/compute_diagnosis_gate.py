#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import dump_json_atomic, load_json, utc_now


def substantive_items_valid(section: dict) -> bool:
    for item in section.get("items", []):
        if not item.get("evidence_refs") or item.get("confidence") not in {"high", "medium", "low", "insufficient"}:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute deterministic result diagnosis gate.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()
    path = Path(args.report).expanduser().resolve()
    report = load_json(path)
    anomalies = report.get("anomalies", {}).get("items", [])
    blocking = [x.get("anomaly_id") for x in anomalies if x.get("severity") == "blocking" and x.get("status", "open") not in {"resolved", "controlled"}]
    material = [x.get("anomaly_id") for x in anomalies if x.get("severity") == "material" and x.get("status", "open") not in {"resolved", "controlled"}]
    comparisons = report.get("method_comparisons", {}).get("items", [])
    strongest = report.get("strongest_baselines", {}).get("items", [])
    setting = report.get("setting_diagnostics", {})
    claims = report.get("claim_implications", {})
    confounds = report.get("confound_assessments", {})
    interpretations_valid = all(substantive_items_valid(s) for s in (setting, claims, confounds))
    performance = report.get("baseline_performance", {})
    change = report.get("method_change_assessment", {})
    change_ready = (
        change.get("status") == "complete"
        and bool(change.get("rationale"))
        and (not change.get("change_required") or bool(change.get("proposed_changes")))
    )
    claim_counts = {}
    for item in claims.get("items", []):
        claim_counts[item.get("status", "unknown")] = claim_counts.get(item.get("status", "unknown"), 0) + 1

    if blocking or not report.get("evidence_snapshot", {}).get("included_run_ids"):
        status = "blocked"; sufficiency = "insufficient"
        next_action = "repair_or_rerun"
    elif change.get("change_required"):
        status = "partial"; sufficiency = "limited"
        next_action = "repair_or_rerun" if change_ready else "add_diagnostic_run"
    elif not performance.get("all_required_baselines_beaten"):
        status = "partial"; sufficiency = "limited"; next_action = "repair_or_rerun"
    elif not comparisons or not strongest or not interpretations_valid:
        status = "partial"; sufficiency = "limited"; next_action = "add_diagnostic_run"
    elif material:
        status = "partial"; sufficiency = "limited"
        next_action = "continue_to_module_attribution" if setting.get("items") else "add_diagnostic_run"
    else:
        status = "ready_for_attribution"; sufficiency = "sufficient_for_attribution"; next_action = "continue_to_module_attribution"

    report["diagnosis_gate"] = {
        "status": status, "evidence_sufficiency": sufficiency,
        "material_anomaly_ids": sorted(x for x in material if x),
        "blocking_issue_ids": sorted(x for x in blocking if x),
        "claim_counts": claim_counts, "next_action": next_action,
        "all_required_baselines_beaten": bool(performance.get("all_required_baselines_beaten")),
        "method_change_required": bool(change.get("change_required")),
        "computed_at": utc_now(), "computation": "diagnosis_gate_v1",
    }
    report["status"] = "complete" if status == "ready_for_attribution" else ("blocked" if status == "blocked" else "partial")
    if args.write_back:
        dump_json_atomic(path, report)
    print(f"{status}: comparisons={len(comparisons)} strongest={len(strongest)} material={len(material)} blocking={len(blocking)}")
    return 2 if status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
