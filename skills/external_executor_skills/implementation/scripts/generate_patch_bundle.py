#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
from pathlib import Path
from typing import Any

from _common import (
    dump_json_atomic,
    is_text_file,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    sha256_file,
    tree_manifest,
    utc_now,
)

DEPENDENCY_NAMES = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg",
    "environment.yml", "environment.yaml", "poetry.lock", "uv.lock", "pdm.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.toml", "cargo.lock",
    "go.mod", "go.sum",
}
CONFIG_SUFFIXES = {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"}
TEST_MARKERS = {"test", "tests", "spec", "specs"}
PROTOCOL_WORDS = {"metric", "split", "dataset", "preprocess", "protocol", "seed", "repeat", "baseline"}


def files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if path.is_file() and not path.is_symlink():
            result[path.relative_to(root).as_posix()] = path
    return result


def line_stats(before: list[str], after: list[str]) -> tuple[int, int]:
    added = deleted = 0
    for line in difflib.ndiff(before, after):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            deleted += 1
    return added, deleted


def hints(rel: str) -> list[str]:
    path = Path(rel)
    lower = rel.lower()
    values = []
    if path.name.lower() in DEPENDENCY_NAMES:
        values.append("dependency")
    if path.suffix.lower() in CONFIG_SUFFIXES or "config" in lower:
        values.append("config")
    if any(part.lower() in TEST_MARKERS or part.lower().startswith("test_") for part in path.parts):
        values.append("test")
    if any(word in lower for word in PROTOCOL_WORDS):
        values.append("protocol_sensitive")
    if path.suffix.lower() in {".sh", ".bash", ".ps1", ".bat"}:
        values.append("executable_script")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate structured implementation patch evidence.")
    parser.add_argument("--workspace")
    parser.add_argument("--contract", default="external_executor/implementation_change_contract.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    contract_path = resolve_in_workspace(workspace, args.contract)
    contract = load_json(contract_path)
    root = resolve_in_workspace(workspace, contract["implementation_root"])
    before_root = root / "before"
    after_root = root / "worktree"
    patch_dir = root / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patch_dir / "implementation.patch"
    bundle_path = patch_dir / "patch_bundle.json"

    before_files = files(before_root)
    after_files = files(after_root)
    all_paths = sorted(set(before_files) | set(after_files))
    changed: list[dict[str, Any]] = []
    diff_lines: list[str] = []
    binary_changes: list[dict[str, Any]] = []
    totals = {"added_files": 0, "modified_files": 0, "deleted_files": 0, "binary_files": 0, "lines_added": 0, "lines_deleted": 0}

    for rel in all_paths:
        before = before_files.get(rel)
        after = after_files.get(rel)
        before_hash = sha256_file(before) if before else None
        after_hash = sha256_file(after) if after else None
        if before_hash == after_hash:
            continue
        operation = "add" if before is None else "delete" if after is None else "modify"
        totals[{"add": "added_files", "modify": "modified_files", "delete": "deleted_files"}[operation]] += 1
        text = (before is None or is_text_file(before)) and (after is None or is_text_file(after))
        item: dict[str, Any] = {
            "path": rel,
            "operation": operation,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "before_size_bytes": before.stat().st_size if before else 0,
            "after_size_bytes": after.stat().st_size if after else 0,
            "content_type": "text" if text else "binary",
            "sensitive_hints": hints(rel),
        }
        if text:
            before_lines = before.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if before else []
            after_lines = after.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if after else []
            added, deleted = line_stats(before_lines, after_lines)
            item["lines_added"] = added
            item["lines_deleted"] = deleted
            totals["lines_added"] += added
            totals["lines_deleted"] += deleted
            diff_lines.extend(difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{rel}" if before else "/dev/null",
                tofile=f"b/{rel}" if after else "/dev/null",
                lineterm="",
            ))
            if diff_lines and not diff_lines[-1].endswith("\n"):
                diff_lines[-1] += "\n"
        else:
            totals["binary_files"] += 1
            binary_changes.append({"path": rel, "operation": operation, "before_sha256": before_hash, "after_sha256": after_hash})
        changed.append(item)

    patch_path.write_text("".join(diff_lines), encoding="utf-8")
    before_manifest = tree_manifest(before_root)
    after_manifest = tree_manifest(after_root)
    bundle = {
        "schema_version": "implementation_patch_bundle.v1",
        "implementation_id": contract["implementation_id"],
        "iteration_id": contract["iteration_id"],
        "generated_at": utc_now(),
        "contract_ref": relpath(workspace, contract_path),
        "before_manifest_sha256": before_manifest["manifest_sha256"],
        "after_manifest_sha256": after_manifest["manifest_sha256"],
        "changed_files": changed,
        "summary": totals,
        "unified_diff_path": relpath(workspace, patch_path),
        "binary_change_manifest": binary_changes,
        "status": "complete",
    }
    dump_json_atomic(bundle_path, bundle)
    print(f"{len(changed)} changed file(s): {relpath(workspace, bundle_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
