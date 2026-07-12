#!/usr/bin/env python3
"""Create a content-addressed review snapshot and optional baseline diff."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from _common import (
    atomic_write_json, is_allowed, iter_files, load_json, parse_allowed_entries,
    relative_path, require_authorized_output, resolve_in_workspace, sha256_file,
    utc_now, workspace_root,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--iteration-id", required=True)
    parser.add_argument("--path", action="append", required=True, dest="paths")
    parser.add_argument("--baseline-snapshot")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    output = resolve_in_workspace(root, args.output)
    try:
        require_authorized_output(root, output)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    allowed = parse_allowed_entries(root)
    entries: dict[str, dict] = {}
    for raw in args.paths:
        try:
            target = resolve_in_workspace(root, raw, must_exist=True)
        except (ValueError, FileNotFoundError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        relative_target = relative_path(root, target)
        if not is_allowed(relative_target, allowed):
            print(f"reviewed change path is outside allowed paths: {relative_target}", file=sys.stderr)
            return 2
        for file_path in iter_files(target):
            rel = relative_path(root, file_path)
            entries[rel] = {
                "path": rel,
                "sha256": sha256_file(file_path),
                "size_bytes": file_path.stat().st_size,
            }
    if not entries:
        print("review scope contains no files", file=sys.stderr)
        return 2

    baseline_entries: dict[str, dict] = {}
    if args.baseline_snapshot:
        try:
            baseline = load_json(resolve_in_workspace(root, args.baseline_snapshot, must_exist=True))
            baseline_entries = {
                item["path"]: item for item in baseline.get("entries", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
        except Exception as exc:
            print(f"invalid baseline snapshot: {exc}", file=sys.stderr)
            return 2

    current_paths = set(entries)
    baseline_paths = set(baseline_entries)
    changes = {
        "added": sorted(current_paths - baseline_paths),
        "removed": sorted(baseline_paths - current_paths),
        "modified": sorted(
            path for path in current_paths & baseline_paths
            if entries[path].get("sha256") != baseline_entries[path].get("sha256")
        ),
        "unchanged": sorted(
            path for path in current_paths & baseline_paths
            if entries[path].get("sha256") == baseline_entries[path].get("sha256")
        ),
    }
    digest = hashlib.sha256()
    for path in sorted(entries):
        item = entries[path]
        digest.update(f"{path}\0{item['sha256']}\0{item['size_bytes']}\n".encode("utf-8"))
    payload = {
        "schema_version": "external_executor_review_snapshot.v1",
        "iteration_id": args.iteration_id,
        "input_fingerprint": digest.hexdigest(),
        "entries": [entries[path] for path in sorted(entries)],
        "changes": changes,
        "baseline_snapshot": args.baseline_snapshot,
        "created_at": utc_now(),
    }
    atomic_write_json(output, payload)
    print(payload["input_fingerprint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
