#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import artifact_ref, assert_write_allowed, dump_json_atomic, load_json, relpath, resolve_in_workspace, resolve_workspace, stable_id, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize module attribution report.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/module_attribution_snapshot.json")
    parser.add_argument("--facts", default="external_executor/module_attribution_facts.json")
    parser.add_argument("--output", default="external_executor/module_attribution_report.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    snapshot_path = resolve_in_workspace(ws, args.snapshot)
    facts_path = resolve_in_workspace(ws, args.facts)
    output = resolve_in_workspace(ws, args.output)
    snapshot = load_json(snapshot_path)
    facts = load_json(facts_path)
    attribution_id = stable_id("ATTR", snapshot.get("iteration_id"), facts.get("input_fingerprint"))
    confounds = facts.get("confounds", {"status": "complete", "items": []})
    risks = []
    for item in confounds.get("items", []):
        if item.get("severity") in {"material", "blocking"}:
            risks.append({"risk_id": stable_id("RISK", item.get("confound_id")), "category": item.get("family"), "severity": item.get("severity"), "summary": item.get("summary"), "evidence_refs": [item.get("confound_id")], "status": "open"})
    ours_facts = [x for x in facts.get("module_facts", {}).get("items", []) if x.get("owner_method_id") == "ours"]
    baseline_facts = [x for x in facts.get("module_facts", {}).get("items", []) if x.get("owner_method_id") != "ours"]
    report = {
        "schema_version": "module_attribution_report.v1", "child_skill": "module-attribution", "status": "partial", "generated_at": utc_now(),
        "attribution_id": attribution_id, "iteration_id": snapshot.get("iteration_id"), "diagnosis_id": snapshot.get("diagnosis_id"),
        "input_fingerprint": facts.get("input_fingerprint"),
        "evidence_snapshot": {"status": snapshot.get("status"), "ref": relpath(ws, snapshot_path)},
        "module_registry": facts.get("module_registry", {"status": "partial", "items": []}),
        "mechanism_registry": facts.get("mechanism_registry", {"status": "partial", "items": []}),
        "intervention_effects": facts.get("intervention_effects", {"status": "partial", "items": []}),
        "interaction_effects": facts.get("interaction_effects", {"status": "partial", "items": []}),
        "module_attributions": {"status": "partial", "items": []},
        "mechanism_attributions": {"status": "partial", "items": []},
        "baseline_module_attributions": {"status": "partial", "items": []},
        "confounds": confounds,
        "recommendations": {"status": "partial", "items": []},
        "unsupported_questions": {"status": "partial", "items": []},
        "risks": {"status": "complete", "items": risks},
        "attribution_gate": {
            "status": "partial", "evidence_sufficiency": "limited",
            "beneficial_module_ids": [x.get("module_id") for x in ours_facts if x.get("empirical_status") == "beneficial"],
            "harmful_module_ids": [x.get("module_id") for x in ours_facts if x.get("empirical_status") == "harmful"],
            "unsupported_mechanism_ids": [x.get("mechanism_id") for x in facts.get("mechanism_facts", {}).get("items", []) if x.get("status") == "unresolved"],
            "material_confound_ids": [x.get("confound_id") for x in confounds.get("items", []) if x.get("severity") == "material"],
            "blocking_issue_ids": [x.get("confound_id") for x in confounds.get("items", []) if x.get("severity") == "blocking"],
            "recommendation_counts": {}, "next_action": "add_controlled_evidence",
        },
        "artifact_refs": [artifact_ref(ws, snapshot_path), artifact_ref(ws, facts_path)],
        "notes": [f"Deterministic facts include {len(ours_facts)} ours modules and {len(baseline_facts)} baseline modules. Complete interpretation sections before recomputing the gate."],
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, report)
    print(f"initialized {attribution_id} -> {relpath(ws, output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
