#!/usr/bin/env python3
"""Create a deterministic SHA-256 fingerprint for external-executor inputs."""

from __future__ import annotations

import argparse
import hashlib
import sys

from _common import (
    atomic_write_json, iter_files, relative_to_workspace, resolve_in_workspace,
    sha256_file, utc_now, workspace_root,
)


DEFAULT_INPUTS = [
    "project.yaml",
    "external_executor/AGENTS.md",
    "external_executor/handoff_pack.json",
    "external_executor/expected_outputs_schema.json",
    "external_executor/allowed_paths.txt",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--output", default="external_executor/input_fingerprint.json")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    output = resolve_in_workspace(root, args.output)
    requested = args.paths or DEFAULT_INPUTS
    entries: list[dict] = []
    missing: list[str] = []

    for raw in requested:
        try:
            path = resolve_in_workspace(root, raw)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not path.exists():
            missing.append(Path(raw).as_posix())
            entries.append({"path": Path(raw).as_posix(), "status": "missing"})
            continue
        for file_path in iter_files(path):
            if file_path == output:
                continue
            entries.append({
                "path": relative_to_workspace(root, file_path),
                "status": "present",
                "size_bytes": file_path.stat().st_size,
                "sha256": sha256_file(file_path),
            })

    entries.sort(key=lambda item: item["path"])
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.get("status", "").encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.get("sha256", "").encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.get("size_bytes", "")).encode("ascii"))
        digest.update(b"\n")

    payload = {
        "schema_version": "external_executor_input_fingerprint.v1",
        "algorithm": "sha256(path\\0status\\0sha256\\0size\\n)",
        "fingerprint": digest.hexdigest(),
        "inputs": entries,
        "missing": missing,
        "created_at": utc_now(),
    }
    atomic_write_json(output, payload)
    print(payload["fingerprint"])
    return 0 if args.allow_missing or not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
