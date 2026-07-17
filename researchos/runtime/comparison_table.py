from __future__ import annotations

"""Repair helpers for literature/comparison_table.csv."""

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ..literature_identity import (
    is_paper_note_file,
    paper_note_match_keys,
    paper_record_match_keys,
)


_EVIDENCE_RANK = {
    "METADATA_ONLY": 0,
    "ABSTRACT_ONLY": 1,
    "PARTIAL_TEXT": 2,
    "FULL_TEXT": 3,
}


def repair_comparison_table_evidence_levels(workspace: Path) -> dict[str, Any]:
    """Normalize stale comparison_table evidence levels from stronger artifacts."""

    table_path = workspace / "literature" / "comparison_table.csv"
    if not table_path.exists() or table_path.stat().st_size <= 0:
        return {"ok": True, "changed": 0, "reason": "comparison_table_missing"}

    evidence_index = _build_evidence_index(workspace)
    if not evidence_index:
        return {"ok": True, "changed": 0, "reason": "no_stronger_evidence_index"}

    try:
        original = table_path.read_text(encoding="utf-8")
        rows = list(csv.DictReader(io.StringIO(original)))
    except Exception as exc:
        return {"ok": False, "changed": 0, "reason": f"csv_parse_failed:{exc}"}
    if not rows:
        return {"ok": True, "changed": 0, "reason": "empty_table"}

    fieldnames = list(rows[0].keys())
    if "evidence_level" not in fieldnames:
        fieldnames.append("evidence_level")

    changed = 0
    for row in rows:
        best = _lookup_best_evidence(row, evidence_index)
        current = str(row.get("evidence_level") or "").strip() or "METADATA_ONLY"
        if best and _EVIDENCE_RANK.get(best, -1) > _EVIDENCE_RANK.get(current, -1):
            row["evidence_level"] = best
            changed += 1

    if changed <= 0:
        return {"ok": True, "changed": 0, "reason": "already_consistent"}

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    table_path.write_text(output.getvalue(), encoding="utf-8")
    return {"ok": True, "changed": changed, "reason": "evidence_level_repaired"}


def _build_evidence_index(workspace: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    literature = workspace / "literature"
    for root in (literature / "deep_read_notes", literature / "bridge_notes"):
        if not root.exists():
            continue
        pattern = "**/*.md" if root.name == "bridge_notes" else "*.md"
        for note_path in sorted(root.glob(pattern)):
            if not is_paper_note_file(note_path):
                continue
            level = _note_evidence_level(note_path)
            if not level:
                continue
            for key in paper_note_match_keys(note_path):
                _set_stronger(index, key, level)

    audit_path = literature / "access_audit.jsonl"
    if audit_path.exists():
        for record in _load_jsonl(audit_path):
            level = str(record.get("evidence_level") or "").strip()
            # A local PDF records availability only.  Do not promote the
            # comparison table unless a Reader note records actual coverage.
            if level not in _EVIDENCE_RANK:
                continue
            for key in paper_record_match_keys(record):
                _set_stronger(index, key, level)
    return index


def _note_evidence_level(note_path: Path) -> str:
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    status = _field(text, "Status").upper()
    if "FULL-TEXT" in status:
        return "FULL_TEXT"
    if "PARTIAL-TEXT" in status:
        return "PARTIAL_TEXT"
    if "ABSTRACT-ONLY" in status:
        return "ABSTRACT_ONLY"
    return ""


def _lookup_best_evidence(row: dict[str, Any], evidence_index: dict[str, str]) -> str:
    keys = paper_record_match_keys(
        {
            "id": row.get("id"),
            "paper_id": row.get("id"),
            "normalized_id": row.get("id"),
            "title": row.get("title"),
            "doi": row.get("doi"),
        }
    )
    best = ""
    for key in keys:
        level = evidence_index.get(key)
        if level and _EVIDENCE_RANK.get(level, -1) > _EVIDENCE_RANK.get(best, -1):
            best = level
    return best


def _set_stronger(index: dict[str, str], key: str, level: str) -> None:
    if _EVIDENCE_RANK.get(level, -1) > _EVIDENCE_RANK.get(index.get(key, ""), -1):
        index[key] = level


def _field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^-\s+\*\*{re.escape(name)}\*\*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records
