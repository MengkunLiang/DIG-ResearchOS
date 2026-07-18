#!/usr/bin/env python3
"""Derive the next safe root or child action from durable executor state."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from _common import emit_report, load_json, resolve_in_workspace, section_status, workspace_root


CHILDREN = {
    "context-alignment", "resource-and-baseline-preparation", "experiment-design",
    "baseline-reproduction", "method-refinement", "implementation",
    "code-and-protocol-review", "experiment-run", "result-diagnosis",
    "module-attribution", "evidence-packaging", "writer-handoff",
}
ROOT_ACTIONS = {"root-iteration-decision", "human-review", "launch-t8", "stop"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "unusable"}


STEP_MAP = {
    "A": "context-alignment", "A1": "context-alignment", "A2": "context-alignment", "A3": "context-alignment",
    "B": "resource-and-baseline-preparation", "B1": "resource-and-baseline-preparation",
    "B2": "resource-and-baseline-preparation", "B3": "resource-and-baseline-preparation",
    "B4": "resource-and-baseline-preparation", "B5": "resource-and-baseline-preparation",
    "B6": "resource-and-baseline-preparation",
    "C": "experiment-design", "C1": "experiment-design", "C2": "experiment-design",
    "C3": "experiment-design", "C4": "experiment-design",
    "D1": "baseline-reproduction", "D2R": "method-refinement", "D2I": "implementation",
    "D3": "code-and-protocol-review", "D4": "experiment-run", "D5": "experiment-run",
    "D6": "experiment-run", "D7": "experiment-run",
    "E1": "result-diagnosis", "E2": "module-attribution", "E3": "root-iteration-decision",
    "F1": "evidence-packaging", "F2": "evidence-packaging", "F3": "evidence-packaging",
    "F4": "writer-handoff", "F5": "writer-handoff",
}


def latest_review(result: dict[str, Any]) -> dict[str, Any] | None:
    value = result.get("implementation_reviews")
    items = value if isinstance(value, list) else value.get("items", []) if isinstance(value, dict) else []
    valid = [item for item in items if isinstance(item, dict)]
    return valid[-1] if valid else None


def records(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = result.get(key)
    raw = value if isinstance(value, list) else value.get("items", []) if isinstance(value, dict) else []
    return [item for item in raw if isinstance(item, dict)]


def diagnosed_run_ids(diagnoses: list[dict[str, Any]]) -> set[str]:
    covered: set[str] = set()
    for diagnosis in diagnoses:
        snapshot = diagnosis.get("evidence_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        for key in ("included_run_ids", "excluded_run_ids", "run_ids"):
            values = snapshot.get(key)
            if isinstance(values, list):
                covered.update(str(value) for value in values if value)
        values = diagnosis.get("run_ids")
        if isinstance(values, list):
            covered.update(str(value) for value in values if value)
    return covered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    report: dict[str, Any] = {"action": "human-review", "child_skill": None, "reason": "ambiguous state", "requires_human": True}

    try:
        status = load_json(resolve_in_workspace(root, "external_executor/executor_status.json"))
        result = load_json(resolve_in_workspace(root, "external_executor/result_pack.json"))
    except Exception as exc:
        report["reason"] = f"invalid core state: {exc}"
        emit_report(report, args.output)
        return 2

    executor_status = status.get("executor_status")
    evidence_packaged = result.get("evidence_packaging") not in (None, {}, [])
    handoff_report = resolve_in_workspace(root, "external_executor/executor_research_report.md")
    validation_path = resolve_in_workspace(root, "external_executor/report/phase_F/writer_handoff_validation.json")
    validation = {}
    if validation_path.is_file():
        try:
            validation = load_json(validation_path)
        except Exception:
            validation = {}
    handoff_ready = handoff_report.is_file() and handoff_report.stat().st_size > 0 and validation.get("status") in {"ready", "partial"}
    t8_receipt = resolve_in_workspace(root, "drafts/t5_t8_handoff.json")
    state_path = resolve_in_workspace(root, "state.yaml")
    state_text = state_path.read_text(encoding="utf-8", errors="replace") if state_path.is_file() else ""
    t8_already_delegated = t8_receipt.is_file() and (
        "current_task: T8" in state_text
        or "current_task: T9" in state_text
        or "t5_t8_handoff_receipt:" in state_text
    )
    if executor_status in {"completed", "partial", "blocked", "failed"} and evidence_packaged and not handoff_ready:
        report.update(action="writer-handoff", child_skill="writer-handoff", reason="terminal core state requires Writer Handoff compilation and validation", requires_human=False)
    elif executor_status in {"completed", "partial", "blocked", "failed"} and handoff_ready:
        if t8_already_delegated:
            report.update(action="stop", reason="Writer Handoff is complete and control has already been delegated to ResearchOS T8", requires_human=False)
        else:
            report.update(
                action="launch-t8",
                reason=f"executor status is {executor_status}; Writer Handoff is complete and ready for ResearchOS T8 ingestion",
                requires_human=False,
                command=[sys.executable, "-m", "researchos.cli", "run-task", "T8", "--workspace", str(root)],
                primary_input="external_executor/executor_research_report.md",
            )
    elif executor_status == "blocked":
        report.update(action="human-review", reason="executor has active blocker", requires_human=True)
    else:
        explicit = status.get("next_action")
        iteration_id = str(status.get("iteration_id") or "")
        diagnoses = [item for item in records(result, "result_diagnoses") if str(item.get("iteration_id")) == iteration_id]
        decisions = [item for item in records(result, "iteration_decisions") if str(item.get("iteration_id")) == iteration_id]
        iteration_runs = [item for item in records(result, "experiment_runs") if str(item.get("iteration_id")) == iteration_id]
        covered_run_ids = diagnosed_run_ids(diagnoses)
        undiagnosed_terminal_runs = [
            item for item in iteration_runs
            if (item.get("run_status") or item.get("status")) in TERMINAL_RUN_STATUSES
            and item.get("run_id")
            and str(item["run_id"]) not in covered_run_ids
        ]
        latest_diagnosis = diagnoses[-1] if diagnoses else None
        diagnosis_decided = bool(latest_diagnosis and any(item.get("diagnosis_id") == latest_diagnosis.get("diagnosis_id") for item in decisions))
        attributions = [item for item in records(result, "module_attributions") if str(item.get("iteration_id")) == iteration_id]
        latest_attribution = attributions[-1] if attributions else None
        attribution_gate = latest_attribution.get("attribution_gate", {}) if isinstance(latest_attribution, dict) else {}
        if undiagnosed_terminal_runs:
            report.update(
                action="result-diagnosis",
                child_skill="result-diagnosis",
                reason="terminal experiment runs have not been covered by a diagnosis",
                requires_human=False,
                run_ids=[str(item["run_id"]) for item in undiagnosed_terminal_runs],
            )
        elif latest_diagnosis and not diagnosis_decided:
            report.update(action="root-iteration-decision", reason="latest diagnosis has no root iteration decision", requires_human=False)
        elif explicit == "module-attribution" and latest_attribution:
            gate_status = attribution_gate.get("status")
            gate_action = attribution_gate.get("next_action")
            if gate_status == "ready_for_iteration_decision":
                report.update(action="evidence-packaging", child_skill="evidence-packaging", reason="terminal-loop attribution evidence is ready", requires_human=False)
            elif gate_status == "partial" and gate_action in {"add_controlled_evidence", "repair_or_rerun"}:
                report.update(action="experiment-design", child_skill="experiment-design", reason=f"module attribution requires more evidence: {gate_action}", requires_human=False)
            elif gate_status == "blocked" or gate_action == "human_review":
                report.update(action="human-review", reason="module attribution is blocked", requires_human=True)
            elif gate_action == "stop_and_report":
                report.update(action="evidence-packaging", child_skill="evidence-packaging", reason="module attribution requested constrained packaging", requires_human=False)
            else:
                report.update(action="human-review", reason="module attribution gate is ambiguous", requires_human=True)
        elif explicit in CHILDREN | ROOT_ACTIONS:
            report.update(
                action=explicit,
                child_skill=explicit if explicit in CHILDREN else None,
                reason="explicit durable next_action",
                requires_human=explicit == "human-review",
            )
        elif section_status(result.get("context_alignment")) not in {"complete", "pass", "partial"}:
            report.update(action="context-alignment", child_skill="context-alignment", reason="alignment incomplete", requires_human=False)
        elif section_status(result.get("resource_readiness")) not in {"complete", "ready", "partial"}:
            report.update(action="resource-and-baseline-preparation", child_skill="resource-and-baseline-preparation", reason="resource readiness incomplete", requires_human=False)
        elif section_status(result.get("experiment_plan")) not in {"complete", "ready", "partial"}:
            report.update(action="experiment-design", child_skill="experiment-design", reason="experiment plan incomplete", requires_human=False)
        else:
            review = latest_review(result)
            if review and review.get("review_status") == "needs_fix":
                owners = review.get("repair_owners") if isinstance(review.get("repair_owners"), list) else []
                owner = review.get("repair_owner") or next((value for value in owners if value in {"baseline-reproduction", "method-refinement", "implementation"}), None)
                mapped = owner if owner in {"baseline-reproduction", "method-refinement", "implementation"} else "human-review"
                report.update(
                    action=mapped,
                    child_skill=mapped if mapped in CHILDREN else None,
                    reason="latest review needs a fix",
                    requires_human=mapped == "human-review",
                )
            else:
                step = str(status.get("current_step") or status.get("current_phase") or "")
                mapped = STEP_MAP.get(step)
                if mapped:
                    report.update(
                        action=mapped,
                        child_skill=mapped if mapped in CHILDREN else None,
                        reason=f"mapped from durable step {step}",
                        requires_human=False,
                    )

    emit_report(report, args.output)
    return 0 if report["action"] != "human-review" or report["reason"] == "executor has active blocker" else 2


if __name__ == "__main__":
    raise SystemExit(main())
