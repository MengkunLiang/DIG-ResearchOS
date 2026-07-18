#!/usr/bin/env python3
"""Close the diagnosis-driven method optimization loop deterministically."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from typing import Any

from _common import atomic_write_json, load_json, resolve_in_workspace, utc_now, workspace_root


MAX_METHOD_ITERATIONS = 10
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "unusable", "stale"}


def items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "runs", "records"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def plans(result: dict[str, Any]) -> list[dict[str, Any]]:
    return items(result.get("iteration_plans"))


def active_plan(result: dict[str, Any], status: dict[str, Any]) -> dict[str, Any] | None:
    direct = result.get("current_iteration_plan") or result.get("active_iteration") or result.get("iteration_plan")
    if isinstance(direct, dict) and direct:
        return direct
    iteration_id = status.get("iteration_id")
    candidates = plans(result)
    if iteration_id:
        matched = [plan for plan in candidates if str(plan.get("iteration_id") or plan.get("id")) == str(iteration_id)]
        if matched:
            return matched[-1]
    active = [plan for plan in candidates if plan.get("status") in {"active", "approved", "planned", "running"}]
    return (active or candidates)[-1] if (active or candidates) else None


def iteration_number(plan: dict[str, Any] | None, all_plans: list[dict[str, Any]]) -> int:
    if plan:
        for key in ("iteration_number", "attempt_number", "sequence"):
            value = plan.get(key)
            if isinstance(value, int) and value > 0:
                return value
    return max(1, len(all_plans))


def budget_exhausted(status: dict[str, Any]) -> tuple[bool, list[str]]:
    budget = status.get("budget") if isinstance(status.get("budget"), dict) else {}
    reasons: list[str] = []
    if budget.get("exhausted") is True or budget.get("stop_required") is True:
        reasons.append("budget_stop_flag")
    remaining = budget.get("remaining") if isinstance(budget.get("remaining"), dict) else {}
    limits = budget.get("limit") if isinstance(budget.get("limit"), dict) else budget.get("total") if isinstance(budget.get("total"), dict) else {}
    for key in ("runs", "wall_clock_seconds", "gpu_hours", "cost"):
        value = remaining.get(key)
        limit = limits.get(key)
        enforced = key in {"runs", "wall_clock_seconds"} or (isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0)
        if enforced and isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
            reasons.append(f"{key}_exhausted")
    return bool(reasons), reasons


def latest_implementation(result: dict[str, Any], iteration_id: str) -> dict[str, Any] | None:
    records = items(result.get("implementations"))
    matching = [record for record in records if str(record.get("iteration_id")) == str(iteration_id)]
    return (matching or records)[-1] if (matching or records) else None


def implementation_worktree(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    if isinstance(record.get("worktree_path"), str):
        return record["worktree_path"]
    root = record.get("implementation_root")
    return f"{str(root).rstrip('/')}/worktree" if root else None


def planned_ablation_ids(result: dict[str, Any]) -> list[str]:
    plan = result.get("experiment_plan") if isinstance(result.get("experiment_plan"), dict) else {}
    experiments = plan.get("experiments") if isinstance(plan.get("experiments"), list) else plan.get("items", [])
    return sorted({
        str(item.get("experiment_id") or item.get("id"))
        for item in experiments
        if isinstance(item, dict)
        and item.get("run_type") == "ablation"
        and item.get("required", True) is not False
        and (item.get("experiment_id") or item.get("id"))
    })


def planned_ablation_entries(result: dict[str, Any]) -> list[dict[str, Any]]:
    plan = result.get("experiment_plan") if isinstance(result.get("experiment_plan"), dict) else {}
    experiments = plan.get("experiments") if isinstance(plan.get("experiments"), list) else plan.get("items", [])
    return [
        item for item in experiments
        if isinstance(item, dict) and item.get("run_type") == "ablation" and item.get("required", True) is not False
    ]


def ablation_completeness(result: dict[str, Any], iteration_id: str) -> tuple[list[str], list[str]]:
    runs = [
        run for run in current_runs(result, iteration_id)
        if run.get("method_role") == "ours"
        and (run.get("run_status") or run.get("status")) == "completed"
        and run.get("run_type") == "ablation"
    ]
    issues: list[str] = []
    complete_experiments: list[str] = []

    def comparable(run: dict[str, Any]) -> str:
        dataset = run.get("dataset")
        payload = {
            "implementation_id": run.get("implementation_id"),
            "protocol_fingerprint": run.get("protocol_fingerprint"),
            "dataset": dataset,
            "dataset_version": run.get("dataset_version") or (dataset.get("version") if isinstance(dataset, dict) else None),
            "split": run.get("split") or (dataset.get("split") if isinstance(dataset, dict) else None),
            "preprocessing_fingerprint": run.get("preprocessing_fingerprint"),
            "setting": run.get("setting", "default"),
            "subset": run.get("subset", "all"),
            "seed": run.get("seed"),
            "repeat_index": run.get("repeat_index", run.get("repeat")),
            "fairness_fingerprint": run.get("fairness_fingerprint"),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    for experiment in planned_ablation_entries(result):
        experiment_id = str(experiment.get("experiment_id") or experiment.get("id") or "")
        contract = experiment.get("attribution_contract")
        if not isinstance(contract, dict):
            issues.append(f"{experiment_id}:missing_attribution_contract")
            continue
        variants = {
            str(item.get("variant_id")): item
            for item in contract.get("variant_contracts", [])
            if isinstance(item, dict) and item.get("variant_id")
        }
        if len(variants) < 2:
            issues.append(f"{experiment_id}:incomplete_variant_contract")
            continue
        groups: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            if str(run.get("experiment_id")) == experiment_id and run.get("pair_id"):
                groups.setdefault(str(run["pair_id"]), []).append(run)
        valid_groups: list[list[dict[str, Any]]] = []
        for pair_id, group in groups.items():
            by_variant = {str(run.get("variant_id")): run for run in group if run.get("variant_id")}
            if set(by_variant) != set(variants):
                continue
            if len({comparable(run) for run in group}) != 1:
                continue
            valid = True
            for variant_id, expected in variants.items():
                run = by_variant[variant_id]
                if run.get("reference_variant_id") != expected.get("reference_variant_id"):
                    valid = False
                if run.get("module_states") != expected.get("module_states"):
                    valid = False
                if sorted(str(x) for x in run.get("target_module_ids", [])) != sorted(str(x) for x in contract.get("target_module_ids", [])):
                    valid = False
                if not isinstance(run.get("intervention"), dict) or run["intervention"].get("controlled") is not True:
                    valid = False
                expected_intervention = expected.get("intervention")
                if isinstance(expected_intervention, dict):
                    for field in ("type", "controlled", "module_ids", "action", "replacements"):
                        if field in expected_intervention and run["intervention"].get(field) != expected_intervention.get(field):
                            valid = False
                if not isinstance(run.get("metric_directions"), dict) or not run.get("metric_directions"):
                    valid = False
            if valid:
                valid_groups.append(group)
        if not valid_groups:
            issues.append(f"{experiment_id}:missing_complete_comparable_variant_pair")
            continue
        expected_seeds = experiment.get("seeds") if isinstance(experiment.get("seeds"), list) else []
        for seed in expected_seeds:
            if not any(all(run.get("seed") == seed for run in group) for group in valid_groups):
                issues.append(f"{experiment_id}:missing_seed:{seed}")
        repeats = experiment.get("repeats")
        if isinstance(repeats, int) and not isinstance(repeats, bool) and repeats > 0:
            seed_surface = expected_seeds or sorted({group[0].get("seed") for group in valid_groups}, key=str)
            for seed in seed_surface:
                present = {
                    group[0].get("repeat_index", group[0].get("repeat"))
                    for group in valid_groups if all(run.get("seed") == seed for run in group)
                }
                for repeat_index in range(repeats):
                    if repeat_index not in present:
                        issues.append(f"{experiment_id}:missing_seed_repeat:{seed}:{repeat_index}")
        if not any(issue.startswith(f"{experiment_id}:") for issue in issues):
            complete_experiments.append(experiment_id)
    return sorted(set(issues)), sorted(set(complete_experiments))


def current_runs(result: dict[str, Any], iteration_id: str) -> list[dict[str, Any]]:
    return [run for run in items(result.get("experiment_runs")) if str(run.get("iteration_id")) == str(iteration_id)]


def decision_id(iteration_id: str, diagnosis_id: str, outcome: str) -> str:
    digest = hashlib.sha256(f"{iteration_id}|{diagnosis_id}|{outcome}".encode()).hexdigest()[:12]
    return f"ITERDEC-{digest}"


def append_decision(result: dict[str, Any], decision: dict[str, Any]) -> None:
    section = result.get("iteration_decisions")
    records = items(section)
    records = [item for item in records if item.get("decision_id") != decision["decision_id"]] + [decision]
    result["iteration_decisions"] = {"status": "complete", "items": records, "latest_decision_id": decision["decision_id"]}


def replace_plans(result: dict[str, Any], records: list[dict[str, Any]], current: dict[str, Any] | None) -> None:
    result["iteration_plans"] = {"status": "complete", "items": records, "active_iteration_id": current.get("iteration_id") if current else None}
    if current:
        result["current_iteration_plan"] = current
    else:
        result.pop("current_iteration_plan", None)


def history_summary(result: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for diagnosis in items(result.get("result_diagnoses")):
        assessment = diagnosis.get("method_change_assessment") if isinstance(diagnosis.get("method_change_assessment"), dict) else {}
        summaries.append({
            "iteration_id": diagnosis.get("iteration_id"),
            "diagnosis_id": diagnosis.get("diagnosis_id"),
            "rationale": assessment.get("rationale"),
            "causes": assessment.get("failure_or_underperformance_causes", []),
            "changes": assessment.get("proposed_changes", []),
            "lessons": assessment.get("prior_iteration_lessons", []),
            "evidence_refs": assessment.get("evidence_refs", []),
        })
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description="Record the root iteration decision and deterministic next route.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--diagnosis", default="external_executor/result_diagnosis_report.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    ext = root / "external_executor"
    result_path = ext / "result_pack.json"
    status_path = ext / "executor_status.json"
    result = load_json(result_path)
    status = load_json(status_path)
    diagnosis = load_json(resolve_in_workspace(root, args.diagnosis, must_exist=True))
    plan = active_plan(result, status)
    if not plan:
        raise SystemExit("No active iteration plan")
    iteration_id = str(plan.get("iteration_id") or plan.get("id") or "")
    if not iteration_id or str(diagnosis.get("iteration_id")) != iteration_id:
        raise SystemExit("Diagnosis does not match the active iteration")

    all_plans = plans(result)
    current_number = iteration_number(plan, all_plans)
    run_records = current_runs(result, iteration_id)
    ours = [run for run in run_records if run.get("method_role") == "ours"]
    failed = [run for run in ours if (run.get("run_status") or run.get("status")) in TERMINAL_RUN_STATUSES - {"completed"}]
    completed_main = [run for run in ours if (run.get("run_status") or run.get("status")) == "completed" and run.get("run_type") == "formal"]
    performance = diagnosis.get("baseline_performance") if isinstance(diagnosis.get("baseline_performance"), dict) else {}
    assessment = diagnosis.get("method_change_assessment") if isinstance(diagnosis.get("method_change_assessment"), dict) else {}
    target_reached = bool(completed_main) and performance.get("all_required_baselines_beaten") is True
    budget_stop, budget_reasons = budget_exhausted(status)
    blockers = status.get("active_blockers") if isinstance(status.get("active_blockers"), list) else []
    max_reached = current_number >= MAX_METHOD_ITERATIONS
    ablation_ids = planned_ablation_ids(result)
    ablation_issues, completed_ablation_ids = ablation_completeness(result, iteration_id)

    next_action: str
    primary: str
    loop_outcome: str
    rationale: str
    new_plan: dict[str, Any] | None = None
    stop_reasons: list[str] = []

    if blockers:
        primary, loop_outcome, next_action = "stop_and_report", "blocked", "human-review"
        stop_reasons = ["active_blocker"]
        rationale = "An existing root-owned blocker prevents another safe method iteration."
    elif budget_stop:
        primary, loop_outcome, next_action = "stop_and_report", "budget_exhausted", "evidence-packaging"
        stop_reasons = budget_reasons
        rationale = "The root budget stop condition was reached."
    elif target_reached and not ablation_ids:
        primary, loop_outcome, next_action = "add_diagnostic_run", "final_ablation_plan_required", "experiment-design"
        rationale = "Our method beats every required baseline, but no required final ablation is present in the experiment plan."
    elif target_reached and ablation_issues:
        needs_design = any("attribution_contract" in issue or "variant_contract" in issue for issue in ablation_issues)
        primary = "add_diagnostic_run" if needs_design else "continue_same_idea"
        loop_outcome = "final_ablation_plan_required" if needs_design else "final_ablation_run_required"
        next_action = "experiment-design" if needs_design else "experiment-run"
        rationale = f"Our method beats every required baseline; complete pairable final-method ablations: {ablation_issues}."
        existing_run_ids = {
            str(item.get("run_id") or item.get("experiment_id")) if isinstance(item, dict) else str(item)
            for item in plan.get("runs_to_execute", [])
            if item
        }
        if next_action == "experiment-run":
            plan["runs_to_execute"] = sorted(existing_run_ids | set(ablation_ids))
        plan["status"] = "active"
    elif target_reached:
        primary, loop_outcome, next_action = "stop_and_report", "target_reached", "module-attribution"
        stop_reasons = ["all_required_baselines_beaten", "final_ablation_complete"]
        rationale = "Our method beats every required baseline on every comparable surface and final-method ablations are complete."
    elif max_reached:
        primary, loop_outcome, next_action = "stop_and_report", "max_iterations_reached", "evidence-packaging"
        stop_reasons = [f"maximum_{MAX_METHOD_ITERATIONS}_method_iterations"]
        rationale = f"The fixed limit of {MAX_METHOD_ITERATIONS} method implementation/debug iterations was reached."
    else:
        if assessment.get("status") != "complete" or not assessment.get("change_required") or not assessment.get("rationale") or not assessment.get("proposed_changes"):
            primary, loop_outcome, next_action = "add_diagnostic_run", "diagnosis_incomplete", "result-diagnosis"
            rationale = "The diagnosis must complete an evidence-backed method change assessment before another implementation is authorized."
        else:
            change_kind = assessment.get("change_kind")
            if change_kind not in {"implementation_debug", "method_refinement"}:
                primary, loop_outcome, next_action = "scope_change_request", "human_review_required", "human-review"
                rationale = "The diagnosed change is outside the automatically authorized debug/refinement classes."
            else:
                implementation = latest_implementation(result, iteration_id)
                base_source = implementation_worktree(implementation)
                if not base_source:
                    raise SystemExit("Cannot create the next method version without the current implementation worktree")
                next_number = current_number + 1
                next_id = f"ITER-{next_number:02d}"
                primary = "minor_method_fix"
                loop_outcome = "debug_required" if failed or change_kind == "implementation_debug" else "refinement_required"
                next_action = "implementation" if change_kind == "implementation_debug" else "method-refinement"
                rationale = str(assessment["rationale"])
                new_plan = {
                    "schema_version": "external_executor_iteration_plan.v1",
                    "iteration_id": next_id,
                    "iteration_number": next_number,
                    "max_method_iterations": MAX_METHOD_ITERATIONS,
                    "status": "active",
                    "trigger": "failed_run_debug" if failed else "diagnosed_baseline_underperformance",
                    "decision_ref": None,
                    "diagnosis_ref": args.diagnosis,
                    "previous_iteration_id": iteration_id,
                    "previous_implementation_id": implementation.get("implementation_id"),
                    "base_source": base_source,
                    "copy_previous_method": True,
                    "implementation_required": True,
                    "approved_changes": copy.deepcopy(assessment.get("proposed_changes", [])),
                    "must_preserve": copy.deepcopy(assessment.get("must_preserve", [])),
                    "affected_experiments": sorted({str(run.get("experiment_id")) for run in ours if run.get("experiment_id")}),
                    "runs_to_execute": [],
                    "reusable_runs": [str(run.get("run_id")) for run in run_records if run.get("method_role") == "baseline"],
                    "iteration_history_summary": history_summary(result),
                    "evidence_refs": copy.deepcopy(assessment.get("evidence_refs", [])),
                    "budget_before_execution": copy.deepcopy(status.get("budget", {})),
                    "expected_decision_surface": "run_success_and_all_required_baselines_beaten",
                    "created_at": utc_now(),
                }

    active_for_execution = new_plan or (plan if next_action in {"experiment-design", "experiment-run", "result-diagnosis"} else None)
    plan_artifact_path = None
    if active_for_execution:
        active_id = str(active_for_execution.get("iteration_id") or active_for_execution.get("id"))
        plan_artifact_path = ext / "report" / "iteration_plans" / f"{active_id}.json"
        active_for_execution["plan_ref"] = plan_artifact_path.relative_to(root).as_posix()

    did = decision_id(iteration_id, str(diagnosis.get("diagnosis_id") or "unknown"), loop_outcome)
    decision = {
        "schema_version": "external_executor_iteration_decision.v1",
        "decision_id": did,
        "iteration_id": iteration_id,
        "iteration_number": current_number,
        "max_method_iterations": MAX_METHOD_ITERATIONS,
        "decision": primary,
        "loop_outcome": loop_outcome,
        "rationale": rationale,
        "diagnosis_id": diagnosis.get("diagnosis_id"),
        "diagnosis_ref": args.diagnosis,
        "baseline_performance": copy.deepcopy(performance),
        "method_change_assessment": copy.deepcopy(assessment),
        "failed_run_ids": [run.get("run_id") for run in failed],
        "completed_main_run_ids": [run.get("run_id") for run in completed_main],
        "completed_ablation_run_ids": [run.get("run_id") for run in ours if run.get("run_type") == "ablation" and (run.get("run_status") or run.get("status")) == "completed"],
        "completed_ablation_experiment_ids": completed_ablation_ids,
        "ablation_completeness_issues": ablation_issues,
        "stop_reasons": stop_reasons,
        "remaining_method_iterations": max(0, MAX_METHOD_ITERATIONS - current_number),
        "next_action": next_action,
        "created_at": utc_now(),
    }
    if new_plan:
        new_plan["decision_ref"] = did

    finish_current = bool(new_plan) or loop_outcome in {"blocked", "budget_exhausted", "target_reached", "max_iterations_reached", "human_review_required"}
    updated_plans = []
    for candidate in all_plans:
        if str(candidate.get("iteration_id") or candidate.get("id")) == iteration_id:
            previous = copy.deepcopy(candidate)
            previous["status"] = "complete" if finish_current else "active"
            if finish_current:
                previous["completed_by_decision_id"] = did
            updated_plans.append(previous)
        else:
            updated_plans.append(candidate)
    if not any(str(candidate.get("iteration_id") or candidate.get("id")) == iteration_id for candidate in all_plans):
        previous = copy.deepcopy(plan)
        previous["status"] = "complete" if finish_current else plan.get("status", "active")
        previous["completed_by_decision_id"] = did if previous["status"] == "complete" else None
        updated_plans.append(previous)
    if new_plan:
        updated_plans.append(new_plan)

    append_decision(result, decision)
    replace_plans(result, updated_plans, active_for_execution)
    status["executor_status"] = "blocked" if next_action == "human-review" else "running"
    status["current_phase"] = "D" if next_action in {"method-refinement", "implementation", "code-and-protocol-review", "experiment-run", "experiment-design"} else "E" if next_action in {"result-diagnosis", "module-attribution"} else "F"
    status["current_step"] = {"experiment-design": "C", "method-refinement": "D2R", "implementation": "D2I", "experiment-run": "D4", "result-diagnosis": "E1", "module-attribution": "E2", "evidence-packaging": "F1", "human-review": "E3"}.get(next_action, "E3")
    status["iteration_id"] = (new_plan or plan).get("iteration_id")
    status["next_action"] = next_action
    status["iteration_loop"] = {
        "current_iteration": (new_plan or plan).get("iteration_number", current_number),
        "max_iterations": MAX_METHOD_ITERATIONS,
        "last_decision_id": did,
        "outcome": loop_outcome,
    }
    status["updated_at"] = utc_now()
    result["executor_status"] = status["executor_status"]

    output = {"decision": decision, "next_iteration_plan": new_plan, "executor_status": status}
    if not args.dry_run:
        if plan_artifact_path and active_for_execution:
            atomic_write_json(plan_artifact_path, active_for_execution)
        atomic_write_json(result_path, result)
        atomic_write_json(status_path, status)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if next_action == "human-review" else 0


if __name__ == "__main__":
    raise SystemExit(main())
