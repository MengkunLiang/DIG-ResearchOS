from __future__ import annotations

"""Shared Literature Artifact Contract for downstream ResearchOS tasks.

This module is intentionally mechanical.  It does not decide scholarly
relevance; it only defines the canonical roots, migrates legacy note layouts,
enumerates readable paper-note cards, and builds a durable manifest that T3.6,
T4, T5, T8, Skills, and resume imports can all consume without guessing paths.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..literature_identity import is_paper_note_file
from .bridge_catalog import (
    CROSS_DOMAIN_CATALOG_INDEX_REL_PATH,
    CROSS_DOMAIN_CATALOG_ROOT_REL_PATH,
    BRIDGE_NOTE_ROOT_REL_PATH,
    migrate_legacy_bridge_catalogs,
)


DEEP_READ_NOTES_REL_PATH = "literature/deep_read_notes"
SHALLOW_READ_NOTES_REL_PATH = "literature/shallow_read_notes"
BRIDGE_NOTES_REL_PATH = BRIDGE_NOTE_ROOT_REL_PATH
CROSS_DOMAIN_CATALOGS_REL_PATH = CROSS_DOMAIN_CATALOG_ROOT_REL_PATH
LITERATURE_MANIFEST_REL_PATH = "literature/literature_manifest.json"
LEGACY_NOTE_ROOT_ALIASES = {
    "literature/paper_notes": DEEP_READ_NOTES_REL_PATH,
    "literature/paper_notes_abstract": SHALLOW_READ_NOTES_REL_PATH,
    "literature/abstract_notes": SHALLOW_READ_NOTES_REL_PATH,
    "literature/reading_notes": DEEP_READ_NOTES_REL_PATH,
    "literature/paper_notes_bridge": BRIDGE_NOTES_REL_PATH,
}

TEXT_NOTE_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class LiteratureNoteCard:
    """One readable paper-note card under a canonical evidence root."""

    paper_id: str
    rel_path: str
    root_type: str
    evidence_level: str
    sha256: str
    size: int
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class LiteratureCorpusValidation:
    ok: bool
    reason: str
    manifest_path: str
    note_count: int
    full_or_partial_note_count: int
    abstract_note_count: int
    bridge_note_count: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_workspace_rel_path(value: str | Path) -> str:
    """Normalize a persisted path to portable workspace-root relative form."""

    text = str(value or "").replace("\\", "/").strip()
    # A path copied through YAML/JSON, a shell, or a Windows/WSL boundary can
    # carry repeated separators.  Persist one canonical POSIX spelling so the
    # same note cannot acquire distinct manifest/fingerprint identities solely
    # because its path was escaped twice.
    text = re.sub(r"/+", "/", text)
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def canonical_literature_roots() -> dict[str, str]:
    return {
        "deep_read_notes": DEEP_READ_NOTES_REL_PATH,
        "shallow_read_notes": SHALLOW_READ_NOTES_REL_PATH,
        "bridge_notes": BRIDGE_NOTES_REL_PATH,
        "cross_domain_catalogs": CROSS_DOMAIN_CATALOGS_REL_PATH,
    }


def migrate_legacy_literature_paths(workspace_dir: Path) -> dict[str, Any]:
    """Run all non-destructive literature migrations known to the contract."""

    workspace = Path(workspace_dir).resolve()
    from .workspace import migrate_workspace_note_directories

    note_report = migrate_workspace_note_directories(workspace)
    catalog_report = migrate_legacy_bridge_catalogs(workspace)
    report = {
        "schema_version": "1.0.0",
        "semantics": "literature_contract_migration_report",
        "generated_at": now_iso(),
        "canonical_roots": canonical_literature_roots(),
        "legacy_aliases": LEGACY_NOTE_ROOT_ALIASES,
        "note_directory_migration": note_report,
        "cross_domain_catalog_migration": catalog_report,
    }
    report_path = workspace / "literature" / "_literature_contract_migration_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = report_path.relative_to(workspace).as_posix()
    return report


def iter_literature_note_cards(workspace_dir: Path, *, include_shallow: bool = True) -> list[LiteratureNoteCard]:
    """Enumerate readable paper-note cards from canonical note roots only."""

    workspace = Path(workspace_dir).resolve()
    roots: list[tuple[str, str, str]] = [
        (DEEP_READ_NOTES_REL_PATH, "deep_read_notes", "FULL_OR_PARTIAL_TEXT"),
        (BRIDGE_NOTES_REL_PATH, "bridge_notes", "FULL_OR_PARTIAL_TEXT"),
    ]
    if include_shallow:
        roots.append((SHALLOW_READ_NOTES_REL_PATH, "shallow_read_notes", "ABSTRACT_ONLY"))

    cards: list[LiteratureNoteCard] = []
    seen_identity: dict[str, LiteratureNoteCard] = {}
    for rel_root, root_type, evidence_level in roots:
        root = workspace / rel_root
        if not root.is_dir():
            continue
        pattern = "**/*.md" if root_type == "bridge_notes" else "*.md"
        for path in sorted(root.glob(pattern), key=lambda item: item.as_posix()):
            if not path.is_file() or path.name.startswith("_"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size <= 0 or not is_paper_note_file(path):
                continue
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            digest = _sha256_file(path)
            rel_path = path.relative_to(workspace).as_posix()
            aliases = tuple(sorted(paper_note_card_aliases(path)))
            identity = _identity_key(path, aliases, digest)
            card = LiteratureNoteCard(
                paper_id=_primary_paper_id(path, aliases),
                rel_path=rel_path,
                root_type=root_type,
                evidence_level=evidence_level,
                sha256=digest,
                size=stat.st_size,
                aliases=aliases,
            )
            existing = seen_identity.get(identity)
            if existing is None or _note_priority(card) < _note_priority(existing):
                seen_identity[identity] = card
    cards = sorted(seen_identity.values(), key=lambda item: (item.root_type, item.rel_path))
    return cards


def build_literature_manifest(workspace_dir: Path, *, write: bool = True) -> dict[str, Any]:
    """Build a deterministic manifest for all live literature artifacts."""

    workspace = Path(workspace_dir).resolve()
    migrate_report = migrate_legacy_literature_paths(workspace)
    note_cards = iter_literature_note_cards(workspace, include_shallow=True)
    root_counts: dict[str, int] = {}
    for card in note_cards:
        root_counts[card.root_type] = root_counts.get(card.root_type, 0) + 1
    catalog_root = workspace / CROSS_DOMAIN_CATALOGS_REL_PATH
    catalog_files = [
        path.relative_to(workspace).as_posix()
        for path in sorted(catalog_root.glob("**/*.json"), key=lambda item: item.as_posix())
        if path.is_file()
    ] if catalog_root.is_dir() else []
    catalog_file_records = [
        {
            "path": rel_path,
            "sha256": _sha256_file(workspace / rel_path),
            "size": (workspace / rel_path).stat().st_size,
        }
        for rel_path in catalog_files
    ]
    pdf_acquisition_path = workspace / "literature/pdf_acquisition_manifest.json"
    pdf_acquisition: dict[str, Any] = {}
    if pdf_acquisition_path.is_file():
        try:
            loaded_acquisition = json.loads(pdf_acquisition_path.read_text(encoding="utf-8"))
            if isinstance(loaded_acquisition, dict):
                pdf_acquisition = {
                    "manifest_path": "literature/pdf_acquisition_manifest.json",
                    "receipts_path": "literature/pdf_acquisition_receipts.jsonl",
                    "counts": loaded_acquisition.get("counts") if isinstance(loaded_acquisition.get("counts"), dict) else {},
                    "evidence_boundary": loaded_acquisition.get("evidence_boundary") or "availability_only_no_reading_level_promotion",
                }
        except (OSError, ValueError, json.JSONDecodeError):
            pdf_acquisition = {
                "manifest_path": "literature/pdf_acquisition_manifest.json",
                "status": "unreadable",
            }
    payload = {
        "schema_version": "1.0.0",
        "semantics": "researchos_literature_artifact_manifest",
        "generated_at": now_iso(),
        "canonical_roots": canonical_literature_roots(),
        "legacy_aliases": LEGACY_NOTE_ROOT_ALIASES,
        "migration_report_path": migrate_report.get("report_path", ""),
        "note_cards": [
            {
                "paper_id": card.paper_id,
                "path": card.rel_path,
                "root_type": card.root_type,
                "evidence_level": card.evidence_level,
                "sha256": card.sha256,
                "size": card.size,
                "aliases": list(card.aliases),
            }
            for card in note_cards
        ],
        "counts": {
            "note_cards": len(note_cards),
            "full_or_partial_note_cards": sum(1 for card in note_cards if card.evidence_level == "FULL_OR_PARTIAL_TEXT"),
            "abstract_note_cards": sum(1 for card in note_cards if card.evidence_level == "ABSTRACT_ONLY"),
            "bridge_note_cards": sum(1 for card in note_cards if card.root_type == "bridge_notes"),
            "cross_domain_catalog_files": len(catalog_files),
        },
        "cross_domain_catalogs": {
            "root": CROSS_DOMAIN_CATALOGS_REL_PATH,
            "index_path": CROSS_DOMAIN_CATALOG_INDEX_REL_PATH,
            "files": catalog_files,
            "file_records": catalog_file_records,
            "usage_boundary": (
                "cross_domain_catalogs is retrieval/context metadata, not a paper-note root; "
                "bridge_notes remains the full/partial Bridge paper-note evidence root."
            ),
        },
        "pdf_acquisition": pdf_acquisition,
        "root_counts": root_counts,
    }
    if write:
        payload = _preserve_manifest_generated_at_if_semantically_unchanged(
            workspace / LITERATURE_MANIFEST_REL_PATH,
            payload,
        )
        path = workspace / LITERATURE_MANIFEST_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == content:
                return payload
        except OSError:
            pass
        path.write_text(content, encoding="utf-8")
    return payload


def _preserve_manifest_generated_at_if_semantically_unchanged(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep manifest bytes stable when the literature contract did not change."""

    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return payload
    if not isinstance(existing, dict):
        return payload
    if _manifest_semantic_view(existing) != _manifest_semantic_view(payload):
        return payload
    existing_generated_at = str(existing.get("generated_at") or "").strip()
    if existing_generated_at:
        payload = dict(payload)
        payload["generated_at"] = existing_generated_at
    return payload


def _manifest_semantic_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the manifest fields that define scientific input identity."""

    semantic = dict(payload)
    semantic.pop("generated_at", None)
    return semantic


def validate_literature_corpus(
    workspace_dir: Path,
    *,
    require_full_or_partial: bool = True,
    write_manifest: bool = True,
) -> LiteratureCorpusValidation:
    """Validate that downstream tasks have real, readable paper-note input."""

    manifest = build_literature_manifest(workspace_dir, write=write_manifest)
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    note_count = int(counts.get("note_cards") or 0)
    full_count = int(counts.get("full_or_partial_note_cards") or 0)
    abstract_count = int(counts.get("abstract_note_cards") or 0)
    bridge_count = int(counts.get("bridge_note_cards") or 0)
    if note_count <= 0:
        return LiteratureCorpusValidation(False, "literature manifest contains zero readable paper-note cards", LITERATURE_MANIFEST_REL_PATH, note_count, full_count, abstract_count, bridge_count)
    if require_full_or_partial and full_count <= 0:
        return LiteratureCorpusValidation(False, "literature manifest contains only abstract-level notes; full/partial paper notes are required", LITERATURE_MANIFEST_REL_PATH, note_count, full_count, abstract_count, bridge_count)
    return LiteratureCorpusValidation(True, "", LITERATURE_MANIFEST_REL_PATH, note_count, full_count, abstract_count, bridge_count)


def build_note_card_lookup(workspace_dir: Path, *, include_shallow: bool = True) -> dict[str, LiteratureNoteCard]:
    lookup: dict[str, LiteratureNoteCard] = {}
    for card in iter_literature_note_cards(workspace_dir, include_shallow=include_shallow):
        for alias in card.aliases:
            lookup.setdefault(alias, card)
    return lookup


def resolve_literature_note_card(workspace_dir: Path, paper_id_or_key: str, *, include_shallow: bool = True) -> LiteratureNoteCard | None:
    key = str(paper_id_or_key or "").strip()
    if not key:
        return None
    lookup = build_note_card_lookup(workspace_dir, include_shallow=include_shallow)
    for alias in (key, *_note_card_lookup_keys(key)):
        card = lookup.get(alias)
        if card is not None:
            return card
    return None


def resolve_literature_note_card_path(
    workspace_dir: Path,
    requested_path: str,
    *,
    include_shallow: bool = True,
) -> str | None:
    """Resolve a guessed note-card path to a real canonical manifest path."""

    normalized = normalize_workspace_rel_path(requested_path)
    if not normalized.endswith(".md"):
        return None
    if not normalized.startswith(
        (DEEP_READ_NOTES_REL_PATH + "/", SHALLOW_READ_NOTES_REL_PATH + "/", BRIDGE_NOTES_REL_PATH + "/")
    ):
        return None
    card = resolve_literature_note_card(Path(workspace_dir), Path(normalized).stem, include_shallow=include_shallow)
    return card.rel_path if card else None


def paper_note_card_aliases(path: Path) -> set[str]:
    aliases = {path.stem, normalize_paper_note_alias(path.stem)}
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return {alias for alias in aliases if alias}
    for pattern in (
        r"(?im)^\s*-\s*\*\*ID\*\*\s*:\s*(.+?)\s*$",
        r"(?im)^\s*ID\s*:\s*(.+?)\s*$",
        r"(?im)^\s*-\s*\*\*DOI/arXiv\*\*\s*:\s*(.+?)\s*$",
        r"(?im)^\s*DOI/arXiv\s*:\s*(.+?)\s*$",
    ):
        for match in re.finditer(pattern, head):
            value = _strip_metadata_value(match.group(1))
            if not value:
                continue
            aliases.update(_identifier_aliases(value))
    aliases.update(_citation_key_aliases(head))
    return {alias for alias in aliases if alias}


def normalize_paper_note_alias(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", str(value or "").strip().casefold())
    return normalized.strip("_")


def _note_card_lookup_keys(value: str) -> set[str]:
    normalized = normalize_paper_note_alias(value)
    keys = {normalized}
    if normalized.startswith("p_10_"):
        keys.add("doi_" + normalized[2:])
        keys.add(normalized[2:])
    if normalized.startswith("doi_10_"):
        keys.add("p_" + normalized[4:])
        keys.add(normalized[4:])
    return {key for key in keys if key}


def _identifier_aliases(value: str) -> set[str]:
    aliases = {value, normalize_paper_note_alias(value)}
    lowered = value.casefold()
    if lowered.startswith("doi:"):
        aliases.add(value[4:])
        aliases.add(normalize_paper_note_alias(value[4:]))
    if lowered.startswith("https://doi.org/"):
        aliases.add(value.split("/", 3)[-1])
        aliases.add(normalize_paper_note_alias(value.split("/", 3)[-1]))
    if value.lower().startswith("10."):
        doi_alias = "doi_" + value
        aliases.add(doi_alias)
        aliases.add(normalize_paper_note_alias(doi_alias))
    if re.match(r"^\d{4}\.\d{4,5}(?:v\d+)?$", value, flags=re.IGNORECASE):
        arxiv_alias = "arxiv_" + value
        aliases.add(arxiv_alias)
        aliases.add(normalize_paper_note_alias(arxiv_alias))
    return aliases


def _strip_metadata_value(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"\s+\(.*?\)\s*$", "", value).strip()
    return value.strip("`[](){}.,; ")


def _citation_key_aliases(text: str) -> set[str]:
    title_match = re.search(r"(?m)^\s*#\s+(.+?)\s*$", text or "")
    authors_match = re.search(r"(?im)^\s*-\s*\*\*Authors\*\*\s*:\s*(.+?)\s*$", text or "")
    if not title_match or not authors_match:
        return set()
    years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", text or "")))
    if not years:
        return set()
    author_tokens = _author_key_tokens(authors_match.group(1))
    title_tokens = _title_key_tokens(title_match.group(1))
    if not author_tokens or not title_tokens:
        return set()
    title_aliases = set(title_tokens[:10])
    for index in range(min(9, len(title_tokens) - 1)):
        title_aliases.add(title_tokens[index] + title_tokens[index + 1])
    aliases: set[str] = set()
    for author in author_tokens:
        for year in years:
            for token in title_aliases:
                aliases.add(normalize_paper_note_alias(f"{author}{year}{token}"))
    return aliases


def _author_key_tokens(authors: str) -> list[str]:
    first_author = str(authors or "").split(",", 1)[0]
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", first_author)
    tokens: list[str] = []
    if raw_tokens:
        tokens.extend([raw_tokens[0], raw_tokens[-1]])
    seen: set[str] = set()
    normalized: list[str] = []
    for token in tokens:
        alias = normalize_paper_note_alias(token)
        if alias and alias not in seen:
            seen.add(alias)
            normalized.append(alias)
    return normalized


def _title_key_tokens(title: str) -> list[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "without",
        "using",
        "via",
    }
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", str(title or "")):
        normalized = normalize_paper_note_alias(token)
        if not normalized or normalized in stopwords:
            continue
        if len(normalized) < 2 and not token.isupper():
            continue
        tokens.append(normalized)
    return tokens


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity_key(path: Path, aliases: tuple[str, ...] | set[str], digest: str) -> str:
    for alias in sorted(aliases):
        if alias.startswith(("doi_", "arxiv_", "w")) or re.match(r"^10_", alias):
            return alias
    return f"sha256:{digest}"


def _primary_paper_id(path: Path, aliases: tuple[str, ...] | set[str]) -> str:
    for alias in sorted(aliases):
        if alias and alias != normalize_paper_note_alias(path.stem):
            return alias
    return path.stem


def _note_priority(card: LiteratureNoteCard) -> int:
    order = {
        "deep_read_notes": 0,
        "bridge_notes": 1,
        "shallow_read_notes": 2,
    }
    return order.get(card.root_type, 99)
