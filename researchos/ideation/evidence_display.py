"""Researcher-facing labels for immutable T4 Evidence Atom identifiers.

Evidence Atom IDs are stable controller keys.  They are useful in artifacts
and logs, but an ``EA-...`` hash alone is not a usable instruction for a
researcher deciding which paper must be checked next.  This module resolves
the identifier to existing workspace metadata without changing the evidence
record, its permission, or any Candidate claim.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


EVIDENCE_INDEX_REL_PATH = "ideation/evidence/evidence_index.jsonl"
# ``\b`` treats CJK characters as word characters.  Candidate prose often
# contains text such as ``依赖EA-...`` without a space, so use the narrower
# controller-ID boundary instead of a Unicode word boundary.
_EVIDENCE_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])EA-[A-Za-z0-9]+(?![A-Za-z0-9_-])")
_NOTE_TITLE_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_CATALOG_TITLE_RE = re.compile(r"Retrieved cross-domain record:\s*(.+?)(?:\.\s+(?:Metadata|Abstract-only)|$)")


def load_evidence_display_catalog(workspace: Path) -> dict[str, dict[str, str]]:
    """Resolve persisted Evidence Atoms to concise researcher-facing metadata."""

    catalog: dict[str, dict[str, str]] = {}
    path = workspace / EVIDENCE_INDEX_REL_PATH
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return catalog
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        atom_id = str(item.get("atom_id") or "").strip()
        if not atom_id:
            continue
        source_path = str(item.get("source_path") or "").strip()
        title = _source_title(workspace, source_path, item)
        catalog[atom_id] = {
            "atom_id": atom_id,
            "title": title,
            "reading_label": _reading_label(str(item.get("reading_level") or "")),
            "evidence_label": _evidence_label(str(item.get("evidence_status") or "")),
            "source_path": source_path,
            "section_title": str(item.get("section_title") or "").strip(),
        }
    return catalog


def humanize_evidence_ids(value: Any, catalog: dict[str, dict[str, str]]) -> Any:
    """Replace known IDs in display-only values while retaining traceability."""

    if isinstance(value, str):
        return _EVIDENCE_ID_RE.sub(lambda match: _display_reference(match.group(0), catalog), value)
    if isinstance(value, list):
        return [humanize_evidence_ids(item, catalog) for item in value]
    if isinstance(value, tuple):
        return tuple(humanize_evidence_ids(item, catalog) for item in value)
    if isinstance(value, dict):
        return {key: humanize_evidence_ids(item, catalog) for key, item in value.items()}
    return value


def referenced_evidence(corpus: Any, catalog: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    """Return each known Evidence Atom mentioned in a Candidate, in text order."""

    try:
        text = json.dumps(corpus, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(corpus or "")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _EVIDENCE_ID_RE.finditer(text):
        atom_id = match.group(0)
        if atom_id in seen or atom_id not in catalog:
            continue
        seen.add(atom_id)
        result.append(dict(catalog[atom_id]))
    return result


def _display_reference(atom_id: str, catalog: dict[str, dict[str, str]]) -> str:
    record = catalog.get(atom_id)
    if not record:
        return atom_id
    return f"《{record['title']}》[{record['reading_label']}；{atom_id}]"


def _source_title(workspace: Path, source_path: str, item: dict[str, Any]) -> str:
    section_title = str(item.get("section_title") or "").strip()
    if section_title.startswith("Bridge catalog:"):
        return section_title.removeprefix("Bridge catalog:").strip()
    content = str(item.get("content") or "")
    catalog_match = _CATALOG_TITLE_RE.search(content)
    if catalog_match:
        return catalog_match.group(1).strip()
    if source_path:
        try:
            note_text = (workspace / source_path).read_text(encoding="utf-8", errors="replace")[:8_000]
        except OSError:
            note_text = ""
        heading = _NOTE_TITLE_RE.search(note_text)
        if heading:
            title = re.sub(r"^\[[^]]+\]\s*", "", heading.group(1).strip())
            if title:
                return title
    if section_title and not re.match(r"^\d+(?:\.\d+)?\s+", section_title):
        return section_title
    return str(item.get("paper_id") or "未命名材料").strip()


def _reading_label(level: str) -> str:
    return {
        "full_text": "全文已读",
        "partial_text": "定向阅读",
        "abstract_only": "仅摘要线索",
        "metadata_only": "仅元数据线索",
        "synthesis_inference": "综合推断",
        "brainstorm": "构想线索",
    }.get(level.strip().lower(), "阅读状态未标注")


def _evidence_label(status: str) -> str:
    return {
        "direct_support": "直接依据",
        "limited_support": "有限依据",
        "abstract_hint": "待全文核验",
        "llm_inference": "模型推断",
        "conjecture": "研究构想",
    }.get(status.strip().lower(), "证据状态未标注")
