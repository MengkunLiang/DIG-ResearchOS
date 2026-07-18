#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import dictify, dump_json_atomic, listify, load_json, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a method specification before implementation.")
    parser.add_argument("--workspace")
    parser.add_argument("--spec", default="external_executor/method_implementation_spec.json")
    parser.add_argument("--delta", default="external_executor/report/phase_D/method_delta.json")
    parser.add_argument("--scope-assessment", default="external_executor/report/phase_D/method_scope_assessment.json")
    parser.add_argument("--spec-validation", default="external_executor/report/phase_D/method_implementation_spec_validation.json")
    parser.add_argument("--output", default="external_executor/report/phase_D/method_refinement_review.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    spec = load_json(resolve_in_workspace(ws, args.spec))
    delta = load_json(resolve_in_workspace(ws, args.delta))
    scope = load_json(resolve_in_workspace(ws, args.scope_assessment))
    validation = load_json(resolve_in_workspace(ws, args.spec_validation))
    preflight = load_json(ext / "report" / "phase_D" / "method_refinement_preflight.json", default={})

    findings: list[dict] = []
    required_fixes: list[str] = []
    blockers: list[str] = []

    if preflight.get("status") == "blocked":
        blockers.extend(preflight.get("errors") or ["preflight_blocked"])
    for error in listify(validation.get("errors")):
        category = "contract"
        severity = "high"
        if str(error).startswith(("intent_fingerprint_mismatch", "protocol_fingerprint_mismatch", "missing_must_preserve_module", "unresolved_scope_sensitive")):
            severity = "critical"
        findings.append({"finding_id": f"SPEC-{len(findings)+1:03d}", "axis": category, "severity": severity, "summary": error,
                         "evidence_refs": [args.spec, args.spec_validation]})
        if severity == "critical":
            blockers.append(str(error))
        else:
            required_fixes.append(str(error))
    for warning in listify(validation.get("warnings")):
        findings.append({"finding_id": f"SPEC-{len(findings)+1:03d}", "axis": "completeness", "severity": "medium", "summary": warning,
                         "evidence_refs": [args.spec, args.spec_validation]})
        required_fixes.append(str(warning))

    if scope.get("drift_level") == "major" or scope.get("requires_human_review") is True:
        blockers.extend(scope.get("blocking_issues") or ["major_scope_change_requires_human_review"])
        findings.append({
            "finding_id": f"SCOPE-{len(findings)+1:03d}",
            "axis": "scope_drift",
            "severity": "critical",
            "summary": "Major method scope drift requires root/human review before implementation.",
            "evidence_refs": [args.delta, args.scope_assessment],
        })
    elif scope.get("review_status") == "needs_fix":
        required_fixes.extend(scope.get("constraints") or ["scope_assessment_needs_fix"])

    # A protocol/interface plan must remain pinned.
    protocol_iface = dictify(spec.get("data_and_protocol_interfaces"))
    if protocol_iface.get("protocol_fingerprint") != spec.get("protocol_fingerprint"):
        blockers.append("spec_internal_protocol_fingerprint_mismatch")
    # Core modules need traceability.
    modules = [x for x in listify(spec.get("modules")) if isinstance(x, dict)]
    core_ids = {str(x.get("module_id")) for x in modules if x.get("contribution_role") == "core"}
    trace_rows = [x for x in listify(spec.get("evidence_traceability")) if isinstance(x, dict)]
    traced = {str(mid) for row in trace_rows for mid in listify(row.get("module_ids"))}
    missing_trace = sorted(core_ids - traced)
    for mid in missing_trace:
        required_fixes.append(f"core_module_missing_evidence_traceability:{mid}")

    blockers = sorted(set(blockers))
    required_fixes = sorted(set(required_fixes) - set(blockers))
    if blockers:
        review_status = "blocked"
        refinement_status = "blocked"
        approved_for = "none"
        next_action = "human_review" if scope.get("requires_human_review") else "return_to_method_refinement"
    elif required_fixes:
        review_status = "needs_fix"
        refinement_status = "needs_fix"
        approved_for = "none"
        next_action = "return_to_method_refinement"
    else:
        review_status = "pass"
        refinement_status = "ready"
        approved_for = "implementation"
        next_action = "continue_to_implementation"

    review = {
        "schema_version": "method_refinement_review.v1",
        "generated_at": utc_now(),
        "review_status": review_status,
        "refinement_status": refinement_status,
        "approved_for": approved_for,
        "spec_id": spec.get("spec_id"),
        "spec_version": spec.get("spec_version"),
        "spec_fingerprint": spec.get("spec_fingerprint"),
        "intent_fingerprint": spec.get("intent_fingerprint"),
        "protocol_fingerprint": spec.get("protocol_fingerprint"),
        "delta_level": delta.get("delta_level"),
        "findings": findings,
        "required_fixes": required_fixes,
        "blocking_issues": blockers,
        "constraints": scope.get("constraints") or [],
        "scope_change_request_ref": scope.get("scope_change_request_ref"),
        "evidence_refs": [args.spec, args.delta, args.scope_assessment, args.spec_validation],
        "recommended_next_action": next_action,
        "approval_boundary": "Specification approval only; actual implementation still requires code-and-protocol-review.",
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), review)
    return 0 if review_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
