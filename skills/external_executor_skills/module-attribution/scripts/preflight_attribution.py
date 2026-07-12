#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import (
    active_iteration_id, assert_write_allowed, canonical_hash, current_diagnosis,
    dump_json_atomic, load_json, relpath, resolve_in_workspace, resolve_workspace,
    schema_major, section_items, utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate module attribution prerequisites.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/module_attribution_preflight.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    issues = []
    warnings = []
    required = ["AGENTS.md", "allowed_paths.txt", "result_pack.json", "handoff_pack.json"]
    for name in required:
        if not (ext / name).exists():
            issues.append({"id": f"missing_{name}", "severity": "blocking", "message": f"Missing external_executor/{name}"})
    result = load_json(ext / "result_pack.json") if (ext / "result_pack.json").exists() else {}
    handoff = load_json(ext / "handoff_pack.json") if (ext / "handoff_pack.json").exists() else {}
    major = schema_major(result.get("schema_version"))
    if major not in (None, 1):
        issues.append({"id": "unsupported_result_schema", "severity": "blocking", "message": str(result.get("schema_version"))})
    iteration_id = active_iteration_id(result)
    if not iteration_id:
        issues.append({"id": "missing_iteration", "severity": "blocking", "message": "No active iteration can be resolved"})
        iteration_id = "unknown"
    diagnosis = current_diagnosis(result, iteration_id)
    if not diagnosis:
        issues.append({"id": "missing_current_diagnosis", "severity": "blocking", "message": f"No diagnosis for {iteration_id}"})
        diagnosis = {}
    gate = diagnosis.get("diagnosis_gate", {}).get("status")
    if gate == "blocked":
        issues.append({"id": "diagnosis_blocked", "severity": "blocking", "message": "Current diagnosis is blocked"})
    elif gate not in {"ready_for_attribution", "partial"}:
        issues.append({"id": "diagnosis_not_ready", "severity": "blocking", "message": f"diagnosis gate is {gate!r}"})
    elif gate == "partial":
        warnings.append({"id": "partial_diagnosis", "message": "Attribution must preserve diagnosis limitations"})
    implementations = section_items(result.get("implementations"))
    method_intent = handoff.get("method_intent", {}) if isinstance(handoff, dict) else {}
    declared_modules = method_intent.get("candidate_modules", []) if isinstance(method_intent, dict) else []
    if not implementations and not declared_modules:
        issues.append({"id": "missing_module_identity", "severity": "blocking", "message": "No implementation mapping or method-intent modules"})
    runs = [x for x in section_items(result.get("experiment_runs")) if str(x.get("iteration_id")) == str(iteration_id)]
    if not runs:
        issues.append({"id": "missing_iteration_runs", "severity": "blocking", "message": "No runs for active iteration"})
    intervention_runs = [x for x in runs if str(x.get("run_type", "")).lower() in {"ablation", "diagnostic"} or x.get("module_states") or x.get("disabled_modules") or x.get("removed_modules")]
    if not intervention_runs:
        warnings.append({"id": "no_intervention_runs", "message": "Only implementation facts/correlational attribution may be possible"})
    targets = [output, ext / "module_attribution_snapshot.json", ext / "module_attribution_facts.json", ext / "module_attribution_report.json", ext / "workdir/module_attribution"]
    for target in targets:
        try:
            assert_write_allowed(ws, target)
        except Exception as exc:
            issues.append({"id": "write_path_not_allowed", "severity": "blocking", "message": str(exc)})
    fingerprint = canonical_hash({"iteration_id": iteration_id, "diagnosis": diagnosis, "implementations": implementations, "runs": runs, "method_intent": method_intent})
    status = "blocked" if issues else ("warning" if warnings else "pass")
    report = {
        "schema_version": "module_attribution_preflight.v1", "generated_at": utc_now(),
        "status": status, "iteration_id": iteration_id, "diagnosis_id": diagnosis.get("diagnosis_id"),
        "diagnosis_gate": gate, "input_fingerprint": fingerprint,
        "run_count": len(runs), "intervention_run_count": len(intervention_runs),
        "issues": issues, "warnings": warnings,
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, report)
    print(f"{status}: wrote {relpath(ws, output)}")
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
