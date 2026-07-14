from __future__ import annotations

"""Deterministic T3 note completion manifest.

T3 completion used to be inferred from note filenames alone. That breaks when
the same paper has several aliases or when a matching note exists but fails the
deep-read structure contract. This module builds a small human-readable ledger
from the queue records and actual notes so validators and resume logic can give
precise diagnostics.
"""

from datetime import datetime, timezone
import hashlib
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

T3_INPUT_FINGERPRINT_PATHS = {
    "deep_read_queue": "literature/deep_read_queue.jsonl",
    "papers_verified": "literature/papers_verified.jsonl",
    "papers_dedup": "literature/papers_dedup.jsonl",
    "domain_map": "literature/domain_map.json",
    "access_audit": "literature/access_audit.md",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
    "seed_pdfs": "user_seeds/pdfs",
    "legacy_seed_papers_dir": "seeds/T2_scout/papers",
    "literature_pdfs": "literature/pdfs",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "seed_constraints": "user_seeds/seed_constraints.md",
    "legacy_seed_constraints": "seeds/T2_scout/constraints.md",
    "seed_external_resources": "user_seeds/seed_external_resources.jsonl",
    "agent_params_config": "config/system_config/agent_params.yaml",
    "model_settings_config": "config/model_settings.yaml",
}


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
        quality = primary.get("quality") if isinstance(primary, dict) and isinstance(primary.get("quality"), dict) else {}
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
            "citation_quality_score": quality.get("citation_quality_score"),
            "citation_quality_band": str(quality.get("citation_quality_band") or ""),
            "citation_use": str(quality.get("citation_use") or ""),
            "citation_quality_rationale": str(quality.get("citation_quality_rationale") or ""),
            "quality_source": str(quality.get("quality_source") or ""),
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
        "input_fingerprints": t3_input_fingerprints(workspace_dir),
        "entries": entries,
        "invalid_unmatched_notes": invalid_unmatched,
    }
    if write:
        _atomic_write_json(workspace_dir / NOTE_MANIFEST_REL_PATH, manifest)
    return manifest


def refresh_t3_notes_manifest(workspace_dir: Path) -> dict[str, Any]:
    """Refresh and return the persisted T3 note manifest."""

    return build_t3_notes_manifest(workspace_dir, write=True)


def t3_input_fingerprints(workspace_dir: Path) -> dict[str, dict[str, Any]]:
    """Fingerprint upstream inputs that determine the T3 reading target set."""

    workspace_dir = workspace_dir.resolve()
    return {
        label: _file_fingerprint(workspace_dir, rel_path)
        for label, rel_path in T3_INPUT_FINGERPRINT_PATHS.items()
    }


def validate_t3_input_fingerprints(workspace_dir: Path, manifest: dict[str, Any]) -> tuple[bool, str | None]:
    """Ensure a manifest still corresponds to the current T2/T3 input files."""

    fingerprints = manifest.get("input_fingerprints")
    if not isinstance(fingerprints, dict):
        return False, "notes_manifest.json 缺少 input_fingerprints，T3 需要重新校验/续跑"
    current = t3_input_fingerprints(workspace_dir)
    stale: list[str] = []
    for label, item in current.items():
        previous = fingerprints.get(label)
        if not isinstance(previous, dict):
            stale.append(label)
            continue
        if bool(previous.get("exists")) != bool(item.get("exists")):
            stale.append(label)
            continue
        if item.get("exists") and str(previous.get("sha256") or "") != str(item.get("sha256") or ""):
            stale.append(label)
    if stale:
        return False, "notes_manifest.json 对应的 T3 输入已变化，需要重新读取: " + ", ".join(stale)
    return True, None


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
        pending_path = literature_dir / "deep_read_queue_pending.jsonl"
        if pending_path.exists():
            # Pending queue ranks are re-numbered for resume. If a pending file
            # exists, an implicit queue_rank must not silently fall back to the
            # full queue, whose ranks refer to the original T2 queue.
            rel_paths.append("literature/deep_read_queue_pending.jsonl")
        else:
            rel_paths.extend([
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
    for rel_name in ("deep_read_queue_pending.jsonl", "deep_read_queue.jsonl", "papers_verified.jsonl", "papers_dedup.jsonl"):
        path = literature_dir / rel_name
        if path.exists():
            return load_jsonl(path), f"literature/{rel_name}"
    return [], "none"


def _file_fingerprint(workspace_dir: Path, rel_path: str) -> dict[str, Any]:
    path = _resolve_fingerprint_path(workspace_dir, rel_path)
    item: dict[str, Any] = {"path": rel_path, "exists": path.exists()}
    if path.exists() and path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        item["sha256"] = digest.hexdigest()
        item["size"] = path.stat().st_size
    elif path.exists() and path.is_dir():
        children = [child for child in path.rglob("*") if child.is_file()]
        item["kind"] = "dir"
        item["file_count"] = len(children)
        digest = hashlib.sha256()
        for child in sorted(children, key=lambda p: p.relative_to(path).as_posix()):
            rel = child.relative_to(path).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            try:
                digest.update(str(child.stat().st_size).encode("ascii"))
                digest.update(b"\0")
                with child.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        item["sha256"] = digest.hexdigest()
    return item


def _resolve_fingerprint_path(workspace_dir: Path, rel_path: str) -> Path:
    workspace_path = workspace_dir / rel_path
    if workspace_path.exists() or not rel_path.startswith("config/"):
        return workspace_path
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / rel_path


def _collect_note_infos(workspace_dir: Path, literature_dir: Path) -> list[dict[str, Any]]:
    note_roots = [
        literature_dir / "deep_read_notes",
        literature_dir / "bridge_notes",
    ]
    infos: list[dict[str, Any]] = []
    for root in note_roots:
        if not root.exists():
            continue
        pattern = "**/*.md" if root.name == "bridge_notes" else "*.md"
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
                    "quality": _extract_note_quality(note_path, valid=ok),
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


def _extract_note_quality(note_path: Path, *, valid: bool) -> dict[str, Any]:
    """Extract Reader-assigned citation quality, with conservative fallback."""

    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _fallback_note_quality("", valid=False)
    score_text = _extract_markdown_field(text, "Citation Quality Score")
    use = _extract_markdown_field(text, "Citation Use")
    rationale = _extract_markdown_field(text, "Citation Quality Rationale")
    score = _parse_score(score_text)
    if score is None:
        quality = _fallback_note_quality(text, valid=valid)
        if use:
            quality["citation_use"] = use
        if rationale:
            quality["citation_quality_rationale"] = rationale
        return quality
    return {
        "citation_quality_score": round(score, 3),
        "citation_quality_band": _quality_band(score),
        "citation_use": use or _citation_use_for_score(score),
        "citation_quality_rationale": rationale,
        "quality_source": "reader_llm_field",
    }


def _fallback_note_quality(text: str, *, valid: bool) -> dict[str, Any]:
    status = _extract_markdown_field(text, "Status").upper()
    if not valid:
        score = 0.0
        use = "do_not_cite"
    elif "FULL-TEXT" in status:
        score = 0.75
        use = "supporting_context"
    elif "PARTIAL-TEXT" in status:
        score = 0.55
        use = "supporting_context"
    elif "ABSTRACT-ONLY" in status:
        score = 0.30
        use = "background_only"
    else:
        score = 0.40
        use = "background_only"
    return {
        "citation_quality_score": score,
        "citation_quality_band": _quality_band(score),
        "citation_use": use,
        "citation_quality_rationale": "deterministic fallback from note status; Reader did not provide explicit score",
        "quality_source": "deterministic_fallback",
    }


def _extract_markdown_field(text: str, name: str) -> str:
    import re

    match = re.search(rf"(?m)^-\s+\*\*{re.escape(name)}\*\*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _parse_score(value: str) -> float | None:
    if not value:
        return None
    import re

    match = re.search(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", value)
    if not match:
        return None
    try:
        score = float(match.group(0))
    except ValueError:
        return None
    return min(1.0, max(0.0, score))


def _quality_band(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    if score > 0:
        return "low"
    return "invalid"


def _citation_use_for_score(score: float) -> str:
    if score >= 0.80:
        return "core_evidence"
    if score >= 0.55:
        return "supporting_context"
    if score >= 0.25:
        return "background_only"
    return "do_not_cite"


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
