#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _common import assert_write_allowed, dump_json_atomic, is_approved_resource_path, is_within, load_json, relpath, resolve_in_workspace, resolve_workspace, slugify, tree_manifest, utc_now

SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}


def reject_symlinks(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Source is symlink: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                target = (child.parent / child.readlink()).resolve(strict=False)
                if not is_within(target, path.resolve()):
                    raise ValueError(f"Escaping symlink: {child} -> {target}")
                raise ValueError(f"Symlink requires explicit manual staging: {child}")


def ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in SKIP}


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare isolated baseline reproduction attempt.")
    ap.add_argument("--workspace")
    ap.add_argument("--plan", default="external_executor/baseline_reproduction_plan.json")
    ap.add_argument("--reproduction-id", required=True)
    ap.add_argument("--attempt", type=int, required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    ws = resolve_workspace(args.workspace)
    plan = load_json(resolve_in_workspace(ws, args.plan))
    item = next((i for i in plan.get("items", []) if i.get("reproduction_id") == args.reproduction_id), None)
    if not item:
        raise SystemExit("Unknown reproduction ID")
    if item.get("status") not in {"planned", "incomplete"}:
        raise SystemExit(f"Plan item status is not preparable: {item.get('status')}")
    source = resolve_in_workspace(ws, item.get("source", {}).get("path", ""))
    if not source.exists():
        raise SystemExit(f"Source missing: {source}")
    if not is_approved_resource_path(ws, source):
        raise SystemExit(
            "Baseline source must come from an approved resource root "
            "(resources/ for by-hand local material or resource/ for acquired/reimplemented material): "
            f"{source}"
        )
    reject_symlinks(source)
    baseline_slug = slugify(item.get("baseline_id") or item.get("baseline_name"))
    dest = ws / "external_executor" / "expr" / "baseline_reproduction" / baseline_slug / args.reproduction_id / f"attempt-{args.attempt}"
    result_dir = ws / "external_executor" / "raw_results" / "baseline_reproduction" / baseline_slug / args.reproduction_id / f"attempt-{args.attempt}"
    assert_write_allowed(ws, dest)
    assert_write_allowed(ws, result_dir)
    if dest.exists() or result_dir.exists():
        if not args.force:
            raise SystemExit(f"Attempt exists: {dest} or {result_dir}")
        if dest.exists():
            shutil.rmtree(dest)
        if result_dir.exists():
            shutil.rmtree(result_dir)
    (dest / "source").parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, dest / "source", symlinks=False, ignore=ignore)
    else:
        (dest / "source").mkdir(parents=True)
        shutil.copy2(source, dest / "source" / source.name)
    (dest / "patches").mkdir()
    (dest / "configs").mkdir()
    (result_dir / "outputs").mkdir(parents=True)
    config_records = []
    for config_value in item.get("config", {}).get("paths", []):
        config = resolve_in_workspace(ws, config_value)
        if not config.exists() or not config.is_file():
            config_records.append({"source": config_value, "status": "missing"})
            continue
        target = dest / "configs" / config.name
        shutil.copy2(config, target)
        config_records.append({"source": relpath(ws, config), "staged": target.relative_to(dest).as_posix(), "status": "copied"})
    fragment = dict(item)
    fragment["attempt"] = args.attempt
    fragment["attempt_dir"] = relpath(ws, dest)
    fragment["deployment_dir"] = relpath(ws, dest)
    fragment["result_dir"] = relpath(ws, result_dir)
    fragment["prepared_at"] = utc_now()
    dump_json_atomic(dest / "plan_fragment.json", fragment)
    source_manifest = tree_manifest(dest / "source")
    config_manifest = tree_manifest(dest / "configs")
    provenance = {
        "schema_version": "baseline_attempt_preparation.v1",
        "prepared_at": utc_now(),
        "reproduction_id": args.reproduction_id,
        "baseline_id": item.get("baseline_id"),
        "candidate_id": item.get("candidate_id"),
        "attempt": args.attempt,
        "original_source_path": item.get("source", {}).get("path"),
        "attempt_path": relpath(ws, dest),
        "deployment_path": relpath(ws, dest),
        "result_path": relpath(ws, result_dir),
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "config_manifest_sha256": config_manifest["manifest_sha256"],
        "config_records": config_records,
        "original_mutated": False,
        "excluded_names": sorted(SKIP),
    }
    dump_json_atomic(dest / "attempt_provenance.json", provenance)
    dump_json_atomic(result_dir / "attempt_provenance.json", provenance)
    print(relpath(ws, dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
