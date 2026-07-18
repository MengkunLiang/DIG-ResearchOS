#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import canonical_json_hash, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace, sha256_file, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate snapshot self-consistency and current source immutability.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/final_evidence_snapshot.json")
    parser.add_argument("--output", default="external_executor/report/final_evidence_snapshot_validation.json")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    snapshot = load_json(resolve_in_workspace(ws, args.snapshot))
    errors: list[str] = []
    warnings: list[str] = []
    if snapshot.get("schema_version") != "final_evidence_snapshot.v1":
        errors.append("invalid_snapshot_schema")
    if not snapshot.get("snapshot_fingerprint") or not snapshot.get("snapshot_id"):
        errors.append("snapshot_identity_missing")

    live_sections = load_json(ext / "result_pack.json")
    for key, entry in snapshot.get("section_digests", {}).items():
        expected = entry.get("sha256") if isinstance(entry, dict) else None
        actual = canonical_json_hash(live_sections.get(key))
        if expected and actual != expected:
            errors.append(f"snapshot_source_changed:{key}")
    for artifact in snapshot.get("manifest_artifacts", []):
        if artifact.get("exists") is False:
            warnings.append(f"missing_artifact:{artifact.get('path')}")
        if artifact.get("checksum_valid") is False:
            errors.append(f"artifact_checksum_mismatch:{artifact.get('path')}")
    selected_spec = snapshot.get("selected_method_spec")
    if not isinstance(selected_spec, dict):
        errors.append("selected_method_spec_missing")
    else:
        spec_path = resolve_in_workspace(ws, str(selected_spec.get("path") or ""))
        if not spec_path.is_file():
            errors.append("selected_method_spec_missing_on_disk")
        elif selected_spec.get("sha256") != sha256_file(spec_path):
            errors.append("selected_method_spec_changed")
        selection = snapshot.get("final_source_selection", {})
        refinement = selection.get("method_refinement", {}) if isinstance(selection, dict) else {}
        if selected_spec.get("spec_fingerprint") != refinement.get("spec_fingerprint"):
            errors.append("selected_method_spec_refinement_mismatch")
    selection = snapshot.get("final_source_selection")
    if not isinstance(selection, dict):
        errors.append("final_source_selection_missing")
    else:
        errors.extend(f"final_source:{item}" for item in selection.get("errors", []))
        warnings.extend(f"final_source:{item}" for item in selection.get("warnings", []))
    if len({r.get("protocol_fingerprint") for r in snapshot.get("active_formal_records", []) if r.get("protocol_fingerprint")}) > 1:
        errors.append("active_formal_evidence_crosses_protocols")
    if not snapshot.get("active_formal_records"):
        warnings.append("no_active_formal_records:package_can_only_be_partial_or_unavailable")

    report = {
        "schema_version": "final_evidence_snapshot_validation.v1",
        "generated_at": utc_now(),
        "status": "pass" if not errors else "blocked",
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "errors": errors,
        "warnings": warnings,
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), report)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
