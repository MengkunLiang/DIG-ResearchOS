from __future__ import annotations

"""Deterministic, auditable PDF acquisition for retained literature records.

This module deliberately models *access* separately from *reading evidence*.
Downloading a PDF is useful for the Reader, but it is not a full-text reading
event.  The only component allowed to promote a paper-note to ``FULL_TEXT`` or
``PARTIAL_TEXT`` is the reading/note workflow after it records coverage.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from ..literature_identity import paper_record_match_keys, record_note_id
from .literature_contract import resolve_literature_note_card
from ..tools.paper_fetch import FetchPaperPdfTool
from ..tools.workspace_policy import WorkspaceAccessPolicy


PDF_ACQUISITION_MANIFEST_REL_PATH = "literature/pdf_acquisition_manifest.json"
PDF_ACQUISITION_RECEIPTS_REL_PATH = "literature/pdf_acquisition_receipts.jsonl"
PDF_ROOT_REL_PATH = "literature/pdfs"


async def acquire_retained_pdfs(
    workspace_dir: Path,
    records: list[dict[str, Any]],
    *,
    max_concurrency: int = 4,
    retry_terminal_failures: bool = False,
    skip_known_books: bool = True,
    max_auto_read_pages: int = 100,
    source_pool: str = "papers_verified",
) -> dict[str, Any]:
    """Attempt open-PDF acquisition once for every retained unique record.

    The operation is idempotent: successful local PDFs and previous terminal
    failures are represented in the receipt manifest and are not silently
    retried on every resume.  ``retry_terminal_failures`` is an explicit
    repair choice, not the normal path.
    """

    workspace = Path(workspace_dir).resolve()
    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace,
        allowed_read_prefixes=["", "literature/", "user_seeds/", "seeds/"],
        allowed_write_prefixes=["literature/"],
    )
    existing = _load_manifest(workspace)
    existing_by_key = _existing_receipts_by_key(existing)
    retained = _dedupe_records(records)
    semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def attempt(index: int, record: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _acquire_one(
                policy,
                record,
                existing_by_key=existing_by_key,
                retry_terminal_failures=retry_terminal_failures,
                skip_known_books=skip_known_books,
                max_auto_read_pages=max_auto_read_pages,
                source_pool=source_pool,
                ordinal=index,
            )

    receipts = await asyncio.gather(*(attempt(index, record) for index, record in enumerate(retained, start=1)))
    merged = _merge_receipts(existing.get("receipts") if isinstance(existing.get("receipts"), list) else [], receipts)
    payload = {
        "schema_version": "1.0.0",
        "semantics": "researchos_pdf_acquisition_manifest",
        "generated_at": _now_iso(),
        "source_pool": source_pool,
        "pdf_root": PDF_ROOT_REL_PATH,
        "evidence_boundary": (
            "PDF acquisition records availability only. It never promotes evidence_level; "
            "only a Reader note with recorded page coverage may become FULL_TEXT or PARTIAL_TEXT."
        ),
        "long_form_policy": {
            "skip_known_books": bool(skip_known_books),
            "max_auto_read_pages": max(1, int(max_auto_read_pages)),
            "reading_rule": (
                "Known books and records whose metadata exceeds the page threshold are not automatically fetched. "
                "An already available long PDF remains on disk for targeted section reading and can only support PARTIAL_TEXT "
                "until a Reader records complete, untruncated coverage."
            ),
        },
        "counts": _count_receipts(merged),
        "receipts": merged,
    }
    _write_manifest(workspace, payload)
    return payload


def attach_pdf_acquisition(records: list[dict[str, Any]], acquisition_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return record copies annotated with availability facts, never evidence upgrades."""

    by_key = _existing_receipts_by_key(acquisition_manifest)
    output: list[dict[str, Any]] = []
    for record in records:
        copied = dict(record)
        receipt = _find_receipt(by_key, copied)
        if receipt:
            copied["pdf_acquisition"] = {
                key: receipt.get(key)
                for key in (
                    "status", "pdf_path", "sha256", "bytes", "page_count", "attempted_at", "source_pool",
                    "attempted_urls", "error",
                )
                if receipt.get(key) not in (None, "", [])
            }
            if receipt.get("status") in {"acquired_parseable", "existing_parseable"}:
                copied["has_local_pdf"] = True
                copied["local_pdf_path"] = str(receipt.get("pdf_path") or "")
                copied["access_level_hint"] = "FULL_TEXT_LOCAL"
                copied["access_score"] = max(_number(copied.get("access_score")), 1.0)
                copied["access_score_estimate"] = max(_number(copied.get("access_score_estimate")), 1.0)
        # Do not modify copied["evidence_level"] here.  Acquisition != reading.
        output.append(copied)
    return output


def repair_access_only_evidence_levels(workspace_dir: Path, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Repair legacy rows that mistook local PDF access for FULL_TEXT.

    A prior version emitted ``FULL_TEXT`` for seed/local PDFs before Reader
    had written a full-coverage paper note.  Preserve genuinely read papers,
    but conservatively demote only those rows for which no canonical full or
    partial note can be resolved.  This migration never deletes a PDF.
    """

    workspace = Path(workspace_dir).resolve()
    repaired = 0
    output: list[dict[str, Any]] = []
    for record in records:
        copied = dict(record)
        current = str(copied.get("evidence_level") or "").upper()
        if current not in {"FULL_TEXT", "PARTIAL_TEXT"}:
            output.append(copied)
            continue
        note = None
        for identity in (_paper_id(copied), record_note_id(copied), *paper_record_match_keys(copied)):
            if identity:
                note = resolve_literature_note_card(workspace, identity, include_shallow=False)
                if note is not None:
                    break
        if note is not None:
            output.append(copied)
            continue
        copied["evidence_level"] = "ABSTRACT_ONLY" if str(copied.get("abstract") or "").strip() else "METADATA_ONLY"
        copied["_needs_reader_evidence_level"] = True
        copied["evidence_level_migration"] = {
            "from": current,
            "to": copied["evidence_level"],
            "reason": "legacy_local_pdf_access_was_not_a_recorded_reading_event",
        }
        repaired += 1
        output.append(copied)
    return output, repaired


async def _acquire_one(
    policy: WorkspaceAccessPolicy,
    record: dict[str, Any],
    *,
    existing_by_key: dict[str, dict[str, Any]],
    retry_terminal_failures: bool,
    skip_known_books: bool,
    max_auto_read_pages: int,
    source_pool: str,
    ordinal: int,
) -> dict[str, Any]:
    paper_id = _paper_id(record)
    title = str(record.get("title") or "").strip()
    keys = sorted(paper_record_match_keys(record))
    keys.extend(key for key in (paper_id, record_note_id(record)) if key and key not in keys)
    prior = _find_receipt(existing_by_key, record)
    if prior and _is_terminal(prior) and not retry_terminal_failures:
        return {**prior, "status": str(prior.get("status") or "skipped_previous_attempt"), "skipped": True}

    skip_reason = _auto_acquisition_skip_reason(
        record,
        skip_known_books=skip_known_books,
        max_auto_read_pages=max_auto_read_pages,
    )
    if skip_reason:
        return _receipt(
            record,
            status="deferred_long_form",
            source_pool=source_pool,
            error=skip_reason,
            reading_recommendation="targeted_partial_text_if_researcher_requests_source",
        )

    rel_pdf = _pdf_rel_path(record, ordinal)
    abs_pdf = policy.workspace_dir / rel_pdf
    if abs_pdf.is_file():
        inspection = _inspect_pdf(abs_pdf)
        return _receipt(
            record,
            status="existing_parseable" if inspection["parseable"] else "existing_invalid_pdf",
            source_pool=source_pool,
            pdf_path=rel_pdf,
            reading_recommendation=_reading_recommendation(record, inspection, max_auto_read_pages),
            **inspection,
        )

    tool = FetchPaperPdfTool(policy)
    identifier = _best_fetch_identifier(record)
    if not identifier:
        return _receipt(record, status="unresolved_identifier", source_pool=source_pool, error="no_resolvable_identifier")
    result = await tool.execute(paper_id=identifier, save_path=rel_pdf)
    attempted_urls = [str(item) for item in (result.data or {}).get("candidates_tried") or [] if str(item).strip()]
    if not result.ok:
        return _receipt(
            record,
            status=_failure_status(result.error),
            source_pool=source_pool,
            pdf_path=rel_pdf,
            attempted_urls=attempted_urls,
            error=str(result.error or result.content or "download_failed"),
        )
    inspection = _inspect_pdf(abs_pdf)
    if not inspection["parseable"]:
        return _receipt(
            record,
            status="acquired_unparseable",
            source_pool=source_pool,
            pdf_path=rel_pdf,
            attempted_urls=attempted_urls,
            source_url=str((result.data or {}).get("url") or ""),
            **inspection,
        )
    return _receipt(
        record,
        status="acquired_parseable",
        source_pool=source_pool,
        pdf_path=rel_pdf,
        attempted_urls=attempted_urls,
        source_url=str((result.data or {}).get("url") or ""),
        reading_recommendation=_reading_recommendation(record, inspection, max_auto_read_pages),
        **inspection,
    )


def _receipt(record: dict[str, Any], *, status: str, source_pool: str, **extra: Any) -> dict[str, Any]:
    return {
        "paper_id": _paper_id(record),
        "title": str(record.get("title") or "").strip(),
        "identity_keys": sorted(paper_record_match_keys(record)),
        "status": status,
        "source_pool": source_pool,
        "attempted_at": _now_iso(),
        "evidence_level_after_acquisition": str(record.get("evidence_level") or "METADATA_ONLY"),
        "evidence_boundary": "availability_only_no_reading_level_promotion",
        **{key: value for key, value in extra.items() if value not in (None, "", [], {})},
    }


def _auto_acquisition_skip_reason(
    record: dict[str, Any],
    *,
    skip_known_books: bool,
    max_auto_read_pages: int,
) -> str:
    """Avoid downloading known long-form sources solely to satisfy a generic PDF pass."""

    if skip_known_books and _record_is_book_like(record):
        return "known_book_or_monograph_requires_targeted_reading"
    known_pages = _known_page_count(record)
    if known_pages is not None and known_pages > max(1, int(max_auto_read_pages)):
        return f"known_page_count_{known_pages}_exceeds_auto_read_threshold_{max(1, int(max_auto_read_pages))}"
    return ""


def _record_is_book_like(record: dict[str, Any]) -> bool:
    values = [
        record.get("type"),
        record.get("work_type"),
        record.get("publication_type"),
        record.get("document_type"),
        record.get("genre"),
    ]
    normalized = " ".join(str(value or "").strip().casefold() for value in values)
    return any(token in normalized for token in ("book", "monograph", "book chapter", "edited volume"))


def _known_page_count(record: dict[str, Any]) -> int | None:
    for key in ("page_count", "pages", "num_pages", "number_of_pages"):
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
        text = str(value or "").strip()
        if text.isdigit():
            return max(0, int(text))
        match = re.search(r"(\d+)\s*(?:-|--|–|—)\s*(\d+)", text)
        if match:
            return max(0, int(match.group(2)) - int(match.group(1)) + 1)
    return None


def _reading_recommendation(record: dict[str, Any], inspection: dict[str, Any], max_auto_read_pages: int) -> str:
    page_count = inspection.get("page_count") or _known_page_count(record)
    try:
        pages = int(page_count)
    except (TypeError, ValueError):
        pages = 0
    if _record_is_book_like(record) or (pages and pages > max(1, int(max_auto_read_pages))):
        return "targeted_partial_text"
    return "full_text_candidate"


def _load_manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / PDF_ACQUISITION_MANIFEST_REL_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_manifest(workspace: Path, payload: dict[str, Any]) -> None:
    path = workspace / PDF_ACQUISITION_MANIFEST_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    receipts_path = workspace / PDF_ACQUISITION_RECEIPTS_REL_PATH
    receipts_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in payload.get("receipts") or []),
        encoding="utf-8",
    )


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        keys = paper_record_match_keys(record)
        fallback = _paper_id(record) or record_note_id(record)
        identity = next(iter(sorted(keys)), fallback)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        output.append(record)
    return output


def _existing_receipts_by_key(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    receipts = manifest.get("receipts") if isinstance(manifest, dict) else []
    for receipt in receipts if isinstance(receipts, list) else []:
        if not isinstance(receipt, dict):
            continue
        for key in receipt.get("identity_keys") or []:
            if str(key).strip():
                out[str(key)] = receipt
        if str(receipt.get("paper_id") or "").strip():
            out[str(receipt["paper_id"])] = receipt
    return out


def _find_receipt(index: dict[str, dict[str, Any]], record: dict[str, Any]) -> dict[str, Any] | None:
    for key in paper_record_match_keys(record):
        if key in index:
            return index[key]
    paper_id = _paper_id(record)
    return index.get(paper_id) if paper_id else None


def _merge_receipts(existing: list[Any], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = _existing_receipts_by_key({"receipts": existing})
    ordered: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for item in existing:
        if not isinstance(item, dict):
            continue
        replacement = _find_receipt({key: update for update in updates for key in update.get("identity_keys") or []}, item)
        if replacement:
            if id(replacement) not in consumed:
                ordered.append(replacement)
                consumed.add(id(replacement))
        else:
            ordered.append(item)
    for update in updates:
        if id(update) not in consumed:
            ordered.append(update)
    return ordered


def _count_receipts(receipts: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(receipts), "acquired_parseable": 0, "available_local": 0, "unavailable": 0, "unparseable": 0}
    for item in receipts:
        status = str(item.get("status") or "")
        if status in {"acquired_parseable", "existing_parseable"}:
            counts["acquired_parseable"] += 1
            counts["available_local"] += 1
        elif "unparseable" in status or "invalid_pdf" in status:
            counts["unparseable"] += 1
        else:
            counts["unavailable"] += 1
    return counts


def _is_terminal(receipt: dict[str, Any]) -> bool:
    return str(receipt.get("status") or "") not in {"", "download_in_progress"}


def _pdf_rel_path(record: dict[str, Any], ordinal: int) -> str:
    stem = record_note_id(record) or f"retained_{ordinal:04d}"
    return f"{PDF_ROOT_REL_PATH}/{stem}.pdf"


def _paper_id(record: dict[str, Any]) -> str:
    for key in ("canonical_id", "paper_id", "id", "doi"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _best_fetch_identifier(record: dict[str, Any]) -> str:
    for key in ("canonical_id", "arxiv_id", "doi", "id", "url", "pdf_url", "open_access_pdf_url", "oa_pdf_url"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _inspect_pdf(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"parseable": False, "error": f"read_failed:{type(exc).__name__}"}
    details: dict[str, Any] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "parseable": False}
    if not data.startswith(b"%PDF-"):
        details["error"] = "invalid_pdf_header"
        return details
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            details["page_count"] = len(pdf.pages)
        details["parseable"] = bool(details["page_count"])
        if not details["parseable"]:
            details["error"] = "zero_page_pdf"
    except Exception as exc:  # parseability is an audit result, not a fatal pipeline error
        details["error"] = f"parse_failed:{type(exc).__name__}"
    return details


def _failure_status(error: str | None) -> str:
    normalized = str(error or "download_failed").strip().lower()
    if normalized in {"access_denied", "dependency_missing", "unsupported_id", "not_found"}:
        return normalized
    return "unavailable"


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
