#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import canonical_json_hash, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble the durable child report from validated Phase C artifacts.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/experiment_design_report.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)

    paths = {
        "preflight": "external_executor/experiment_design_preflight.json",
        "claim_evidence_matrix": "external_executor/claim_evidence_matrix.json",
        "protocol_snapshot": "external_executor/protocol_snapshot.json",
        "protocol_fingerprint": "external_executor/protocol_fingerprint.json",
        "experiment_plan": "external_executor/experiment_plan.json",
        "plan_validation": "external_executor/experiment_plan_validation.json",
        "dag_validation": "external_executor/experiment_plan_dag_validation.json",
        "design_gate": "external_executor/experiment_design_gate.json",
    }
    data = {key: load_json(resolve_in_workspace(ws, value)) for key, value in paths.items()}
    gate = data["design_gate"]
    child_status = "complete" if gate.get("status") in {"ready", "partial"} else "blocked"
    report = {
        "schema_version": "experiment_design_report.v1",
        "child_skill": "experiment-design",
        "generated_at": utc_now(),
        "status": child_status,
        "design_readiness": gate.get("status"),
        "input_fingerprint": data["preflight"].get("input_fingerprint"),
        "claim_evidence_matrix": data["claim_evidence_matrix"],
        "experiment_plan": data["experiment_plan"],
        "validation": {
            "plan": data["plan_validation"],
            "dag": data["dag_validation"],
            "gate": gate,
        },
        "artifact_refs": [{"path": value, "kind": key} for key, value in paths.items()],
        "report_fingerprint": canonical_json_hash({"claims": data["claim_evidence_matrix"], "plan": data["experiment_plan"], "gate": gate}),
        "blocking_issues": gate.get("blocking_issues", []),
        "constraints": gate.get("constraints", []),
        "recommended_next_action": gate.get("next_action"),
        "notes": [],
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
