#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import collect_known_ids, load_json, resolve_in_workspace, resolve_workspace

TOP = {
    "schema_version", "child_skill", "status", "generated_at", "attribution_id", "iteration_id", "diagnosis_id", "input_fingerprint",
    "evidence_snapshot", "module_registry", "mechanism_registry", "intervention_effects", "interaction_effects", "module_attributions",
    "mechanism_attributions", "baseline_module_attributions", "confounds", "recommendations", "unsupported_questions", "risks",
    "attribution_gate", "artifact_refs", "notes",
}
SECTION_STATUS = {"complete", "partial", "blocked", "stale", "unavailable", "not_started"}
CONFIDENCE = {"high", "medium", "low", "insufficient"}
EVIDENCE_TYPES = {"direct_ablation", "controlled_diagnostic", "correlational_hint", "implementation_fact", "unsupported"}
CAUSAL = {"local_intervention_effect", "mechanism_consistent", "correlational_only", "implementation_only", "unsupported"}
EMPIRICAL = {"beneficial", "neutral", "harmful", "mixed", "implementation_only", "unsupported"}
MECH_STATUS = {"supported", "consistent", "weakened", "contradicted", "unresolved"}
ACTIONS = {"keep", "modify", "drop", "narrow", "collect_evidence"}
GATES = {"ready_for_iteration_decision", "partial", "blocked"}


def validate(data: dict[str, Any], known: set[str]) -> list[str]:
    errors = []
    missing = TOP - set(data)
    if missing:
        errors.append(f"missing top-level keys: {sorted(missing)}")
    if data.get("schema_version") != "module_attribution_report.v1":
        errors.append("schema_version must be module_attribution_report.v1")
    if data.get("child_skill") != "module-attribution":
        errors.append("child_skill mismatch")
    for name in ("module_registry", "mechanism_registry", "intervention_effects", "interaction_effects", "module_attributions", "mechanism_attributions", "baseline_module_attributions", "confounds", "recommendations", "unsupported_questions", "risks"):
        sec = data.get(name)
        if not isinstance(sec, dict):
            errors.append(f"{name} must be object")
            continue
        if sec.get("status") not in SECTION_STATUS:
            errors.append(f"invalid {name}.status: {sec.get('status')}")
        if not isinstance(sec.get("items", []), list):
            errors.append(f"{name}.items must be list")
    module_ids = {str(x.get("module_id")) for x in data.get("module_registry", {}).get("items", []) if x.get("module_id")}
    mechanism_ids = {str(x.get("mechanism_id")) for x in data.get("mechanism_registry", {}).get("items", []) if x.get("mechanism_id")}
    confound_ids = {str(x.get("confound_id")) for x in data.get("confounds", {}).get("items", []) if x.get("confound_id")}
    known |= module_ids | mechanism_ids | confound_ids
    for name in ("module_attributions", "baseline_module_attributions"):
        for item in data.get(name, {}).get("items", []):
            if item.get("module_id") not in module_ids:
                errors.append(f"{name} references unknown module: {item.get('module_id')}")
            if item.get("empirical_status") not in EMPIRICAL:
                errors.append(f"invalid empirical_status: {item.get('empirical_status')}")
            if item.get("evidence_type") not in EVIDENCE_TYPES:
                errors.append(f"invalid evidence_type: {item.get('evidence_type')}")
            if item.get("causal_status") not in CAUSAL:
                errors.append(f"invalid causal_status: {item.get('causal_status')}")
            if item.get("confidence") not in CONFIDENCE:
                errors.append(f"invalid confidence: {item.get('confidence')}")
            refs = set(str(x) for x in item.get("evidence_refs", []))
            unknown = refs - known
            if unknown:
                errors.append(f"unknown evidence refs in {name}: {sorted(unknown)}")
            if not refs:
                errors.append(f"{name} item missing evidence_refs")
            if item.get("causal_status") == "local_intervention_effect" and item.get("evidence_type") not in {"direct_ablation", "controlled_diagnostic"}:
                errors.append("local_intervention_effect requires direct_ablation or controlled_diagnostic")
            if item.get("evidence_type") == "correlational_hint" and item.get("causal_status") not in {"correlational_only", "unsupported"}:
                errors.append("correlational_hint cannot support causal status")
            if item.get("evidence_type") == "implementation_fact" and item.get("causal_status") != "implementation_only":
                errors.append("implementation_fact requires implementation_only causal status")
            bad_conf = set(str(x) for x in item.get("confound_ids", [])) - confound_ids
            if bad_conf:
                errors.append(f"unknown confound ids: {sorted(bad_conf)}")
    for item in data.get("mechanism_attributions", {}).get("items", []):
        if item.get("mechanism_id") not in mechanism_ids:
            errors.append(f"unknown mechanism: {item.get('mechanism_id')}")
        if item.get("status") not in MECH_STATUS:
            errors.append(f"invalid mechanism status: {item.get('status')}")
        if item.get("evidence_type") not in EVIDENCE_TYPES or item.get("causal_status") not in CAUSAL or item.get("confidence") not in CONFIDENCE:
            errors.append(f"invalid mechanism attribution enums: {item.get('mechanism_attribution_id')}")
        refs = set(str(x) for x in item.get("evidence_refs", []))
        if not refs:
            errors.append("mechanism attribution missing evidence_refs")
        unknown = refs - known
        if unknown:
            errors.append(f"unknown evidence refs in mechanism_attributions: {sorted(unknown)}")
        if item.get("status") == "supported" and item.get("evidence_type") not in {"direct_ablation", "controlled_diagnostic"}:
            errors.append("supported mechanism requires intervention evidence")
        if item.get("evidence_type") == "correlational_hint" and item.get("causal_status") not in {"correlational_only", "unsupported"}:
            errors.append("correlational mechanism evidence cannot be causal")
    for item in data.get("recommendations", {}).get("items", []):
        if item.get("action") not in ACTIONS:
            errors.append(f"invalid recommendation action: {item.get('action')}")
        if item.get("confidence") not in CONFIDENCE:
            errors.append(f"invalid recommendation confidence: {item.get('confidence')}")
        refs = set(str(x) for x in item.get("evidence_refs", []))
        if not refs:
            errors.append("recommendation missing evidence_refs")
        unknown = refs - known
        if unknown:
            errors.append(f"unknown evidence refs in recommendations: {sorted(unknown)}")
        if item.get("action") == "drop" and item.get("root_review_required") is None:
            errors.append("drop recommendation must state root_review_required")
    forbidden_keys = {"iteration_decision", "next_iteration_decision", "scope_change_approved", "claim_boundary_update", "final_claim"}
    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in forbidden_keys:
                    errors.append(f"forbidden authority field: {path + k}")
                walk(v, path + k + ".")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, path + str(i) + ".")
    walk(data)
    gate = data.get("attribution_gate", {})
    if gate.get("status") not in GATES:
        errors.append(f"invalid attribution gate: {gate.get('status')}")
    if gate.get("status") == "ready_for_iteration_decision" and (gate.get("blocking_issue_ids") or not data.get("module_attributions", {}).get("items")):
        errors.append("ready gate inconsistent with blockers or empty module attributions")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate module attribution report.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/module_attribution_report.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    report_path = resolve_in_workspace(ws, args.report)
    data = load_json(report_path)
    snapshot = load_json(ws / "external_executor/module_attribution_snapshot.json")
    facts = load_json(ws / "external_executor/module_attribution_facts.json")
    result = load_json(ws / "external_executor/result_pack.json")
    known = collect_known_ids(snapshot, facts, result)
    errors = validate(data, known)
    for error in errors:
        print(f"ERROR: {error}")
    print(f"validation: {len(errors)} errors")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
