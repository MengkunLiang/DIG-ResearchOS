#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import (
    assert_write_allowed,
    canonical_json_hash,
    dump_json_atomic,
    get_nested,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    schema_major,
    utc_now,
)

TERMINAL_DECISIONS = {"stop_and_report", "claim_narrowing", "scope_change_request"}
TERMINAL_STATES = {"completed", "partial", "blocked", "failed"}


def latest_decision(result: dict) -> dict:
    candidates = result.get("iteration_decisions") or result.get("iteration_decision") or []
    if isinstance(candidates, dict):
        candidates = candidates.get("items", candidates.get("decisions", [candidates]))
    if not isinstance(candidates, list) or not candidates:
        return {}
    records = [item for item in candidates if isinstance(item, dict)]
    if not records:
        return {}
    return records[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase F1-F3 packaging prerequisites and boundaries.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/report/evidence_packaging_preflight.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    errors: list[str] = []
    warnings: list[str] = []

    required_files = {
        "agents": ext / "AGENTS.md",
        "allowed_paths": ext / "allowed_paths.txt",
        "handoff": ext / "handoff_pack.json",
        "expected_schema": ext / "expected_outputs_schema.json",
        "result_pack": ext / "result_pack.json",
        "executor_status": ext / "executor_status.json",
        "run_manifest": ext / "report" / "run_manifest.json",
    }
    for name, path in required_files.items():
        if not path.exists():
            errors.append(f"missing_required_file:{name}:{path.relative_to(ws).as_posix()}")

    data: dict[str, dict] = {}
    for name in ("handoff", "expected_schema", "result_pack", "executor_status", "run_manifest"):
        path = required_files[name]
        if not path.exists():
            data[name] = {}
            continue
        try:
            data[name] = load_json(path)
        except Exception as exc:  # noqa: BLE001
            data[name] = {}
            errors.append(f"malformed_json:{name}:{exc}")

    for name, payload in data.items():
        major = schema_major(payload.get("schema_version") if isinstance(payload, dict) else None)
        if major not in (None, 1):
            errors.append(f"unsupported_schema_major:{name}:{major}")

    result = data["result_pack"]
    status = data["executor_status"]
    decision = latest_decision(result)
    decision_name = decision.get("decision") or decision.get("primary_decision") or decision.get("type")
    executor_state = str(status.get("executor_status") or status.get("status") or "unknown").lower()
    current_phase = str(status.get("current_phase") or status.get("phase") or "").upper()
    can_package = (
        executor_state in TERMINAL_STATES
        or current_phase == "F"
        or decision_name in TERMINAL_DECISIONS
    )
    if not can_package:
        errors.append("iteration_not_stopped_or_phase_f_not_authorized")

    unresolved_scope = result.get("scope_change_request")
    if isinstance(unresolved_scope, dict) and unresolved_scope.get("status") in {"pending", "requested", "blocked"}:
        warnings.append("pending_scope_change:package_only_existing_evidence_and_mark_constraints")

    if not nonempty(result.get("context_alignment")):
        warnings.append("context_alignment_missing_from_result_pack")
    if not nonempty(result.get("claim_evidence_matrix")):
        warnings.append("claim_evidence_matrix_missing_or_empty")

    run_sections = [
        result.get("experiment_runs"), result.get("run_records"), result.get("runs"),
        result.get("baseline_reproductions"), result.get("baseline_reproduction"),
    ]
    if not any(nonempty(section) for section in run_sections):
        warnings.append("no_run_section_detected:partial_or_unavailable_package_expected")

    if not any(nonempty(result.get(key)) for key in ("module_attribution", "module_attributions", "attribution_records")):
        warnings.append("module_attribution_missing:do_not_claim_empirical_mechanism_support")
    if not any(nonempty(result.get(key)) for key in (
        "method_refinements", "implementations", "implementation_spec", "method_specification",
        "realized_method", "implementation_records",
    )):
        warnings.append("implementation_definition_missing:realized_method_may_be_unavailable")

    write_targets = [
        output,
        ext / "report" / "final_evidence_snapshot.json",
        ext / "evidence_package" / "realized_method_package.json",
        ext / "report" / "framework_figure_spec.json",
        ext / "figure" / "framework_figure.svg",
        ext / "report" / "framework_figure.mmd",
        ext / "table" / "main_comparison.csv",
        ext / "table" / "ablation_results.csv",
        ext / "figure" / "main_result.svg",
        ext / "report" / "result_table_build_report.json",
        ext / "report" / "result_figure_build_report.json",
        ext / "report" / "figure_table_inventory.json",
        ext / "report" / "evidence_mapping.json",
        ext / "report" / "evidence_package_manifest.json",
        ext / "report" / "evidence_packaging_gate.json",
        ext / "report" / "evidence_packaging_report.json",
        ext / "report" / "evidence_packaging_report_validation.json",
    ]
    for path in write_targets:
        try:
            assert_write_allowed(ws, path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"write_boundary_error:{path}:{exc}")

    fingerprint_input = {
        "handoff": data["handoff"],
        "expected_schema": data["expected_schema"],
        "result_pack": result,
        "executor_status": status,
        "run_manifest": data["run_manifest"],
    }
    report = {
        "schema_version": "evidence_packaging_preflight.v1",
        "generated_at": utc_now(),
        "status": "blocked" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "executor_state": executor_state,
        "current_phase": current_phase or None,
        "latest_iteration_decision": decision_name,
        "best_effort_mode": executor_state in {"partial", "blocked", "failed"} or bool(warnings),
        "input_fingerprint": canonical_json_hash(fingerprint_input),
    }
    dump_json_atomic(output, report)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
