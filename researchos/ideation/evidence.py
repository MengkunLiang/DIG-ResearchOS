"""Deterministic Evidence Index construction for T4.

This module indexes existing paper notes and applies the system permission
policy. It does not infer scientific conclusions, rank research opportunities,
or allow an LLM to upgrade a reading level.
"""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re
from typing import Any

import yaml

from ..pydantic_compat import model_dump
from ..runtime.system_config import system_config_path
from .models import DomainRole, EvidenceAtom, EvidencePermission, EvidenceStatus, ReadingLevel
from .state import T4ArtifactStore


_NOTE_ROOTS: tuple[tuple[str, DomainRole], ...] = (
    ("literature/paper_notes", DomainRole.CORE),
    ("literature/paper_notes_abstract", DomainRole.CORE),
    ("literature/paper_notes_bridge", DomainRole.BRIDGE),
    ("literature/deep_read_notes", DomainRole.CORE),
    ("literature/shallow_read_notes", DomainRole.CORE),
    ("literature/bridge_notes", DomainRole.BRIDGE),
)
_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)


def build_idea_evidence_index(
    workspace_dir: Path,
    *,
    store: T4ArtifactStore | None = None,
    permissions_path: Path | None = None,
) -> dict[str, Any]:
    """Build and persist section-level EvidenceAtoms from all note tracks."""

    workspace = Path(workspace_dir)
    policy = _load_permission_policy(permissions_path)
    atoms: list[EvidenceAtom] = []
    seen: set[tuple[str, str, str]] = set()
    visited: set[Path] = set()
    for relative_root, domain_role in _NOTE_ROOTS:
        root = workspace / relative_root
        if not root.is_dir():
            continue
        for note_path in sorted(root.rglob("*.md")):
            if not note_path.is_file() or note_path.name.startswith("_") or note_path.name.casefold() == "readme.md":
                continue
            resolved = note_path.resolve()
            if resolved in visited:
                continue
            visited.add(resolved)
            try:
                text = note_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            reading_level = _reading_level(note_path, text)
            for section_key, section_title, content in _extract_sections(text):
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                relative_path = note_path.relative_to(workspace).as_posix()
                duplicate_key = (relative_path, section_key, content_hash)
                if duplicate_key in seen:
                    continue
                seen.add(duplicate_key)
                allowed, forbidden = _permissions_for(policy, reading_level)
                status = _status_for(reading_level)
                atom_id = "EA-" + hashlib.sha256(
                    f"{relative_path}\0{section_key}\0{content_hash}".encode("utf-8")
                ).hexdigest()[:16]
                atoms.append(
                    EvidenceAtom(
                        atom_id=atom_id,
                        paper_id=note_path.stem,
                        source_path=relative_path,
                        section_key=section_key,
                        section_title=section_title,
                        content=content,
                        domain_role=domain_role,
                        reading_level=reading_level,
                        evidence_status=status,
                        allowed_uses=allowed,
                        forbidden_uses=forbidden,
                        requires_original_section_check=True,
                        content_fingerprint=content_hash,
                    )
                )
    atoms.sort(key=lambda item: item.atom_id)
    summary = _index_summary(atoms)
    target_store = store or T4ArtifactStore(workspace)
    atom_records = [model_dump(atom, mode="json") for atom in atoms]
    target_store.write_jsonl("ideation/evidence/evidence_index.jsonl", atom_records)
    target_store.write_json("ideation/evidence/evidence_index_summary.json", summary)
    return {
        "atoms": atoms,
        "summary": summary,
        "atoms_path": "ideation/evidence/evidence_index.jsonl",
        "summary_path": "ideation/evidence/evidence_index_summary.json",
    }


def _load_permission_policy(path: Path | None) -> dict[str, Any]:
    source = path or system_config_path("idea_evidence_permissions.yaml")
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) if source.exists() else {}
    levels = raw.get("levels") if isinstance(raw, dict) and isinstance(raw.get("levels"), dict) else {}
    if not levels:
        raise ValueError(f"T4 evidence permission policy is missing or invalid: {source}")
    return levels


def _permissions_for(policy: dict[str, Any], reading_level: ReadingLevel) -> tuple[set[EvidencePermission], set[EvidencePermission]]:
    raw = policy.get(reading_level.value) if isinstance(policy.get(reading_level.value), dict) else {}
    return (
        {EvidencePermission(str(item)) for item in raw.get("allowed", [])},
        {EvidencePermission(str(item)) for item in raw.get("forbidden", [])},
    )


def _reading_level(path: Path, text: str) -> ReadingLevel:
    source = path.as_posix().casefold()
    head = text[:10000].casefold()
    if "abstract" in source or "shallow" in source or "[abstract" in head or "abstract-only" in head:
        return ReadingLevel.ABSTRACT_ONLY
    if "[partial" in head or "partial-text" in head:
        return ReadingLevel.PARTIAL_TEXT
    if "[metadata" in head or "metadata-only" in head:
        return ReadingLevel.METADATA_ONLY
    return ReadingLevel.FULL_TEXT


def _status_for(reading_level: ReadingLevel) -> EvidenceStatus:
    return {
        ReadingLevel.FULL_TEXT: EvidenceStatus.DIRECT_SUPPORT,
        ReadingLevel.PARTIAL_TEXT: EvidenceStatus.LIMITED_SUPPORT,
        ReadingLevel.ABSTRACT_ONLY: EvidenceStatus.ABSTRACT_HINT,
        ReadingLevel.METADATA_ONLY: EvidenceStatus.ABSTRACT_HINT,
    }.get(reading_level, EvidenceStatus.CONJECTURE)


def _extract_sections(text: str) -> list[tuple[str, str, str]]:
    matches = list(_HEADING_RE.finditer(text))
    sections: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        title = match.group("title").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = " ".join(text[start:end].strip().split())
        if not content or title.casefold() in {"references", "references and notes"}:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")[:96] or "section"
        sections.append((key, title, content))
    if sections:
        return sections
    content = " ".join(text.strip().split())
    return [("note_body", "Paper note", content)] if content else []


def _index_summary(atoms: list[EvidenceAtom]) -> dict[str, Any]:
    by_level = Counter(item.reading_level.value for item in atoms)
    by_domain = Counter(item.domain_role.value for item in atoms)
    by_section = Counter(item.section_key for item in atoms)
    upgrades = [
        item.atom_id
        for item in atoms
        if item.reading_level in {ReadingLevel.ABSTRACT_ONLY, ReadingLevel.METADATA_ONLY}
    ]
    return {
        "schema_version": "1.0.0",
        "semantics": "t4_evidence_index_summary",
        "atom_count": len(atoms),
        "counts_by_reading_level": dict(sorted(by_level.items())),
        "counts_by_domain_role": dict(sorted(by_domain.items())),
        "counts_by_section": dict(sorted(by_section.items())),
        "reading_upgrade_candidates": upgrades,
        "permission_policy": "config/system_config/idea_evidence_permissions.yaml",
    }
