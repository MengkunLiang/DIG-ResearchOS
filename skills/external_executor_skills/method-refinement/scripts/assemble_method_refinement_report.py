#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import canonical_json_hash, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace, stable_id, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble the durable method-refinement child report.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/method_refinement_report.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"

    paths = {
        "preflight": "external_executor/method_refinement_preflight.json",
        "intent_contract": "external_executor/method_intent_contract.json",
        "implementation_spec": "external_executor/method_implementation_spec.json",
        "spec_fingerprint": "external_executor/method_spec_fingerprint.json",
        "delta": "external_executor/method_delta.json",
        "scope_assessment": "external_executor/method_scope_assessment.json",
        "spec_validation": "external_executor/method_implementation_spec_validation.json",
        "review": "external_executor/method_refinement_review.json",
        "implementation_brief": "external_executor/method_implementation_brief.md",
    }
    data = {key: load_json(resolve_in_workspace(ws, value)) for key, value in paths.items() if key != "implementation_brief"}
    review = data["review"]
    spec = data["implementation_spec"]
    fp = data["spec_fingerprint"]
    scope = data["scope_assessment"]

    refinement_status = review.get("refinement_status")
    child_status = "complete" if refinement_status == "ready" else ("partial" if refinement_status == "needs_fix" else "blocked")
    refinement_id = stable_id("method-refinement", spec.get("iteration_id"), spec.get("spec_version"), fp.get("fingerprint"))
    record = {
        "refinement_id": refinement_id,
        "iteration_id": spec.get("iteration_id"),
        "status": refinement_status,
        "spec_id": spec.get("spec_id"),
        "spec_version": spec.get("spec_version"),
        "spec_ref": paths["implementation_spec"],
        "snapshot_ref": fp.get("snapshot_ref"),
        "intent_fingerprint": spec.get("intent_fingerprint"),
        "spec_fingerprint": fp.get("fingerprint"),
        "protocol_fingerprint": spec.get("protocol_fingerprint"),
        "plan_version": spec.get("plan_version"),
        "delta_level": data["delta"].get("delta_level"),
        "approved_for": review.get("approved_for"),
        "scope_change_request_id": (scope.get("scope_change_request") or {}).get("request_id"),
        "blocking_issues": review.get("blocking_issues") or [],
        "constraints": review.get("constraints") or [],
        "artifact_refs": [{"path": value, "kind": key} for key, value in paths.items()],
    }
    report = {
        "schema_version": "method_refinement_report.v1",
        "child_skill": "method-refinement",
        "generated_at": utc_now(),
        "status": child_status,
        "refinement_status": refinement_status,
        "refinement_id": refinement_id,
        "input_fingerprint": data["preflight"].get("input_fingerprint"),
        "method_refinement_record": record,
        "method_intent_contract": data["intent_contract"],
        "method_implementation_spec": spec,
        "method_spec_fingerprint": fp,
        "method_delta": data["delta"],
        "scope_assessment": scope,
        "spec_validation": data["spec_validation"],
        "review": review,
        "scope_change_request": scope.get("scope_change_request"),
        "artifact_refs": record["artifact_refs"],
        "report_fingerprint": canonical_json_hash({"record": record, "delta": data["delta"], "scope": scope, "review": review}),
        "blocking_issues": review.get("blocking_issues") or [],
        "constraints": review.get("constraints") or [],
        "recommended_next_action": review.get("recommended_next_action"),
        "notes": [],
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
