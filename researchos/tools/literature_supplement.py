"""Bounded, model-initiated literature supplements for downstream stages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from .base import Tool, ToolResult
from .multi_source_search import MultiSourceSearchTool
from .paper_enrichment_tool import BuildVerifiedPapersTool
from .paper_utils import deduplicate_papers
from .workspace_policy import WorkspaceAccessPolicy
from ..literature_identity import paper_record_match_keys
from ..literature_resources import refresh_resource_catalog
from ..runtime.literature_contract import build_literature_manifest
from ..runtime.pdf_acquisition import acquire_retained_pdfs, attach_pdf_acquisition


class TargetedLiteratureSupplementParams(BaseModel):
    queries: list[str] = Field(
        ...,
        min_length=1,
        max_length=6,
        description="One to six specific literature queries derived from a visible evidence gap.",
    )
    reason: str = Field(..., min_length=8, max_length=800)
    target_sections: list[str] = Field(default_factory=list, max_length=8)
    stage: str = Field(default="downstream", min_length=2, max_length=40)
    target_record_count: int = Field(default=8, ge=1, le=24)
    max_results_per_query: int = Field(default=6, ge=1, le=12)

    @field_validator("queries")
    @classmethod
    def _normalize_queries(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item or "").split()).strip()
            if len(text) < 4:
                continue
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                cleaned.append(text)
        if not cleaned:
            raise ValueError("at least one concrete query is required")
        return cleaned


class TargetedLiteratureSupplementTool(Tool):
    """Search, verify and materialize a small downstream evidence supplement."""

    name = "targeted_literature_supplement"
    description = (
        "Run one bounded literature supplement when synthesis, ideation, novelty positioning, or manuscript writing "
        "finds a concrete evidence gap. Results are metadata-verified and materialized as ABSTRACT_ONLY notes; they "
        "cannot support mechanism, causal, result, or novelty claims until upgraded by actual reading."
    )
    parameters_schema = TargetedLiteratureSupplementParams
    timeout_seconds = 1800.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = TargetedLiteratureSupplementParams(**kwargs)
        caller_task_id = str(self.policy.task_id or "").strip()
        requested_query_count = len(params.queries)
        params.queries = self._bounded_queries_for_task(caller_task_id, params.queries)
        stage_caps = {
            "T3.6-SEC": 8,
            "T8-SEC": 6,
            "T4.5": 6,
            "T6": 6,
        }
        family = next((prefix for prefix in stage_caps if caller_task_id.startswith(prefix)), "")
        if family:
            params.target_record_count = min(params.target_record_count, stage_caps[family])
        stage = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in params.stage).strip("-")
        stage = stage or "downstream"
        query_fingerprint = self._query_fingerprint(params)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "queries": params.queries,
                    "reason": params.reason,
                    "target_sections": params.target_sections,
                    "target_record_count": params.target_record_count,
                    "max_results_per_query": params.max_results_per_query,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        supplement_dir = self.policy.resolve_write(f"literature/targeted_supplements/{stage}/{fingerprint[:12]}")
        output_path = supplement_dir / "supplement.json"
        if output_path.is_file():
            try:
                existing = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                existing = {}
            if isinstance(existing, dict) and existing.get("request_fingerprint") == fingerprint:
                return ToolResult(ok=True, content="Reused the completed targeted literature supplement.", data=existing)

        reusable = self._find_reusable_query_result(
            query_fingerprint=query_fingerprint,
            minimum_verified=params.target_record_count,
            excluding=output_path,
        )
        if reusable is not None:
            reused_path, reused = reusable
            supplement_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                **reused,
                "request_fingerprint": fingerprint,
                "query_fingerprint": query_fingerprint,
                "caller_task_id": caller_task_id,
                "stage": stage,
                "reason": params.reason,
                "target_sections": params.target_sections,
                "queries": params.queries,
                "requested_query_count": requested_query_count,
                "applied_query_count": len(params.queries),
                "target_record_count": params.target_record_count,
                "cache_reused": True,
                "executed_external_retrieval": False,
                "reused_from": reused_path.relative_to(self.policy.workspace_dir).as_posix(),
            }
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return ToolResult(
                ok=True,
                content="Reused an existing verified supplement for the same normalized queries; no external search or verification was run.",
                data=payload,
            )

        quota_error = self._autonomous_quota_error(caller_task_id)
        if quota_error:
            return ToolResult(
                ok=False,
                content=quota_error,
                error="autonomous_supplement_quota_reached",
                data={"task_id": caller_task_id, "stage": stage},
            )
        supplement_dir.mkdir(parents=True, exist_ok=True)

        search_tool = MultiSourceSearchTool()
        retrieved: list[dict[str, Any]] = []
        search_log: list[dict[str, Any]] = []
        for query in params.queries:
            result = await search_tool.execute(
                query=query,
                max_results=params.max_results_per_query,
                query_bucket="downstream_targeted_supplement",
                sources=["openalex", "crossref", "arxiv"],
                # Merge all available sources.  OpenAlex/arXiv often supply
                # an abstract or OA location that a DOI-only Crossref record
                # lacks; stopping after the first successful provider can
                # leave a verified but unusable downstream supplement.
                try_all_sources=True,
            )
            papers = (result.data or {}).get("papers") or []
            search_log.append(
                {
                    "query": query,
                    "ok": result.ok,
                    "error": result.error,
                    "count": len(papers) if isinstance(papers, list) else 0,
                    "source_stats": (result.data or {}).get("source_stats") or {},
                }
            )
            if result.ok and isinstance(papers, list):
                retrieved.extend(item for item in papers if isinstance(item, dict))

        retrieved = deduplicate_papers(retrieved)
        existing_keys: set[str] = set()
        for rel_path in ("literature/papers_verified.jsonl", "literature/targeted_supplements"):
            path = self.policy.workspace_dir / rel_path
            candidates = path.rglob("papers_verified.jsonl") if path.is_dir() else [path]
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        existing_keys.update(paper_record_match_keys(record))
        novel = [
            paper
            for paper in retrieved
            if not (set(paper_record_match_keys(paper)) & existing_keys)
        ]
        # Keep the search engine's relevance order within each group, while
        # preferring records that can immediately become an ABSTRACT_ONLY
        # note.  Metadata-only records remain eligible and may still provide
        # an acquired PDF for a later real reading pass.
        novel.sort(key=lambda paper: 0 if len(str(paper.get("abstract") or "").strip()) >= 80 else 1)
        novel = novel[: params.target_record_count * 2]

        verifier = BuildVerifiedPapersTool(self.policy)
        verified: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for paper in novel:
                record, failure = await verifier._verify_one_paper(
                    client,
                    paper,
                    title_similarity_threshold=0.84,
                )
                if record is not None:
                    record["targeted_supplement"] = {
                        "stage": stage,
                        "reason": params.reason,
                        "target_sections": params.target_sections,
                        "request_fingerprint": fingerprint,
                    }
                    verified.append(record)
                elif failure is not None:
                    failures.append(failure)
                if len(verified) >= params.target_record_count:
                    break

        pdf_result = await acquire_retained_pdfs(
            self.policy.workspace_dir,
            verified,
            source_pool=f"targeted_supplement_{stage}",
        ) if verified else {"counts": {}}
        verified = attach_pdf_acquisition(verified, pdf_result) if verified else []

        # Reuse the canonical abstract-note formatter so downstream retrieval
        # joins the same note and BibTeX contract as T3 and T3.6.
        from .survey_tools import _materialize_survey_supplement_shallow_notes

        note_summary = _materialize_survey_supplement_shallow_notes(self.policy.workspace_dir, verified)
        resource_catalog = refresh_resource_catalog(self.policy.workspace_dir)
        self._write_jsonl(supplement_dir / "papers_retrieved.jsonl", retrieved)
        self._write_jsonl(supplement_dir / "papers_verified.jsonl", verified)
        self._write_jsonl(supplement_dir / "verification_failures.jsonl", failures)
        self._write_jsonl(supplement_dir / "search_log.jsonl", search_log)
        # Build the shared resolver only after the verified supplement and its
        # canonical notes exist, otherwise this run's records are invisible
        # until an unrelated later stage happens to rebuild the manifest.
        build_literature_manifest(self.policy.workspace_dir, write=True)
        payload = {
            "version": "1.0",
            "semantics": "bounded_downstream_literature_supplement",
            "request_fingerprint": fingerprint,
            "query_fingerprint": query_fingerprint,
            "caller_task_id": caller_task_id,
            "stage": stage,
            "reason": params.reason,
            "target_sections": params.target_sections,
            "queries": params.queries,
            "requested_query_count": requested_query_count,
            "applied_query_count": len(params.queries),
            "target_record_count": params.target_record_count,
            "max_results_per_query": params.max_results_per_query,
            "cache_reused": False,
            "executed_external_retrieval": True,
            "retrieved_count": len(retrieved),
            "novel_candidate_count": len(novel),
            "verified_count": len(verified),
            "verification_failure_count": len(failures),
            "reading_notes": note_summary,
            "resource_catalog": {
                **resource_catalog,
                "usage_boundary": (
                    "Associated code, dataset, benchmark, model, and project links are discovery leads only. "
                    "T5 must verify identity, license, immutable version, and protocol compatibility before use."
                ),
            },
            "evidence_boundary": (
                "New records are real-search, metadata-verified discovery sources. Generated notes remain ABSTRACT_ONLY. "
                "Use them for coverage, taxonomy, history, trends, or explicitly abstract-level context. Upgrade by actual "
                "full/partial reading before using them for mechanisms, causal claims, detailed results, or novelty conclusions."
            ),
            "artifacts": {
                "root": supplement_dir.relative_to(self.policy.workspace_dir).as_posix(),
                "verified": (supplement_dir / "papers_verified.jsonl").relative_to(self.policy.workspace_dir).as_posix(),
                "search_log": (supplement_dir / "search_log.jsonl").relative_to(self.policy.workspace_dir).as_posix(),
            },
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ToolResult(
            ok=True,
            content=(
                f"Targeted supplement completed: retrieved={len(retrieved)}, verified={len(verified)}, "
                f"abstract_notes={note_summary.get('generated_count', 0)}. Evidence remains abstract-level until upgraded."
            ),
            data=payload,
        )

    def _autonomous_quota_error(self, caller_task_id: str) -> str | None:
        """Bound autonomous retrieval while allowing it at useful stages."""

        if not caller_task_id:
            return None
        root = self.policy.workspace_dir / "literature" / "targeted_supplements"
        same_task = 0
        family_count = 0
        all_autonomous = 0
        if caller_task_id.startswith("T3.6-SEC-"):
            family, per_task_limit, family_limit = "T3.6-SEC", 1, 4
        elif caller_task_id.startswith("T8-SEC-"):
            family, per_task_limit, family_limit = "T8-SEC", 1, 3
        elif caller_task_id in {"T3.5", "T4", "T4.5", "T4.5-FORMALIZE", "T4.5-REVIEW", "T6", "T8-RESOURCE", "T8-WRITE"}:
            family = caller_task_id.split("-")[0]
            per_task_limit, family_limit = ((1, 2) if caller_task_id == "T3.5" else (1, 3))
        else:
            return None
        if root.is_dir():
            for path in root.rglob("supplement.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                task_id = str(payload.get("caller_task_id") or "") if isinstance(payload, dict) else ""
                if not task_id:
                    continue
                if payload.get("executed_external_retrieval") is False:
                    continue
                all_autonomous += 1
                if task_id == caller_task_id:
                    same_task += 1
                same_family = task_id.startswith(family) if family.endswith("-SEC") else task_id.split("-")[0] == family
                if same_family:
                    family_count += 1
        if same_task >= per_task_limit:
            return f"Task {caller_task_id} reached its autonomous literature supplement limit; use the archived evidence or request an explicit scope expansion."
        if family_count >= family_limit:
            return f"Stage family {family} reached its autonomous literature supplement limit; continue with existing evidence or request an explicit scope expansion."
        if all_autonomous >= 10:
            return "This workspace reached the downstream autonomous literature supplement safety limit; review accumulated evidence before expanding scope."
        return None

    @staticmethod
    def _bounded_queries_for_task(caller_task_id: str, queries: list[str]) -> list[str]:
        """Keep one autonomous action focused instead of fanning out searches."""

        caps = (
            ("T8-SEC-", 1),
            ("T8-WRITE", 1),
            ("T3.6-SEC-", 2),
            ("T3.5", 2),
            ("T4.5", 2),
            ("T4", 2),
            ("T6", 2),
        )
        cap = next((value for prefix, value in caps if caller_task_id.startswith(prefix)), len(queries))
        selected: list[str] = []
        normalized_seen: set[str] = set()
        for query in queries:
            normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", query.casefold()).strip()
            if normalized in normalized_seen:
                continue
            normalized_seen.add(normalized)
            selected.append(query)
            if len(selected) >= cap:
                break
        return selected

    @staticmethod
    def _query_fingerprint(params: TargetedLiteratureSupplementParams) -> str:
        normalized_queries = sorted({" ".join(query.casefold().split()) for query in params.queries})
        return hashlib.sha256(
            json.dumps(
                {
                    "queries": normalized_queries,
                    "max_results_per_query": params.max_results_per_query,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def _find_reusable_query_result(
        self,
        *,
        query_fingerprint: str,
        minimum_verified: int,
        excluding: Path,
    ) -> tuple[Path, dict[str, Any]] | None:
        root = self.policy.workspace_dir / "literature" / "targeted_supplements"
        if not root.is_dir():
            return None
        candidates: list[tuple[int, Path, dict[str, Any]]] = []
        for path in root.rglob("supplement.json"):
            if path == excluding:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            existing_query_fingerprint = str(payload.get("query_fingerprint") or "")
            if not existing_query_fingerprint:
                try:
                    legacy = TargetedLiteratureSupplementParams(
                        queries=list(payload.get("queries") or []),
                        reason=str(payload.get("reason") or "legacy supplement"),
                        target_record_count=max(1, int(payload.get("target_record_count") or 1)),
                        max_results_per_query=int(payload.get("max_results_per_query") or 6),
                    )
                except (TypeError, ValueError):
                    continue
                existing_query_fingerprint = self._query_fingerprint(legacy)
            verified_count = int(payload.get("verified_count") or 0)
            if existing_query_fingerprint == query_fingerprint and verified_count >= minimum_verified:
                candidates.append((verified_count, path, payload))
        if not candidates:
            return None
        _count, path, payload = min(candidates, key=lambda item: (item[0], item[1].as_posix()))
        return path, payload

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
        path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + ("\n" if records else ""),
            encoding="utf-8",
        )
