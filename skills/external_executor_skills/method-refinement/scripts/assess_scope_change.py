#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import (
    active_iteration_plan,
    dictify,
    dump_json_atomic,
    listify,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify method scope drift and create a pending request for major changes.")
    parser.add_argument("--workspace")
    parser.add_argument("--intent", default="external_executor/report/phase_D/method_intent_contract.json")
    parser.add_argument("--spec", default="external_executor/method_implementation_spec.json")
    parser.add_argument("--delta", default="external_executor/report/phase_D/method_delta.json")
    parser.add_argument("--output", default="external_executor/report/phase_D/method_scope_assessment.json")
    parser.add_argument("--request-output", default="external_executor/report/phase_D/scope_change_request.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    intent = load_json(resolve_in_workspace(ws, args.intent))
    spec = load_json(resolve_in_workspace(ws, args.spec))
    delta = load_json(resolve_in_workspace(ws, args.delta))
    result = load_json(ext / "result_pack.json")
    iteration = active_iteration_plan(result)
    iteration_id = str(spec.get("iteration_id") or iteration.get("iteration_id") or iteration.get("id") or "")

    findings: list[dict] = []
    for change in listify(delta.get("changes")):
        if not isinstance(change, dict):
            continue
        classification = change.get("classification")
        findings.append({
            "finding_id": change.get("change_id"),
            "severity": "major" if classification == "scope_change_major" else ("minor" if classification == "claim_affecting_minor" else "info"),
            "category": classification,
            "path": change.get("path"),
            "summary": change.get("rationale"),
            "requires_human_review": bool(change.get("requires_human_review")),
            "evidence_refs": change.get("authorization_refs") or [],
        })

    major = delta.get("delta_level") == "major" or any(x["severity"] == "major" for x in findings)
    minor_claim = any(x.get("category") == "claim_affecting_minor" for x in findings)
    decision_ref = iteration.get("decision_ref") or iteration.get("iteration_decision_id")
    approved_changes = iteration.get("approved_changes") or iteration.get("planned_changes") or []
    missing_minor_authority = minor_claim and not nonempty(decision_ref) and not nonempty(approved_changes)

    request = None
    if major:
        request_id = stable_id("scope-change", iteration_id, spec.get("spec_id"), delta.get("current_spec_fingerprint"))
        major_changes = [x for x in listify(delta.get("changes")) if isinstance(x, dict) and x.get("classification") == "scope_change_major"]
        request = {
            "schema_version": "scope_change_request.v1",
            "request_id": request_id,
            "status": "pending_human_review",
            "created_at": utc_now(),
            "created_by": "method-refinement",
            "iteration_id": iteration_id,
            "trigger": iteration.get("trigger") or iteration.get("reason"),
            "proposed_change": "; ".join(str(x.get("rationale")) for x in major_changes),
            "current_scope": {
                "central_hypothesis": intent.get("central_hypothesis"),
                "contribution_type": intent.get("contribution_type"),
                "core_mechanism": intent.get("core_mechanism"),
                "must_preserve_components": [x.get("component_id") for x in listify(intent.get("must_preserve_components")) if isinstance(x, dict)],
                "claim_boundary": intent.get("claim_boundary") or [],
            },
            "proposed_scope": spec.get("scope_boundary") or spec.get("research_contract") or {},
            "why_needed": "The proposed implementation specification contains one or more major changes outside the approved method intent.",
            "evidence_refs": listify(iteration.get("evidence_refs")) + ["external_executor/report/phase_D/method_delta.json"],
            "affected_claims": delta.get("affected_claims") or [],
            "affected_baselines": [],
            "affected_experiments": [],
            "novelty_risk": "material; post-novelty review may be required",
            "fairness_risk": "review required before protocol or comparison changes",
            "budget_impact": iteration.get("budget_impact") or "unknown until scope decision",
            "alternatives_within_scope": listify(iteration.get("alternatives_within_scope")),
            "requested_decision": "approve | reject | narrow | return_to_T4_5",
            "implementation_must_pause": True,
            "major_change_records": major_changes,
        }
        dump_json_atomic(resolve_in_workspace(ws, args.request_output), request)

    drift_level = "major" if major else ("minor" if delta.get("delta_level") == "minor" else "none")
    review_status = "blocked" if major else ("needs_fix" if missing_minor_authority else "pass")
    blocking_issues = []
    constraints = []
    if major:
        blocking_issues.append("major_scope_change_requires_human_review")
    if missing_minor_authority:
        constraints.append("claim_affecting_minor_change_missing_explicit_root_authorization")

    assessment = {
        "schema_version": "method_scope_assessment.v1",
        "generated_at": utc_now(),
        "iteration_id": iteration_id,
        "drift_level": drift_level,
        "review_status": review_status,
        "approved_for": "implementation" if review_status == "pass" else "none",
        "requires_human_review": major,
        "scope_change_request": request,
        "scope_change_request_ref": args.request_output if request else None,
        "findings": findings,
        "blocking_issues": blocking_issues,
        "constraints": constraints,
        "recommended_next_action": "human_review" if major else ("return_to_method_refinement" if missing_minor_authority else "continue_review"),
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), assessment)
    return 1 if major else 0


if __name__ == "__main__":
    raise SystemExit(main())
