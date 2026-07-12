#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _common import (
    assert_write_allowed,
    dump_json_atomic,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    tree_manifest,
    utc_now,
)

SKIP_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}


def reject_symlinks(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Source is a symlink: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ValueError(f"Symlink found; stage manually after review: {child}")


def ignore(directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in SKIP_NAMES}


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy local material into controlled workdir with provenance.")
    parser.add_argument("--workspace")
    parser.add_argument("--source", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    source = resolve_in_workspace(workspace, args.source)
    if not source.exists():
        raise SystemExit(f"Source does not exist: {source}")
    reject_symlinks(source)

    destination = workspace / "external_executor" / "workdir" / "resources" / "local" / args.candidate_id
    assert_write_allowed(workspace, destination)
    if destination.exists():
        if not args.force:
            raise SystemExit(f"Destination exists: {destination}")
        shutil.rmtree(destination) if destination.is_dir() else destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False, ignore=ignore)
    else:
        destination.mkdir(parents=True)
        shutil.copy2(source, destination / source.name)

    source_manifest = tree_manifest(source)
    staged_manifest = tree_manifest(destination)
    provenance = {
        "schema_version": "staged_local_resource.v1",
        "candidate_id": args.candidate_id,
        "created_at": utc_now(),
        "source_path": relpath(workspace, source),
        "destination_path": relpath(workspace, destination),
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "staged_manifest_sha256": staged_manifest["manifest_sha256"],
        "source_total_bytes": source_manifest["total_bytes"],
        "staged_total_bytes": staged_manifest["total_bytes"],
        "source_mutated": False,
        "excluded_names": sorted(SKIP_NAMES),
    }
    dump_json_atomic(destination / "RESOURCE_PROVENANCE.json", provenance)
    print(relpath(workspace, destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
