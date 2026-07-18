"""Source-traceable discovery records for paper-associated research resources.

Literature reading should preserve references to author code, data, benchmarks,
models, project pages, and supplementary materials without treating a link as
scientific evidence or downloading/executing third-party content.  T5 Phase B
is the only stage that may acquire an approved immutable resource revision.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse


RESOURCE_CATALOG_REL_PATH = "literature/resource_catalog.jsonl"
RESOURCE_CATALOG_SUMMARY_REL_PATH = "literature/resource_catalog_summary.json"
RESOURCE_CATALOG_SCHEMA = "literature_resource_catalog.v1"
RESOURCE_ENTRY_SCHEMA = "paper_associated_resource.v1"
RESOURCE_SECTION_HEADING = "## 21. Associated Research Resources"

RESOURCE_TYPES = {
    "code_repository",
    "project_page",
    "dataset",
    "benchmark",
    "model_checkpoint",
    "evaluation_code",
    "supplementary_material",
    "documentation",
    "other",
}

_RESOURCE_HOST_TYPES = {
    "github.com": "code_repository",
    "gitlab.com": "code_repository",
    "bitbucket.org": "code_repository",
    "huggingface.co": "model_checkpoint",
    "hf.co": "model_checkpoint",
    "modelscope.cn": "model_checkpoint",
    "www.modelscope.cn": "model_checkpoint",
    "kaggle.com": "dataset",
    "openml.org": "dataset",
    "archive.ics.uci.edu": "dataset",
    "zenodo.org": "supplementary_material",
    "figshare.com": "supplementary_material",
    "dataverse.harvard.edu": "dataset",
    "codabench.org": "benchmark",
    "eval.ai": "benchmark",
    "evalai.cloudcv.org": "benchmark",
}

_RESOURCE_FIELD_HINTS = {
    "code": "code_repository",
    "repo": "code_repository",
    "github": "code_repository",
    "gitlab": "code_repository",
    "project": "project_page",
    "website": "project_page",
    "demo": "project_page",
    "dataset": "dataset",
    "data": "dataset",
    "benchmark": "benchmark",
    "leaderboard": "benchmark",
    "model": "model_checkpoint",
    "checkpoint": "model_checkpoint",
    "weight": "model_checkpoint",
    "supplement": "supplementary_material",
    "appendix": "supplementary_material",
    "artifact": "supplementary_material",
    "evaluation": "evaluation_code",
    "doc": "documentation",
}

_URL_PATTERN = re.compile(r"https?://[^\s<>)\]}`\"']+", re.IGNORECASE)
_SECTION_PATTERN = re.compile(
    r"(?ms)^##\s+21\.\s+(?:Associated\s+Research\s+Resources|Research\s+Resources|相关研究资源)\s*$"
    r"(?P<body>.*?)(?=^##\s+|\Z)"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, *, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _canonical_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip(".,;:)")
    if raw.startswith("url:"):
        raw = raw[4:].strip()
    elif raw.startswith("github:"):
        raw = "https://github.com/" + raw[7:].lstrip("/")
    elif raw.startswith("gitlab:"):
        raw = "https://gitlab.com/" + raw[7:].lstrip("/")
    elif raw.startswith("huggingface:"):
        raw = "https://huggingface.co/" + raw[12:].lstrip("/")
    elif raw.startswith("modelscope:"):
        raw = "https://modelscope.cn/" + raw[11:].lstrip("/")
    if not raw or not raw.lower().startswith(("http://", "https://")):
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    # Tracking query strings are not stable resource identity. Keep an explicit
    # query only when it is part of a nonempty path-less URL is unnecessary.
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _resource_type_from_text(*values: Any) -> str:
    text = " ".join(_clean_text(value, limit=500).casefold() for value in values if value)
    for token, resource_type in _RESOURCE_FIELD_HINTS.items():
        if token in text:
            return resource_type
    for value in values:
        url = _canonical_url(value)
        if not url:
            continue
        host = (urlparse(url).hostname or "").lower()
        if host in _RESOURCE_HOST_TYPES:
            return _RESOURCE_HOST_TYPES[host]
    return "other"


def _resource_id(paper_id: str, resource_type: str, url: str, name: str) -> str:
    # A stable URL is the identity. Reader prose may name the same repository
    # differently in the markdown section and structured tool payload.
    identity = url.casefold() if url else name.casefold()
    material = "\x1f".join([paper_id.casefold(), resource_type, identity])
    return "RES-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _paper_identity(record: dict[str, Any], *, note_path: str = "", reading_status: str = "") -> dict[str, str]:
    paper_id = _clean_text(
        record.get("paper_id")
        or record.get("canonical_id")
        or record.get("openalex_id")
        or record.get("id")
        or record.get("doi")
        or record.get("arxiv_id"),
        limit=220,
    )
    title = _clean_text(record.get("title") or record.get("paper_title"), limit=700)
    return {
        "paper_id": paper_id or "unknown_paper",
        "title": title or "Untitled paper",
        "note_path": note_path,
        "reading_status": reading_status or "UNKNOWN",
    }


def _normalize_relationship(value: Any) -> str:
    normalized = _clean_text(value, limit=100).casefold().replace("-", "_").replace(" ", "_")
    allowed = {
        "official_author",
        "official_project",
        "official_benchmark",
        "official_dataset",
        "author_recognized",
        "third_party",
        "publisher",
        "unknown",
    }
    return normalized if normalized in allowed else "unknown"


def normalize_resource_record(
    raw: dict[str, Any],
    *,
    paper: dict[str, str],
    source_kind: str,
    default_locator: str,
) -> dict[str, Any] | None:
    """Normalize one reader-reported resource without claiming it was acquired."""

    if not isinstance(raw, dict):
        return None
    raw_url = raw.get("url") or raw.get("source") or raw.get("repository_url") or raw.get("resource_url")
    url = _canonical_url(raw_url)
    name = _clean_text(raw.get("name") or raw.get("title") or raw.get("resource_name"), limit=240)
    resource_type = _clean_text(raw.get("resource_type") or raw.get("type"), limit=80).casefold().replace("-", "_")
    if resource_type not in RESOURCE_TYPES:
        resource_type = _resource_type_from_text(resource_type, raw.get("field"), raw.get("purpose"), url)
    locator = _clean_text(raw.get("locator") or raw.get("discovery_location") or default_locator, limit=500)
    relationship = _normalize_relationship(raw.get("relationship") or raw.get("source_relationship"))
    access_hint = _clean_text(raw.get("access_hint") or raw.get("access_status"), limit=100)
    if not url and not name:
        return None
    if not name:
        name = (urlparse(url).path.strip("/") if url else resource_type).split("/")[-1] or resource_type
    if not access_hint:
        access_hint = "link_declared_in_reading_material" if url else "mentioned_without_resolvable_link"
    paper_id = paper["paper_id"]
    return {
        "schema_version": RESOURCE_ENTRY_SCHEMA,
        "resource_id": _resource_id(paper_id, resource_type, url, name),
        "paper": paper,
        "resource": {
            "resource_type": resource_type,
            "name": name,
            "url": url,
            "relationship_to_paper": relationship,
            "declared_revision": _clean_text(raw.get("revision") or raw.get("commit") or raw.get("version"), limit=160),
            "license_hint": _clean_text(raw.get("license") or raw.get("license_hint"), limit=200),
        },
        "discovery": {
            "source_kind": source_kind,
            "locator": locator or "resource availability statement",
            "reading_status": paper["reading_status"],
            "access_hint": access_hint,
            "discovered_at": _now_iso(),
        },
        "lifecycle": {
            "status": "discovered",
            "acquired": False,
            "static_reviewed": False,
            "approved_for_execution": False,
        },
        "downstream_boundary": {
            "allowed_uses": [
                "feasibility assessment",
                "baseline or dataset discovery",
                "resource requirement planning",
                "reproduction provenance",
            ],
            "prohibited_uses": [
                "mechanism evidence",
                "empirical performance evidence",
                "proof of baseline equivalence",
                "evidence that a resource is executable or licensed",
            ],
            "t5_action": "verify identity, license, immutable version, and protocol compatibility before acquisition or use",
        },
    }


def resource_records_from_paper_metadata(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract explicit resource-like URLs from a paper metadata record.

    Generic landing pages and PDF URLs are intentionally ignored. This makes a
    metadata-derived record a narrow discovery hint rather than an optimistic
    list of every URL returned by a scholarly API.
    """

    paper = _paper_identity(record, reading_status="METADATA_ONLY")
    candidates: list[dict[str, Any]] = []

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, (*path, str(key)))
            return
        if isinstance(value, list):
            for child in value:
                walk(child, path)
            return
        if not isinstance(value, str):
            return
        field = ".".join(path).casefold()
        if "pdf" in field or "doi" in field or "openalex" in field or "landing" in field:
            return
        urls = _URL_PATTERN.findall(value)
        if not urls and value.strip().startswith(("github:", "gitlab:", "huggingface:", "modelscope:", "url:")):
            urls = [value.strip()]
        for raw_url in urls:
            url = _canonical_url(raw_url)
            if not url:
                continue
            host = (urlparse(url).hostname or "").lower()
            type_from_field = _resource_type_from_text(field, url)
            resource_like = any(token in field for token in _RESOURCE_FIELD_HINTS) or host in _RESOURCE_HOST_TYPES
            if not resource_like:
                continue
            candidates.append(
                {
                    "resource_type": type_from_field,
                    "name": path[-1] if path else type_from_field,
                    "url": url,
                    "locator": f"metadata field: {'.'.join(path)}",
                    "relationship": "unknown",
                    "access_hint": "metadata_link_not_yet_verified",
                }
            )

    walk(record, ())
    normalized = [
        normalize_resource_record(item, paper=paper, source_kind="paper_metadata", default_locator="metadata")
        for item in candidates
    ]
    return [item for item in normalized if item is not None]


def resource_records_from_note(
    note_text: str,
    *,
    paper_record: dict[str, Any],
    note_path: str,
    reading_status: str,
) -> list[dict[str, Any]]:
    """Extract URLs only from the dedicated resource section of a paper note."""

    match = _SECTION_PATTERN.search(note_text or "")
    if match is None:
        return []
    paper = _paper_identity(paper_record, note_path=note_path, reading_status=reading_status)
    body = match.group("body")
    items: list[dict[str, Any]] = []
    for line_no, line in enumerate(body.splitlines(), start=1):
        for raw_url in _URL_PATTERN.findall(line):
            items.append(
                {
                    "resource_type": _resource_type_from_text(line, raw_url),
                    "name": _clean_text(line.replace(raw_url, "").lstrip("- "), limit=240),
                    "url": raw_url,
                    "locator": f"{RESOURCE_SECTION_HEADING}, line {line_no}",
                    "relationship": "unknown",
                    "access_hint": "link_declared_in_reader_note",
                }
            )
    normalized = [
        normalize_resource_record(item, paper=paper, source_kind="reader_note", default_locator=RESOURCE_SECTION_HEADING)
        for item in items
    ]
    return [item for item in normalized if item is not None]


def _read_catalog(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and str(value.get("resource_id") or ""):
            records[str(value["resource_id"])] = value
    return records


def _merge_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(existing, ensure_ascii=False))
    merged["paper"] = incoming["paper"]
    merged["resource"] = {**(existing.get("resource") or {}), **(incoming.get("resource") or {})}
    old_discovery = existing.get("discovery") if isinstance(existing.get("discovery"), dict) else {}
    incoming_discovery = incoming["discovery"]
    merged["discovery"] = {
        **old_discovery,
        **incoming_discovery,
        "first_discovered_at": old_discovery.get("first_discovered_at") or old_discovery.get("discovered_at") or incoming_discovery["discovered_at"],
        "last_seen_at": incoming_discovery["discovered_at"],
    }
    merged.setdefault("lifecycle", incoming["lifecycle"])
    merged.setdefault("downstream_boundary", incoming["downstream_boundary"])
    return merged


def _write_catalog(workspace: Path, records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    catalog_path = workspace / RESOURCE_CATALOG_REL_PATH
    summary_path = workspace / RESOURCE_CATALOG_SUMMARY_REL_PATH
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [records[key] for key in sorted(records)]
    payload = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in ordered)
    temp_path = catalog_path.with_suffix(".jsonl.tmp")
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(catalog_path)
    type_counts = Counter(
        str((item.get("resource") or {}).get("resource_type") or "other")
        for item in ordered
    )
    status_counts = Counter(
        str((item.get("lifecycle") or {}).get("status") or "unknown")
        for item in ordered
    )
    paper_ids = {str((item.get("paper") or {}).get("paper_id") or "") for item in ordered}
    summary = {
        "schema_version": RESOURCE_CATALOG_SCHEMA,
        "catalog_path": RESOURCE_CATALOG_REL_PATH,
        "record_count": len(ordered),
        "paper_count": len(paper_ids - {""}),
        "by_resource_type": dict(sorted(type_counts.items())),
        "by_lifecycle_status": dict(sorted(status_counts.items())),
        "t4_usage": "feasibility and baseline-resource risk context only",
        "t5_usage": "resource requirement discovery; acquire only after Phase B verification",
        "evidence_boundary": "Resource records never establish a paper mechanism, baseline equivalence, or empirical result.",
        "updated_at": _now_iso(),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def ingest_paper_resources(
    workspace: Path,
    *,
    paper_record: dict[str, Any],
    note_path: str,
    reading_status: str,
    note_content: str,
    reported_resources: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Upsert one paper's resource discoveries and return a compact receipt."""

    catalog_path = workspace / RESOURCE_CATALOG_REL_PATH
    records = _read_catalog(catalog_path)
    paper = _paper_identity(paper_record, note_path=note_path, reading_status=reading_status)
    incoming: list[dict[str, Any]] = []
    for raw in reported_resources:
        normalized = normalize_resource_record(
            raw,
            paper=paper,
            source_kind="reader_reported",
            default_locator=RESOURCE_SECTION_HEADING,
        )
        if normalized is not None:
            incoming.append(normalized)
    incoming.extend(
        resource_records_from_note(
            note_content,
            paper_record=paper_record,
            note_path=note_path,
            reading_status=reading_status,
        )
    )
    if not note_path:
        incoming.extend(resource_records_from_paper_metadata(paper_record))
    written = 0
    for item in incoming:
        resource_id = str(item["resource_id"])
        records[resource_id] = _merge_record(records[resource_id], item) if resource_id in records else item
        written += 1
    summary = _write_catalog(workspace, records)
    return {
        "catalog_path": RESOURCE_CATALOG_REL_PATH,
        "summary_path": RESOURCE_CATALOG_SUMMARY_REL_PATH,
        "paper_id": paper["paper_id"],
        "resource_records_processed": written,
        "catalog_record_count": summary["record_count"],
        "evidence_boundary": summary["evidence_boundary"],
    }


def format_resource_section(reported_resources: Iterable[dict[str, Any]] = ()) -> str:
    """Render the durable note section without asserting acquisition success."""

    lines = [RESOURCE_SECTION_HEADING]
    normalized_rows: list[tuple[str, str, str]] = []
    for raw in reported_resources:
        if not isinstance(raw, dict):
            continue
        resource = raw.get("resource") if isinstance(raw.get("resource"), dict) else raw
        resource_type = _clean_text(resource.get("resource_type") or resource.get("type"), limit=80) or "other"
        name = _clean_text(resource.get("name") or resource.get("title"), limit=240) or resource_type
        url = _canonical_url(resource.get("url") or resource.get("source") or resource.get("repository_url"))
        if name or url:
            normalized_rows.append((resource_type, name, url))
    if not normalized_rows:
        lines.extend(
            [
                "- **Discovery status**: No code, dataset, benchmark, model, project page, or supplementary-material link was located in the material read.",
                "- **Boundary**: This is a reading-time discovery statement, not proof that no resource exists; T5 may perform a targeted official-source lookup when the resource is required for execution.",
            ]
        )
        return "\n".join(lines)
    lines.append("- **Discovered resources**:")
    for resource_type, name, url in normalized_rows:
        rendered = f"  - `{resource_type}`: {name}"
        if url:
            rendered += f" | {url}"
        lines.append(rendered)
    lines.append("- **Boundary**: Listed links are discovery leads only. They are not acquired, license-verified, security-reviewed, or evidence of method equivalence.")
    return "\n".join(lines)


def ensure_resource_section(note_content: str, reported_resources: Iterable[dict[str, Any]] = ()) -> str:
    text = str(note_content or "").rstrip()
    if _SECTION_PATTERN.search(text):
        return text + "\n"
    return text + "\n\n" + format_resource_section(reported_resources) + "\n"


def refresh_resource_catalog(workspace: Path) -> dict[str, Any]:
    """Rebuild catalogue coverage from all canonical reading notes and source records.

    This deterministic pass covers older notes and T3.6 supplemental reading
    cards that do not go through ``save_paper_note``. It never fetches URLs.
    """

    catalog_path = workspace / RESOURCE_CATALOG_REL_PATH
    records = _read_catalog(catalog_path)
    note_roots = (
        workspace / "literature" / "deep_read_notes",
        workspace / "literature" / "bridge_notes",
        workspace / "literature" / "shallow_read_notes",
    )
    scanned_notes = 0
    for root in note_roots:
        if not root.is_dir():
            continue
        for note_path in sorted(root.rglob("*.md")):
            if note_path.name.startswith((".", "_")):
                continue
            text = note_path.read_text(encoding="utf-8", errors="replace")
            status_match = re.search(r"(?im)^- \*\*Status\*\*:\s*\[?([^\]\n]+)", text)
            title_match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
            id_match = re.search(r"(?im)^- \*\*ID\*\*:\s*(.+?)\s*$", text)
            paper_record = {
                "id": id_match.group(1).strip() if id_match else note_path.stem,
                "title": title_match.group(1).strip() if title_match else note_path.stem,
            }
            rel_path = note_path.relative_to(workspace).as_posix()
            status = status_match.group(1).strip().upper() if status_match else "UNKNOWN"
            for item in resource_records_from_note(
                text,
                paper_record=paper_record,
                note_path=rel_path,
                reading_status=status,
            ):
                resource_id = str(item["resource_id"])
                records[resource_id] = _merge_record(records[resource_id], item) if resource_id in records else item
            scanned_notes += 1
    source_records = (
        workspace / "literature" / "papers_verified.jsonl",
        workspace / "literature" / "papers_dedup.jsonl",
        workspace / "literature" / "survey_supplement" / "papers_retrieved.jsonl",
    )
    scanned_metadata = 0
    for source_path in source_records:
        if not source_path.is_file():
            continue
        for line in source_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                paper_record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(paper_record, dict):
                continue
            for item in resource_records_from_paper_metadata(paper_record):
                resource_id = str(item["resource_id"])
                records[resource_id] = _merge_record(records[resource_id], item) if resource_id in records else item
            scanned_metadata += 1
    summary = _write_catalog(workspace, records)
    return {
        "catalog_path": RESOURCE_CATALOG_REL_PATH,
        "summary_path": RESOURCE_CATALOG_SUMMARY_REL_PATH,
        "scanned_note_count": scanned_notes,
        "scanned_metadata_record_count": scanned_metadata,
        "resource_record_count": summary["record_count"],
        "by_resource_type": summary["by_resource_type"],
        "evidence_boundary": summary["evidence_boundary"],
    }
