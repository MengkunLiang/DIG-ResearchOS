#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import (
    active_iteration_plan,
    assert_write_allowed,
    canonical_json_hash,
    dictify,
    dump_json_atomic,
    extract_plan_version,
    extract_protocol_fingerprint,
    get_nested,
    latest_record,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    schema_major,
    utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate method-refinement prerequisites and authorization.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/method_refinement_preflight.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    errors: list[str] = []
    warnings: list[str] = []

    required = [
        ext / "AGENTS.md",
        ext / "allowed_paths.txt",
        ext / "handoff_pack.json",
        ext / "expected_outputs_schema.json",
        ext / "result_pack.json",
    ]
    for path in required:
        if not path.exists():
            errors.append(f"missing_required_file:{path.relative_to(ws).as_posix()}")

    handoff: dict = {}
    expected: dict = {}
    result: dict = {}
    if not errors:
        try:
            handoff = load_json(ext / "handoff_pack.json")
            expected = load_json(ext / "expected_outputs_schema.json")
            result = load_json(ext / "result_pack.json")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"malformed_control_json:{exc}")

    for label, data in (("handoff", handoff), ("expected", expected), ("result", result)):
        major = schema_major(data.get("schema_version") if isinstance(data, dict) else None)
        if major is not None and major != 1:
            errors.append(f"unsupported_schema_major:{label}:{major}")

    alignment = dictify(result.get("context_alignment"))
    if alignment.get("status") not in {"pass", "mismatch"}:
        errors.append(f"blocking_context_alignment:{alignment.get('status', 'missing')}")
    scope = dictify(alignment.get("confirmed_execution_scope"))
    if not scope:
        errors.append("missing_confirmed_execution_scope")

    method_intent = dictify(handoff.get("method_intent"))
    if not method_intent:
        errors.append("missing_method_intent")
    else:
        if method_intent.get("not_final_method_source") is not True:
            warnings.append("method_intent_not_explicitly_marked_non_final")
        source_status = str(method_intent.get("status") or "")
        if source_status and source_status != "draft_intent_only":
            warnings.append(f"unexpected_method_intent_status:{source_status}")
        core = method_intent.get("central_mechanism_hypothesis") or method_intent.get("core_mechanism")
        if not nonempty(core):
            errors.append("missing_core_mechanism")

    plan = dictify(result.get("experiment_plan"))
    if not plan:
        errors.append("missing_experiment_plan")
    plan_status = str(plan.get("status") or "")
    if plan_status and plan_status not in {"complete", "ready", "partial"}:
        warnings.append(f"experiment_plan_status_requires_review:{plan_status}")
    protocol_fp = extract_protocol_fingerprint(result)
    if not protocol_fp:
        errors.append("missing_protocol_fingerprint")
    if extract_plan_version(result) <= 0:
        errors.append("missing_plan_version")

    iteration = active_iteration_plan(result)
    if not iteration:
        errors.append("missing_active_iteration_plan")
    iteration_id = str(iteration.get("iteration_id") or iteration.get("id") or "")
    if iteration and not iteration_id:
        errors.append("active_iteration_plan_missing_id")
    trigger = str(iteration.get("trigger") or iteration.get("reason") or "")
    if iteration and not trigger:
        warnings.append("active_iteration_plan_missing_trigger")
    approved_changes = iteration.get("approved_changes") or iteration.get("planned_changes") or iteration.get("change_scope")
    if iteration and not nonempty(approved_changes):
        warnings.append("active_iteration_plan_missing_approved_change_surface")

    latest_decision = latest_record(result, ("iteration_decisions", "current_iteration_decision"))
    diagnosis_trigger = any(word in trigger.lower() for word in ("diagnos", "attribution", "result", "module", "underperform"))
    if diagnosis_trigger and not latest_decision:
        errors.append("diagnosis_based_refinement_without_root_decision")

    required_baselines = scope.get("required_baselines") or get_nested(handoff, "context_reboost.required_baselines", default=[])
    if not nonempty(required_baselines):
        warnings.append("required_baselines_not_explicit_in_scope")
    claim_boundary = scope.get("claim_boundaries") or scope.get("claim_boundary") or get_nested(handoff, "context_reboost.claim_boundaries", default=[])
    if not nonempty(claim_boundary):
        warnings.append("claim_boundary_not_explicit")

    output_paths = [
        output,
        ext / "method_intent_contract.json",
        ext / "method_implementation_spec.json",
        ext / "method_specs",
        ext / "method_spec_fingerprint.json",
        ext / "method_delta.json",
        ext / "method_scope_assessment.json",
        ext / "method_refinement_review.json",
        ext / "method_implementation_brief.md",
        ext / "method_refinement_report.json",
        ext / "method_refinement_report_validation.json",
        ext / "result_pack.json",
    ]
    for path in output_paths:
        try:
            assert_write_allowed(ws, path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"write_not_allowed:{path.relative_to(ws).as_posix()}:{exc}")

    input_material = {
        "handoff_schema": handoff.get("schema_version"),
        "method_intent": method_intent,
        "confirmed_scope": scope,
        "experiment_plan": plan,
        "iteration_plan": iteration,
        "latest_iteration_decision": latest_decision,
    }
    report = {
        "schema_version": "method_refinement_preflight.v1",
        "generated_at": utc_now(),
        "status": "blocked" if errors else ("warning" if warnings else "pass"),
        "errors": errors,
        "warnings": warnings,
        "input_fingerprint": canonical_json_hash(input_material),
        "iteration_id": iteration_id,
        "trigger": trigger,
        "approved_change_surface": approved_changes or [],
        "protocol_fingerprint": protocol_fp,
        "plan_version": extract_plan_version(result),
        "source_refs": [
            "external_executor/handoff_pack.json#method_intent",
            "external_executor/result_pack.json#context_alignment",
            "external_executor/result_pack.json#experiment_plan",
            "external_executor/result_pack.json#current_iteration_plan|iteration_plans",
        ],
        "capability_summary": {
            "has_method_intent": bool(method_intent),
            "has_confirmed_scope": bool(scope),
            "has_experiment_plan": bool(plan),
            "has_active_iteration_plan": bool(iteration),
            "has_protocol_fingerprint": bool(protocol_fp),
        },
    }
    dump_json_atomic(output, report)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
