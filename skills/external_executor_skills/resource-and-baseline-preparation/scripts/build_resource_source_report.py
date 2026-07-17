#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    assert_write_allowed,
    dump_json_atomic,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    tree_manifest,
    utc_now,
)

CATEGORIES = ("byhand", "Remote_acquisition", "reproduction")
IGNORED_NAMES = {"_DIR_GUIDE.md", "README.md", ".gitkeep"}


def read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = load_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def provenance_for(product: Path) -> tuple[dict[str, Any] | None, Path | None]:
    for name in ("RESOURCE_PROVENANCE.json", "provenance.json"):
        path = product / name if product.is_dir() else product.parent / name
        data = read_json_if_present(path)
        if data is not None:
            return data, path
    return None, None


def category_from(product: Path, resource_root: Path, provenance: dict[str, Any] | None) -> str:
    if provenance:
        explicit = provenance.get("source_category") or provenance.get("resource_source_category")
        if explicit in CATEGORIES:
            return str(explicit)
        schema = str(provenance.get("schema_version", ""))
        if "reimplementation" in schema:
            return "reproduction"
        if "acquisition" in schema:
            return "Remote_acquisition"
        if "staged_local_resource" in schema:
            return "byhand"
    try:
        first = product.relative_to(resource_root).parts[0]
    except Exception:
        first = ""
    if first in CATEGORIES:
        return first
    return "byhand" if resource_root.name == "resources" else "byhand"


def iter_products(resource_root: Path) -> list[Path]:
    if not resource_root.exists():
        return []
    products: list[Path] = []
    for child in sorted(resource_root.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith(".") or child.name in IGNORED_NAMES:
            continue
        if child.name in CATEGORIES and child.is_dir():
            for nested in sorted(child.iterdir(), key=lambda p: p.name.lower()):
                if not nested.name.startswith(".") and nested.name not in IGNORED_NAMES:
                    products.append(nested)
        else:
            products.append(child)
    return products


def product_record(workspace: Path, resource_root: Path, product: Path) -> dict[str, Any]:
    provenance, provenance_path = provenance_for(product)
    manifest = tree_manifest(product)
    source_category = category_from(product, resource_root, provenance)
    return {
        "product_id": stable_id("RES", relpath(workspace, product), manifest["manifest_sha256"]),
        "name": product.name,
        "path": relpath(workspace, product),
        "source_category": source_category,
        "source_detail": provenance or {},
        "provenance_ref": relpath(workspace, provenance_path) if provenance_path else None,
        "manifest_sha256": manifest["manifest_sha256"],
        "entry_count": manifest["entry_count"],
        "total_bytes": manifest["total_bytes"],
        "inventory_truncated": manifest["truncated"],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Resource Source Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Resource roots: `{', '.join(report['source_roots'])}`",
        "",
    ]
    for category in CATEGORIES:
        items = report["categories"][category]
        lines.extend([f"## {category}", ""])
        if not items:
            lines.extend(["No products recorded.", ""])
            continue
        for item in items:
            provenance = item.get("provenance_ref") or "not recorded"
            lines.append(f"- `{item['path']}` - provenance: `{provenance}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a source report for products under resources/ and resource/.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/resource_source_report.json")
    parser.add_argument("--markdown-output", default="external_executor/resource_source_report.md")
    parser.add_argument("--report", default="external_executor/resource_preparation_report.json")
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    byhand_root = workspace / "resources"
    resource_root = workspace / "resource"
    output = resolve_in_workspace(workspace, args.output)
    markdown_output = resolve_in_workspace(workspace, args.markdown_output)
    records = [
        *[product_record(workspace, byhand_root, product) for product in iter_products(byhand_root)],
        *[product_record(workspace, resource_root, product) for product in iter_products(resource_root)],
    ]
    categories = {category: [] for category in CATEGORIES}
    for record in records:
        categories[record["source_category"]].append(record)

    payload = {
        "schema_version": "resource_source_report.v1",
        "generated_at": utc_now(),
        "resource_root": "resource",
        "source_roots": ["resources", "resource"],
        "status": "complete" if (byhand_root.exists() or resource_root.exists()) else "partial",
        "categories": categories,
        "counts": {category: len(categories[category]) for category in CATEGORIES},
        "missing_resource_root": not resource_root.exists(),
        "missing_resources_root": not byhand_root.exists(),
    }
    assert_write_allowed(workspace, output)
    assert_write_allowed(workspace, markdown_output)
    dump_json_atomic(output, payload)
    markdown_output.write_text(render_markdown(payload), encoding="utf-8")

    if args.write_back:
        report_path = resolve_in_workspace(workspace, args.report)
        report = load_json(report_path)
        report["resource_source_report"] = {
            "status": payload["status"],
            "json_path": relpath(workspace, output),
            "markdown_path": relpath(workspace, markdown_output),
            "source_roots": payload["source_roots"],
            "counts": payload["counts"],
            "categories": {
                category: [item["path"] for item in items]
                for category, items in categories.items()
            },
        }
        existing_paths = {item.get("path") for item in report.get("artifact_refs", []) if isinstance(item, dict)}
        for path, level in (
            (output, "provenance"),
            (markdown_output, "provenance"),
        ):
            rel = relpath(workspace, path)
            if rel not in existing_paths:
                report.setdefault("artifact_refs", []).append({
                    "artifact_id": stable_id("ART", rel),
                    "path": rel,
                    "producer": "resource-and-baseline-preparation",
                    "evidence_level": level,
                })
        assert_write_allowed(workspace, report_path)
        dump_json_atomic(report_path, report)

    print(f"wrote resource source report for {len(records)} products")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
