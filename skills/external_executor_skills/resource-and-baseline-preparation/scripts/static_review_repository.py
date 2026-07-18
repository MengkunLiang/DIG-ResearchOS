#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from _common import assert_write_allowed, dump_json_atomic, is_within, relpath, resolve_in_workspace, resolve_workspace, tree_manifest, utc_now

TEXT_LIMIT = 2 * 1024 * 1024
PATTERNS = [
    ("critical", "destructive_root", re.compile(r"rm\s+-rf\s+/(?:\s|$)|mkfs\.|dd\s+if=.*\s+of=/dev/", re.I)),
    ("critical", "reverse_shell", re.compile(r"/dev/tcp/|nc\s+-e\s+|bash\s+-i\s+>&|socket\.connect\(", re.I)),
    ("critical", "private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("high", "pipe_remote_to_shell", re.compile(r"(?:curl|wget)[^\n|]{0,300}\|\s*(?:bash|sh|zsh|python)", re.I)),
    ("high", "privilege_escalation", re.compile(r"\bsudo\b|--privileged|cap_add\s*:", re.I)),
    ("high", "dynamic_remote_eval", re.compile(r"eval\s*\(.*(?:requests|get\(|urlopen|curl|wget)", re.I | re.S)),
    ("medium", "unpinned_download", re.compile(r"(?:curl|wget)\s+https?://", re.I)),
    ("medium", "shell_true", re.compile(r"subprocess\.(?:run|Popen|call)\([^\n]{0,400}shell\s*=\s*True", re.I)),
    ("low", "shell_script", re.compile(r"^#!.*\b(?:bash|sh|zsh)\b", re.I | re.M)),
]
LIFECYCLE_KEYS = {"preinstall", "install", "postinstall", "prepare", "prepublish", "prepack"}


def add_finding(findings: list[dict[str, Any]], severity: str, category: str, path: str, detail: str, line: int | None = None) -> None:
    finding = {"severity": severity, "category": category, "path": path, "detail": detail}
    if line is not None:
        finding["line"] = line
    findings.append(finding)


def inspect_package_json(path: Path, rel: str, findings: list[dict[str, Any]]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        add_finding(findings, "medium", "malformed_package_json", rel, str(exc))
        return
    scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
    for key, command in scripts.items() if isinstance(scripts, dict) else []:
        if key in LIFECYCLE_KEYS:
            add_finding(findings, "high", "package_lifecycle_hook", rel, f"{key}: {command}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Static, non-executing repository risk review.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--output")
    parser.add_argument("--max-files", type=int, default=30000)
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    root = Path(args.path).expanduser().resolve()
    output = resolve_in_workspace(
        workspace,
        args.output or "external_executor/report/static_review.json",
    )
    if not is_within(root, workspace):
        raise SystemExit(f"Repository path is outside workspace: {root}")
    assert_write_allowed(workspace, output)
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Repository path is not a directory: {root}")

    manifest = tree_manifest(root, max_files=args.max_files)
    findings: list[dict[str, Any]] = []
    license_files = []
    for entry in manifest["entries"]:
        rel = entry.get("path", "")
        if entry.get("type") == "symlink":
            target = (root / rel).parent / entry.get("target", "")
            severity = "critical" if not str(target.resolve(strict=False)).startswith(str(root)) else "medium"
            add_finding(findings, severity, "symlink", rel, f"target={entry.get('target')}")
            continue
        if entry.get("type") != "file":
            continue
        path = root / rel
        lower_name = path.name.lower()
        if lower_name in {"license", "license.md", "license.txt", "copying", "notice"}:
            license_files.append(rel)
        if rel == ".gitmodules":
            add_finding(findings, "medium", "git_submodules", rel, "Repository declares submodules; they were not initialized")
        if path.stat().st_size > TEXT_LIMIT:
            continue
        try:
            raw = path.read_bytes()
            if b"\x00" in raw[:4096]:
                continue
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        if lower_name == "package.json":
            inspect_package_json(path, rel, findings)
        for severity, category, pattern in PATTERNS:
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                snippet = text[match.start():match.end()].replace("\n", " ")[:240]
                add_finding(findings, severity, category, rel, snippet, line)

    counts = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in ("critical", "high", "medium", "low")}
    if counts["critical"]:
        status = "blocked"
    elif counts["high"]:
        status = "needs_review"
    else:
        status = "pass"
    report = {
        "schema_version": "repository_static_review.v1",
        "generated_at": utc_now(),
        "path": relpath(workspace, root),
        "status": status,
        "manifest_sha256": manifest["manifest_sha256"],
        "manifest_truncated": manifest["truncated"],
        "counts": counts,
        "license_files": sorted(license_files),
        "findings": findings,
        "repository_content_executed": False,
        "limitations": [
            "Static review cannot prove safety or dependency integrity.",
            "Pattern matches require human interpretation.",
            "Files above the text inspection limit and binary files were not content-scanned.",
        ],
        "approved_for": ["static_inspection"] if status != "blocked" else ["none"],
    }
    dump_json_atomic(output, report)
    print(f"{status}: {counts}")
    return 2 if status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
