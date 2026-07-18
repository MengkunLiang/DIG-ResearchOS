#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import assert_write_allowed, canonical_hash, dump_json_atomic, get_nested, load_json, relpath, resolve_in_workspace, resolve_workspace, utc_now


def find_active_iteration(result: dict) -> dict | None:
    for path in ("current_iteration_plan", "iteration_plan", "iteration_plans.current"):
        value = get_nested(result, path, default=None)
        if isinstance(value, dict):
            return value
    plans = get_nested(result, "iteration_plans.items", default=[])
    if isinstance(plans, list) and plans:
        active = [p for p in plans if isinstance(p, dict) and p.get("status") in {"active", "planned", "running"}]
        return (active or plans)[-1]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate baseline reproduction prerequisites and authorization.")
    ap.add_argument("--workspace")
    ap.add_argument("--output", default="external_executor/report/baseline_reproduction_preflight.json")
    args = ap.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    issues = []
    warnings = []
    required_files = [ext / "AGENTS.md", ext / "allowed_paths.txt", ext / "result_pack.json", ext / "expected_outputs_schema.json"]
    for p in required_files:
        if not p.exists():
            issues.append({"id": "missing_control", "severity": "blocking", "message": relpath(ws, p)})
    result = {}
    if (ext / "result_pack.json").exists():
        try:
            result = load_json(ext / "result_pack.json")
        except Exception as exc:
            issues.append({"id": "malformed_result_pack", "severity": "blocking", "message": str(exc)})

    alignment = result.get("context_alignment", {}) if isinstance(result, dict) else {}
    if alignment.get("status") not in {"pass", "mismatch"}:
        issues.append({"id": "context_alignment_blocking", "severity": "blocking", "message": repr(alignment.get("status"))})
    readiness = result.get("resource_readiness", {}) if isinstance(result, dict) else {}
    if readiness.get("status") not in {"ready", "partial"} or not readiness.get("minimum_loop_feasible", False):
        issues.append({"id": "resource_readiness_blocking", "severity": "blocking", "message": repr(readiness)[:500]})

    candidates_section = result.get("baseline_candidates", {}) if isinstance(result, dict) else {}
    candidates = candidates_section.get("items", []) if isinstance(candidates_section, dict) else []
    if not candidates:
        issues.append({"id": "missing_baseline_candidates", "severity": "blocking", "message": "No baseline_candidates.items"})
    approved = []
    for c in candidates:
        approvals = c.get("approved_for", [])
        if isinstance(approvals, str):
            approvals = [approvals]
        if set(approvals) & {"baseline_reproduction", "formal_comparison"}:
            approved.append(c)
    if candidates and not approved:
        issues.append({"id": "no_candidate_approved_for_reproduction", "severity": "blocking", "message": "Candidates exist but none are approved"})

    plan = result.get("experiment_plan", {}) if isinstance(result, dict) else {}
    if not isinstance(plan, dict) or plan.get("status") not in {"complete", "partial", "approved", "ready"}:
        issues.append({"id": "missing_or_unready_experiment_plan", "severity": "blocking", "message": repr(plan.get("status") if isinstance(plan, dict) else None)})
    protocol_fp = get_nested(plan, "protocol_fingerprint", "protocol.fingerprint", default=None)
    if not protocol_fp:
        issues.append({"id": "missing_protocol_fingerprint", "severity": "blocking", "message": "experiment_plan lacks protocol fingerprint"})
    fairness_fp = get_nested(plan, "fairness_fingerprint", "protocol.fairness_fingerprint", default=None)
    if not fairness_fp:
        warnings.append({"id": "missing_fairness_fingerprint", "message": "A deterministic scaffold can be built, but execution must not start until fairness fingerprint is set"})

    iteration = find_active_iteration(result)
    if not iteration:
        issues.append({"id": "missing_active_iteration_plan", "severity": "blocking", "message": "Root-owned iteration plan is required"})
    else:
        actions = iteration.get("actions") or iteration.get("planned_actions") or iteration.get("runs") or []
        text = str(actions).lower()
        if "baseline" not in text and not iteration.get("baseline_ids"):
            warnings.append({"id": "iteration_baseline_authorization_ambiguous", "message": "Active iteration does not clearly name baseline reproduction"})

    write_targets = [
        output,
        ext / "report" / "baseline_reproduction_plan.json",
        ext / "report" / "baseline_reproduction_report.json",
        ext / "expr" / "baselines",
        ext / "raw_results" / "baseline_reproduction",
        ext / "report" / "baseline_reproduction",
    ]
    for target in write_targets:
        try:
            assert_write_allowed(ws, target)
        except Exception as exc:
            issues.append({"id": "write_not_allowed", "severity": "blocking", "message": f"{target}: {exc}"})

    payload = {
        "schema_version": "baseline_reproduction_preflight.v1",
        "generated_at": utc_now(),
        "status": "blocked" if issues else ("warning" if warnings else "pass"),
        "input_fingerprint": canonical_hash({"alignment": alignment, "readiness": readiness, "candidates": candidates, "experiment_plan": plan, "iteration": iteration}),
        "protocol_fingerprint": protocol_fp,
        "fairness_fingerprint": fairness_fp,
        "iteration_id": iteration.get("iteration_id") if iteration else None,
        "approved_candidate_ids": [c.get("candidate_id") for c in approved],
        "issues": issues,
        "warnings": warnings,
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, payload)
    print(f"{payload['status']}: wrote {relpath(ws, output)}")
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
