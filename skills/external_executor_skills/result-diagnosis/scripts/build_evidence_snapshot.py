#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    active_iteration_id, artifact_ref, assert_write_allowed, canonical_hash, dump_json_atomic,
    get_nested, listify, load_json, metric_direction, relpath, resolve_in_workspace,
    resolve_workspace, section_items, stable_id, utc_now,
)

TERMINAL = {"completed", "failed", "cancelled", "stale", "unusable"}
FORMAL_TYPES = {"formal", "ablation", "robustness", "efficiency"}
SMALL_TYPES = {"small_scale", "small-scale"}
DIAG_TYPES = {"diagnostic", "exploratory"}
ENGINEERING_TYPES = {"smoke", "toy", "synthetic", "dry_run", "dry-run", "engineering"}


def run_type(run: dict[str, Any]) -> str:
    return str(run.get("run_type") or run.get("type") or "unknown").lower()


def method_identity(run: dict[str, Any]) -> tuple[str | None, str]:
    role = str(run.get("method_role") or run.get("role") or "").lower()
    mid = run.get("method_id") or run.get("baseline_id") or run.get("variant_id") or run.get("method")
    if not role:
        role = "baseline" if run.get("baseline_id") or run.get("is_baseline") else ("ours" if run.get("is_ours") else "other")
    return (str(mid) if mid is not None else None, role)


def has_metric(run: dict[str, Any]) -> bool:
    return bool(run.get("metrics") or run.get("metric_output") or run.get("metric_outputs") or run.get("metric_output_ref"))


def raw_ref_path(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("path"), str):
        return value["path"]
    if isinstance(value, str):
        return value
    return ""


def is_raw_results_ref(value: Any) -> bool:
    path = raw_ref_path(value).lstrip("./")
    return path == "external_executor/raw_results" or path.startswith("external_executor/raw_results/")


def dataset_fields(run: dict[str, Any]) -> tuple[Any, Any, Any]:
    dataset = run.get("dataset")
    if isinstance(dataset, dict):
        return (
            dataset.get("id") or dataset.get("name"),
            dataset.get("version") or run.get("dataset_version"),
            dataset.get("split") or run.get("dataset_split") or run.get("split"),
        )
    return dataset or run.get("dataset_id"), run.get("dataset_version"), run.get("dataset_split") or run.get("split")


def baseline_metric_map(metrics: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for item in metrics.get("items", []) if isinstance(metrics, dict) else []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        output[str(item["name"])] = {
            "value": item.get("value"),
            "direction": item.get("direction"),
            "unit": item.get("units"),
            "aggregation": item.get("aggregation") or "reported",
            "source_ref": item.get("raw_csv_path"),
        }
    return output


def baseline_reproduction_runs(ws: Path, result: dict[str, Any], iteration_id: str) -> list[dict[str, Any]]:
    """Materialize reviewed baseline attempts as read-only comparison records."""
    section = result.get("baseline_reproduction")
    items = section.get("items", []) if isinstance(section, dict) else []
    materialized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in {"reproduced", "partially_reproduced"}:
            continue
        review = item.get("review") if isinstance(item.get("review"), dict) else {}
        if review.get("verdict") != "pass" or review.get("approved_for") != "formal_review_candidate":
            continue
        selected = item.get("selected_attempt_id")
        attempts = item.get("attempts", []) if isinstance(item.get("attempts"), list) else []
        attempt = next((x for x in attempts if isinstance(x, dict) and (x.get("attempt_id") == selected or x.get("run_id") == selected)), None)
        if attempt is None and attempts:
            attempt = next((x for x in reversed(attempts) if isinstance(x, dict)), None)
        if not attempt or not attempt.get("run_record_ref"):
            continue
        try:
            record_path = resolve_in_workspace(ws, str(attempt["run_record_ref"]))
            record = load_json(record_path)
            metrics_ref = attempt.get("metrics_ref") or attempt.get("metric_report_ref")
            metrics_path = resolve_in_workspace(ws, str(metrics_ref)) if metrics_ref else record_path.parent / "metrics.json"
            metrics_doc = load_json(metrics_path)
        except Exception:
            continue
        metrics = baseline_metric_map(metrics_doc)
        baseline_dataset, baseline_dataset_version, baseline_split = dataset_fields(record)
        seeds = record.get("seeds") if isinstance(record.get("seeds"), list) else []
        materialized.append({
            **record,
            "iteration_id": str(iteration_id),
            "experiment_id": item.get("experiment_id") or item.get("reproduction_id") or item.get("baseline_id"),
            "method_id": item.get("baseline_id") or item.get("candidate_id"),
            "method_role": "baseline",
            "implementation_id": item.get("reproduction_id") or record.get("run_id"),
            "run_type": "formal",
            "analysis_role": "confirmatory",
            "run_status": "completed" if record.get("status") == "completed" else record.get("status"),
            "review_approval": "formal",
            "dataset": baseline_dataset,
            "dataset_version": baseline_dataset_version,
            "dataset_split": baseline_split,
            "seed": record.get("seed") if record.get("seed") is not None else (seeds[0] if seeds else None),
            "repeat_id": record.get("repeat_id") or attempt.get("attempt_id") or record.get("run_id"),
            "config_ref": record.get("deployment_dir") or record.get("working_directory"),
            "raw_log_ref": record.get("stdout_path"),
            "metric_output_ref": None,
            "metrics": metrics,
            "environment_ref": record.get("environment_path"),
            "code_version": record.get("source_manifest_sha256"),
            "resource_version": item.get("candidate_id"),
            "protocol_fingerprint": item.get("protocol_fingerprint") or record.get("protocol_fingerprint"),
            "fairness_fingerprint": item.get("fairness_fingerprint") or record.get("fairness_fingerprint"),
            "baseline_reproduction_ref": relpath(ws, record_path),
            "metrics_report_ref": relpath(ws, metrics_path),
        })
    return materialized


def formal_provenance_missing(run: dict[str, Any]) -> list[str]:
    checks = {
        "config_ref": run.get("config_ref") or run.get("config") or run.get("config_path"),
        "dataset_split": run.get("dataset_split") or run.get("split"),
        "seed_or_repeat": run.get("seed") is not None or run.get("repeat_id") is not None,
        "code_version": run.get("code_version") or run.get("implementation_id") or run.get("patch_id"),
        "raw_log_ref": run.get("raw_log_ref") or run.get("log_ref") or run.get("raw_log_path"),
        "metric_output": run.get("metric_output_ref") or run.get("metric_output") or run.get("metrics"),
        "environment": run.get("environment_ref") or run.get("environment") or run.get("hardware"),
        "protocol_fingerprint": run.get("protocol_fingerprint"),
    }
    return [k for k, v in checks.items() if not v]


def classify(run: dict[str, Any]) -> tuple[str, list[str]]:
    reasons = []
    status = str(run.get("run_status") or run.get("status") or "unknown")
    rtype = run_type(run)
    mid, _ = method_identity(run)
    if status not in TERMINAL:
        return "excluded", ["non_terminal"]
    if status != "completed":
        return "excluded", [{"failed": "failed_run", "cancelled": "cancelled_run", "stale": "stale_run", "unusable": "unusable_run"}.get(status, "unusable_run")]
    if not mid:
        reasons.append("missing_method_identity")
    if not has_metric(run):
        reasons.append("missing_metric")
    if rtype in FORMAL_TYPES:
        missing = formal_provenance_missing(run)
        if missing:
            reasons.append("missing_formal_provenance:" + ",".join(missing))
        approval = run.get("review_approval") or run.get("approved_for") or get_nested(run, "review.approved_for", default=[])
        approval_values = set(str(x) for x in listify(approval))
        if not approval_values or not ({"formal", "formal_run", "formal_comparison"} & approval_values):
            reasons.append("review_not_approved")
        return ("formal_candidate" if not reasons else "excluded"), reasons
    if rtype in SMALL_TYPES:
        return ("small_scale" if not reasons else "excluded"), reasons
    if rtype in DIAG_TYPES:
        return ("diagnostic" if not reasons else "excluded"), reasons
    if rtype in ENGINEERING_TYPES:
        return ("engineering" if not reasons else "excluded"), reasons
    reasons.append("unknown_run_type")
    return "excluded", reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Pin and classify one iteration's run evidence.")
    parser.add_argument("--workspace")
    parser.add_argument("--iteration-id")
    parser.add_argument("--output", default="external_executor/report/phase_E/diagnosis_evidence_snapshot.json")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    result = load_json(ext / "result_pack.json")
    iteration_id = args.iteration_id or active_iteration_id(result)
    if not iteration_id:
        raise SystemExit("Cannot identify iteration")
    output = resolve_in_workspace(ws, args.output)
    iteration_runs = [r for r in section_items(result.get("experiment_runs")) if str(r.get("iteration_id")) == str(iteration_id)]
    baseline_runs = baseline_reproduction_runs(ws, result, str(iteration_id))
    existing_ids = {str(r.get("run_id")) for r in iteration_runs if r.get("run_id")}
    runs = iteration_runs + [r for r in baseline_runs if str(r.get("run_id")) not in existing_ids]
    plan = result.get("experiment_plan", {})
    protocol_fp = plan.get("protocol_fingerprint") or result.get("protocol_fingerprint")
    claim_items = section_items(result.get("claim_evidence_matrix"))
    metric_contracts = plan.get("metrics") or plan.get("primary_metrics") or []
    if isinstance(metric_contracts, dict):
        metric_contracts = [metric_contracts]
    elif not isinstance(metric_contracts, list):
        metric_contracts = []
    exp_to_claims: dict[str, list[str]] = {}
    for claim in claim_items:
        cid = claim.get("claim_id")
        for exp in listify(claim.get("experiment_ids") or claim.get("experiments") or claim.get("experiment_id")):
            if isinstance(exp, dict): exp = exp.get("experiment_id") or exp.get("id")
            if exp and cid: exp_to_claims.setdefault(str(exp), []).append(str(cid))

    items = []
    for index, run in enumerate(runs, 1):
        rid = str(run.get("run_id") or stable_id("RUNRAW", iteration_id, index, canonical_hash(run)))
        exp_id = str(run.get("experiment_id") or run.get("plan_experiment_id") or "unknown")
        mid, role = method_identity(run)
        eligibility, reasons = classify(run)
        evidence_id = stable_id("RUN", rid, canonical_hash(run))
        dataset, dataset_version, split = dataset_fields(run)
        item = {
            "evidence_id": evidence_id,
            "run_id": rid,
            "iteration_id": str(iteration_id),
            "experiment_id": exp_id,
            "claim_ids": sorted(set(str(x) for x in listify(run.get("claim_ids")) + exp_to_claims.get(exp_id, []))),
            "method_id": mid,
            "method_role": role,
            "run_type": run_type(run),
            "analysis_role": str(run.get("analysis_role") or "unknown"),
            "status": run.get("run_status") or run.get("status"),
            "eligibility": eligibility,
            "exclusion_reasons": reasons,
            "setting": run.get("setting") or run.get("setting_id") or run.get("subset") or "default",
            "dataset": dataset,
            "dataset_version": dataset_version,
            "split": split,
            "preprocessing_fingerprint": run.get("preprocessing_fingerprint"),
            "protocol_fingerprint": run.get("protocol_fingerprint") or protocol_fp,
            "fairness_fingerprint": run.get("fairness_fingerprint"),
            "seed": run.get("seed"),
            "repeat_id": run.get("repeat_id"),
            "code_version": run.get("code_version") or run.get("implementation_id") or run.get("patch_id"),
            "resource_version": run.get("resource_version") or run.get("baseline_resource_version"),
            "review_approval": run.get("review_approval") or run.get("approved_for") or get_nested(run, "review.approved_for", default=[]),
            "metrics": run.get("metrics") or run.get("metric_output") or run.get("metric_outputs"),
            "metric_output_ref": run.get("metric_output_ref"),
            "raw_log_ref": run.get("raw_log_ref") or run.get("log_ref") or run.get("raw_log_path"),
            "config_ref": run.get("config_ref") or run.get("config_path") or run.get("config"),
            "environment_ref": run.get("environment_ref") or run.get("environment") or run.get("hardware"),
            "artifact_refs": run.get("artifact_refs", []),
            "source_record": run,
        }
        for key in ("metric_output_ref", "raw_log_ref"):
            if item.get(key) and not is_raw_results_ref(item[key]):
                item["eligibility"] = "excluded"
                item["exclusion_reasons"].append(f"{key}_outside_raw_results")
        items.append(item)

    included = [x["run_id"] for x in items if x["eligibility"] != "excluded"]
    excluded = [x["run_id"] for x in items if x["eligibility"] == "excluded"]
    snapshot = {
        "schema_version": "diagnosis_evidence_snapshot.v1",
        "generated_at": utc_now(),
        "status": "complete" if included and not excluded else ("partial" if included else "blocked"),
        "iteration_id": str(iteration_id),
        "protocol_fingerprint": protocol_fp,
        "input_fingerprint": canonical_hash({"runs": runs, "plan": plan, "claims": claim_items, "baseline_reproduction": result.get("baseline_reproduction"), "reviews": result.get("implementation_reviews")}),
        "runs": items,
        "included_run_ids": included,
        "excluded_run_ids": excluded,
        "claim_contracts": claim_items,
        "metric_contracts": metric_contracts,
        "required_baselines": get_nested(result, "context_alignment.confirmed_execution_scope.required_baselines", default=[]),
        "baseline_reproduction": result.get("baseline_reproduction"),
        "implementation_reviews": result.get("implementation_reviews"),
        "experiment_plan_ref": "external_executor/result_pack.json#experiment_plan",
        "limitations": [],
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, snapshot)
    print(f"{snapshot['status']}: included={len(included)} excluded={len(excluded)}")
    # Failure-only iterations are still valid diagnosis inputs: they must flow
    # through result-diagnosis and become an explicit repair/debug decision.
    return 0 if items else 2


if __name__ == "__main__":
    raise SystemExit(main())
