#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from _common import dump_json_atomic, load_json, match_any, relpath, resolve_in_workspace, resolve_workspace, utc_now

DEPENDENCY_NAMES = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg",
    "environment.yml", "environment.yaml", "poetry.lock", "uv.lock", "pdm.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.toml", "cargo.lock", "go.mod", "go.sum",
}
BINARY_SUFFIXES = {".pt", ".pth", ".ckpt", ".safetensors", ".onnx", ".bin", ".zip", ".tar", ".gz", ".so", ".dll", ".dylib", ".exe", ".pdf"}
SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic_secret_assignment", re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"][^'\"\n]{8,}['\"]")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
]
DANGEROUS_PATTERNS = [
    ("shell_true", re.compile(r"subprocess\.(?:run|Popen|call)\([^\n]{0,500}shell\s*=\s*True", re.I)),
    ("pipe_to_shell", re.compile(r"(?:curl|wget)[^\n|]{0,300}\|\s*(?:bash|sh|zsh|python)", re.I)),
    ("privilege_escalation", re.compile(r"\bsudo\b|--privileged", re.I)),
    ("destructive_root", re.compile(r"rm\s+-rf\s+/(?:\s|$)|mkfs\.", re.I)),
    ("dynamic_remote_eval", re.compile(r"eval\s*\(.*(?:requests|get\(|urlopen)", re.I | re.S)),
]
PROTOCOL_TERMS = {"dataset", "split", "metric", "preprocess", "protocol", "aggregation", "seed", "repeat", "baseline"}


def allowed_for_change(path: str, operation: str, changes: list[dict[str, Any]]) -> list[str]:
    matches = []
    for change in changes:
        if operation in change.get("allowed_operations", []) and match_any(path, change.get("target_paths", [])):
            matches.append(change.get("change_id"))
    return [value for value in matches if value]


def dependency_allowed(path: str, contract: dict[str, Any]) -> bool:
    for item in contract.get("allowed_dependency_changes", []):
        if isinstance(item, str) and match_any(path, [item]):
            return True
        if isinstance(item, dict) and match_any(path, item.get("paths", [item.get("path", "")])):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan implementation changes against approved path and safety scope.")
    parser.add_argument("--workspace")
    parser.add_argument("--contract", default="external_executor/implementation_change_contract.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    contract_path = resolve_in_workspace(workspace, args.contract)
    contract = load_json(contract_path)
    root = resolve_in_workspace(workspace, contract["implementation_root"])
    bundle_path = root / "patches" / "patch_bundle.json"
    bundle = load_json(bundle_path)
    findings: list[dict[str, Any]] = []
    worktree = root / "worktree"
    changes = contract.get("approved_changes", [])
    protected = contract.get("protected_paths", [])

    for item in bundle.get("changed_files", []):
        path = item["path"]
        operation = item["operation"]
        approved_by = allowed_for_change(path, operation, changes)
        if not approved_by:
            findings.append({"id": "unauthorized_path_or_operation", "severity": "blocking", "path": path, "operation": operation})
        item["approved_by_change_ids"] = approved_by
        if protected and match_any(path, protected):
            findings.append({"id": "protected_path_changed", "severity": "blocking", "path": path})
        file_path = worktree / path
        if operation != "delete" and file_path.exists():
            if file_path.is_symlink():
                findings.append({"id": "symlink_in_worktree", "severity": "blocking", "path": path})
                continue
            suffix = file_path.suffix.lower()
            if suffix in BINARY_SUFFIXES or item.get("content_type") == "binary":
                findings.append({"id": "binary_or_generated_file", "severity": "needs_review", "path": path})
            if file_path.stat().st_size <= 2 * 1024 * 1024 and item.get("content_type") == "text":
                text = file_path.read_text(encoding="utf-8", errors="replace")
                for category, pattern in SECRET_PATTERNS:
                    if pattern.search(text):
                        findings.append({"id": "secret_pattern", "category": category, "severity": "blocking", "path": path})
                for category, pattern in DANGEROUS_PATTERNS:
                    if pattern.search(text):
                        findings.append({"id": "dangerous_pattern", "category": category, "severity": "needs_review", "path": path})
        if Path(path).name.lower() in DEPENDENCY_NAMES and not dependency_allowed(path, contract):
            findings.append({"id": "unapproved_dependency_change", "severity": "blocking", "path": path})
        if operation == "delete" and ("test" in path.lower() or "provenance" in path.lower()):
            findings.append({"id": "sensitive_deletion", "severity": "needs_review", "path": path})
        if any(term in path.lower() for term in PROTOCOL_TERMS):
            findings.append({"id": "protocol_sensitive_path", "severity": "needs_review", "path": path})

    blocking = [item for item in findings if item.get("severity") == "blocking"]
    review = [item for item in findings if item.get("severity") == "needs_review"]
    status = "blocked" if blocking else ("needs_review" if review else "pass")
    report = {
        "schema_version": "implementation_scope_scan.v1",
        "generated_at": utc_now(),
        "implementation_id": contract["implementation_id"],
        "iteration_id": contract["iteration_id"],
        "status": status,
        "contract_ref": relpath(workspace, contract_path),
        "patch_bundle_ref": relpath(workspace, bundle_path),
        "findings": findings,
        "counts": {"blocking": len(blocking), "needs_review": len(review), "total": len(findings)},
        "limitations": [
            "Pattern scanning is heuristic and does not prove safety or absence of secrets.",
            "Protocol-sensitive paths require semantic Reviewer analysis.",
        ],
    }
    output = resolve_in_workspace(workspace, args.output)
    dump_json_atomic(output, report)
    print(f"{status}: {relpath(workspace, output)}")
    return 2 if blocking else (1 if review else 0)


if __name__ == "__main__":
    raise SystemExit(main())
