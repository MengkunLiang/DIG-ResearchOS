#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import assert_write_allowed, dump_json_atomic, is_within, relpath, resolve_in_workspace, resolve_workspace, tree_manifest, utc_now

FORBIDDEN_LABELS = {"official", "author_implementation", "exact_reproduction", "protocol_equivalent", "paper_result_reproduced"}
ALLOWED_LABELS = {"executor_reimplementation", "approximate_reproduction"}


def nonempty_files(path: Path) -> list[Path]:
    return [p for p in path.rglob("*") if p.is_file() and p.name != ".gitkeep" and p.stat().st_size > 0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a baseline reimplementation package.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--mode", choices=["draft", "candidate"], default="candidate")
    parser.add_argument("--output")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    root = Path(args.path).expanduser().resolve()
    if not is_within(root, workspace):
        raise SystemExit(f"Package path is outside workspace: {root}")
    errors = []
    warnings = []
    required = ["README.md", "REIMPLEMENTATION_SPEC.md", "provenance.json", "assumptions.json", "paper_to_code_map.json", "src", "configs", "tests"]
    for rel in required:
        if not (root / rel).exists():
            errors.append(f"missing {rel}")
    provenance = {}
    if (root / "provenance.json").exists():
        try:
            provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"invalid provenance.json: {exc}")
    label = provenance.get("implementation_label")
    if label in FORBIDDEN_LABELS:
        errors.append(f"forbidden implementation_label: {label}")
    if label not in ALLOWED_LABELS:
        errors.append(f"implementation_label must be one of {sorted(ALLOWED_LABELS)}")
    if provenance.get("official") is True:
        errors.append("official must be false")
    if not provenance.get("source_refs"):
        errors.append("source_refs are required")
    if args.mode == "candidate":
        for directory in ("src", "configs", "tests"):
            if (root / directory).exists() and not nonempty_files(root / directory):
                errors.append(f"{directory}/ has no non-empty implementation files")
        try:
            mapping = json.loads((root / "paper_to_code_map.json").read_text(encoding="utf-8"))
            if not mapping.get("items"):
                errors.append("paper_to_code_map.json has no items")
        except Exception:
            pass
        spec = (root / "REIMPLEMENTATION_SPEC.md").read_text(encoding="utf-8", errors="replace") if (root / "REIMPLEMENTATION_SPEC.md").exists() else ""
        if "spec_draft" in spec:
            errors.append("REIMPLEMENTATION_SPEC.md still has spec_draft status")
    manifest = tree_manifest(root) if root.exists() else {}
    status = "pass" if not errors else "fail"
    report = {
        "schema_version": "baseline_reimplementation_validation.v1",
        "generated_at": utc_now(),
        "path": relpath(workspace, root),
        "mode": args.mode,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "manifest_sha256": manifest.get("manifest_sha256"),
        "approved_for": ["resource_review"] if status == "pass" else ["none"],
    }
    output = resolve_in_workspace(
        workspace,
        args.output or "external_executor/report/validation_report.json",
    )
    assert_write_allowed(workspace, output)
    dump_json_atomic(output, report)
    print(f"{status}: {len(errors)} errors")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
