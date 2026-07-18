#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import ensure_known_ids, load_json, resolve_in_workspace, resolve_workspace

TOP_REQUIRED = {
    "schema_version", "child_skill", "status", "generated_at", "input_fingerprint", "policy_snapshot",
    "resource_requirement_matrix", "local_inventory", "remote_search_records", "staged_resources",
    "acquired_resources", "baseline_candidates", "dataset_inventory", "reimplementations",
    "resource_source_report", "resource_reviews", "material_gaps", "resource_risks",
    "resource_readiness", "artifact_refs", "notes",
}
SECTION_STATUSES = {"not_started", "not_needed", "complete", "partial", "blocked", "stale"}
READINESS = {"ready", "partial", "blocked"}
CHILD_STATUS = {"complete", "partial", "blocked", "failed"}
VERDICTS = {"pass", "needs_fix", "blocked"}
APPROVALS = {"static_inspection", "smoke_preparation", "experiment_design", "baseline_reproduction", "formal_comparison", "dataset_use", "metric_use", "preprocessing_use", "checkpoint_use", "none"}
EXECUTABLE_BASELINE_CRITERIA = {
    "accessible_code_or_model",
    "revision_locked",
    "license_clear",
    "environment_or_dependencies",
    "dataset_version_and_split",
    "metric_implementation",
    "traceable_result_record",
}


def _is_outside_resources_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/").strip().lstrip("./").rstrip("/")
    return bool(normalized) and not (normalized == "resources" or normalized.startswith("resources/"))


def _candidate_paths(candidate: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("path", "local_path", "source_path", "destination_path"):
        value = candidate.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("paths", "source_paths", "artifact_paths"):
        value = candidate.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if item)
    return values


def _criterion_passed(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, dict):
        return value.get("status") in {"pass", "present", "confirmed"} or value.get("available") is True
    return False


def validate_data(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    missing = sorted(TOP_REQUIRED - set(data))
    if missing:
        errors.append(f"missing top-level keys: {missing}")
    if data.get("schema_version") != "resource_preparation_report.v1":
        errors.append("schema_version must be resource_preparation_report.v1")
    if data.get("child_skill") != "resource-and-baseline-preparation":
        errors.append("child_skill mismatch")
    if data.get("status") not in CHILD_STATUS:
        errors.append(f"invalid child status: {data.get('status')}")

    section_names = [
        "resource_requirement_matrix", "local_inventory", "remote_search_records", "staged_resources",
        "acquired_resources", "baseline_candidates", "dataset_inventory", "reimplementations",
        "resource_reviews", "material_gaps", "resource_risks",
    ]
    for name in section_names:
        section = data.get(name)
        if not isinstance(section, dict):
            errors.append(f"{name} must be an object")
            continue
        if section.get("status") not in SECTION_STATUSES:
            errors.append(f"invalid {name}.status: {section.get('status')}")
        if not isinstance(section.get("items", []), list):
            errors.append(f"{name}.items must be a list")

    matrix = data.get("resource_requirement_matrix", {})
    requirements = matrix.get("items", []) if isinstance(matrix, dict) else []
    req_ids = [r.get("requirement_id") for r in requirements if isinstance(r, dict)]
    if None in req_ids or "" in req_ids:
        errors.append("every requirement needs requirement_id")
    if len(req_ids) != len(set(req_ids)):
        errors.append("requirement IDs are not unique")
    known_req = set(req_ids)

    candidates = []
    for name in ("baseline_candidates", "dataset_inventory", "staged_resources", "acquired_resources", "reimplementations"):
        candidates.extend(data.get(name, {}).get("items", []))
    candidate_ids = [c.get("candidate_id") for c in candidates if isinstance(c, dict) and c.get("candidate_id")]
    known_candidates = set(candidate_ids)
    if len(candidate_ids) != len(set(candidate_ids)):
        warnings.append("candidate IDs repeat across sections; ensure records are aliases, not conflicting copies")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            errors.append("candidate item must be object")
            continue
        errors.extend(ensure_known_ids(candidate.get("requirement_ids", []), known_req, "requirement ID in candidate"))
        for candidate_path in _candidate_paths(candidate):
            if _is_outside_resources_path(candidate_path):
                errors.append(f"candidate path is outside resources/: {candidate.get('candidate_id')}")

    reviews = data.get("resource_reviews", {}).get("items", [])
    for review in reviews:
        if not isinstance(review, dict):
            errors.append("review item must be object")
            continue
        if review.get("verdict") not in VERDICTS:
            errors.append(f"invalid review verdict: {review.get('verdict')}")
        if review.get("candidate_id") not in known_candidates:
            errors.append(f"review references unknown candidate: {review.get('candidate_id')}")
        errors.extend(ensure_known_ids(review.get("requirement_ids", []), known_req, "requirement ID in review"))
        unknown_approvals = set(review.get("approved_for", [])) - APPROVALS
        if unknown_approvals:
            errors.append(f"unknown approved_for values: {sorted(unknown_approvals)}")
        if review.get("verdict") != "pass" and set(review.get("approved_for", [])) & {"baseline_reproduction", "formal_comparison", "dataset_use", "metric_use"}:
            errors.append(f"non-pass review grants execution approval: {review.get('review_id')}")
        baseline_req_ids = [
            req.get("requirement_id")
            for req in requirements
            if isinstance(req, dict)
            and req.get("resource_type") == "baseline_implementation"
            and req.get("requirement_id") in set(review.get("requirement_ids", []))
        ]
        if baseline_req_ids and set(review.get("approved_for", [])) & {"baseline_reproduction", "formal_comparison"}:
            criteria = review.get("executable_baseline_criteria")
            if not isinstance(criteria, dict):
                errors.append(f"executable baseline review lacks criteria object: {review.get('review_id')}")
            else:
                missing = sorted(key for key in EXECUTABLE_BASELINE_CRITERIA if not _criterion_passed(criteria.get(key)))
                if missing:
                    errors.append(f"executable baseline criteria not satisfied for {review.get('review_id')}: {missing}")

    source_report = data.get("resource_source_report")
    if not isinstance(source_report, dict):
        errors.append("resource_source_report must be an object")
    else:
        if source_report.get("status") not in SECTION_STATUSES:
            errors.append(f"invalid resource_source_report.status: {source_report.get('status')}")
        categories = source_report.get("categories")
        if not isinstance(categories, dict):
            errors.append("resource_source_report.categories must be an object")
        else:
            for category in ("byhand", "Remote_acquisition", "reproduction"):
                if category not in categories:
                    errors.append(f"resource_source_report missing category: {category}")
        if data.get("status") == "complete" and source_report.get("status") != "complete":
            errors.append("complete resource preparation requires a complete resource_source_report")

    gap_req_ids = set()
    for gap in data.get("material_gaps", {}).get("items", []):
        if isinstance(gap, dict):
            gap_req_ids.update(gap.get("requirement_ids", []))
            if gap.get("requirement_id"):
                gap_req_ids.add(gap["requirement_id"])
    reviewed_req_ids = {req for review in reviews if isinstance(review, dict) for req in review.get("requirement_ids", [])}
    represented_req_ids = reviewed_req_ids | gap_req_ids | {req for c in candidates if isinstance(c, dict) for req in c.get("requirement_ids", [])}
    for req in requirements:
        if req.get("resource_type") == "baseline_implementation" and req.get("required") and req.get("requirement_id") not in represented_req_ids:
            errors.append(f"required baseline is neither candidate, reviewed, nor gap: {req.get('requirement_id')}")

    readiness = data.get("resource_readiness")
    if not isinstance(readiness, dict):
        errors.append("resource_readiness must be an object")
    else:
        status = readiness.get("status")
        if status not in READINESS:
            errors.append(f"invalid resource_readiness.status: {status}")
        for field in ("approved_requirement_ids", "constrained_requirement_ids", "blocking_requirement_ids"):
            values = readiness.get(field, [])
            if not isinstance(values, list):
                errors.append(f"resource_readiness.{field} must be a list")
            else:
                errors.extend(ensure_known_ids(values, known_req, f"{field}"))
        blocking = readiness.get("blocking_requirement_ids", [])
        constrained = readiness.get("constrained_requirement_ids", [])
        if status == "ready" and (blocking or constrained or readiness.get("claim_constraints") or not readiness.get("minimum_loop_feasible")):
            errors.append("ready is inconsistent with blockers/constraints/infeasible minimum loop")
        if status == "partial" and (blocking or not readiness.get("minimum_loop_feasible")):
            errors.append("partial requires feasible minimum loop and no blocking requirement IDs")
        if status == "partial" and not (constrained or readiness.get("claim_constraints") or data.get("material_gaps", {}).get("items") or data.get("resource_risks", {}).get("items")):
            errors.append("partial needs at least one documented constraint, gap, or risk")
        if status == "blocked" and readiness.get("minimum_loop_feasible") and not blocking and not readiness.get("blocking_issues"):
            errors.append("blocked needs infeasible minimum loop or explicit blockers")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase B report structure and gate consistency.")
    parser.add_argument("--workspace")
    parser.add_argument("--report", default="external_executor/report/resource_preparation_report.json")
    args = parser.parse_args()
    workspace = resolve_workspace(args.workspace)
    path = resolve_in_workspace(workspace, args.report)
    data = load_json(path)
    errors, warnings = validate_data(data)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"validation: {len(errors)} errors, {len(warnings)} warnings")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
