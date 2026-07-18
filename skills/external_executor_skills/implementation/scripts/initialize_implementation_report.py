#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import artifact_ref, dump_json_atomic, load_json, relpath, resolve_in_workspace, resolve_workspace, tree_manifest, utc_now


def load_optional(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return load_json(path)
    except Exception:
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a complete implementation report envelope.")
    parser.add_argument("--workspace")
    parser.add_argument("--contract", default="external_executor/report/implementation_change_contract.json")
    parser.add_argument("--output", default="external_executor/report/implementation_report.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    contract_path = resolve_in_workspace(workspace, args.contract)
    output = resolve_in_workspace(workspace, args.output)
    contract = load_json(contract_path)
    root = resolve_in_workspace(workspace, contract["implementation_root"])
    if output.exists() and not args.force:
        existing = load_json(output)
        if existing.get("implementation_id") == contract.get("implementation_id"):
            print(f"reuse: {relpath(workspace, output)}")
            return 0
        raise SystemExit(f"Report exists for a different implementation: {output}")

    patch_bundle_path = root / "patches" / "patch_bundle.json"
    scope_scan_path = root / "patches" / "scope_scan.json"
    mapping_path = root / "mappings" / "module_mapping.json"
    mapping_validation_path = root / "mappings" / "module_mapping_validation.json"
    patch_bundle = load_optional(patch_bundle_path, {})
    scope_scan = load_optional(scope_scan_path, {})
    mapping = load_optional(mapping_path, {"items": []})
    mapping_validation = load_optional(mapping_validation_path, {})

    verification_items = []
    verification_root = root / "verification"
    if verification_root.exists():
        for path in sorted(verification_root.rglob("*.json")):
            if path.name.endswith("stdout.log") or path.name.endswith("stderr.log"):
                continue
            data = load_optional(path, None)
            if isinstance(data, dict) and data.get("schema_version") == "implementation_verification.v1":
                data = dict(data)
                data["record_path"] = relpath(workspace, path)
                verification_items.append(data)
    tdd_items = []
    if verification_root.exists():
        for path in sorted(verification_root.rglob("*.json")):
            data = load_optional(path, None)
            if isinstance(data, dict) and data.get("schema_version") == "implementation_tdd_cycle.v1":
                data = dict(data)
                data["record_path"] = relpath(workspace, path)
                tdd_items.append(data)

    artifacts = [artifact_ref(workspace, contract_path, level="implementation_definition")]
    for path, level in ((patch_bundle_path, "patch"), (scope_scan_path, "patch"), (mapping_path, "mapping"), (mapping_validation_path, "mapping")):
        if path.exists() and path.is_file():
            artifacts.append(artifact_ref(workspace, path, level=level))

    worktree = root / "worktree"
    worktree_fp = tree_manifest(worktree)["manifest_sha256"] if worktree.exists() else None
    approved = contract.get("approved_changes", [])
    report = {
        "schema_version": "implementation_report.v1",
        "child_skill": "implementation",
        "status": "partial",
        "generated_at": utc_now(),
        "implementation_id": contract["implementation_id"],
        "iteration_id": contract["iteration_id"],
        "input_fingerprint": contract["input_fingerprint"],
        "contract_ref": relpath(workspace, contract_path),
        "implementation_root": relpath(workspace, root),
        "base_source": contract.get("base_source", {}),
        "final_worktree_fingerprint": worktree_fp,
        "authorized_noop": bool(contract.get("authorized_noop", False)),
        "approved_changes": {"status": "complete" if contract.get("status") == "ready" else "blocked", "items": approved},
        "implemented_changes": {
            "status": "partial",
            "items": [{
                "change_id": change.get("change_id"),
                "status": "not_implemented",
                "changed_paths": [],
                "summary": "",
                "spec_item_ids": change.get("spec_item_ids", []),
                "module_ids": change.get("module_ids", []),
                "config_keys": [],
                "tests": [],
                "engineering_only": False,
                "known_limitations": [],
                "evidence_refs": [],
            } for change in approved],
        },
        "module_mapping": {
            "status": "complete" if mapping_validation.get("status") == "pass" else "partial",
            "items": mapping.get("items", []),
            "mapping_ref": relpath(workspace, mapping_path) if mapping_path.exists() else "",
            "validation_ref": relpath(workspace, mapping_validation_path) if mapping_validation_path.exists() else "",
            "validation_status": mapping_validation.get("status"),
        },
        "verification_records": {"status": "complete" if verification_items else "partial", "items": verification_items},
        "tdd_cycles": {"status": "complete" if tdd_items else "not_applicable", "items": tdd_items},
        "patch_bundle": {
            "status": patch_bundle.get("status", "partial"),
            "path": relpath(workspace, patch_bundle_path) if patch_bundle_path.exists() else "",
            "changed_files": patch_bundle.get("changed_files", []),
            "summary": patch_bundle.get("summary", {}),
            "before_manifest_sha256": patch_bundle.get("before_manifest_sha256"),
            "after_manifest_sha256": patch_bundle.get("after_manifest_sha256"),
        },
        "scope_scan": {
            "status": scope_scan.get("status", "stale" if patch_bundle else "blocked"),
            "path": relpath(workspace, scope_scan_path) if scope_scan_path.exists() else "",
            "findings": scope_scan.get("findings", []),
        },
        "drift_assessment": {
            "contribution_drift": "none",
            "protocol_impact": "none",
            "fairness_impact": "none",
            "baseline_reproduction_impact": "none",
            "affected_reproduction_ids": [],
            "rationale": [],
            "evidence_refs": [],
        },
        "implementation_risks": {"status": "complete", "items": []},
        "scope_change_proposals": {"status": "not_needed", "items": []},
        "implementation_gate": {
            "status": "needs_fix",
            "required_verifications_complete": False,
            "mapping_complete": mapping_validation.get("status") == "pass",
            "patch_in_scope": scope_scan.get("status") == "pass",
            "fresh_final_verification": False,
            "blocking_issues": [],
            "fixable_issues": ["report_requires_completion"],
            "next_action": "repair_implementation",
        },
        "artifact_refs": artifacts,
        "notes": [],
    }
    dump_json_atomic(output, report)
    print(relpath(workspace, output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
