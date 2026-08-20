"""Query and archive small, purpose-specific research evidence bundles.

The workspace already stores canonical paper notes and several derived
indexes.  This tool provides one stable retrieval surface so an agent does not
have to enumerate whole directories or mistake the first files it sees for
the most relevant evidence.  Retrieval is deterministic and lexical on
purpose: it selects existing material, records provenance and reading level,
and never asks a model to manufacture a relevance judgment or evidence.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, field_validator

from ..runtime.errors import ToolAccessDenied
from ..literature_identity import is_paper_note_file
from ..runtime.literature_contract import (
    BRIDGE_NOTES_REL_PATH,
    DEEP_READ_NOTES_REL_PATH,
    SHALLOW_READ_NOTES_REL_PATH,
)
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{1,}|[\u4e00-\u9fff]+")
_HEADING_RE = re.compile(r"(?m)^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
_PURPOSE_PERMISSIONS: dict[str, set[str]] = {
    "citation": {"support", "mechanism_support", "conditional_final_claim", "final_claim"},
    "mechanism": {"mechanism_support", "support"},
    "novelty": {"recall", "problem_anchor", "support"},
    "taxonomy": {"recall", "problem_anchor", "support", "inspiration"},
    "idea": {"recall", "problem_anchor", "support", "inspiration"},
    "proposal": {"problem_anchor", "mechanism_support", "support", "inspiration"},
    "resource": {"resource_lead", "recall", "inspiration"},
}


class QueryResearchEvidenceParams(BaseModel):
    query: str = Field(..., min_length=3, max_length=1200)
    purpose: Literal["citation", "mechanism", "novelty", "taxonomy", "idea", "proposal", "resource"] = "idea"
    stage: str = Field(default="downstream", min_length=2, max_length=64)
    max_results: int = Field(default=10, ge=1, le=30)
    include_abstract_leads: bool = True
    include_synthesis_context: bool = True
    include_resource_leads: bool = True

    @field_validator("query", "stage")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        return " ".join(str(value or "").split())

    @field_validator("purpose", mode="before")
    @classmethod
    def _normalize_purpose_alias(cls, value: Any) -> Any:
        aliases = {
            "positioning": "taxonomy",
            "literature_review": "taxonomy",
            "literature review": "taxonomy",
            "framing": "proposal",
            "design": "mechanism",
        }
        normalized = " ".join(str(value or "").strip().lower().split())
        return aliases.get(normalized, normalized)


class QueryResearchEvidenceTool(Tool):
    """Return the best existing evidence fragments for one visible need."""

    name = "query_research_evidence"
    description = (
        "Select a small purpose-specific bundle from canonical paper-note sections, the T4 Evidence Index, "
        "literature synthesis, and discovered code/data/benchmark/resource leads. It archives the exact query and "
        "returned provenance for downstream reuse. Use it before broad directory scans or before deciding that a "
        "new literature search is needed. Abstract, metadata, synthesis, and resource records remain leads rather "
        "than claim evidence; model knowledge is not searched or presented as workspace evidence."
    )
    parameters_schema = QueryResearchEvidenceParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy
        self._cached_corpus_signature = ""
        self._cached_records: list[dict[str, Any]] = []
        self._cached_collection_stats: dict[str, Any] = {}
        self._request_count = 0
        self._empty_result_count = 0

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = QueryResearchEvidenceParams(**kwargs)
        workspace = self.policy.workspace_dir
        requested_max_results = params.max_results
        applied_result_cap = _task_result_cap(str(self.policy.task_id or ""))
        request_budget = _task_query_budget(str(self.policy.task_id or ""))
        if request_budget is not None and self._request_count >= request_budget:
            return ToolResult(
                ok=False,
                content=(
                    f"This stage already used its {request_budget} purpose-specific evidence queries. "
                    "Do not keep reformulating near-synonymous queries. Reuse the returned bundles and model reasoning; "
                    "only one bounded external supplement is appropriate if a concrete missing external fact would change the decision."
                ),
                error="autonomous_evidence_query_quota_reached",
                data={
                    "caller_task_id": str(self.policy.task_id or ""),
                    "query_budget": request_budget,
                    "queries_used": self._request_count,
                    "empty_queries": self._empty_result_count,
                },
            )
        self._request_count += 1
        if applied_result_cap is not None:
            params.max_results = min(params.max_results, applied_result_cap)
        request = {
            "query": params.query,
            "purpose": params.purpose,
            "stage": params.stage,
            "max_results": params.max_results,
            "include_abstract_leads": params.include_abstract_leads,
            "include_synthesis_context": params.include_synthesis_context,
            "include_resource_leads": params.include_resource_leads,
        }
        fingerprint = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        corpus_signature = _corpus_signature(
            workspace,
            include_synthesis=params.include_synthesis_context,
            include_resources=params.include_resource_leads,
        )
        receipt_path = _receipt_path(params.stage, str(self.policy.task_id or "task"), fingerprint)
        reused = _read_reusable_receipt(self.policy, receipt_path, corpus_signature)
        if reused is not None:
            reused = dict(reused)
            run_id = str(self.policy.run_id or "").strip()
            reused["caller_run_id"] = run_id
            events = {
                str(item).strip()
                for item in reused.get("retrieval_events", [])
                if str(item).strip()
            }
            if run_id:
                events.add(run_id)
            reused["retrieval_events"] = sorted(events)
            reused["cache_reused"] = True
            reused["requested_max_results"] = requested_max_results
            reused["applied_result_cap"] = applied_result_cap
            reused["query_budget"] = request_budget
            reused["queries_used"] = self._request_count
            if not (reused.get("hits") or []):
                self._empty_result_count += 1
            reused["empty_queries"] = self._empty_result_count
            try:
                absolute = self.policy.resolve_write(receipt_path)
                absolute.write_text(json.dumps(reused, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except ToolAccessDenied:
                pass
            return ToolResult(ok=True, content=_result_text(reused, reused_receipt=True), data=reused)

        if corpus_signature == self._cached_corpus_signature:
            records = [dict(item) for item in self._cached_records]
            collection_stats = dict(self._cached_collection_stats)
            collection_stats["memory_cache_reused"] = True
        else:
            records, collection_stats = _collect_records(
                workspace,
                include_synthesis=params.include_synthesis_context,
                include_resources=params.include_resource_leads,
            )
            self._cached_corpus_signature = corpus_signature
            self._cached_records = [dict(item) for item in records]
            self._cached_collection_stats = dict(collection_stats)
        query_tokens = _tokens(params.query)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for record in records:
            if not params.include_abstract_leads and record.get("reading_level") in {"abstract_only", "metadata_only"}:
                continue
            score = _score_record(record, params.query, query_tokens, purpose=params.purpose)
            if score <= 0:
                continue
            ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], _strength_rank(item[1]), str(item[1].get("source_path") or "")))

        # Prefer paper and evidence diversity. A long note with many matching
        # headings must not crowd every other relevant paper out of the bundle.
        hits: list[dict[str, Any]] = []
        per_source: Counter[str] = Counter()
        for score, record in ranked:
            source = str(record.get("source_path") or "")
            source_limit = 3 if record.get("record_type") == "paper_note_section" else 2
            if per_source[source] >= source_limit:
                continue
            hit = dict(record)
            hit["score"] = round(score, 6)
            hit["claim_usable_for_purpose"] = _claim_usable(record, params.purpose)
            hit["usage_note"] = _usage_note(record, params.purpose)
            hits.append(hit)
            per_source[source] += 1
            if len(hits) >= params.max_results:
                break

        strong_count = sum(1 for item in hits if item["claim_usable_for_purpose"])
        lead_count = len(hits) - strong_count
        payload = {
            "schema_version": "1.0.0",
            "semantics": "purpose_specific_research_evidence_bundle",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "request_fingerprint": fingerprint,
            "corpus_signature": corpus_signature,
            "caller_task_id": str(self.policy.task_id or ""),
            "caller_run_id": str(self.policy.run_id or ""),
            "retrieval_events": [str(self.policy.run_id)] if str(self.policy.run_id or "").strip() else [],
            "requested_max_results": requested_max_results,
            "applied_result_cap": applied_result_cap,
            "query_budget": request_budget,
            "queries_used": self._request_count,
            "request": request,
            "corpus_record_count": len(records),
            "matched_record_count": len(ranked),
            "returned_count": len(hits),
            "strong_support_count": strong_count,
            "lead_only_count": lead_count,
            "retrieval_recommended": strong_count == 0,
            "cache_reused": False,
            "collection_stats": collection_stats,
            "reasoning_policy": (
                "Integrate this retrieved context naturally with the model's scholarly understanding, logical inference, "
                "and creative reasoning. Do not mechanically label ordinary reasoning. Require traceable support only when "
                "asserting what a paper found, an empirical fact, a mechanism attributed to prior work, or external novelty."
            ),
            "hits": hits,
        }
        if not hits:
            self._empty_result_count += 1
        payload["empty_queries"] = self._empty_result_count
        try:
            absolute = self.policy.resolve_write(receipt_path)
            if absolute.is_file():
                try:
                    existing = json.loads(absolute.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    existing = None
                if isinstance(existing, dict) and existing.get("request_fingerprint") == fingerprint:
                    payload["generated_at"] = str(existing.get("generated_at") or payload["generated_at"])
            absolute.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            payload["receipt_path"] = receipt_path
        except ToolAccessDenied:
            payload["receipt_path"] = ""
            payload["receipt_warning"] = "Current task can query evidence but is not authorized to archive the receipt."

        return ToolResult(ok=True, content=_result_text(payload), data=payload)


def _task_result_cap(task_id: str) -> int | None:
    """Bound autonomous context bundles without weakening explicit retrieval.

    Downstream agents need enough evidence to decide, not a second literature
    corpus in every tool result.  These caps apply only to known autonomous
    stage callers; direct/standalone use retains the public parameter limit.
    """

    normalized = str(task_id or "").strip().upper()
    if normalized.startswith(("T4.5", "T6")):
        return 8
    if normalized.startswith(("T3.5", "T3.6", "T8")):
        return 10
    return None


def _task_query_budget(task_id: str) -> int | None:
    """Limit autonomous query reformulation within one reasoning decision."""

    normalized = str(task_id or "").strip().upper()
    if normalized == "T8-WRITE" or normalized.startswith("T8-SEC-"):
        return 1
    if normalized.startswith(("T3.5", "T3.6", "T4.5", "T6", "T8")):
        return 2
    return None


def select_evidence_index_records(
    records: Iterable[dict[str, Any]],
    *,
    queries: Iterable[str],
    explicit_atom_ids: Iterable[str] = (),
    max_results: int = 16,
) -> list[dict[str, Any]]:
    """Select route-specific T4 atoms while always preserving explicit IDs."""

    material = [dict(item) for item in records if isinstance(item, dict)]
    explicit = {str(value).strip() for value in explicit_atom_ids if str(value).strip()}
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    by_id = {str(item.get("atom_id") or ""): item for item in material}
    for atom_id in sorted(explicit):
        item = by_id.get(atom_id)
        if item is not None:
            selected.append(item)
            selected_ids.add(atom_id)
    query = " ".join(str(value or "") for value in queries).strip()
    tokens = _tokens(query)
    ranked = sorted(
        (
            (_score_record(item, query, tokens, purpose="idea"), item)
            for item in material
            if str(item.get("atom_id") or "") not in selected_ids
        ),
        key=lambda pair: (-pair[0], _strength_rank(pair[1]), str(pair[1].get("atom_id") or "")),
    )
    seen_papers = {str(item.get("paper_id") or item.get("source_path") or "") for item in selected}
    deferred: list[dict[str, Any]] = []
    for score, item in ranked:
        if score <= 0:
            continue
        paper = str(item.get("paper_id") or item.get("source_path") or "")
        if paper in seen_papers:
            deferred.append(item)
            continue
        selected.append(item)
        selected_ids.add(str(item.get("atom_id") or ""))
        seen_papers.add(paper)
        if len(selected) >= max_results:
            return selected[:max_results]
    for item in deferred:
        if len(selected) >= max_results:
            break
        selected.append(item)
    return selected[:max_results]


def _collect_records(
    workspace: Path,
    *,
    include_synthesis: bool,
    include_resources: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    evidence_index = workspace / "ideation/evidence/evidence_index.jsonl"
    indexed_paths: set[str] = set()
    index_mtime_ns = 0
    if evidence_index.is_file():
        indexed = _read_jsonl(evidence_index)
        records.extend(indexed)
        indexed_paths = {str(item.get("source_path") or "") for item in indexed if item.get("source_path")}
        try:
            index_mtime_ns = evidence_index.stat().st_mtime_ns
        except OSError:
            index_mtime_ns = 0

    # The T4 Evidence Index is an efficient base, not a frozen view of the
    # project.  Read only notes created or updated after it so a T4.5/T8
    # supplement immediately becomes retrievable without rebuilding T4.
    refreshed_paths: set[str] = set()
    note_paths = _canonical_note_paths(workspace)
    citation_keys_by_path = _citation_keys_by_note_path(workspace)
    for path, evidence_level in note_paths:
        rel_path = path.relative_to(workspace).as_posix()
        try:
            changed_since_index = path.stat().st_mtime_ns > index_mtime_ns
        except OSError:
            continue
        if rel_path in indexed_paths and not changed_since_index:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Replace stale atoms for an updated path rather than returning both
        # old and new versions of the same note section.
        if rel_path in indexed_paths:
            records = [item for item in records if str(item.get("source_path") or "") != rel_path]
        refreshed_paths.add(rel_path)
        for locator, title, content in _sections(text):
            records.append(
                {
                    "record_type": "paper_note_section",
                    "paper_id": path.stem,
                    "source_path": rel_path,
                    "locator": locator,
                    "section_title": title,
                    "content": content[:2400],
                    "reading_level": _normalized_level(evidence_level),
                    "evidence_status": "direct_support" if evidence_level == "FULL_OR_PARTIAL_TEXT" else "abstract_hint",
                    "allowed_uses": _default_allowed_uses(evidence_level),
                    "citation_key": _citation_key(text),
                }
            )
    normalized: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("record_type", "paper_note_section" if str(item.get("source_path") or "").startswith("literature/") else "evidence_atom")
        item.setdefault("locator", item.get("section_key") or item.get("atom_id") or "record")
        item["reading_level"] = _normalized_level(str(item.get("reading_level") or ""))
        item["content"] = " ".join(str(item.get("content") or "").split())[:2400]
        if not str(item.get("citation_key") or "").strip():
            item["citation_key"] = citation_keys_by_path.get(str(item.get("source_path") or ""), "")
        if item["content"]:
            normalized.append(item)
    records = normalized
    if include_synthesis:
        path = workspace / "literature/synthesis.md"
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for locator, title, content in _sections(text):
                records.append(
                    {
                        "record_type": "synthesis_context",
                        "paper_id": "",
                        "source_path": "literature/synthesis.md",
                        "locator": locator,
                        "section_title": title,
                        "content": content[:2400],
                        "reading_level": "synthesis_inference",
                        "evidence_status": "synthesis_inference",
                        "allowed_uses": ["recall", "inspiration"],
                        "citation_key": "",
                    }
                )
    if include_resources:
        for item in _read_jsonl(workspace / "literature/resource_catalog.jsonl"):
            resource = item.get("resource") if isinstance(item.get("resource"), dict) else {}
            paper = item.get("paper") if isinstance(item.get("paper"), dict) else {}
            lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
            content = " ".join(
                str(value or "")
                for value in (
                    resource.get("name"),
                    resource.get("resource_type"),
                    resource.get("url"),
                    resource.get("relationship_to_paper"),
                    resource.get("license_hint"),
                    paper.get("title"),
                    lifecycle.get("status"),
                )
            ).strip()
            if not content:
                continue
            records.append(
                {
                    "record_type": "resource_lead",
                    "paper_id": str(paper.get("paper_id") or item.get("paper_id") or ""),
                    "source_path": "literature/resource_catalog.jsonl",
                    "locator": str(item.get("resource_id") or resource.get("url") or "resource"),
                    "section_title": str(resource.get("resource_type") or "Resource lead"),
                    "content": content[:2400],
                    "reading_level": "metadata_only",
                    "evidence_status": "resource_lead_unverified",
                    "allowed_uses": ["resource_lead", "recall", "inspiration"],
                    "citation_key": "",
                }
            )
    return records, {
        "memory_cache_reused": False,
        "evidence_index_used": evidence_index.is_file(),
        "canonical_note_count": len(note_paths),
        "incremental_note_count": len(refreshed_paths),
        "incremental_note_paths": sorted(refreshed_paths),
    }


def _canonical_note_paths(workspace: Path) -> list[tuple[Path, str]]:
    specs = (
        (DEEP_READ_NOTES_REL_PATH, "FULL_OR_PARTIAL_TEXT", False),
        (BRIDGE_NOTES_REL_PATH, "FULL_OR_PARTIAL_TEXT", True),
        (SHALLOW_READ_NOTES_REL_PATH, "ABSTRACT_ONLY", False),
    )
    result: list[tuple[Path, str]] = []
    for rel_root, level, recursive in specs:
        root = workspace / rel_root
        if not root.is_dir():
            continue
        paths = root.glob("**/*.md" if recursive else "*.md")
        for path in sorted(paths, key=lambda item: item.as_posix()):
            if not path.is_file() or path.name.startswith("_"):
                continue
            try:
                if path.stat().st_size <= 0 or not is_paper_note_file(path):
                    continue
            except OSError:
                continue
            result.append((path, level))
    return result


def _corpus_signature(workspace: Path, *, include_synthesis: bool, include_resources: bool) -> str:
    paths = [path for path, _level in _canonical_note_paths(workspace)]
    paths.append(workspace / "ideation/evidence/evidence_index.jsonl")
    # T8 enriches Evidence-Index atoms with the canonical note-to-BibTeX map
    # in this resource index. Include its identity so an older receipt cannot
    # silently preserve blank citation keys after the map becomes available.
    paths.append(workspace / "drafts/manuscript_resource_index.json")
    if include_synthesis:
        paths.append(workspace / "literature/synthesis.md")
    if include_resources:
        paths.append(workspace / "literature/resource_catalog.jsonl")
    material: list[tuple[str, int, int]] = []
    for path in sorted(set(paths), key=lambda item: item.as_posix()):
        try:
            stat = path.stat()
        except OSError:
            continue
        try:
            rel = path.relative_to(workspace).as_posix()
        except ValueError:
            rel = path.as_posix()
        material.append((rel, stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_reusable_receipt(
    policy: WorkspaceAccessPolicy,
    receipt_path: str,
    corpus_signature: str,
) -> dict[str, Any] | None:
    try:
        path = policy.resolve_read(receipt_path)
    except ToolAccessDenied:
        return None
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("corpus_signature") != corpus_signature:
        return None
    payload["receipt_path"] = receipt_path
    return payload


def _result_text(payload: dict[str, Any], *, reused_receipt: bool = False) -> str:
    hits = payload.get("hits") if isinstance(payload.get("hits"), list) else []
    lines = [
        (
            f"Evidence query reused {len(hits)} archived fragments."
            if reused_receipt
            else f"Evidence query returned {len(hits)} fragments: strong={payload.get('strong_support_count', 0)}, leads={payload.get('lead_only_count', 0)}."
        ),
        f"Archived receipt: {payload.get('receipt_path') or 'not authorized'}.",
    ]
    if not hits:
        if payload.get("query_budget") and payload.get("queries_used", 0) >= payload.get("query_budget", 0):
            lines.append(
                "No lexical match was found and this stage's local query budget is exhausted. Do not reformulate again; "
                "use the existing materials with model reasoning, or run one bounded supplement only if a concrete external fact changes the decision."
            )
        else:
            lines.append(
                "No lexical match was found. At most one materially different query remains; otherwise use model reasoning for ordinary inference "
                "or run a bounded supplement only if the missing external fact changes the current decision."
            )
    else:
        for hit in hits:
            citation_key = str(hit.get("citation_key") or "").strip()
            lines.append(
                f"- {hit.get('source_path')}#{hit.get('locator')} | {hit.get('reading_level')} | "
                f"citation_key={citation_key or 'none'} | claim_usable={hit.get('claim_usable_for_purpose')} | "
                f"exact saved fragment: {str(hit.get('content') or '')[:1200]}"
            )
    return "\n".join(lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        try:
            item = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def _sections(text: str) -> list[tuple[str, str, str]]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        content = " ".join(text.split())
        return [("body", "Document body", content)] if content else []
    result: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = " ".join(text[match.end():end].split())
        if not content:
            continue
        title = " ".join(match.group("title").split())
        locator = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", title.casefold()).strip("_")[:96] or f"section_{index + 1}"
        result.append((locator, title, content))
    return result


def _tokens(value: str) -> Counter[str]:
    out: Counter[str] = Counter()
    for match in _WORD_RE.finditer(str(value or "")):
        token = match.group(0).casefold()
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) == 1:
                out[token] += 1
            else:
                for width in (2, 3):
                    for index in range(max(0, len(token) - width + 1)):
                        out[token[index:index + width]] += 1
        elif len(token) >= 2:
            out[token] += 1
    return out


def _score_record(record: dict[str, Any], raw_query: str, query_tokens: Counter[str], *, purpose: str) -> float:
    title = f"{record.get('section_title') or ''} {record.get('paper_id') or ''}"
    content = str(record.get("content") or "")
    title_tokens = _tokens(title)
    content_tokens = _tokens(content)
    if not query_tokens:
        return 0.0
    score = 0.0
    for token, q_count in query_tokens.items():
        tf = 2.4 * title_tokens.get(token, 0) + content_tokens.get(token, 0)
        if tf:
            score += (1.0 + math.log1p(tf)) * (1.0 + math.log1p(q_count))
    normalized_query = " ".join(str(raw_query or "").casefold().split())
    normalized_text = " ".join(f"{title} {content}".casefold().split())
    if len(normalized_query) >= 5 and normalized_query in normalized_text:
        score += 8.0
    if _claim_usable(record, purpose):
        score *= 1.22
    elif record.get("record_type") == "resource_lead" and purpose == "resource":
        score *= 1.35
    return score


def _claim_usable(record: dict[str, Any], purpose: str) -> bool:
    level = _normalized_level(str(record.get("reading_level") or ""))
    if level not in {"full_text", "partial_text"}:
        return False
    allowed = {str(value) for value in record.get("allowed_uses", []) if str(value)}
    required = _PURPOSE_PERMISSIONS.get(purpose, {"support"})
    return bool(allowed & required)


def _usage_note(record: dict[str, Any], purpose: str) -> str:
    if _claim_usable(record, purpose):
        return "May support this purpose after checking the exact saved section and citation key."
    level = _normalized_level(str(record.get("reading_level") or ""))
    if record.get("record_type") == "resource_lead":
        return "Discovery lead only. Verify availability, ownership, license, version, and task fit before use."
    if level == "abstract_only":
        return "Abstract-level lead. Use for recall, coverage, taxonomy, or inspiration; upgrade before mechanism, result, causal, or novelty claims."
    if level == "synthesis_inference":
        return "Cross-paper synthesis context. Trace any citation-bearing statement back to the underlying paper note."
    return "Context or discovery lead only; do not cite it as direct support."


def _strength_rank(record: dict[str, Any]) -> int:
    return {
        "full_text": 0,
        "partial_text": 1,
        "abstract_only": 2,
        "synthesis_inference": 3,
        "metadata_only": 4,
    }.get(_normalized_level(str(record.get("reading_level") or "")), 5)


def _normalized_level(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    aliases = {
        "full_or_partial_text": "partial_text",
        "full": "full_text",
        "partial": "partial_text",
        "abstract": "abstract_only",
        "metadata": "metadata_only",
    }
    return aliases.get(normalized, normalized or "metadata_only")


def _default_allowed_uses(level: str) -> list[str]:
    return (
        ["recall", "problem_anchor", "mechanism_support", "support", "inspiration", "conditional_final_claim"]
        if level == "FULL_OR_PARTIAL_TEXT"
        else ["recall", "inspiration"]
    )


def _citation_key(text: str) -> str:
    for pattern in (r"(?im)^\s*-\s*\*\*Citation Key\*\*\s*:\s*(\S+)", r"\\cite\{([^},]+)"):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _citation_keys_by_note_path(workspace: Path) -> dict[str, str]:
    """Load T8's canonical note-to-BibTeX map without model-visible reads."""

    path = workspace / "drafts/manuscript_resource_index.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    cards = payload.get("paper_note_cards") if isinstance(payload, dict) else []
    if not isinstance(cards, list):
        return {}
    return {
        str(card.get("path") or "").strip(): str(card.get("bib_key") or "").strip()
        for card in cards
        if isinstance(card, dict) and str(card.get("path") or "").strip()
    }


def _receipt_path(stage: str, task_id: str, fingerprint: str) -> str:
    safe_stage = re.sub(r"[^a-zA-Z0-9_.-]+", "-", stage).strip("-.") or "downstream"
    safe_task = re.sub(r"[^a-zA-Z0-9_.-]+", "-", task_id).strip("-.") or "task"
    return f"literature/evidence_queries/{safe_stage}/{safe_task}_{fingerprint[:12]}.json"
