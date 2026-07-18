#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from _common import (
    canonical_hash,
    dump_json_atomic,
    load_json,
    manifest_items,
    normalize_status,
    output_path,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    schema_major,
    sha256_file,
    utc_now,
)


TERMINAL_STATUSES = {"completed", "partial", "blocked", "failed"}
REQUIRED_HEADINGS = [
    "## 1. Project Summary",
    "## 2. Implementation Summary",
    "## 3. Experiment Inventory",
    "## 4. Comprehensive Results",
    "## 5. Claim Support Table",
    "## 6. Verified Literature Additions",
    "## 7. Limitations and Open Issues",
    "## 8. Artifact Index",
]
EXPERIMENT_COLUMNS = [
    "Experiment ID", "Objective", "Hypothesis", "Contribution", "Dataset", "Method", "Baseline",
    "Configuration", "Random Seeds", "Metrics", "Status", "Result Files", "Log Files", "Figures", "Tables",
]
CLAIM_COLUMNS = ["Claim ID", "Proposed Claim", "Supporting Experiment", "Supporting File", "Strength", "Limitation"]
FORBIDDEN_PHRASES = (
    "state-of-the-art", "state of the art", "proves that", "proven that",
    "demonstrates conclusively", "universally superior", "paper-ready",
)


def add(bucket: list[dict[str, str]], code: str, path: str, message: str) -> None:
    bucket.append({"code": code, "path": path, "message": message})


def read_object(ws: Path, rel: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    path = resolve_in_workspace(ws, rel)
    if not path.is_file():
        add(errors, "missing_required_file", rel, "required final handoff file is missing")
        return {}
    try:
        value = load_json(path)
    except Exception as exc:  # noqa: BLE001
        add(errors, "invalid_json", rel, str(exc))
        return {}
    if not isinstance(value, dict):
        add(errors, "invalid_json_type", rel, "expected a JSON object")
        return {}
    return value


def current_core_hashes(ws: Path) -> dict[str, str]:
    paths = {
        "result_pack": "external_executor/result_pack.json",
        "executor_status": "external_executor/executor_status.json",
        "run_manifest": "external_executor/report/run_manifest.json",
        "handoff_pack": "external_executor/handoff_pack.json",
        "expected_outputs_schema": "external_executor/expected_outputs_schema.json",
    }
    return {
        name: sha256_file(resolve_in_workspace(ws, rel))
        for name, rel in paths.items()
        if resolve_in_workspace(ws, rel).is_file()
    }


def current_assets(ws: Path) -> list[dict[str, Any]]:
    output = []
    for kind in ("figure", "table"):
        root = ws / "external_executor" / kind
        for path in sorted(item for item in root.rglob("*") if item.is_file()) if root.is_dir() else []:
            output.append({"kind": kind, "path": relpath(ws, path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    return output


def validate_manifest(
    ws: Path,
    manifest: dict[str, Any],
    assets: list[dict[str, Any]],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> dict[str, int]:
    indexed: dict[str, dict[str, Any]] = {}
    checked = 0
    for item in manifest_items(manifest):
        raw_path = item.get("path") or item.get("artifact_path")
        if not isinstance(raw_path, str) or not raw_path:
            add(errors, "manifest_entry_missing_path", "external_executor/report/run_manifest.json", "artifact entry has no path")
            continue
        if raw_path in indexed:
            add(warnings, "duplicate_manifest_path", raw_path, "multiple manifest records reference the same path")
        indexed[raw_path] = item
        try:
            path = resolve_in_workspace(ws, raw_path)
        except Exception as exc:  # noqa: BLE001
            add(errors, "manifest_path_escape", raw_path, str(exc))
            continue
        if not path.is_file():
            add(errors, "manifest_artifact_missing", raw_path, "registered artifact is missing")
            continue
        checked += 1
        actual_hash = sha256_file(path)
        if item.get("sha256") and item.get("sha256") != actual_hash:
            add(errors, "manifest_checksum_mismatch", raw_path, "registered checksum differs from the file")
        if item.get("size_bytes") is not None and int(item.get("size_bytes")) != path.stat().st_size:
            add(errors, "manifest_size_mismatch", raw_path, "registered size differs from the file")
    for asset in assets:
        path = str(asset["path"])
        suffix = Path(path).suffix.lower()
        allowed = {".svg", ".png"} if asset.get("kind") == "figure" else {".csv", ".tsv"}
        if int(asset.get("size_bytes") or 0) <= 0:
            add(errors, "empty_final_asset", path, "final figures and tables must be nonempty")
        if suffix not in allowed:
            add(errors, "unsupported_final_asset_format", path, f"allowed formats are {sorted(allowed)}")
        item = indexed.get(path)
        if not item:
            add(errors, "final_asset_unregistered", path, "every final figure and table must be registered in run_manifest.json")
        elif item.get("sha256") and item.get("sha256") != asset.get("sha256"):
            add(errors, "final_asset_manifest_hash_mismatch", path, "final asset differs from its manifest record")
    return {"manifest_artifacts_checked": checked, "manifest_paths": len(indexed)}


def validate_report(
    ws: Path,
    report_text: str,
    facts: dict[str, Any],
    assets: list[dict[str, Any]],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> dict[str, int]:
    rel = "external_executor/executor_research_report.md"
    for heading in REQUIRED_HEADINGS:
        if heading not in report_text:
            add(errors, "missing_report_section", rel, heading)
    for column in EXPERIMENT_COLUMNS:
        if column not in report_text:
            add(errors, "missing_experiment_inventory_column", rel, column)
    for column in CLAIM_COLUMNS:
        if column not in report_text:
            add(errors, "missing_claim_support_column", rel, column)
    if "—" in report_text:
        add(errors, "em_dash_forbidden", rel, "use ordinary academic sentences rather than em-dash structure")
    lowered = report_text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            add(errors, "forbidden_authority_or_promotional_phrase", rel, phrase)

    for experiment in facts.get("experiments", []):
        exp_id = str(experiment.get("experiment_id") or "")
        if exp_id and exp_id not in report_text:
            add(errors, "experiment_omitted_from_report", rel, exp_id)
    for result in facts.get("comprehensive_results", []):
        result_id = str(result.get("result_id") or "")
        if result_id and result_id not in report_text:
            add(errors, "result_omitted_from_report", rel, result_id)
        source_paths = result.get("raw_result_files", []) + result.get("table_files", []) + result.get("figure_files", [])
        if not result.get("raw_result_files"):
            add(errors, "result_without_raw_source", result_id, "observed results require raw result paths")
        for path in source_paths:
            if str(path) not in report_text:
                add(errors, "result_source_omitted_from_report", rel, str(path))
    result_experiment_ids = {
        str(exp_id)
        for item in facts.get("comprehensive_results", [])
        for exp_id in item.get("experiment_ids", [])
        if exp_id
    }
    for experiment in facts.get("experiments", []):
        exp_id = str(experiment.get("experiment_id") or "")
        if experiment.get("status") == "success" and exp_id not in result_experiment_ids:
            add(errors, "successful_experiment_missing_comprehensive_result", rel, exp_id)
    for claim in facts.get("claim_support", []):
        claim_id = str(claim.get("claim_id") or "")
        if claim_id and claim_id not in report_text:
            add(errors, "claim_omitted_from_report", rel, claim_id)
    for item in facts.get("verified_literature_additions", []):
        for identifier in item.get("identifiers", {}).values():
            if str(identifier) not in report_text:
                add(errors, "verified_reference_identifier_omitted", rel, str(identifier))
    for asset in assets:
        if str(asset["path"]) not in report_text:
            add(errors, "final_asset_omitted_from_artifact_index", rel, str(asset["path"]))

    for match in re.finditer(r"`([^`]+)`", report_text):
        raw = match.group(1)
        if not raw.startswith(("external_executor/", "resources/", "literature/", "ideation/", "novelty/")):
            continue
        path_text = raw.split("#", 1)[0]
        try:
            path = resolve_in_workspace(ws, path_text)
        except Exception as exc:  # noqa: BLE001
            add(errors, "report_path_escape", raw, str(exc))
            continue
        if path_text != rel and not path.exists():
            add(errors, "report_references_missing_path", raw, "referenced artifact does not exist")

    if not facts.get("experiments"):
        add(warnings, "no_experiments_resolved", rel, "report contains no experiment inventory records")
    if not facts.get("comprehensive_results"):
        add(warnings, "no_structured_results_resolved", rel, "report contains no source-bound quantitative result records")
    return {
        "report_characters": len(report_text),
        "experiments_covered": len(facts.get("experiments", [])),
        "results_covered": len(facts.get("comprehensive_results", [])),
        "claims_covered": len(facts.get("claim_support", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the complete Writer Handoff package.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/phase_F/writer_handoff_snapshot.json")
    parser.add_argument("--facts", default="external_executor/report/phase_F/writer_handoff_facts.json")
    parser.add_argument("--report", default="external_executor/executor_research_report.md")
    parser.add_argument("--output")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    snapshot = read_object(ws, args.snapshot, errors)
    facts = read_object(ws, args.facts, errors)
    result = read_object(ws, "external_executor/result_pack.json", errors)
    status = read_object(ws, "external_executor/executor_status.json", errors)
    manifest = read_object(ws, "external_executor/report/run_manifest.json", errors)
    report_path = resolve_in_workspace(ws, args.report)
    report_text = report_path.read_text(encoding="utf-8", errors="replace") if report_path.is_file() else ""
    if not report_text.strip():
        add(errors, "missing_or_empty_research_report", args.report, "executor_research_report.md is required")

    current_hashes = current_core_hashes(ws)
    for name, entry in snapshot.get("core_files", {}).items():
        if current_hashes.get(name) != entry.get("sha256"):
            add(errors, "core_file_changed_after_snapshot", str(entry.get("path")), name)
    assets = current_assets(ws)
    if canonical_hash(assets) != canonical_hash(snapshot.get("assets", [])):
        add(errors, "figure_or_table_changed_after_snapshot", "external_executor/figure|table", "asset set or checksum changed")
    if facts.get("input_fingerprint") != snapshot.get("input_fingerprint"):
        add(errors, "facts_snapshot_fingerprint_mismatch", args.facts, "facts were not built from this snapshot")

    for rel, document in (
        ("external_executor/result_pack.json", result),
        ("external_executor/executor_status.json", status),
        ("external_executor/report/run_manifest.json", manifest),
    ):
        major = schema_major(document.get("schema_version"))
        if major not in {None, 1}:
            add(errors, "unsupported_schema_major", rel, repr(document.get("schema_version")))

    result_status = normalize_status(result.get("executor_status") or result.get("status"))
    executor_status = normalize_status(status.get("executor_status") or status.get("status") or status.get("current_state"))
    if result_status not in TERMINAL_STATUSES:
        add(errors, "result_pack_not_terminal", "external_executor/result_pack.json", repr(result_status))
    if executor_status not in TERMINAL_STATUSES:
        add(errors, "executor_status_not_terminal", "external_executor/executor_status.json", repr(executor_status))
    if result_status and executor_status and result_status != executor_status:
        add(errors, "terminal_status_mismatch", "external_executor/executor_status.json", f"result_pack={result_status}, executor_status={executor_status}")
    if status.get("accepted") is True:
        add(errors, "executor_status_accepted_true", "external_executor/executor_status.json", "ResearchOS acceptance remains downstream")

    required_sections = (
        "experiment_plan", "experiment_runs", "implementations", "implementation_reviews",
        "result_diagnoses", "module_attributions", "realized_method_package", "framework_figure",
        "figure_table_inventory", "evidence_packaging",
    )
    for section in required_sections:
        if result.get(section) in (None, {}, []):
            target = errors if result_status == "completed" else warnings
            add(target, "final_result_section_missing", "external_executor/result_pack.json", section)
        elif result_status == "completed" and isinstance(result.get(section), dict):
            section_status = normalize_status(result[section].get("status"))
            if section_status in {"not_started", "blocked", "failed", "stale", "unavailable", "invalid"}:
                add(errors, "completed_result_has_invalid_section", "external_executor/result_pack.json", f"{section}={section_status}")

    checks = {}
    checks.update(validate_manifest(ws, manifest, assets, errors, warnings))
    registered_paths = {
        str(item.get("path") or item.get("artifact_path"))
        for item in manifest_items(manifest)
        if item.get("path") or item.get("artifact_path")
    }
    for experiment in facts.get("experiments", []):
        for key in ("configurations", "result_files", "log_files", "figures", "tables"):
            for path in experiment.get(key, []):
                if path not in registered_paths:
                    add(errors, "experiment_artifact_unregistered", str(path), f"experiment {experiment.get('experiment_id')} field {key}")
    for result_record in facts.get("comprehensive_results", []):
        for key in ("raw_result_files", "figure_files", "table_files"):
            for path in result_record.get(key, []):
                if path not in registered_paths:
                    add(errors, "result_artifact_unregistered", str(path), f"result {result_record.get('result_id')} field {key}")
    if report_text:
        checks.update(validate_report(ws, report_text, facts, assets, errors, warnings))
    status_label = "blocked" if errors else ("partial" if warnings or executor_status != "completed" else "ready")
    validation = {
        "schema_version": "writer_handoff_validation.v2",
        "status": status_label,
        "handoff_id": snapshot.get("handoff_id"),
        "input_fingerprint": snapshot.get("input_fingerprint"),
        "validated_inputs": {
            "executor_status": "external_executor/executor_status.json",
            "result_pack": "external_executor/result_pack.json",
            "run_manifest": "external_executor/report/run_manifest.json",
            "executor_research_report": args.report,
            "figure_directory": "external_executor/figure/",
            "table_directory": "external_executor/table/",
        },
        "hashes": {
            **current_hashes,
            "executor_research_report": sha256_file(report_path) if report_path.is_file() else None,
        },
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "recommended_next_action": "handoff_complete" if status_label == "ready" else "handoff_complete_with_constraints" if status_label == "partial" else "repair_writer_handoff",
        "validated_at": utc_now(),
    }
    destination = output_path(ws, args.output, "external_executor/report/phase_F/writer_handoff_validation.json")
    dump_json_atomic(destination, validation)
    print(json.dumps({"status": status_label, "errors": len(errors), "warnings": len(warnings)}))
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
