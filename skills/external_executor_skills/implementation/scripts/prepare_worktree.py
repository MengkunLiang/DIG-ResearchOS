#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import stat
from pathlib import Path

from _common import (
    assert_write_allowed,
    dump_json_atomic,
    load_json,
    reject_symlinks,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    tree_manifest,
    utc_now,
)

EXCLUDE_NAMES = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "wandb", "tensorboard",
    "raw_results", "logs", "checkpoints", "figures", "tables", "dist", "build",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".so", ".dll", ".dylib"}


def ignore(directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDE_NAMES or Path(name).suffix.lower() in EXCLUDE_SUFFIXES}


def make_read_only(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        try:
            mode = path.stat().st_mode
            if path.is_dir():
                path.chmod((mode | stat.S_IRUSR | stat.S_IXUSR) & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            else:
                path.chmod((mode | stat.S_IRUSR) & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        except OSError:
            pass


def copy_source(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False, ignore=ignore)
    else:
        destination.mkdir(parents=True)
        shutil.copy2(source, destination / source.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a before snapshot and editable implementation worktree.")
    parser.add_argument("--workspace")
    parser.add_argument("--contract", default="external_executor/implementation_change_contract.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    contract_path = resolve_in_workspace(workspace, args.contract)
    contract = load_json(contract_path)
    if contract.get("status") != "ready":
        raise SystemExit(f"Contract must be ready, got {contract.get('status')!r}")

    source = resolve_in_workspace(workspace, contract["base_source"]["path"])
    symlinks = reject_symlinks(source)
    if symlinks:
        raise SystemExit(f"Source contains symlink(s); controlled manual staging required: {symlinks[:5]}")
    root = resolve_in_workspace(workspace, contract["implementation_root"])
    before = root / "before"
    worktree = root / "worktree"
    assert_write_allowed(workspace, root)

    if root.exists():
        provenance_path = root / "implementation_provenance.json"
        if not args.force and provenance_path.exists():
            existing = load_json(provenance_path)
            if existing.get("contract_input_fingerprint") == contract.get("input_fingerprint"):
                print(f"reuse: {relpath(workspace, root)}")
                return 0
        if not args.force:
            raise SystemExit(f"Implementation root exists with different or unknown provenance: {root}")
        shutil.rmtree(root)

    for directory in (root, root / "verification", root / "mappings", root / "patches"):
        directory.mkdir(parents=True, exist_ok=True)
    copy_source(source, before)
    copy_source(source, worktree)
    before_manifest = tree_manifest(before)
    worktree_manifest = tree_manifest(worktree)
    source_manifest = tree_manifest(source)
    if before_manifest["manifest_sha256"] != worktree_manifest["manifest_sha256"]:
        raise SystemExit("before and worktree snapshots differ immediately after copy")
    make_read_only(before)

    provenance = {
        "schema_version": "implementation_provenance.v1",
        "implementation_id": contract["implementation_id"],
        "iteration_id": contract["iteration_id"],
        "created_at": utc_now(),
        "contract_path": relpath(workspace, contract_path),
        "contract_input_fingerprint": contract["input_fingerprint"],
        "source_path": relpath(workspace, source),
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "before_path": relpath(workspace, before),
        "before_manifest_sha256": before_manifest["manifest_sha256"],
        "worktree_path": relpath(workspace, worktree),
        "worktree_manifest_sha256_at_creation": worktree_manifest["manifest_sha256"],
        "source_mutated": False,
        "before_read_only": True,
        "symlinks_allowed": False,
        "excluded_names": sorted(EXCLUDE_NAMES),
        "excluded_suffixes": sorted(EXCLUDE_SUFFIXES),
        "git_commit_created": False,
    }
    dump_json_atomic(root / "implementation_provenance.json", provenance)
    print(relpath(workspace, root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
