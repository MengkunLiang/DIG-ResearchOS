from __future__ import annotations

"""Deterministic T3 note completion manifest.

T3 completion used to be inferred from note filenames alone. That breaks when
the same paper has several aliases or when a matching note exists but fails the
deep-read structure contract. This module builds a small human-readable ledger
from the queue records and actual notes so validators and resume logic can give
precise diagnostics.
"""

from datetime import datetime, timezone
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..literature_identity import (
    canonical_note_id,
    display_record_key,
    is_paper_note_file,
    paper_note_match_keys,
    record_is_covered,
    record_note_id,
)


NOTE_MANIFEST_REL_PATH = "literature/notes_manifest.json"


def build_t3_notes_manifest(
    workspace_dir: Path,
    *,
    queue_records: list[dict[str, Any]] | None = None,
    source_queue: str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Build and optionally persist ``literature/notes_manifest.json``.

    The manifest is refreshed from disk whenever validators/recovery run, so it
    remains compatible with older workspaces where notes were written with
    ``write_file`` instead of the newer ``save_paper_note`` tool.
    """

    workspace_dir = workspace_dir.resolve()
    literature_dir = workspace_dir / "literature"
    if queue_records is None:
        queue_records, source_queue = _load_default_queue(literature_dir)
    source_queue = source_queue or "provided_queue"

    note_infos = _collect_note_infos(workspace_dir, literature_dir)
    entries: list[dict[str, Any]] = []
    matched_note_paths: set[str] = set()

    for index, record in enumerate(queue_records, start=1):
        complete_matches, incomplete_matches = _match_note_infos(record, note_infos)
        primary = complete_matches[0] if complete_matches else incomplete_matches[0] if incomplete_matches else None
        status = "complete" if complete_matches else "incomplete" if incomplete_matches else "missing"
        note_path = str(primary.get("rel_path") or "") if primary else ""
        validation_error = "" if complete_matches else str(primary.get("validation_error") or "") if primary else ""
        if primary:
            matched_note_paths.add(str(primary.get("rel_path") or ""))
        entry = {
            "paper_id": str(record.get("paper_id") or record.get("canonical_id") or record.get("id") or ""),
            "canonical_id": record_note_id(record),
            "normalized_id": canonical_note_id(record.get("normalized_id") or record_note_id(record)),
            "title": str(record.get("title") or ""),
            "queue_rank": int(record.get("queue_rank") or index),
            "target_bucket": str(record.get("target_bucket") or ""),
            "seed_priority": bool(record.get("seed_priority")),
            "protected_slot": bool(record.get("protected_slot") or record.get("citation_hub_protected_slot")),
            "triaged_out": bool(record.get("triaged_out")),
            "read_disposition": str(record.get("read_disposition") or ""),
            "read_disposition_reason": str(record.get("read_disposition_reason") or ""),
            "queue_reason": str(record.get("queue_reason") or ""),
            "bridge_id": str(record.get("bridge_id") or ""),
            "recalled_by_bridges": [
                str(item)
                for item in record.get("recalled_by_bridges") or []
                if str(item).strip()
            ],
            "contributed_bridges": [
                str(item)
                for item in record.get("contributed_bridges") or []
                if str(item).strip()
            ],
            "core_screen_passed": bool(record.get("core_screen_passed")),
            "semantic_role": str(record.get("semantic_role") or ""),
            "relation_to_project": str(record.get("relation_to_project") or ""),
            "is_citation_hub": bool(record.get("is_citation_hub")),
            "hub_type": str(record.get("hub_type") or ""),
            "hub_score": float(record.get("hub_score") or 0.0),
            "citation_hub_protected_slot": bool(record.get("citation_hub_protected_slot")),
            "has_abstract": bool(record.get("has_abstract")),
            "abstract_chars": int(record.get("abstract_chars") or 0),
            "reference_hint_count": int(record.get("reference_hint_count") or 0),
            "has_pdf_url_hint": bool(record.get("has_pdf_url_hint")),
            "pdf_url_hint_count": int(record.get("pdf_url_hint_count") or 0),
            "note_status": status,
            "status": status,
            "note_path": note_path,
            "matched_note_paths": [str(item.get("rel_path") or "") for item in [*complete_matches, *incomplete_matches]],
            "validation_error": validation_error,
            "sections_missing": _sections_missing_from_error(validation_error),
            "record_display_key": display_record_key(record),
        }
        entries.append(entry)

    invalid_unmatched = [
        {
            "note_path": str(info.get("rel_path") or ""),
            "validation_error": str(info.get("validation_error") or ""),
            "sections_missing": _sections_missing_from_error(str(info.get("validation_error") or "")),
        }
        for info in note_infos
        if not info.get("valid") and str(info.get("rel_path") or "") not in matched_note_paths
    ]

    duplicate_canonical_ids = _duplicate_values(
        str(entry.get("canonical_id") or "")
        for entry in entries
        if str(entry.get("canonical_id") or "")
    )
    target = [
        entry
        for entry in entries
        if not bool(entry.get("triaged_out")) and str(entry.get("target_bucket") or "") != "overflow"
    ]
    manifest = {
        "version": 1,
        "semantics": "t3_notes_manifest",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_queue": source_queue,
        "queue_count": len(queue_records),
        "entry_count": len(entries),
        "complete_count": sum(1 for entry in entries if entry["status"] == "complete"),
        "incomplete_count": sum(1 for entry in entries if entry["status"] == "incomplete"),
        "missing_count": sum(1 for entry in entries if entry["status"] == "missing"),
        "target_entry_count": len(target),
        "target_complete_count": sum(1 for entry in target if entry["status"] == "complete"),
        "target_incomplete_count": sum(1 for entry in target if entry["status"] == "incomplete"),
        "target_missing_count": sum(1 for entry in target if entry["status"] == "missing"),
        "valid_note_file_count": sum(1 for info in note_infos if info.get("valid")),
        "invalid_note_file_count": sum(1 for info in note_infos if not info.get("valid")),
        "duplicate_canonical_ids": duplicate_canonical_ids,
        "entries": entries,
        "invalid_unmatched_notes": invalid_unmatched,
    }
    if write:
        _atomic_write_json(workspace_dir / NOTE_MANIFEST_REL_PATH, manifest)
    return manifest


def refresh_t3_notes_manifest(workspace_dir: Path) -> dict[str, Any]:
    """Refresh and return the persisted T3 note manifest."""

    return build_t3_notes_manifest(workspace_dir, write=True)


def target_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Entries that count toward T3 deep-read completion."""

    entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and not bool(entry.get("triaged_out"))
        and str(entry.get("target_bucket") or "") != "overflow"
    ]


def format_completion_diagnostics(
    entries: list[dict[str, Any]],
    *,
    max_items: int = 6,
) -> str:
    """Return a concise Chinese diagnostic for incomplete/missing queue notes."""

    incomplete = [entry for entry in entries if entry.get("status") == "incomplete"]
    missing = [entry for entry in entries if entry.get("status") == "missing"]
    parts: list[str] = []
    if incomplete:
        examples = []
        for entry in incomplete[:max_items]:
            missing_sections = entry.get("sections_missing") or []
            section_text = ", ".join(str(item) for item in missing_sections[:3]) or str(entry.get("validation_error") or "结构不合格")
            examples.append(
                f"rank {entry.get('queue_rank')} {entry.get('record_display_key')} -> "
                f"{entry.get('note_path') or 'matched note'} 缺 {section_text}"
            )
        parts.append(f"已匹配但结构不合格 {len(incomplete)} 篇: " + "; ".join(examples))
    if missing:
        examples = [
            f"rank {entry.get('queue_rank')} {entry.get('record_display_key')}"
            for entry in missing[:max_items]
        ]
        parts.append(f"未找到 note {len(missing)} 篇: " + ", ".join(examples))
    return "；".join(parts)


def find_queue_record_by_rank(
    workspace_dir: Path,
    queue_rank: int,
    *,
    queue_path: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Find a queue record by rank, preferring the active pending queue."""

    literature_dir = workspace_dir / "literature"
    rel_paths: list[str] = []
    if queue_path and queue_path != "auto":
        rel_paths.append(queue_path)
    else:
        rel_paths.extend([
            "literature/deep_read_queue_pending.jsonl",
            "literature/deep_read_queue.jsonl",
            "literature/papers_verified.jsonl",
            "literature/papers_dedup.jsonl",
        ])
    for rel_path in rel_paths:
        path = workspace_dir / rel_path
        if not path.exists():
            continue
        records = load_jsonl(path)
        for index, record in enumerate(records, start=1):
            rank = int(record.get("queue_rank") or index)
            if rank == queue_rank:
                return record, rel_path
    return None, ""


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL records without importing agent modules."""

    if not path.exists():
        return []
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


def _load_default_queue(literature_dir: Path) -> tuple[list[dict[str, Any]], str]:
    for rel_name in ("deep_read_queue.jsonl", "deep_read_queue_pending.jsonl", "papers_verified.jsonl", "papers_dedup.jsonl"):
        path = literature_dir / rel_name
        if path.exists():
            return load_jsonl(path), f"literature/{rel_name}"
    return [], "none"


def _collect_note_infos(workspace_dir: Path, literature_dir: Path) -> list[dict[str, Any]]:
    note_roots = [
        literature_dir / "paper_notes",
        literature_dir / "paper_notes_bridge",
    ]
    infos: list[dict[str, Any]] = []
    for root in note_roots:
        if not root.exists():
            continue
        pattern = "**/*.md" if root.name == "paper_notes_bridge" else "*.md"
        for note_path in sorted(root.glob(pattern)):
            if not is_paper_note_file(note_path):
                continue
            ok, err = _validate_note(note_path)
            try:
                rel_path = note_path.relative_to(workspace_dir).as_posix()
            except ValueError:
                rel_path = note_path.as_posix()
            infos.append(
                {
                    "path": note_path,
                    "rel_path": rel_path,
                    "valid": ok,
                    "validation_error": err or "",
                    "keys": paper_note_match_keys(note_path),
                }
            )
    return infos


def _match_note_infos(
    record: dict[str, Any],
    note_infos: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    complete: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for info in note_infos:
        keys = info.get("keys")
        if not isinstance(keys, set):
            continue
        if not record_is_covered(record, keys):
            continue
        if info.get("valid"):
            complete.append(info)
        else:
            incomplete.append(info)
    return complete, incomplete


def _validate_note(note_path: Path) -> tuple[bool, str | None]:
    try:
        from ..agents.reader import _validate_note_structure

        return _validate_note_structure(note_path)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return False, f"{note_path.name} note validation crashed: {exc}"


def _sections_missing_from_error(error: str) -> list[str]:
    if not error:
        return []
    markers: list[str] = []
    for pattern in (
        r"缺少必要结构:\s*([^；\n]+)",
        r"缺少必要轻字段:\s*([^；\n]+)",
        r"Reading Coverage 缺少字段:\s*([^；\n]+)",
        r"Mechanism Claim 缺少字段:\s*([^；\n]+)",
        r"缺少 (##\s*[^；\n]+)",
    ):
        for match in re_findall(pattern, error):
            value = str(match).strip()
            if value and value not in markers:
                markers.append(value)
    if not markers and error:
        markers.append(error)
    return markers


def re_findall(pattern: str, text: str) -> list[str]:
    import re

    return re.findall(pattern, text)


def _duplicate_values(values: Any) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            tmp_path.unlink()
