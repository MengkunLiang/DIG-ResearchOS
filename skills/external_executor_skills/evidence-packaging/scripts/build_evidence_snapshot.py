#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    extract_refs,
    file_ref,
    get_nested,
    load_json,
    nonempty,
    record_id,
    record_status,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
    walk_dicts,
)

SNAPSHOT_SECTIONS = [
    "context_alignment", "resource_requirement_matrix", "resources", "resource_readiness",
    "claim_evidence_matrix", "experiment_plan", "baseline_reproductions", "baseline_reproduction",
    "method_specification", "implementation_spec", "implementation_records", "implementation",
    "code_and_protocol_reviews", "code_and_protocol_review", "experiment_runs", "run_records", "runs",
    "result_diagnoses", "result_diagnosis", "diagnosis_records", "module_attributions", "module_attribution",
    "attribution_records", "iteration_plans", "iteration_decisions", "claim_boundaries", "claim_boundary",
    "scope_change_request", "material_gaps", "resource_risks", "risks",
]

ACTIVE_STATUSES = {"complete", "completed", "pass", "passed", "success", "succeeded", "usable", "valid", "ready"}
INACTIVE_STATUSES = {"stale", "superseded", "invalid", "unusable", "excluded", "failed", "blocked"}


def section_digest(result: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for key in SNAPSHOT_SECTIONS:
        if key not in result:
            continue
        value = result[key]
        output[key] = {
            "sha256": canonical_json_hash(value),
            "present": nonempty(value),
            "value": value,
        }
    return output


def classify_records(result: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
    active: list[dict] = []
    inactive: list[dict] = []
    other: list[dict] = []
    seen: set[str] = set()
    for path, record in walk_dicts(result):
        if not any(key in record for key in ("run_id", "experiment_id", "record_id")):
            continue
        rid = record_id(record, path)
        if rid in seen:
            continue
        seen.add(rid)
        run_type = str(record.get("run_type") or record.get("evidence_type") or "unknown").lower()
        status = record_status(record)
        formal = bool(record.get("formal", False)) or run_type in {"formal", "ablation", "robustness", "diagnostic", "efficiency"}
        entry = {
            "record_id": rid,
            "source_path": path,
            "status": status,
            "run_type": run_type,
            "formal_candidate": formal,
            "protocol_fingerprint": record.get("protocol_fingerprint"),
            "claim_ids": unique_strings(record.get("claim_ids", [])),
            "artifact_refs": extract_refs(record),
            "record_sha256": canonical_json_hash(record),
        }
        if status in INACTIVE_STATUSES or record.get("stale") is True or record.get("usable") is False:
            inactive.append(entry)
        elif formal and (status in ACTIVE_STATUSES or record.get("usable") is True):
            active.append(entry)
        else:
            other.append(entry)
    return active, inactive, other


def manifest_artifacts(ws: Path, manifest: dict[str, Any]) -> tuple[list[dict], list[str]]:
    raw = manifest.get("artifacts") or manifest.get("items") or manifest.get("entries") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    output: list[dict] = []
    issues: list[str] = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        path_value = item.get("path") or item.get("artifact_path")
        if not path_value:
            continue
        try:
            path = resolve_in_workspace(ws, str(path_value))
            exists = path.exists()
            actual = file_ref(ws, path, producer=str(item.get("producer") or "unknown"), evidence_level=str(item.get("evidence_level") or "unknown"))
            expected_sha = item.get("sha256")
            checksum_valid = (not expected_sha) or (actual.get("sha256") == expected_sha)
            if not exists:
                issues.append(f"manifest_artifact_missing:{path_value}")
            elif not checksum_valid:
                issues.append(f"manifest_checksum_mismatch:{path_value}")
            output.append({
                "artifact_id": item.get("artifact_id") or actual["artifact_id"],
                "path": relpath(ws, path),
                "exists": exists,
                "expected_sha256": expected_sha,
                "actual_sha256": actual.get("sha256"),
                "checksum_valid": checksum_valid,
                "producer": item.get("producer"),
                "evidence_level": item.get("evidence_level"),
                "source_index": index,
            })
        except Exception as exc:  # noqa: BLE001
            issues.append(f"invalid_manifest_path:{path_value}:{exc}")
    return output, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Pin the final evidence input set used by all Phase F1-F3 products.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/final_evidence_snapshot.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    result = load_json(ext / "result_pack.json")
    manifest = load_json(ext / "run_manifest.json")
    status = load_json(ext / "executor_status.json")
    handoff = load_json(ext / "handoff_pack.json")
    preflight = load_json(ext / "evidence_packaging_preflight.json")

    sections = section_digest(result)
    active, inactive, other = classify_records(result)
    artifacts, artifact_issues = manifest_artifacts(ws, manifest)
    current_protocols = sorted({item.get("protocol_fingerprint") for item in active if item.get("protocol_fingerprint")})
    issues = list(artifact_issues)
    if len(current_protocols) > 1:
        issues.append("multiple_active_protocol_fingerprints")
    if not active:
        issues.append("no_active_formal_evidence")

    snapshot_payload = {
        "handoff_sha256": canonical_json_hash(handoff),
        "result_pack_sections": {key: value["sha256"] for key, value in sections.items()},
        "executor_status_sha256": canonical_json_hash(status),
        "run_manifest_sha256": canonical_json_hash(manifest),
        "active_formal_records": active,
        "inactive_or_stale_records": inactive,
        "other_records": other,
        "manifest_artifacts": artifacts,
        "active_protocol_fingerprints": current_protocols,
    }
    fingerprint = canonical_json_hash(snapshot_payload)
    snapshot = {
        "schema_version": "final_evidence_snapshot.v1",
        "snapshot_id": stable_id("SNAP", fingerprint),
        "snapshot_fingerprint": fingerprint,
        "generated_at": utc_now(),
        "status": "stable" if not [x for x in issues if x != "no_active_formal_evidence"] else "constrained",
        "best_effort_mode": preflight.get("best_effort_mode", False),
        "executor_state_at_snapshot": status.get("status"),
        "current_phase_at_snapshot": status.get("current_phase") or status.get("phase"),
        "active_protocol_fingerprint": current_protocols[0] if len(current_protocols) == 1 else None,
        "section_digests": sections,
        "active_formal_records": active,
        "inactive_or_stale_records": inactive,
        "other_records": other,
        "manifest_artifacts": artifacts,
        "issues": issues,
        "source_refs": [
            "external_executor/result_pack.json",
            "external_executor/run_manifest.json",
            "external_executor/executor_status.json",
            "external_executor/handoff_pack.json",
        ],
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
