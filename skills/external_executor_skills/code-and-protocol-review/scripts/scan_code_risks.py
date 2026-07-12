#!/usr/bin/env python3
"""Scan pinned text inputs for review candidates; never emits final findings."""

from __future__ import annotations

import argparse
import re
import sys

from _common import atomic_write_json, load_json, require_authorized_output, resolve_in_workspace, utc_now, workspace_root


RULES = [
    ("SECRET_LITERAL", "security_and_paths", "blocking", re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*=\s*['\"][^'\"]{6,}")),
    ("SHELL_EXECUTION", "security_and_paths", "warning", re.compile(r"shell\s*=\s*True|\bos\.system\s*\(")),
    ("DYNAMIC_EXECUTION", "code_correctness", "warning", re.compile(r"\b(eval|exec)\s*\(")),
    ("UNSAFE_DESERIALIZATION", "security_and_paths", "warning", re.compile(r"\b(pickle|joblib)\.load\s*\(|torch\.load\s*\(")),
    ("INCOMPLETE_CODE", "spec_alignment", "warning", re.compile(r"\b(TODO|FIXME|NotImplementedError)\b|^\s*pass\s*(#.*)?$")),
    ("TEST_DATA_FIT", "data_integrity", "major", re.compile(r"(?i)(fit|fit_transform)\s*\([^\n]*(test|eval)")),
    ("TEST_SELECTION", "data_integrity", "major", re.compile(r"(?i)(best|select|early_stop)[^\n]*(test|eval)[_-]?(metric|score|loss)")),
    ("HARDCODED_PATH", "reproducibility", "warning", re.compile(r"(?:['\"])(?:/home/|/Users/|[A-Za-z]:\\\\)")),
    ("DEBUG_OUTPUT", "reproducibility", "info", re.compile(r"\b(print|pprint)\s*\(")),
]
TEXT_SUFFIXES = {".py", ".sh", ".bash", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".txt"}


def redact(rule_id: str, text: str) -> str:
    if rule_id == "SECRET_LITERAL":
        return re.sub(r"(['\"])[^'\"]+(['\"])", r"\1<redacted>\2", text)
    return text[:300]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-file-bytes", type=int, default=2_000_000)
    args = parser.parse_args()
    root = workspace_root(args.workspace)
    output = resolve_in_workspace(root, args.output)
    try:
        require_authorized_output(root, output)
        snapshot = load_json(resolve_in_workspace(root, args.snapshot, must_exist=True))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    candidates: list[dict] = []
    skipped: list[dict] = []
    for entry in snapshot.get("entries", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = resolve_in_workspace(root, entry["path"], must_exist=True)
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > args.max_file_bytes:
            skipped.append({"path": entry["path"], "reason": "binary_or_large"})
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            skipped.append({"path": entry["path"], "reason": "not_utf8"})
            continue
        for number, line in enumerate(lines, 1):
            for rule_id, axis, severity, pattern in RULES:
                if pattern.search(line):
                    candidates.append({
                        "candidate_id": f"SCAN-{len(candidates)+1:04d}",
                        "rule_id": rule_id,
                        "axis": axis,
                        "suggested_severity": severity,
                        "path": entry["path"],
                        "line": number,
                        "excerpt": redact(rule_id, line.strip()),
                        "requires_adjudication": True,
                    })
    payload = {
        "schema_version": "external_executor_static_review_candidates.v1",
        "input_fingerprint": snapshot.get("input_fingerprint"),
        "created_at": utc_now(),
        "candidates": candidates,
        "skipped": skipped,
        "disclaimer": "Candidates are heuristic signals, not review findings.",
    }
    atomic_write_json(output, payload)
    print(f"{len(candidates)} candidate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
