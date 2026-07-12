#!/usr/bin/env python3
"""Derive the next safe root or child action from durable executor state."""

from __future__ import annotations

import argparse
from typing import Any

from _common import emit_report, load_json, resolve_in_workspace, section_status, workspace_root


CHILDREN = {
    "context-alignment", "resource-and-baseline-preparation", "experiment-design",
    "baseline-reproduction", "method-refinement", "implementation",
    "code-and-protocol-review", "experiment-run", "result-diagnosis",
    "module-attribution", "evidence-packaging", "writer-handoff",
}
ROOT_ACTIONS = {"root-iteration-decision", "human-review", "final-validation", "stop"}


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
    if executor_status in {"completed", "failed"}:
        report.update(action="stop", reason=f"executor status is {executor_status}", requires_human=False)
    elif executor_status == "blocked":
        report.update(action="human-review", reason="executor has active blocker", requires_human=True)
    else:
        explicit = status.get("next_action")
        if explicit in CHILDREN | ROOT_ACTIONS:
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
                owner = review.get("repair_owner")
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
