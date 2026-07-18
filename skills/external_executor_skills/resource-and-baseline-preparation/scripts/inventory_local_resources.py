#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any

from _common import (
    assert_write_allowed,
    dump_json_atomic,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    tree_manifest,
    utc_now,
)

LICENSE_NAMES = {"license", "license.md", "license.txt", "copying", "copying.md", "notice", "notice.txt"}
README_NAMES = {"readme", "readme.md", "readme.rst", "readme.txt"}
MANIFEST_NAMES = {
    "requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "pdm.lock",
    "dockerfile", "docker-compose.yml", "compose.yml", "makefile", ".gitmodules", ".gitattributes",
}
CONFIG_SUFFIXES = {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"}
DATA_SUFFIXES = {".csv", ".tsv", ".parquet", ".arrow", ".jsonl", ".h5", ".hdf5", ".npz", ".npy", ".pt", ".pth"}
CHECKPOINT_SUFFIXES = {".ckpt", ".safetensors", ".bin", ".onnx", ".pb", ".pt", ".pth"}
DEFAULT_ROOTS = ["resources"]


def normalized_root(value: str) -> str:
    return value.replace("\\", "/").strip().rstrip("/")


def is_allowed_resource_root(value: str) -> bool:
    root = normalized_root(value)
    return root == "resources" or root.startswith("resources/")


def git_metadata(path: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            proc = subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, timeout=10,
                                  env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_LFS_SKIP_SMUDGE": "1"})
            return proc.stdout.strip()
        except Exception:
            return None
    root = run("rev-parse", "--show-toplevel")
    if not root:
        return {"is_git_repository": False}
    return {
        "is_git_repository": True,
        "root": root,
        "commit": run("rev-parse", "HEAD"),
        "describe": run("describe", "--tags", "--always", "--dirty"),
        "remote_origin": run("remote", "get-url", "origin"),
        "dirty": bool(run("status", "--porcelain")),
    }


def candidate_summary(workspace: Path, path: Path, root_label: str, max_files: int, max_hash_bytes: int) -> dict[str, Any]:
    manifest = tree_manifest(path, max_files=max_files, max_hash_bytes=max_hash_bytes)
    names = {Path(e["path"]).name.lower() for e in manifest["entries"] if e.get("type") == "file"}
    suffixes = {Path(e["path"]).suffix.lower() for e in manifest["entries"] if e.get("type") == "file"}
    symlinks = [e for e in manifest["entries"] if e.get("type") == "symlink"]
    hint = "unknown"
    if names & {n.lower() for n in MANIFEST_NAMES} or any(s in suffixes for s in {".py", ".ipynb", ".sh", ".r", ".jl"}):
        hint = "code_or_baseline"
    if suffixes & DATA_SUFFIXES:
        hint = "dataset_or_results"
    if suffixes & CHECKPOINT_SUFFIXES:
        hint = "checkpoint_or_model"
    if path.name.lower() in {"datasets", "dataset", "data"}:
        hint = "dataset"
    return {
        "candidate_id": stable_id("LOCAL", root_label, path.name, manifest["manifest_sha256"]),
        "name": path.name,
        "source_root": root_label,
        "path": relpath(workspace, path),
        "kind": "directory" if path.is_dir() else "file",
        "resource_type_hint": hint,
        "manifest_sha256": manifest["manifest_sha256"],
        "entry_count": manifest["entry_count"],
        "total_bytes": manifest["total_bytes"],
        "inventory_truncated": manifest["truncated"],
        "readme_files": sorted([e["path"] for e in manifest["entries"] if Path(e.get("path", "")).name.lower() in README_NAMES]),
        "license_files": sorted([e["path"] for e in manifest["entries"] if Path(e.get("path", "")).name.lower() in LICENSE_NAMES]),
        "dependency_manifests": sorted([e["path"] for e in manifest["entries"] if Path(e.get("path", "")).name.lower() in MANIFEST_NAMES]),
        "config_files": sorted([e["path"] for e in manifest["entries"] if Path(e.get("path", "")).suffix.lower() in CONFIG_SUFFIXES])[:200],
        "symlinks": symlinks,
        "git": git_metadata(path if path.is_dir() else path.parent),
        "status": "inventoried",
        "notes": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory local resource candidates without executing content.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/report/resource_local_inventory.json")
    parser.add_argument("--root", action="append", help="Override/add workspace-relative root; repeatable")
    parser.add_argument("--max-files-per-candidate", type=int, default=20000)
    parser.add_argument("--max-hash-bytes", type=int, default=128 * 1024 * 1024)
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    output = resolve_in_workspace(workspace, args.output)
    roots = args.root or DEFAULT_ROOTS
    items = []
    missing_roots = []
    skipped_roots = []
    for root_value in roots:
        normalized = normalized_root(root_value)
        if not is_allowed_resource_root(root_value):
            skipped_roots.append({
                "root": root_value,
                "reason": "resource candidates for this skill must be under resources/",
            })
            continue
        root = resolve_in_workspace(workspace, root_value)
        if not root.exists():
            missing_roots.append(root_value)
            continue
        candidates = [root] if root.is_file() else sorted([p for p in root.iterdir() if p.name not in {".git", "__pycache__"}], key=lambda p: p.name.lower())
        if not candidates:
            candidates = [root]
        for path in candidates:
            items.append(candidate_summary(workspace, path, root_value, args.max_files_per_candidate, args.max_hash_bytes))

    payload = {
        "schema_version": "resource_local_inventory.v1",
        "generated_at": utc_now(),
        "status": "partial" if missing_roots else "complete",
        "roots_checked": roots,
        "missing_roots": missing_roots,
        "skipped_roots": skipped_roots,
        "items": items,
        "non_execution_statement": "No candidate code, setup, notebook, container, training, evaluation, or download script was executed. Resource materials for this skill belong under resources/.",
    }
    assert_write_allowed(workspace, output)
    dump_json_atomic(output, payload)
    print(f"wrote {len(items)} local candidates to {relpath(workspace, output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
