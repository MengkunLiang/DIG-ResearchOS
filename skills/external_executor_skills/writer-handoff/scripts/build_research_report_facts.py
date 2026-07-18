#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from _common import (
    canonical_hash,
    dump_json_atomic,
    get_nested,
    keyed_paths,
    listify,
    load_json,
    manifest_items,
    normalize_status,
    output_path,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    section_items,
    sha256_file,
    stable_id,
    utc_now,
    walk_dicts,
)


SUCCESS = {"complete", "completed", "pass", "passed", "success", "succeeded", "ready"}
FAILED = {"failed", "blocked", "cancelled"}
INVALID = {"invalid", "unusable", "stale", "superseded"}
WORKSPACE_PREFIXES = (
    "external_executor/", "resources/", "literature/", "ideation/", "novelty/", "user_seeds/",
)


def unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value not in (None, "", [], {})))


def scalar(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        for key in ("statement", "summary", "description", "name", "id", "value"):
            if value.get(key) not in (None, "", [], {}):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return "; ".join(unique(scalar(item) for item in value))
    return str(value)


def first(*values: Any, default: str = "Not recorded") -> str:
    for value in values:
        rendered = scalar(value).strip()
        if rendered:
            return rendered
    return default


def section_records(value: Any) -> list[dict[str, Any]]:
    records = section_items(value)
    if records:
        return records
    if isinstance(value, dict) and any(key.endswith("_id") for key in value):
        return [value]
    return []


def record_id(record: dict[str, Any], *keys: str) -> str:
    for key in (*keys, "experiment_id", "run_id", "claim_id", "record_id", "id"):
        if record.get(key) not in (None, ""):
            return str(record[key])
    return stable_id("REC", canonical_hash(record)[:16])


def status_of(record: dict[str, Any]) -> str:
    return normalize_status(record.get("run_status") or record.get("status"))


def aggregate_experiment_status(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "partial"
    states = {status_of(run) for run in runs}
    if states and states <= SUCCESS:
        return "success"
    if states and states <= FAILED:
        return "failed"
    if states and states <= INVALID:
        return "invalid"
    return "partial"


def record_paths(record: Any) -> list[str]:
    values = [canonical_workspace_path(path) for _, path in keyed_paths(record) if canonical_workspace_path(path)]
    for item in walk_dicts(record):
        if isinstance(item.get("path"), str):
            canonical = canonical_workspace_path(item["path"])
            if canonical:
                values.append(canonical)
    return unique(values)


def canonical_workspace_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.strip().replace("\\", "/")
    if not path or Path(path).is_absolute():
        return None
    if path.startswith(WORKSPACE_PREFIXES):
        return path
    if re.match(r"^[a-z0-9-]+/scripts/", path, flags=re.IGNORECASE):
        return f"external_executor/skills/{path}"
    return None


def path_kind(path: str, key: str = "") -> str:
    lowered = f"{key} {path}".lower()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    suffix = Path(path).suffix.lower()
    if "figure" in tokens or "/figure/" in lowered or suffix in {".svg", ".png", ".pdf", ".eps"}:
        return "figure"
    if "table" in tokens or "/table/" in lowered or suffix in {".csv", ".tsv", ".parquet"} and "/table/" in lowered:
        return "table"
    if "log" in lowered or suffix in {".log", ".out", ".err"}:
        return "log"
    if "config" in lowered or suffix in {".yaml", ".yml", ".toml", ".ini"}:
        return "config"
    if "environment" in lowered or "/env/" in lowered or "dependenc" in lowered:
        return "environment"
    if "raw_results" in lowered or "metric" in lowered or "result" in lowered:
        return "result"
    if suffix in {".py", ".sh", ".r", ".jl", ".ipynb"}:
        return "source_code"
    return "other"


def paths_by_kind(record: Any) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for key, path in keyed_paths(record):
        canonical = canonical_workspace_path(path)
        if canonical:
            output.setdefault(path_kind(canonical, key), []).append(canonical)
    for item in walk_dicts(record):
        if isinstance(item.get("path"), str):
            canonical = canonical_workspace_path(item["path"])
            if canonical:
                output.setdefault(path_kind(canonical), []).append(canonical)
    return {key: unique(value) for key, value in output.items()}


def metric_names(run: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    metrics = run.get("metrics")
    if isinstance(metrics, dict):
        values.extend(metrics.keys())
    elif isinstance(metrics, list):
        for item in metrics:
            values.append(item.get("metric") or item.get("name") if isinstance(item, dict) else item)
    values.extend(listify(run.get("metric_names") or run.get("metric_ids") or run.get("metric")))
    return unique(values)


def project_summary(result: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any]:
    alignment = result.get("context_alignment", {}) if isinstance(result.get("context_alignment"), dict) else {}
    context = handoff.get("context_reboost", {}) if isinstance(handoff.get("context_reboost"), dict) else {}
    intent = handoff.get("method_intent", {}) if isinstance(handoff.get("method_intent"), dict) else {}
    claims = section_records(result.get("claim_evidence_matrix"))
    plan = result.get("experiment_plan", {}) if isinstance(result.get("experiment_plan"), dict) else {}

    hypotheses: list[dict[str, str]] = []
    for index, item in enumerate(section_records(plan.get("hypotheses")) + claims, start=1):
        statement = first(item.get("hypothesis"), item.get("hypothesis_statement"), item.get("formal_hypothesis"), default="")
        if statement:
            hypotheses.append({"hypothesis_id": str(item.get("hypothesis_id") or f"H{index}"), "statement": statement})
    if not hypotheses:
        for index, value in enumerate(listify(context.get("hypotheses") or handoff.get("hypotheses")), start=1):
            hypotheses.append({"hypothesis_id": f"H{index}", "statement": scalar(value)})

    contributions: list[dict[str, str]] = []
    candidates = listify(context.get("expected_contributions") or context.get("contributions") or intent.get("expected_contributions"))
    for index, value in enumerate(candidates, start=1):
        if isinstance(value, dict):
            contributions.append({
                "contribution_id": str(value.get("contribution_id") or value.get("id") or f"CONTR-{index}"),
                "statement": first(value.get("statement"), value.get("summary"), value.get("description")),
            })
        else:
            contributions.append({"contribution_id": f"CONTR-{index}", "statement": scalar(value)})

    completed_work = []
    for key in (
        "baseline_reproductions", "baseline_reproduction", "method_refinements", "implementations",
        "implementation_reviews", "experiment_runs", "result_diagnoses", "module_attributions",
        "realized_method_package", "framework_figure", "figure_table_inventory", "evidence_packaging",
    ):
        value = result.get(key)
        if value in (None, {}, []):
            continue
        state = value.get("status") if isinstance(value, dict) else "recorded"
        completed_work.append({"workstream": key, "status": str(state or "recorded")})

    changes = []
    realized = result.get("realized_method_package", {}) if isinstance(result.get("realized_method_package"), dict) else {}
    for item in listify(realized.get("delta_from_method_intent")):
        changes.append({"change": scalar(item), "reason": "Recorded by the realized method package"})
    for item in section_records(result.get("scope_change_requests")):
        changes.append({
            "change": first(item.get("proposed_change"), item.get("summary"), item.get("description")),
            "reason": first(item.get("reason"), item.get("rationale"), item.get("status")),
        })
    for item in section_records(result.get("iteration_decisions")):
        if item.get("decision") in {"claim_narrowing", "scope_change_request"}:
            changes.append({"change": first(item.get("decision")), "reason": first(item.get("rationale"), item.get("reason"))})

    return {
        "research_question": first(
            alignment.get("research_question"), context.get("research_question"),
            get_nested(context, "study_scope.research_question"), intent.get("research_question"),
        ),
        "hypotheses": hypotheses,
        "expected_contributions": contributions,
        "completed_work": completed_work,
        "plan_changes": changes,
        "plan_comparison_note": first(
            realized.get("intent_comparison_summary"), result.get("plan_change_summary"),
            default="No explicit T4.5/T5 plan-change summary was recorded. The detailed deltas below remain authoritative.",
        ),
    }


def implementation_summary(ws: Path, result: dict[str, Any]) -> dict[str, Any]:
    method = result.get("realized_method_package", {}) if isinstance(result.get("realized_method_package"), dict) else {}
    implementations = section_records(result.get("implementations"))
    active_id = get_nested(result, "implementations.active_implementation_id")
    active = next((item for item in implementations if str(item.get("implementation_id")) == str(active_id)), implementations[-1] if implementations else {})
    modules = []
    for item in section_records(method.get("implemented_modules")):
        modules.append({
            "module_id": record_id(item, "module_id"),
            "name": first(item.get("name"), item.get("module_name")),
            "role": first(item.get("actual_role"), item.get("role"), item.get("description")),
            "paths": unique(record_paths(item)),
            "config_keys": unique(listify(item.get("config_keys"))),
            "support_status": first(get_nested(item, "empirical_support.status"), item.get("status")),
        })
    combined_paths = paths_by_kind({"method": method, "active_implementation": active, "runs": result.get("experiment_runs")})
    dependencies = unique(listify(method.get("dependencies")) + listify(active.get("dependencies")))
    for raw_path in combined_paths.get("environment", []):
        try:
            path = resolve_in_workspace(ws, raw_path)
            if not path.is_file() or path.suffix.lower() != ".json" or path.stat().st_size > 10 * 1024 * 1024:
                continue
            environment = load_json(path)
        except Exception:
            continue
        if isinstance(environment, dict):
            for key in ("python", "python_version", "platform", "cuda", "cuda_version"):
                if environment.get(key) not in (None, ""):
                    dependencies.append(f"{key}={environment[key]}")
            packages = environment.get("packages") or environment.get("dependencies")
            if isinstance(packages, dict):
                dependencies.extend(f"{name}={version}" for name, version in packages.items())
            elif isinstance(packages, list):
                dependencies.extend(scalar(item) for item in packages)
    unresolved = unique(
        listify(method.get("unresolved_fields"))
        + [first(item.get("summary"), item.get("message"), item.get("description")) for item in section_records(result.get("material_gaps"))]
    )
    return {
        "method_name": first(method.get("final_method_name"), method.get("method_name")),
        "method_summary": first(method.get("one_sentence_method"), method.get("actual_core_mechanism")),
        "implementation_id": first(active.get("implementation_id"), get_nested(method, "final_version.implementation_id")),
        "implementation_root": first(active.get("implementation_root"), active.get("worktree"), get_nested(method, "final_version.implementation_root")),
        "modules": modules,
        "code_entrypoints": combined_paths.get("source_code", []),
        "configurations": combined_paths.get("config", []),
        "environments": combined_paths.get("environment", []),
        "dependencies": unique(dependencies),
        "data_processing_flow": listify(method.get("training_flow")) + listify(method.get("inference_flow")),
        "design_differences": listify(method.get("delta_from_method_intent")),
        "incomplete_items": unresolved,
    }


def claims_by_experiment(claims: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        for exp_id in unique(listify(claim.get("experiment_ids") or claim.get("experiment_id") or claim.get("supporting_experiments"))):
            output.setdefault(exp_id, []).append(claim)
    return output


def asset_experiment_links(
    ws: Path,
    snapshot: dict[str, Any],
    result: dict[str, Any],
    claims: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    table_links: dict[str, list[str]] = {}
    table_experiments: dict[str, list[str]] = {}
    for asset in snapshot.get("assets", []):
        if asset.get("kind") != "table" or Path(str(asset.get("path"))).suffix.lower() not in {".csv", ".tsv"}:
            continue
        path = resolve_in_workspace(ws, str(asset["path"]))
        if not path.is_file() or sha256_file(path) != asset.get("sha256"):
            continue
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter=delimiter))
        except Exception:
            continue
        exp_ids = unique(
            exp_id
            for row in rows
            for exp_id in str(row.get("experiment_ids") or row.get("experiment_id") or "").split(";")
        )
        table_experiments[str(asset["path"])] = exp_ids
        for exp_id in exp_ids:
            table_links.setdefault(exp_id, []).append(str(asset["path"]))

    claim_experiments = {
        str(claim.get("claim_id")): unique(listify(claim.get("experiment_ids") or claim.get("experiment_id")))
        for claim in claims if claim.get("claim_id")
    }
    figure_links: dict[str, list[str]] = {}
    packaging = result.get("evidence_packaging", {}) if isinstance(result.get("evidence_packaging"), dict) else {}
    result_figures = packaging.get("result_figures", {}) if isinstance(packaging.get("result_figures"), dict) else {}
    figure_records = section_records(result_figures)
    for item in figure_records:
        path = item.get("path")
        source_table = item.get("source_table_ref")
        for exp_id in table_experiments.get(str(source_table), []):
            if path:
                figure_links.setdefault(exp_id, []).append(str(path))
    for item in section_records(result.get("figure_table_inventory")):
        path = item.get("path")
        if not path:
            rendered = item.get("rendered_files")
            if isinstance(rendered, list) and rendered:
                first_render = rendered[0]
                path = first_render.get("path") if isinstance(first_render, dict) else first_render
        if not path:
            continue
        exp_ids = unique(listify(item.get("experiment_ids") or item.get("experiment_id")))
        for claim_id in unique(listify(item.get("claim_ids") or item.get("claim_id"))):
            exp_ids.extend(claim_experiments.get(claim_id, []))
        target = table_links if "table" in str(item.get("kind") or item.get("type") or "").lower() else figure_links
        for exp_id in unique(exp_ids):
            target.setdefault(exp_id, []).append(str(path))
    return (
        {key: unique(value) for key, value in table_links.items()},
        {key: unique(value) for key, value in figure_links.items()},
    )


def experiment_inventory(ws: Path, snapshot: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    plan = result.get("experiment_plan", {}) if isinstance(result.get("experiment_plan"), dict) else {}
    planned = section_records(plan.get("experiments")) or section_records(plan)
    runs = section_records(result.get("experiment_runs"))
    claims = section_records(result.get("claim_evidence_matrix"))
    table_links, figure_links = asset_experiment_links(ws, snapshot, result, claims)
    by_claim_exp = claims_by_experiment(claims)
    plan_by_id = {record_id(item): item for item in planned}
    run_by_id: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        exp_id = str(run.get("experiment_id") or "UNASSIGNED")
        run_by_id.setdefault(exp_id, []).append(run)
    experiment_ids = unique([*plan_by_id.keys(), *run_by_id.keys()])
    output = []
    for exp_id in experiment_ids:
        spec = plan_by_id.get(exp_id, {})
        exp_runs = run_by_id.get(exp_id, [])
        related_claims = by_claim_exp.get(exp_id, [])
        roles = [(str(run.get("method_role") or "").lower(), run) for run in exp_runs]
        methods = unique(
            run.get("method_id") or run.get("method_name") or run.get("implementation_id")
            for role, run in roles if role != "baseline" and not run.get("baseline_id")
        )
        baselines = unique(run.get("baseline_id") or run.get("method_id") for role, run in roles if role == "baseline" or run.get("baseline_id"))
        all_paths = paths_by_kind(exp_runs)
        hypotheses = unique(
            listify(spec.get("hypothesis_ids") or spec.get("hypothesis_id"))
            + [claim.get("hypothesis_id") for claim in related_claims]
        )
        contributions = unique(
            listify(spec.get("contribution_ids") or spec.get("contribution_id"))
            + [claim.get("contribution_id") for claim in related_claims]
        )
        output.append({
            "experiment_id": exp_id,
            "objective": first(spec.get("objective"), spec.get("purpose"), spec.get("reviewer_question")),
            "hypotheses": hypotheses,
            "contributions": contributions,
            "datasets": unique(
                listify(spec.get("datasets") or spec.get("dataset_ids") or spec.get("dataset"))
                + [run.get("dataset") or run.get("dataset_id") for run in exp_runs]
            ),
            "methods": methods,
            "baselines": baselines,
            "configurations": all_paths.get("config", []),
            "random_seeds": unique(
                [run.get("seed") for run in exp_runs]
                + [value for run in exp_runs for value in listify(run.get("seeds"))]
            ),
            "metrics": unique(value for run in exp_runs for value in metric_names(run)),
            "status": aggregate_experiment_status(exp_runs),
            "result_files": all_paths.get("result", []),
            "log_files": all_paths.get("log", []),
            "figures": unique(all_paths.get("figure", []) + figure_links.get(exp_id, [])),
            "tables": unique(all_paths.get("table", []) + table_links.get(exp_id, [])),
            "run_ids": unique(record_id(run, "run_id") for run in exp_runs),
        })
    return output


def read_csv_checked(ws: Path, asset: dict[str, Any]) -> list[dict[str, str]]:
    path = resolve_in_workspace(ws, str(asset["path"]))
    if not path.is_file() or sha256_file(path) != asset.get("sha256"):
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def figure_refs_for(assets: list[dict[str, Any]], kind: str, dataset: str, metric: str) -> list[str]:
    tokens = [token.lower().replace("_", "-") for token in (kind, dataset, metric) if token]
    output = []
    for asset in assets:
        if asset.get("kind") != "figure":
            continue
        name = Path(str(asset.get("path"))).name.lower().replace("_", "-")
        if all(token in name for token in tokens):
            output.append(str(asset["path"]))
    return unique(output)


def result_records(ws: Path, snapshot: dict[str, Any], experiments: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    table_assets = {Path(str(item["path"])).name: item for item in snapshot.get("assets", []) if item.get("kind") == "table"}
    figures = [item for item in snapshot.get("assets", []) if item.get("kind") == "figure"]
    experiment_ids = {item["experiment_id"] for item in experiments}
    processing_scripts = unique(
        canonical_workspace_path(path)
        for key, path in keyed_paths(result.get("evidence_packaging", {}))
        if ("script" in key or "script" in path.lower()) and canonical_workspace_path(path)
    )
    runs = section_records(result.get("experiment_runs"))

    def statistical_tests(exp_ids: list[str]) -> list[str]:
        return unique(
            path
            for run in runs
            if str(run.get("experiment_id") or "") in exp_ids
            for key, path in keyed_paths(run)
            if "statistical" in key or "significance" in key or "statistical" in path.lower() or "significance" in path.lower()
        )

    output: list[dict[str, Any]] = []
    for filename, kind in (("main_comparison.csv", "main"), ("ablation_results.csv", "ablation"), ("other_experiments.csv", "other")):
        asset = table_assets.get(filename)
        if not asset:
            continue
        rows = read_csv_checked(ws, asset)
        groups: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = {}
        for row in rows:
            key = (
                row.get("dataset") or "unknown", row.get("split") or "unknown",
                row.get("metric") or "unknown", row.get("metric_direction") or "unknown",
                row.get("protocol_fingerprint") or "unknown",
            )
            groups.setdefault(key, []).append(row)
        for (dataset, split, metric, direction, protocol), records in sorted(groups.items()):
            ours = [row for row in records if row.get("method_role") == "ours"]
            baselines = [row for row in records if row.get("method_role") == "baseline"]
            pairs: list[tuple[dict[str, str], dict[str, str] | None]]
            if kind == "main" and ours and baselines:
                pairs = [(left, right) for left in ours for right in baselines]
            elif kind == "ablation" and len(records) >= 2:
                reference = next((row for row in records if str(row.get("variant")).lower() in {"full", "reference", "ours"}), records[0])
                pairs = [(reference, row) for row in records if row is not reference]
            else:
                pairs = [(row, None) for row in records]
            for left, right in pairs:
                exp_ids = unique(
                    str(left.get("experiment_ids") or "").split(";")
                    + (str(right.get("experiment_ids") or "").split(";") if right else [])
                )
                exp_ids = [item for item in exp_ids if item in experiment_ids] or exp_ids
                left_value = finite(left.get("mean"))
                right_value = finite(right.get("mean")) if right else None
                comparison = "observation"
                if right_value is not None and left_value is not None:
                    better = left_value > right_value if direction == "higher" else left_value < right_value if direction == "lower" else None
                    comparison = "favorable" if better is True else "unfavorable" if better is False else "direction_unknown"
                sources = unique(
                    str(left.get("source_files") or "").split(";")
                    + (str(right.get("source_files") or "").split(";") if right else [])
                )
                label_left = left.get("baseline_id") or left.get("variant") or left.get("method_id") or "method"
                label_right = (right.get("baseline_id") or right.get("variant") or right.get("method_id")) if right else None
                result_id = stable_id("RESULT", kind, dataset, split, metric, protocol, label_left, label_right)
                tests = statistical_tests(exp_ids)
                output.append({
                    "result_id": result_id,
                    "result_kind": kind,
                    "experiment_ids": exp_ids,
                    "dataset": dataset,
                    "split": split,
                    "metric": metric,
                    "metric_direction": direction,
                    "protocol_fingerprint": protocol,
                    "method": label_left,
                    "method_mean": left_value,
                    "method_std": finite(left.get("std")),
                    "method_n": int(float(left.get("n") or 0)),
                    "comparator": label_right,
                    "comparator_mean": right_value,
                    "comparator_std": finite(right.get("std")) if right else None,
                    "comparator_n": int(float(right.get("n") or 0)) if right else None,
                    "comparison_outcome": comparison,
                    "statistical_test": "; ".join(tests) if tests else "Not recorded",
                    "raw_result_files": sources,
                    "table_files": [str(asset["path"])],
                    "figure_files": figure_refs_for(figures, kind, dataset, metric),
                    "processing_scripts": processing_scripts,
                    "supports": "A bounded empirical comparison under the recorded dataset, split, metric, and protocol.",
                    "does_not_support": "Generalization beyond the recorded setting, causal mechanism claims, or statistical significance without a linked test.",
                })

    normalized_asset = table_assets.get("all_results.csv")
    if normalized_asset:
        normalized_rows = read_csv_checked(ws, normalized_asset)
        for row in normalized_rows:
            source = str(row.get("source_file") or "")
            represented = any(
                source in item.get("raw_result_files", [])
                and str(row.get("dataset") or "unknown") == item.get("dataset")
                and str(row.get("metric") or "unknown") == item.get("metric")
                for item in output
            )
            if represented:
                continue
            exp_ids = unique([row.get("experiment_id")])
            kind = "ablation" if str(row.get("run_type")).lower() == "ablation" else "other"
            output.append({
                "result_id": stable_id(
                    "RESULT", "normalized", row.get("dataset"), row.get("split"), row.get("metric"),
                    row.get("protocol_fingerprint"), row.get("method_id"), row.get("run_id"),
                ),
                "result_kind": kind,
                "experiment_ids": exp_ids,
                "dataset": row.get("dataset") or "unknown",
                "split": row.get("split") or "unknown",
                "metric": row.get("metric") or "unknown",
                "metric_direction": row.get("metric_direction") or "unknown",
                "protocol_fingerprint": row.get("protocol_fingerprint") or "unknown",
                "method": row.get("method_id") or "unknown",
                "method_mean": finite(row.get("value")),
                "method_std": None,
                "method_n": 1,
                "comparator": None,
                "comparator_mean": None,
                "comparator_std": None,
                "comparator_n": None,
                "comparison_outcome": "observation_without_comparator",
                "statistical_test": "; ".join(statistical_tests(exp_ids)) or "Not recorded",
                "raw_result_files": [source] if source else [],
                "table_files": [str(normalized_asset["path"])],
                "figure_files": figure_refs_for(figures, kind, str(row.get("dataset") or "unknown"), str(row.get("metric") or "unknown")),
                "processing_scripts": processing_scripts,
                "supports": "A source-bound observation under the recorded run conditions.",
                "does_not_support": "A comparative advantage, statistical significance, causal mechanism, or generalization without a matched comparator and linked evidence.",
            })
    return output


def claim_support(result: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims = section_records(result.get("claim_evidence_matrix"))
    output = []
    for claim in claims:
        claim_id = record_id(claim, "claim_id")
        exp_ids = unique(listify(claim.get("experiment_ids") or claim.get("experiment_id")))
        related = [item for item in results if set(exp_ids) & set(item.get("experiment_ids", []))]
        files = unique(path for item in related for path in item.get("raw_result_files", []) + item.get("table_files", []) + item.get("figure_files", []))
        main_results = [item for item in related if item.get("result_kind") == "main"]
        if main_results and all(item.get("comparison_outcome") == "favorable" for item in main_results):
            strength = "Supported candidate"
        elif any(item.get("comparison_outcome") == "favorable" for item in related):
            strength = "Partially supported candidate"
        else:
            strength = "Unsupported"
        output.append({
            "claim_id": claim_id,
            "proposed_claim": first(claim.get("claim"), claim.get("statement"), claim.get("summary"), claim.get("reviewer_question")),
            "supporting_experiments": exp_ids,
            "supporting_files": files,
            "strength": strength,
            "limitation": first(claim.get("limitations"), claim.get("must_not_claim"), default="No claim-specific limitation was recorded; global boundaries still apply."),
            "authority": "Preliminary external-executor organization only. T8 performs final claim adjudication.",
        })
    return output


def verified_literature(ws: Path, snapshot: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("verified_literature_additions", "literature_additions", "executor_verified_references"):
        for item in section_records(result.get(key)):
            candidates.append({**item, "_writer_handoff_source_path": f"external_executor/result_pack.json#{key}"})
    manifest = snapshot.get("documents", {}).get("run_manifest", {})
    for artifact in manifest_items(manifest if isinstance(manifest, dict) else {}):
        raw_path = artifact.get("path") or artifact.get("artifact_path")
        if not isinstance(raw_path, str):
            continue
        lowered = raw_path.lower()
        if not any(token in lowered for token in ("verified_reference", "verified-references", "literature_addition", "literature-addition")):
            continue
        try:
            path = resolve_in_workspace(ws, raw_path)
            if not path.is_file() or path.suffix.lower() != ".json" or path.stat().st_size > 10 * 1024 * 1024:
                continue
            value = load_json(path)
        except Exception:
            continue
        for item in section_records(value):
            candidates.append({**item, "_writer_handoff_source_path": raw_path})
    output = []
    seen = set()
    for item in candidates:
        identifiers = {
            "doi": item.get("doi"),
            "openalex_id": item.get("openalex_id"),
            "semantic_scholar_id": item.get("semantic_scholar_id") or item.get("s2_id"),
            "other_id": item.get("identifier") or item.get("url"),
        }
        identifiers = {key: str(value) for key, value in identifiers.items() if value not in (None, "")}
        title = scalar(item.get("title")).strip()
        if not title or not identifiers:
            continue
        signature = canonical_hash({"title": title, "identifiers": identifiers})
        if signature in seen:
            continue
        seen.add(signature)
        output.append({
            "title": title,
            "authors": first(item.get("authors")),
            "year": first(item.get("year")),
            "venue": first(item.get("venue"), item.get("journal"), item.get("conference")),
            "identifiers": identifiers,
            "supported_point": first(item.get("supported_point"), item.get("supports"), item.get("claim_supported")),
            "used_material": first(item.get("used_material"), item.get("definition_or_method_used")),
            "access_level": first(item.get("access_level"), default="Not recorded"),
            "bibtex_or_reference": first(item.get("bibtex"), item.get("citation"), item.get("standard_reference")),
            "source_paths": unique(record_paths(item) + [item.get("_writer_handoff_source_path")]),
        })
    return output


def limitations_and_issues(result: dict[str, Any], experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for key, category in (
        ("material_gaps", "experimental coverage"), ("resource_risks", "compute or resource"),
        ("open_risks", "open risk"), ("open_blockers", "blocker"),
    ):
        for item in section_records(result.get(key)):
            output.append({
                "issue_id": record_id(item, "risk_id", "limitation_id", "blocker_id"),
                "category": category,
                "description": first(item.get("summary"), item.get("message"), item.get("description")),
                "source_refs": unique([record_id(item)] + record_paths(item)),
            })
    for item in section_records(result.get("experiment_runs")):
        if status_of(item) not in SUCCESS:
            output.append({
                "issue_id": record_id(item, "run_id"),
                "category": "failed or incomplete experiment",
                "description": first(item.get("failure_reason"), item.get("error"), item.get("status")),
                "source_refs": unique([record_id(item, "run_id")] + record_paths(item)),
            })
    boundary = result.get("claim_boundary") or get_nested(result, "realized_method_package.claim_boundary") or {}
    for index, statement in enumerate(listify(boundary.get("must_not_claim") if isinstance(boundary, dict) else boundary), start=1):
        output.append({
            "issue_id": f"MUST-NOT-CLAIM-{index}",
            "category": "prohibited over-claim",
            "description": scalar(statement),
            "source_refs": ["result_pack.json#claim_boundary"],
        })
    if not experiments:
        output.append({
            "issue_id": "NO-EXPERIMENT-INVENTORY",
            "category": "experimental coverage",
            "description": "No planned or executed experiment could be resolved from result_pack.json.",
            "source_refs": ["external_executor/result_pack.json"],
        })
    return output


def artifact_index(ws: Path, snapshot: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = snapshot.get("documents", {}).get("run_manifest", {})
    entries: dict[str, dict[str, Any]] = {}
    for name, item in snapshot.get("core_files", {}).items():
        if not isinstance(item, dict) or not item.get("path"):
            continue
        path = str(item["path"])
        entries[path] = {
            "category": "core handoff file", "path": path, "sha256": item.get("sha256"),
            "size_bytes": item.get("size_bytes"), "source": f"writer_handoff_snapshot:{name}",
        }
    for item in manifest_items(manifest if isinstance(manifest, dict) else {}):
        path = item.get("path") or item.get("artifact_path")
        if not isinstance(path, str) or not path:
            continue
        entries[path] = {
            "category": path_kind(path), "path": path, "sha256": item.get("sha256"),
            "size_bytes": item.get("size_bytes"), "source": "run_manifest",
        }
    for asset in snapshot.get("assets", []):
        entries[str(asset["path"])] = {
            "category": str(asset.get("kind")), "path": str(asset["path"]),
            "sha256": asset.get("sha256"), "size_bytes": asset.get("size_bytes"), "source": "writer_handoff_snapshot",
        }
    for path in record_paths(result):
        try:
            resolved = resolve_in_workspace(ws, path)
        except Exception:
            continue
        if path not in entries:
            entries[path] = {
                "category": path_kind(path), "path": path,
                "sha256": sha256_file(resolved) if resolved.is_file() else None,
                "size_bytes": resolved.stat().st_size if resolved.is_file() else None,
                "source": "result_pack_reference",
            }
    entries["external_executor/executor_research_report.md"] = {
        "category": "T8 input document", "path": "external_executor/executor_research_report.md",
        "sha256": None, "size_bytes": None, "source": "writer-handoff",
    }
    return [entries[key] for key in sorted(entries)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build source-bound facts for executor_research_report.md.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/phase_F/writer_handoff_snapshot.json")
    parser.add_argument("--output")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    documents = snapshot.get("documents", {})
    result = documents.get("result_pack", {}) if isinstance(documents.get("result_pack"), dict) else {}
    status = documents.get("executor_status", {}) if isinstance(documents.get("executor_status"), dict) else {}
    handoff = documents.get("handoff_pack", {}) if isinstance(documents.get("handoff_pack"), dict) else {}
    experiments = experiment_inventory(ws, snapshot, result)
    results = result_records(ws, snapshot, experiments, result)
    claims = claim_support(result, results)
    literature = verified_literature(ws, snapshot, result)
    facts = {
        "schema_version": "writer_handoff_facts.v1",
        "handoff_id": snapshot.get("handoff_id"),
        "input_fingerprint": snapshot.get("input_fingerprint"),
        "executor_status": normalize_status(status.get("executor_status") or status.get("status") or status.get("current_state")),
        "result_pack_status": normalize_status(result.get("executor_status") or result.get("status")),
        "project_summary": project_summary(result, handoff),
        "implementation_summary": implementation_summary(ws, result),
        "experiments": experiments,
        "comprehensive_results": results,
        "claim_support": claims,
        "verified_literature_additions": literature,
        "limitations_and_open_issues": limitations_and_issues(result, experiments),
        "artifact_index": artifact_index(ws, snapshot, result),
        "coverage": {
            "experiment_count": len(experiments),
            "successful_experiment_count": sum(item["status"] == "success" for item in experiments),
            "result_record_count": len(results),
            "claim_count": len(claims),
            "verified_literature_addition_count": len(literature),
            "figure_count": sum(item.get("kind") == "figure" for item in snapshot.get("assets", [])),
            "table_count": sum(item.get("kind") == "table" for item in snapshot.get("assets", [])),
        },
        "created_at": utc_now(),
    }
    facts["facts_fingerprint"] = canonical_hash({key: value for key, value in facts.items() if key not in {"created_at", "facts_fingerprint"}})
    destination = output_path(ws, args.output, "external_executor/report/phase_F/writer_handoff_facts.json")
    dump_json_atomic(destination, facts)
    print(json.dumps(facts["coverage"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
