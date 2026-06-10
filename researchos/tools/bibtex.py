from __future__ import annotations

"""Small BibTeX helpers used by runtime checks.

The project intentionally avoids a hard dependency on a BibTeX parser. These
helpers implement conservative parsing for the generated bibliographies we
control: extracting entry keys, checking common quality problems, deduplicating
entries, and escaping generated field values.
"""

import re
from typing import Any


_ENTRY_START_RE = re.compile(r"@([A-Za-z]+)\s*\{\s*([^,\s{}]+)\s*,", re.MULTILINE)
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:\-+.]*$")
_FIELD_RE = re.compile(r"(?is)(?:^|,)\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*")


def extract_bib_keys_from_text(text: str) -> list[str]:
    """Return BibTeX entry keys in source order."""

    keys: list[str] = []
    for match in _ENTRY_START_RE.finditer(text or ""):
        key = match.group(2).strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def parse_bib_entries(text: str) -> list[dict[str, Any]]:
    """Parse BibTeX entries enough for validation and deduplication.

    The parser tracks brace depth so field values containing braces do not
    prematurely end an entry. It does not try to interpret macros or comments.
    """

    entries: list[dict[str, Any]] = []
    for match in _ENTRY_START_RE.finditer(text or ""):
        start = match.start()
        body_start = match.end()
        depth = 1
        idx = body_start
        while idx < len(text):
            char = text[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    raw = text[start : idx + 1]
                    body = text[body_start:idx]
                    entries.append(
                        {
                            "entry_type": match.group(1).lower(),
                            "key": match.group(2).strip(),
                            "fields": _parse_fields(body),
                            "raw": raw,
                            "start": start,
                            "end": idx + 1,
                        }
                    )
                    break
            idx += 1
    return entries


def _parse_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    matches = list(_FIELD_RE.finditer(body or ""))
    for idx, match in enumerate(matches):
        name = match.group(1).lower()
        value_start = match.end()
        value_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        raw_value = body[value_start:value_end].strip().rstrip(",").strip()
        fields[name] = _strip_bib_value(raw_value)
    return fields


def _strip_bib_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == "{" and value[-1] == "}":
        return value[1:-1].strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].strip()
    return value


def bibtex_quality_issues(text: str, *, require_author: bool = False) -> list[str]:
    """Return human-readable bibliography issues.

    This is deliberately stricter than BibTeX syntax. It flags generated
    placeholders and irrelevant records that compile but should not enter a
    scholarly references file.
    """

    entries = parse_bib_entries(text)
    issues: list[str] = []
    if "@" in (text or "") and not entries:
        issues.append("no_parseable_bibtex_entries")
    keys: dict[str, int] = {}
    for entry in entries:
        key = str(entry.get("key") or "")
        fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
        keys[key] = keys.get(key, 0) + 1
        if not _KEY_RE.match(key):
            issues.append(f"{key}: invalid_key")
        title = str(fields.get("title") or "").strip()
        year = str(fields.get("year") or "").strip()
        author = str(fields.get("author") or fields.get("editor") or fields.get("organization") or "").strip()
        if not title or title.casefold() == "unknown":
            issues.append(f"{key}: missing_or_unknown_title")
        if not year or year.upper() == "XXXX":
            issues.append(f"{key}: missing_year")
        if require_author and not author:
            issues.append(f"{key}: missing_author_or_organization")
        raw = str(entry.get("raw") or "")
        lowered = raw.casefold()
        title_lower = title.casefold()
        author_lower = author.casefold()
        note_lower = str(fields.get("note") or fields.get("annotation") or "").casefold()
        if "irrelevant" in note_lower:
            issues.append(f"{key}: marked_irrelevant")
        if title_lower == "unknown" or author_lower == "unknown" or note_lower in {"unknown", "placeholder", "todo"}:
            issues.append(f"{key}: contains_unknown_placeholder")
        if "123456" in str(fields.get("doi") or ""):
            issues.append(f"{key}: placeholder_doi")
        if re.search(r"author\s*=\s*\{[^{}]*\band\s+others\b", raw, flags=re.IGNORECASE):
            issues.append(f"{key}: author_uses_and_others_placeholder")
        if entry.get("entry_type") == "inproceedings":
            booktitle = str(fields.get("booktitle") or "").casefold()
            if booktitle in {"unknown", ""}:
                issues.append(f"{key}: missing_booktitle")
            if any(name in booktitle for name in ("mis quarterly", "management science", "information systems research")):
                issues.append(f"{key}: likely_journal_record_as_inproceedings")
    for key, count in sorted(keys.items()):
        if count > 1:
            issues.append(f"{key}: duplicate_key_{count}")
    if _unbalanced_braces(text):
        issues.append("unbalanced_braces")
    return issues


def dedupe_bibtex_entries(text: str) -> str:
    """Return a bibliography with duplicate keys removed, preserving first use."""

    entries = parse_bib_entries(text)
    if not entries:
        return text
    seen: set[str] = set()
    kept: list[str] = []
    for entry in entries:
        key = str(entry.get("key") or "")
        if key in seen:
            continue
        seen.add(key)
        kept.append(str(entry.get("raw") or "").strip())
    return "\n\n".join(item for item in kept if item) + "\n"


def escape_bibtex_value(value: object) -> str:
    """Escape common characters for generated BibTeX field values."""

    text = re.sub(r"\s+", " ", str(value or "").strip())
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
    }
    return "".join(replacements.get(char, char) for char in text)


def stable_bib_key(seed: object, *, fallback: str = "paper") -> str:
    """Create a BibTeX key from an identifier/title without unsafe chars."""

    raw = str(seed or "").strip()
    if not raw:
        raw = fallback
    raw = re.sub(r"https?://", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    if not raw:
        raw = fallback
    if not raw[0].isalpha():
        raw = f"p_{raw}"
    return raw[:60]


def _unbalanced_braces(text: str) -> bool:
    depth = 0
    escaped = False
    for char in text or "":
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return True
    return depth != 0
