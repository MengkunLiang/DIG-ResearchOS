#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    get_nested,
    listify,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
)


def approved_baselines(result: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    items = get_nested(result, "baseline_candidates.items", default=listify(result.get("baseline_candidates")))
    output: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            output.append({
                "baseline_id": item.get("baseline_id") or item.get("candidate_id") or item.get("name"),
                "name": item.get("name") or item.get("baseline_name") or item.get("candidate_id"),
                "resource_ref": item.get("artifact_ref") or item.get("path") or item.get("candidate_id"),
                "required": item.get("required", True),
                "approximation_level": item.get("approximation_level", "unknown"),
            })
    if output:
        return output
    for item in listify(scope.get("required_baselines")):
        if isinstance(item, str):
            output.append({"baseline_id": item, "name": item, "resource_ref": None, "required": True})
        elif isinstance(item, dict):
            output.append({
                "baseline_id": item.get("baseline_id") or item.get("name"),
                "name": item.get("name"),
                "resource_ref": item.get("resource_ref"),
                "required": item.get("required", True),
            })
    return output


def experiment_base(exp_id: str, name: str, role: str, run_type: str, kind: str, protocol: dict[str, Any]) -> dict[str, Any]:
    p = protocol.get("protocol", {})
    preprocessing_fingerprint = (
        get_nested(p, "preprocessing.fingerprint")
        or get_nested(p, "preprocessing_fingerprint")
        or get_nested(protocol, "fingerprints.preprocessing")
    )
    if not preprocessing_fingerprint and nonempty(get_nested(p, "dataset.preprocessing")):
        preprocessing_fingerprint = canonical_json_hash(get_nested(p, "dataset.preprocessing"))
    fairness_fingerprint = (
        get_nested(p, "fairness.fingerprint")
        or get_nested(p, "fairness_fingerprint")
        or get_nested(protocol, "fingerprints.fairness")
    )
    if not fairness_fingerprint and nonempty(get_nested(p, "hyperparameters.fairness_rule")):
        fairness_fingerprint = canonical_json_hash(get_nested(p, "hyperparameters.fairness_rule"))
    return {
        "experiment_id": exp_id,
        "name": name,
        "experiment_kind": kind,
        "analysis_role": role,
        "run_type": run_type,
        "status": "planned",
        "claim_ids": [],
        "reviewer_question": None,
        "evidence_needed": [],
        "dataset": get_nested(p, "dataset.name"),
        "dataset_version": get_nested(p, "dataset.version"),
        "split": get_nested(p, "dataset.split"),
        "variants": [],
        "baseline_refs": [],
        "mechanism_ref": None,
        "ablation_action": None,
        "ablation_replacements": [],
        "metrics": get_nested(p, "metrics.primary", default=[]),
        "secondary_metrics": get_nested(p, "metrics.secondary", default=[]),
        "metric_directions": get_nested(p, "metrics.directions", default={}),
        "preprocessing_fingerprint": preprocessing_fingerprint,
        "fairness_fingerprint": fairness_fingerprint,
        "setting": "default",
        "subset": "all",
        "seeds": get_nested(p, "seeds_and_repeats.seeds", default=[]),
        "seed_count": get_nested(p, "seeds_and_repeats.seed_count"),
        "repeats": get_nested(p, "seeds_and_repeats.repeats"),
        "protocol_fingerprint": protocol.get("protocol_fingerprint"),
        "preconditions": [],
        "depends_on": [],
        "expected_artifacts": ["run_record", "config", "raw_log", "metric_output"],
        "estimated_cost": {"runs": None, "gpu_hours": None, "wall_clock_hours": None, "monetary_cost": None},
        "decision_rule": None,
        "interpretation_if_positive": None,
        "interpretation_if_negative": None,
        "interpretation_if_inconclusive": None,
        "failure_handling": "retain failed runs and classify before exclusion",
        "risk_refs": [],
        "source_refs": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a versioned, claim-bound experiment plan scaffold.")
    parser.add_argument("--workspace")
    parser.add_argument("--claims", default="external_executor/report/claim_evidence_matrix.json")
    parser.add_argument("--protocol", default="external_executor/report/protocol_snapshot.json")
    parser.add_argument("--fingerprint", default="external_executor/report/protocol_fingerprint.json")
    parser.add_argument("--output", default="external_executor/experiment_plan.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    result = load_json(ext / "result_pack.json")
    handoff = load_json(ext / "handoff_pack.json")
    claims = load_json(resolve_in_workspace(ws, args.claims))
    protocol = load_json(resolve_in_workspace(ws, args.protocol))
    fp = load_json(resolve_in_workspace(ws, args.fingerprint))
    protocol["protocol_fingerprint"] = fp.get("fingerprint")
    scope = get_nested(result, "context_alignment.confirmed_execution_scope", default={})
    baselines = approved_baselines(result, scope)
    risks = [
        item.get("risk_id") or item.get("gap_id") or item.get("requirement_id")
        for section in ("resource_risks", "material_gaps")
        for item in get_nested(result, f"{section}.items", default=listify(result.get(section)))
        if isinstance(item, dict)
    ]

    experiments: list[dict[str, Any]] = []
    baseline_exp_ids: list[str] = []
    for baseline in baselines:
        bid = str(baseline.get("baseline_id") or baseline.get("name"))
        exp_id = stable_id("EXP-BL", bid)
        exp = experiment_base(exp_id, f"Reproduce {baseline.get('name') or bid}", "confirmatory", "formal", "baseline_reproduction", protocol)
        exp["variants"] = [baseline.get("name") or bid]
        exp["baseline_refs"] = [baseline.get("resource_ref")] if baseline.get("resource_ref") else []
        exp["preconditions"] = ["resource_review_pass", "code_and_protocol_review_before_formal"]
        exp["decision_rule"] = "record protocol-aligned metric and reproduction fidelity; do not infer superiority"
        exp["interpretation_if_positive"] = "baseline is available for fair formal comparison"
        exp["interpretation_if_negative"] = "classify reproduction failure and propagate claim risk"
        exp["interpretation_if_inconclusive"] = "repair, rerun, or mark unavailable under root decision"
        exp["risk_refs"] = risks
        exp["source_refs"] = ["result_pack.json#baseline_candidates", "protocol_snapshot.json"]
        experiments.append(exp)
        baseline_exp_ids.append(exp_id)

    smoke_id = stable_id("EXP", "ours-smoke")
    smoke = experiment_base(smoke_id, "Ours smoke validation", "diagnostic", "smoke", "ours_smoke", protocol)
    smoke["variants"] = ["ours"]
    smoke["preconditions"] = ["method_spec_available", "implementation_available", "review_approved_for_smoke"]
    smoke["decision_rule"] = "pass only when data loading, forward/backward path, metric, and logging complete without invalid values"
    smoke["interpretation_if_positive"] = "implementation is eligible for small-scale review"
    smoke["interpretation_if_negative"] = "repair implementation; no research conclusion"
    smoke["interpretation_if_inconclusive"] = "collect diagnostics; no research conclusion"
    smoke["expected_artifacts"] += ["smoke_diagnostics"]
    experiments.append(smoke)

    explicit_minimum = listify(scope.get("minimum_experiment_loop")) + listify(get_nested(handoff, "context_reboost.minimum_experiment_loop"))
    claim_main_ids: list[str] = []
    for claim in claims.get("items", []):
        if not isinstance(claim, dict) or claim.get("status") == "unsupported":
            continue
        cid = claim.get("claim_id")
        statement = claim.get("statement")
        exp_id = stable_id("EXP-MAIN", cid, statement)
        exp = experiment_base(exp_id, f"Main evidence for {cid}", claim.get("analysis_role", "confirmatory"), "formal", "main_comparison", protocol)
        exp["claim_ids"] = [cid]
        exp["reviewer_question"] = (claim.get("reviewer_questions") or [None])[0]
        exp["evidence_needed"] = claim.get("evidence_needed", [])
        exp["variants"] = ["ours"] + [b.get("name") or b.get("baseline_id") for b in baselines]
        exp["baseline_refs"] = [b.get("resource_ref") for b in baselines if b.get("resource_ref")]
        exp["depends_on"] = baseline_exp_ids + [smoke_id]
        exp["preconditions"] = ["required_baselines_reproduced_or_claim_constrained", "review_approved_for_formal"]
        exp["decision_rule"] = "evaluate the predeclared primary metric under the shared protocol; retain all planned seeds/repeats"
        exp["interpretation_if_positive"] = "supports only the mapped claim within the declared boundary and uncertainty policy"
        exp["interpretation_if_negative"] = "weakens or refutes the mapped claim; diagnose before refinement"
        exp["interpretation_if_inconclusive"] = "do not promote the claim; inspect variance, power, fidelity, and protocol risks"
        exp["risk_refs"] = risks
        exp["source_refs"] = claim.get("source_refs", []) + ["protocol_snapshot.json"]
        experiments.append(exp)
        claim_main_ids.append(exp_id)
        claim["planned_experiment_ids"] = unique_strings(claim.get("planned_experiment_ids", []) + [exp_id])
        claim["status"] = "planned"

    ablation_specs = listify(get_nested(handoff, "method_intent.mechanism_to_ablation_plan"))
    for index, spec in enumerate(ablation_specs):
        if not isinstance(spec, dict):
            continue
        mechanism = spec.get("mechanism") or spec.get("module") or f"mechanism-{index + 1}"
        exp_id = stable_id("EXP-ABL", mechanism, index)
        exp = experiment_base(exp_id, f"Mechanism ablation: {mechanism}", "confirmatory", "ablation", "mechanism_ablation", protocol)
        related_claim = spec.get("related_claim") or spec.get("claim_id")
        exp["claim_ids"] = unique_strings(listify(related_claim))
        exp["reviewer_question"] = spec.get("reviewer_question") or f"Is the observed effect attributable to {mechanism} rather than a confound?"
        exp["evidence_needed"] = unique_strings(listify(spec.get("planned_test")) + ["controlled mechanism comparison"])
        exp["variants"] = unique_strings(["ours", spec.get("planned_test") or spec.get("replacement") or f"without {mechanism}"])
        exp["mechanism_ref"] = mechanism
        exp["ablation_action"] = spec.get("action", "REMOVE_OR_REPLACE_AS_SPECIFIED")
        exp["ablation_replacements"] = listify(spec.get("replacement"))
        target_module_ids = unique_strings(
            listify(spec.get("target_module_ids"))
            + listify(spec.get("module_ids"))
            + listify(spec.get("target_module_id"))
            + listify(spec.get("module_id"))
            + listify(spec.get("related_module"))
            + listify(spec.get("module"))
            + listify(spec.get("mechanism_id"))
        )
        reference_variant_id = f"{exp_id}:full"
        intervention_variant_id = f"{exp_id}:intervention"
        exp["target_module_ids"] = target_module_ids
        exp["attribution_contract"] = {
            "target_module_ids": target_module_ids,
            "reference_variant_id": reference_variant_id,
            "variant_contracts": [
                {
                    "variant_id": reference_variant_id,
                    "reference_variant_id": reference_variant_id,
                    "module_states": {module_id: True for module_id in target_module_ids},
                    "intervention": {"type": "none", "controlled": True, "module_ids": target_module_ids},
                },
                {
                    "variant_id": intervention_variant_id,
                    "reference_variant_id": reference_variant_id,
                    "module_states": {module_id: False for module_id in target_module_ids},
                    "intervention": {
                        "type": "module_ablation",
                        "controlled": True,
                        "module_ids": target_module_ids,
                        "action": exp["ablation_action"],
                        "replacements": exp["ablation_replacements"],
                    },
                },
            ],
            "pairing_dimensions": [
                "implementation_id", "protocol_fingerprint", "dataset.id", "dataset.version", "dataset.split",
                "preprocessing_fingerprint", "setting", "subset", "metric", "seed", "repeat_index",
                "fairness_fingerprint",
            ],
            "required_run_fields": [
                "variant_id", "reference_variant_id", "pair_id", "target_module_ids", "module_states",
                "intervention", "preprocessing_fingerprint", "fairness_fingerprint", "metric_directions",
            ],
        }
        exp["variants"] = [reference_variant_id, intervention_variant_id]
        exp["depends_on"] = [smoke_id] + claim_main_ids
        exp["preconditions"] = ["ablation_switch_reviewed", "review_approved_for_ablation"]
        exp["decision_rule"] = "compare controlled variants with all non-target protocol factors fixed"
        exp["interpretation_if_positive"] = spec.get("expected_observation_if_supported")
        exp["interpretation_if_negative"] = spec.get("expected_observation_if_not_supported")
        exp["interpretation_if_inconclusive"] = "mark mechanism unsupported or add a predeclared controlled diagnostic"
        exp["risk_refs"] = risks
        exp["source_refs"] = ["handoff_pack.json#method_intent.mechanism_to_ablation_plan"]
        experiments.append(exp)

    # Explicit minimum-loop entries not represented above are retained as planning gaps, not guessed experiments.
    represented_text = " ".join((e["name"] + " " + e["experiment_kind"]).lower() for e in experiments)
    unexpanded_minimum = [str(item) for item in explicit_minimum if str(item).strip() and str(item).lower() not in represented_text]

    old_plan = load_json(output) if output.exists() else (result.get("experiment_plan") if isinstance(result.get("experiment_plan"), dict) else None)
    input_fp = canonical_json_hash({"claims": claims, "protocol": protocol, "resources": result.get("resource_readiness"), "minimum": explicit_minimum})
    old_version = old_plan.get("plan_version", 0) if isinstance(old_plan, dict) else 0
    changed = isinstance(old_plan, dict) and old_plan.get("input_fingerprint") != input_fp
    plan_version = old_version + 1 if changed else max(1, old_version)

    # Preserve project-specific completions by stable experiment ID when rebuilding.
    if isinstance(old_plan, dict) and not args.force:
        old_map = {e.get("experiment_id"): e for e in old_plan.get("experiments", []) if isinstance(e, dict)}
        for exp in experiments:
            prior = old_map.get(exp["experiment_id"])
            if not prior:
                continue
            for key in (
                "reviewer_question", "evidence_needed", "variants", "baseline_refs", "metrics", "secondary_metrics",
                "seeds", "seed_count", "repeats", "preconditions", "depends_on", "expected_artifacts",
                "estimated_cost", "decision_rule", "interpretation_if_positive", "interpretation_if_negative",
                "interpretation_if_inconclusive", "risk_refs", "notes",
            ):
                if exp.get("run_type") == "ablation" and key == "variants":
                    continue
                if nonempty(prior.get(key)):
                    exp[key] = deepcopy(prior[key])

    nodes = [{"experiment_id": e["experiment_id"], "priority": index + 1} for index, e in enumerate(experiments)]
    edges = [
        {"from": dep, "to": e["experiment_id"], "type": "requires"}
        for e in experiments for dep in e.get("depends_on", [])
    ]
    parallel_groups = [
        {"group_id": "baseline-reproductions", "experiment_ids": baseline_exp_ids, "condition": "independent resources and compute available"},
        {"group_id": "claim-main-comparisons", "experiment_ids": claim_main_ids, "condition": "shared protocol and no resource collision"},
    ]
    parallel_groups = [g for g in parallel_groups if len(g["experiment_ids"]) > 1]

    budget = get_nested(protocol, "protocol.compute_budget", default={})
    plan = {
        "schema_version": "experiment_plan.v1",
        "plan_version": plan_version,
        "generated_at": utc_now(),
        "status": "needs_review",
        "input_fingerprint": input_fp,
        "supersedes_plan_version": old_version if changed and old_version else None,
        "protocol_snapshot": protocol,
        "protocol_fingerprint": fp,
        "claim_evidence_matrix": claims,
        "experiments": experiments,
        "execution_dag": {"nodes": nodes, "edges": edges, "parallel_groups": parallel_groups},
        "budget": budget,
        "estimated_budget": {"total_runs": None, "total_gpu_hours": None, "total_wall_clock_hours": None, "total_cost": None},
        "max_refinement_rounds": budget.get("max_refinement_rounds"),
        "early_stop": get_nested(protocol, "protocol.early_stop", default={}),
        "unexpanded_minimum_loop_items": unexpanded_minimum,
        "unsupported_claims": [
            {"claim_id": c.get("claim_id"), "reason": c.get("unsupported_reason")}
            for c in claims.get("items", []) if c.get("status") == "unsupported"
        ],
        "resource_constraints": {
            "readiness_status": get_nested(result, "resource_readiness.status"),
            "claim_constraints": get_nested(result, "resource_readiness.claim_constraints", default=[]),
            "blocking_issues": get_nested(result, "resource_readiness.blocking_issues", default=[]),
            "risk_refs": risks,
        },
        "plan_review": {"status": "not_started", "findings": [], "required_fixes": [], "approved_for_phase_d": False},
        "design_gate": {"status": "not_evaluated", "blocking_issues": [], "constraints": [], "next_action": None},
        "change_log": [],
        "notes": [],
    }
    dump_json_atomic(output, plan)
    # Keep the standalone claim matrix synchronized with generated experiment IDs.
    dump_json_atomic(resolve_in_workspace(ws, args.claims), claims)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
