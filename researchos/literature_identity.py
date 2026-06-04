from __future__ import annotations

"""Conservative identity helpers for literature records and T3 notes.

The same paper can appear as a title, DOI, arXiv id, OpenAlex id, URL, or a
filesystem-safe stem. These helpers intentionally generate several full-record
keys, then compare records by set intersection. They do not perform substring
matching or domain-knowledge classification.
"""

from pathlib import Path
import re
from typing import Any


def is_paper_note_file(path: Path) -> bool:
    """Return true for real T3 paper note files, not directory guides/docs."""

    if not path.is_file() or path.suffix.lower() != ".md":
        return False
    name = path.name.strip().lower()
    if name.startswith("_"):
        return False
    if name in {"readme.md", "dir_guide.md"}:
        return False
    return True


def normalize_identity_key(value: str) -> str:
    """Case-fold and collapse whitespace while preserving meaningful symbols."""

    if not value:
        return ""
    return " ".join(str(value).casefold().split())


def normalize_loose_identity_key(value: str) -> str:
    """Drop punctuation differences for title/file-stem comparisons."""

    if not value:
        return ""
    text = str(value).casefold()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def add_identity_key_variants(keys: set[str], value: Any) -> None:
    raw = str(value or "").strip()
    if not raw:
        return

    candidates = {
        raw,
        raw.replace(":", "_").replace("/", "_"),
        raw.replace("_", " "),
        raw.replace("_", ":"),
        raw.replace("_", "/"),
        raw.replace("-", " "),
    }
    lowered = raw.lower()
    if lowered.startswith("arxiv_"):
        candidates.add("arxiv:" + raw[len("arxiv_"):])
        candidates.add(raw[len("arxiv_"):])
    if lowered.startswith("arxiv:"):
        arxiv_id = raw.split(":", 1)[1].strip()
        candidates.add("arxiv_" + arxiv_id)
        candidates.add(arxiv_id)
    if lowered.startswith("https://doi.org/") or lowered.startswith("http://doi.org/"):
        candidates.add(raw.split("doi.org/", 1)[1])
    if lowered.startswith("doi:"):
        candidates.add(raw.split(":", 1)[1])

    for token in extract_identifier_tokens(raw):
        candidates.add(token)

    for candidate in candidates:
        strict = normalize_identity_key(candidate)
        loose = normalize_loose_identity_key(candidate)
        if strict:
            keys.add(strict)
        if loose:
            keys.add(loose)


def extract_identifier_tokens(value: str) -> list[str]:
    text = str(value or "")
    tokens: list[str] = []
    for token in re.findall(
        r"(?:arxiv:\s*)?\d{4}\.\d{4,5}(?:v\d+)?|10\.\d{4,9}/[^\s,;)\]]+",
        text,
        flags=re.IGNORECASE,
    ):
        cleaned = token.replace(" ", "").rstrip(".,;")
        if not cleaned.lower().startswith("arxiv:") and re.fullmatch(
            r"\d{4}\.\d{4,5}(?:v\d+)?",
            cleaned,
        ):
            cleaned = f"arxiv:{cleaned}"
        tokens.append(cleaned)
    return tokens


def paper_record_match_keys(record: dict[str, Any]) -> set[str]:
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    candidates = [
        record.get("normalized_id"),
        record.get("paper_id"),
        record.get("id"),
        record.get("canonical_id"),
        record.get("title"),
        record.get("doi"),
        record.get("url"),
        external_ids.get("ArXiv"),
        external_ids.get("DOI"),
    ]
    keys: set[str] = set()
    for candidate in candidates:
        add_identity_key_variants(keys, candidate)
    return {key for key in keys if key}


def paper_note_match_keys(note_path: Path) -> set[str]:
    keys: set[str] = set()
    add_identity_key_variants(keys, note_path.stem)
    try:
        content = note_path.read_text(encoding="utf-8")
    except OSError:
        return keys

    for line in content.splitlines()[:80]:
        stripped = line.strip()
        if stripped.startswith("# "):
            add_identity_key_variants(keys, stripped.lstrip("#").strip())
            continue
        match = re.match(r"-\s+\*\*(ID|DOI/arXiv)\*\*:\s*(.+)$", stripped, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(2).strip()
        add_identity_key_variants(keys, value)
    return {key for key in keys if key}


def record_is_covered(record: dict[str, Any], completed_keys: set[str]) -> bool:
    return bool(paper_record_match_keys(record) & completed_keys)


def display_record_key(record: dict[str, Any]) -> str:
    value = (
        record.get("normalized_id")
        or record.get("paper_id")
        or record.get("id")
        or record.get("title")
        or "unknown"
    )
    return normalize_identity_key(str(value))
