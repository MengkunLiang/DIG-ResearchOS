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


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase C prerequisites without changing domain artifacts.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/experiment_design_preflight.json")
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

    handoff = {}
    result = {}
    expected = {}
    if not errors:
        try:
            handoff = load_json(ext / "handoff_pack.json")
            result = load_json(ext / "result_pack.json")
            expected = load_json(ext / "expected_outputs_schema.json")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"malformed_control_json:{exc}")

    for label, data in (("handoff", handoff), ("result", result), ("expected", expected)):
        major = schema_major(data.get("schema_version") if isinstance(data, dict) else None)
        if major is not None and major != 1:
            errors.append(f"unsupported_{label}_schema_major:{major}")

    alignment = result.get("context_alignment", {}) if isinstance(result, dict) else {}
    if alignment.get("status") not in {"pass", "mismatch"}:
        errors.append("context_alignment_not_nonblocking")
    scope = alignment.get("confirmed_execution_scope", {}) if isinstance(alignment, dict) else {}
    if not isinstance(scope, dict) or not scope:
        errors.append("confirmed_execution_scope_missing")

    readiness = result.get("resource_readiness", {}) if isinstance(result, dict) else {}
    readiness_status = readiness.get("status")
    if readiness_status not in {"ready", "partial"}:
        errors.append(f"resource_readiness_not_feasible:{readiness_status or 'missing'}")
    if readiness.get("minimum_loop_feasible") is False:
        errors.append("minimum_loop_not_feasible")
    if readiness_status == "partial":
        warnings.append("resource_readiness_partial:propagate_constraints_into_plan")

    required_sections = ["resource_requirement_matrix", "resources", "baseline_candidates", "dataset_inventory"]
    for section in required_sections:
        if section not in result:
            errors.append(f"missing_phase_b_section:{section}")

    benchmark = get_nested(
        scope,
        "benchmark_protocol_summary",
        "benchmark_protocol",
        default={},
    )
    if not isinstance(benchmark, dict) or not benchmark:
        warnings.append("benchmark_protocol_summary_missing_or_unstructured")

    claims = get_nested(
        scope,
        "claim_evidence_matrix",
        default=get_nested(handoff, "context_reboost.claim_evidence_matrix", default=[]),
    )
    if not claims:
        hypothesis = scope.get("central_hypothesis") or get_nested(handoff, "context_reboost.central_hypothesis")
        if not nonempty(hypothesis):
            errors.append("no_claim_or_central_hypothesis_available")
        else:
            warnings.append("claim_evidence_matrix_missing:scaffold_from_central_hypothesis")

    budget = scope.get("iteration_budget") or get_nested(handoff, "context_reboost.iteration_budget", default={})
    if not isinstance(budget, dict) or not budget:
        warnings.append("iteration_budget_missing:design_gate_will_block_until_completed")

    try:
        assert_write_allowed(ws, output)
        for rel in [
            "external_executor/claim_evidence_matrix.json",
            "external_executor/protocol_snapshot.json",
            "external_executor/protocol_fingerprint.json",
            "external_executor/experiment_plan.json",
            "external_executor/experiment_design_report.json",
        ]:
            assert_write_allowed(ws, ws / rel)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"write_boundary_error:{exc}")

    fingerprint_input = {
        "handoff_schema": handoff.get("schema_version") if isinstance(handoff, dict) else None,
        "alignment": alignment,
        "resource_requirement_matrix": result.get("resource_requirement_matrix"),
        "resources": result.get("resources"),
        "baseline_candidates": result.get("baseline_candidates"),
        "dataset_inventory": result.get("dataset_inventory"),
        "material_gaps": result.get("material_gaps"),
        "resource_risks": result.get("resource_risks"),
        "resource_readiness": readiness,
    }
    report = {
        "schema_version": "experiment_design_preflight.v1",
        "generated_at": utc_now(),
        "status": "blocked" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "alignment_status": alignment.get("status"),
        "resource_readiness_status": readiness_status,
        "minimum_loop_feasible": readiness.get("minimum_loop_feasible"),
        "input_fingerprint": canonical_json_hash(fingerprint_input),
        "output_path": output.relative_to(ws).as_posix(),
    }
    dump_json_atomic(output, report)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
