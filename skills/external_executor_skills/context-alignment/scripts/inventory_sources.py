#!/usr/bin/env python3
"""Inventory context sources and calculate a deterministic alignment fingerprint."""

from __future__ import annotations

import argparse
import hashlib

from _common import (
    atomic_write_json, is_allowed_relative, iter_files, parse_allowed_entries, relative_path, resolve_in_workspace,
    sha256_file, utc_now, workspace_root,
)


DEFAULT_SOURCES = [
    "project.yaml",
    "external_executor/AGENTS.md",
    "external_executor/handoff_pack.json",
    "external_executor/expected_outputs_schema.json",
    "external_executor/allowed_paths.txt",
    "external_executor/report/executor_capabilities.json",
    "literature/synthesis.md",
    "literature/synthesis_workbench.json",
    "literature/domain_map.json",
    "literature/bridge_domain_plan.json",
    "literature/cross_domain_catalogs/index.json",
    "literature/bridge_notes",
    "literature/cross_domain_catalogs",
    "literature/comparison_table.csv",
    "ideation/hypotheses.md",
    "ideation/exp_plan.yaml",
    "ideation/idea_scorecard.yaml",
    "ideation/risks.md",
    "novelty/novelty_audit.md",
    "ideation/hypothesis_brief.yaml",
    "ideation/selected/t45_search_targets.json",
    "user_seeds/seed_external_resources.jsonl",
    "user_seeds/bridge_domains.yaml",
]


def role_for(path: str) -> str:
    if path.startswith("external_executor/"):
        return "control" if not path.endswith("handoff_pack.json") else "compiled_handoff"
    if path.endswith("hypotheses.md"):
        return "hypothesis"
    if path.endswith("exp_plan.yaml"):
        return "protocol"
    if path.endswith("novelty_audit.md"):
        return "novelty"
    if path.endswith("hypothesis_brief.yaml") or path.endswith("t45_search_targets.json"):
        return "pre_novelty_context"
    if path.startswith("literature/"):
        return "literature"
    if path.endswith("risks.md"):
        return "risk"
    return "optional_detail"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--output", default="external_executor/context_source_inventory.json")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    output = resolve_in_workspace(root, args.output)
    allowed_path = resolve_in_workspace(root, "external_executor/allowed_paths.txt")
    allowed_entries, allowed_errors = parse_allowed_entries(root, allowed_path)
    output_relative = relative_path(root, output)
    if allowed_errors or not allowed_entries or not is_allowed_relative(output_relative, allowed_entries):
        raise SystemExit(f"output path is not authorized: {output_relative}")
    records: list[dict] = []
    digest = hashlib.sha256()

    for raw in dict.fromkeys(DEFAULT_SOURCES + args.include):
        path = resolve_in_workspace(root, raw)
        if not path.exists():
            record = {"path": raw, "role": role_for(raw), "status": "missing", "files": 0, "size_bytes": 0}
            records.append(record)
            digest.update(f"{raw}\0missing\n".encode("utf-8"))
            continue
        files = [item for item in iter_files(path) if item != output]
        if path.is_file():
            file_path = files[0]
            rel = relative_path(root, file_path)
            checksum = sha256_file(file_path)
            size = file_path.stat().st_size
            record = {"path": rel, "role": role_for(rel), "status": "present", "files": 1, "size_bytes": size, "sha256": checksum}
            digest.update(f"{rel}\0{checksum}\0{size}\n".encode("utf-8"))
        else:
            aggregate = hashlib.sha256()
            total = 0
            for file_path in files:
                rel = relative_path(root, file_path)
                checksum = sha256_file(file_path)
                size = file_path.stat().st_size
                total += size
                aggregate.update(f"{rel}\0{checksum}\0{size}\n".encode("utf-8"))
            checksum = aggregate.hexdigest()
            rel_dir = relative_path(root, path)
            record = {"path": rel_dir, "role": role_for(rel_dir), "status": "present", "files": len(files), "size_bytes": total, "sha256": checksum}
            digest.update(f"{rel_dir}\0{checksum}\0{total}\n".encode("utf-8"))
        records.append(record)

    records.sort(key=lambda item: item["path"])
    payload = {
        "schema_version": "external_executor_context_inventory.v1",
        "alignment_fingerprint": digest.hexdigest(),
        "sources": records,
        "created_at": utc_now(),
    }
    atomic_write_json(output, payload)
    print(payload["alignment_fingerprint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
