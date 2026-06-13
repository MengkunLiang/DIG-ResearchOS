from __future__ import annotations

"""Human-readable citation/index helpers for paper notes.

ResearchOS stores notes by stable machine identifiers so resume/dedup remain
safe. This module builds a separate human-facing ledger that maps note IDs,
titles, DOI/arXiv/OpenAlex aliases, and BibTeX keys for downstream writing.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .literature_identity import (
    add_identity_key_variants,
    canonical_note_id,
    is_paper_note_file,
    normalize_loose_identity_key,
)
from .tools.bibtex import parse_bib_entries


PAPER_NOTE_INDEX_REL_PATH = "literature/paper_note_index.json"
CITATION_MAP_REL_PATH = "literature/citation_map.json"


def refresh_literature_citation_maps(workspace_dir: Path, *, write: bool = True) -> dict[str, Any]:
    """Build and optionally persist paper note index and citation map."""

    workspace_dir = workspace_dir.resolve()
    literature_dir = workspace_dir / "literature"
    notes = _collect_note_entries(literature_dir)
    bib_entries = _collect_bib_entries(literature_dir / "related_work.bib")
    _attach_bib_keys(notes, bib_entries)
    citation_map = _build_citation_map(notes, bib_entries)
    note_index = {
        "version": 1,
        "semantics": "paper_note_index_human_readable_aliases",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note_count": len(notes),
        "entries": notes,
    }
    if write:
        _atomic_write_json(workspace_dir / PAPER_NOTE_INDEX_REL_PATH, note_index)
        _atomic_write_json(workspace_dir / CITATION_MAP_REL_PATH, citation_map)
    return {
        "paper_note_index": note_index,
        "citation_map": citation_map,
    }


def load_or_build_citation_map(literature_dir: Path) -> dict[str, Any]:
    """Load citation_map.json or build it from local notes/BibTeX."""

    literature_dir = literature_dir.resolve()
    workspace_dir = literature_dir.parent
    map_path = literature_dir / "citation_map.json"
    if map_path.exists():
        try:
            data = json.loads(map_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("semantics") == "paper_note_to_bibtex_citation_map":
                return data
        except Exception:
            pass
    return refresh_literature_citation_maps(workspace_dir, write=False)["citation_map"]


def citation_map_key_lookup(citation_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return alias-key lookup for note IDs, titles, DOI/arXiv aliases, and bib keys."""

    lookup: dict[str, dict[str, Any]] = {}
    for entry in citation_map.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        aliases = entry.get("aliases") if isinstance(entry.get("aliases"), list) else []
        for alias in aliases:
            for key in _alias_lookup_keys(alias):
                lookup.setdefault(key, entry)
        for field in ("note_id", "paper_id", "bib_key", "title", "doi", "arxiv", "openalex_id"):
            value = str(entry.get(field) or "").strip()
            if not value:
                continue
            for key in _alias_lookup_keys(value):
                lookup.setdefault(key, entry)
    return lookup


def citation_ref_for_id(
    raw_id: Any,
    citation_map: dict[str, Any] | None = None,
    lookup: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Return a writing reference for a note/paper ID, preferring real BibTeX."""

    raw = str(raw_id or "").strip()
    if not raw:
        return ""
    if citation_map or lookup:
        entry = (lookup or citation_map_key_lookup(citation_map or {})).get(_lookup_key(raw))
        if entry and entry.get("bib_key"):
            return f"\\cite{{{entry['bib_key']}}}"
        if entry and entry.get("note_id"):
            return f"[note:{entry['note_id']}]"
    return f"[note:{canonical_note_id(raw)}]"


def citation_entry_for_id(
    raw_id: Any,
    citation_map: dict[str, Any] | None = None,
    lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not citation_map and not lookup:
        return None
    return (lookup or citation_map_key_lookup(citation_map or {})).get(_lookup_key(str(raw_id or "")))


def _collect_note_entries(literature_dir: Path) -> list[dict[str, Any]]:
    paths: list[Path] = []
    for rel in ("paper_notes", "paper_notes_bridge", "paper_notes_abstract"):
        root = literature_dir / rel
        if not root.exists():
            continue
        paths.extend(path for path in root.glob("**/*.md") if is_paper_note_file(path))

    entries: list[dict[str, Any]] = []
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        title = _title_from_note(path, text)
        paper_id = _field(text, "ID") or path.stem
        doi_or_arxiv = _field(text, "DOI/arXiv")
        doi = _extract_doi(doi_or_arxiv)
        arxiv = _extract_arxiv(doi_or_arxiv)
        aliases = _entry_aliases(
            path.stem,
            paper_id,
            title,
            doi,
            arxiv,
            doi_or_arxiv,
            _field(text, "Normalized ID"),
        )
        note_id = canonical_note_id(paper_id) or path.stem
        entries.append(
            {
                "note_id": note_id,
                "paper_id": paper_id,
                "source_file": str(path.relative_to(literature_dir)),
                "title": title,
                "authors": _field(text, "Authors"),
                "venue": _field(text, "Venue"),
                "doi": doi,
                "arxiv": arxiv,
                "openalex_id": _extract_openalex_id(paper_id),
                "evidence_level": _evidence_level(_field(text, "Status"), path),
                "bib_key": "",
                "citation_ref": f"[note:{note_id}]",
                "display_label": _display_label(title, _field(text, "Venue")),
                "aliases": sorted(aliases),
            }
        )
    return entries


def _collect_bib_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = parse_bib_entries(path.read_text(encoding="utf-8", errors="replace"))
    out: list[dict[str, Any]] = []
    for entry in entries:
        fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        doi = _extract_doi(str(fields.get("doi") or fields.get("url") or ""))
        arxiv = _extract_arxiv(str(fields.get("eprint") or fields.get("url") or ""))
        out.append(
            {
                "key": key,
                "title": str(fields.get("title") or "").strip(),
                "doi": doi,
                "arxiv": arxiv,
                "url": str(fields.get("url") or "").strip(),
                "year": str(fields.get("year") or "").strip(),
                "aliases": sorted(_entry_aliases(key, fields.get("title"), doi, arxiv, fields.get("url"), fields.get("eprint"))),
            }
        )
    return out


def _attach_bib_keys(notes: list[dict[str, Any]], bib_entries: list[dict[str, Any]]) -> None:
    bib_lookup: dict[str, dict[str, Any]] = {}
    for bib in bib_entries:
        for alias in bib.get("aliases") or []:
            bib_lookup.setdefault(_lookup_key(alias), bib)
    for note in notes:
        matched: dict[str, Any] | None = None
        for alias in note.get("aliases") or []:
            matched = bib_lookup.get(_lookup_key(alias))
            if matched:
                break
        if matched is None:
            matched = _match_bib_by_title(note, bib_entries)
        if matched:
            bib_key = str(matched.get("key") or "").strip()
            if bib_key:
                note["bib_key"] = bib_key
                note["citation_ref"] = f"\\cite{{{bib_key}}}"


def _match_bib_by_title(note: dict[str, Any], bib_entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    title = normalize_loose_identity_key(str(note.get("title") or ""))
    if not title:
        return None
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    title_tokens = _tokens(title)
    for bib in bib_entries:
        bib_title = normalize_loose_identity_key(str(bib.get("title") or ""))
        if not bib_title:
            continue
        if title == bib_title:
            return bib
        if title in bib_title or bib_title in title:
            ratio = min(len(title), len(bib_title)) / max(1, max(len(title), len(bib_title)))
            if ratio > best[0]:
                best = (ratio, bib)
            continue
        bib_tokens = _tokens(bib_title)
        if not title_tokens or not bib_tokens:
            continue
        overlap = title_tokens & bib_tokens
        recall = len(overlap) / max(1, len(title_tokens))
        precision = len(overlap) / max(1, len(bib_tokens))
        score = (2 * recall * precision) / max(0.001, recall + precision)
        if len(overlap) >= 4 and score > best[0]:
            best = (score, bib)
    return best[1] if best[0] >= 0.82 else None


def _build_citation_map(notes: list[dict[str, Any]], bib_entries: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for note in notes:
        entries.append(
            {
                "note_id": note.get("note_id"),
                "paper_id": note.get("paper_id"),
                "title": note.get("title"),
                "source_file": note.get("source_file"),
                "display_label": note.get("display_label"),
                "bib_key": note.get("bib_key", ""),
                "citation_ref": note.get("citation_ref"),
                "doi": note.get("doi", ""),
                "arxiv": note.get("arxiv", ""),
                "openalex_id": note.get("openalex_id", ""),
                "evidence_level": note.get("evidence_level"),
                "aliases": sorted(
                    {
                        *list(note.get("aliases", []) if isinstance(note.get("aliases"), list) else []),
                        str(Path(str(note.get("source_file") or "")).stem),
                    }
                ),
            }
        )
    return {
        "version": 1,
        "semantics": "paper_note_to_bibtex_citation_map",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note_count": len(notes),
        "bib_entry_count": len(bib_entries),
        "mapped_bib_count": sum(1 for note in notes if note.get("bib_key")),
        "entries": entries,
        "usage": {
            "storage_key_rule": "Keep paper note filenames stable; use display_label/title for humans.",
            "markdown_synthesis_ref_rule": "Prefer citation_ref when bib_key exists; otherwise use [note:<note_id>].",
        },
    }


def _field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^-\s+\*\*{re.escape(name)}\*\*:\s*(.+)$", text or "")
    return match.group(1).strip() if match else ""


def _title_from_note(path: Path, text: str) -> str:
    match = re.search(r"(?m)^#\s+(.+)$", text or "")
    return match.group(1).strip() if match else path.stem


def _entry_aliases(*values: Any) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw or raw.casefold() in {"n/a", "none", "null", "unknown"}:
            continue
        aliases.add(raw)
        aliases.add(canonical_note_id(raw))
        add_identity_key_variants(aliases, raw)
    return {alias for alias in aliases if alias}


def _alias_lookup_keys(value: Any) -> set[str]:
    keys = {_lookup_key(value)}
    expanded: set[str] = set()
    add_identity_key_variants(expanded, value)
    keys.update(_lookup_key(item) for item in expanded if item)
    return {key for key in keys if key}


def _lookup_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return normalize_loose_identity_key(canonical_note_id(raw)) or normalize_loose_identity_key(raw)


def _extract_doi(value: str) -> str:
    match = re.search(r"10\.\d{4,9}/[^\s,;)\]}]+", str(value or ""), flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;").lower() if match else ""


def _extract_arxiv(value: str) -> str:
    text = str(value or "")
    match = re.search(
        r"(?:arxiv\s*[:/]\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).lower()
    match = re.search(r"(?<![A-Za-z0-9./])(\d{4}\.\d{4,5}(?:v\d+)?)(?![A-Za-z0-9])", text)
    return match.group(1).lower() if match else ""


def _extract_openalex_id(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"\bW\d+\b", text)
    return match.group(0) if match else ""


def _evidence_level(status: str, path: Path) -> str:
    raw = str(status or "").upper()
    if "ABSTRACT" in raw or "paper_notes_abstract" in str(path):
        return "ABSTRACT_ONLY"
    if "PARTIAL" in raw:
        return "PARTIAL_TEXT"
    if "FULL" in raw:
        return "FULL_TEXT"
    return "UNKNOWN"


def _display_label(title: str, venue: str) -> str:
    year = ""
    match = re.search(r"\b(19|20)\d{2}\b", venue or "")
    if match:
        year = match.group(0)
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    return f"{title} ({year})" if year and title else title


def _tokens(value: str) -> set[str]:
    stop = {
        "a",
        "an",
        "and",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
        "等",
    }
    return {token for token in value.split() if len(token) > 1 and token not in stop}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
