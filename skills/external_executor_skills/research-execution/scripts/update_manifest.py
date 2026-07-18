#!/usr/bin/env python3
"""Register artifacts and checksums in the external-executor run manifest."""

from __future__ import annotations

import argparse
import json
import sys

from _common import (
    atomic_write_json, iter_files, load_json, relative_to_workspace,
    resolve_in_workspace, sha256_file, utc_now, workspace_root,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--manifest", default="external_executor/report/run_manifest.json")
    parser.add_argument("--artifact", action="append", required=True)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--evidence-level", default="method_definition")
    parser.add_argument("--metadata-json")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    manifest_path = resolve_in_workspace(root, args.manifest)
    if manifest_path.exists():
        try:
            manifest = load_json(manifest_path)
        except Exception as exc:
            print(f"invalid manifest: {exc}", file=sys.stderr)
            return 2
    else:
        manifest = {"schema_version": "external_executor_manifest.v1", "artifacts": [], "checkpoints": []}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifacts", []), list):
        print("manifest must be an object with an artifacts array", file=sys.stderr)
        return 2

    metadata = {}
    if args.metadata_json:
        try:
            metadata = json.loads(args.metadata_json)
        except json.JSONDecodeError as exc:
            print(f"invalid --metadata-json: {exc}", file=sys.stderr)
            return 2

    by_path = {item.get("path"): item for item in manifest.get("artifacts", []) if isinstance(item, dict)}
    found = 0
    for raw in args.artifact:
        try:
            target = resolve_in_workspace(root, raw, must_exist=True)
        except (ValueError, FileNotFoundError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        for file_path in iter_files(target):
            if file_path == manifest_path:
                print("refusing to register the manifest inside itself", file=sys.stderr)
                return 2
            rel = relative_to_workspace(root, file_path)
            digest = sha256_file(file_path)
            record = {
                "artifact_id": f"sha256:{digest}",
                "path": rel,
                "sha256": digest,
                "size_bytes": file_path.stat().st_size,
                "producer": args.producer,
                "phase": args.phase,
                "evidence_level": args.evidence_level,
                "created_at": utc_now(),
            }
            if metadata:
                record["metadata"] = metadata
            by_path[rel] = record
            found += 1
    if found == 0:
        print("no artifact files found", file=sys.stderr)
        return 2

    manifest["artifacts"] = [by_path[key] for key in sorted(by_path)]
    manifest["updated_at"] = utc_now()
    atomic_write_json(manifest_path, manifest)
    print(f"registered {found} artifact file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
