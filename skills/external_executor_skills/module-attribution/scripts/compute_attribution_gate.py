#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import dump_json_atomic, load_json, utc_now


def items_valid(section: dict) -> bool:
    for item in section.get("items", []):
        if not item.get("evidence_refs") or item.get("confidence") not in {"high", "medium", "low", "insufficient"}:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute module attribution gate.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()
    path = Path(args.report).expanduser().resolve()
    report = load_json(path)
    modules = report.get("module_attributions", {})
    mechanisms = report.get("mechanism_attributions", {})
    baseline = report.get("baseline_module_attributions", {})
    recommendations = report.get("recommendations", {})
    confounds = report.get("confounds", {}).get("items", [])
    blocking = [x.get("confound_id") for x in confounds if x.get("severity") == "blocking" and x.get("status", "open") not in {"resolved", "controlled"}]
    material = [x.get("confound_id") for x in confounds if x.get("severity") == "material" and x.get("status", "open") not in {"resolved", "controlled"}]
    valid = all(items_valid(x) for x in (modules, mechanisms, baseline, recommendations))
    module_items = modules.get("items", [])
    beneficial = [x.get("module_id") for x in module_items if x.get("empirical_status") == "beneficial"]
    harmful = [x.get("module_id") for x in module_items if x.get("empirical_status") == "harmful"]
    unsupported_mechs = [x.get("mechanism_id") for x in mechanisms.get("items", []) if x.get("status") == "unresolved"]
    counts = {}
    for item in recommendations.get("items", []):
        action = item.get("action", "unknown")
        counts[action] = counts.get(action, 0) + 1
    if blocking:
        status = "blocked"; sufficiency = "insufficient"; next_action = "human_review"
    elif not module_items:
        status = "blocked"; sufficiency = "insufficient"; next_action = "stop_and_report"
    elif not valid:
        status = "partial"; sufficiency = "limited"; next_action = "add_controlled_evidence"
    elif material or any(x.get("confidence") in {"low", "insufficient"} for x in module_items + mechanisms.get("items", [])):
        status = "partial"; sufficiency = "limited"; next_action = "add_controlled_evidence"
    else:
        status = "ready_for_iteration_decision"; sufficiency = "sufficient"; next_action = "return_for_iteration_decision"
    report["attribution_gate"] = {
        "status": status, "evidence_sufficiency": sufficiency,
        "beneficial_module_ids": sorted(set(x for x in beneficial if x)),
        "harmful_module_ids": sorted(set(x for x in harmful if x)),
        "unsupported_mechanism_ids": sorted(set(x for x in unsupported_mechs if x)),
        "material_confound_ids": sorted(set(x for x in material if x)),
        "blocking_issue_ids": sorted(set(x for x in blocking if x)),
        "recommendation_counts": counts, "next_action": next_action,
        "computed_at": utc_now(), "computation": "deterministic_attribution_gate_v1",
    }
    report["status"] = "complete" if status == "ready_for_iteration_decision" else ("blocked" if status == "blocked" else "partial")
    if args.write_back:
        dump_json_atomic(path, report)
    print(f"{status}: modules={len(module_items)} material={len(material)} blocking={len(blocking)}")
    return 2 if status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
