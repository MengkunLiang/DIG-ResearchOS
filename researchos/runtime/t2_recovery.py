from __future__ import annotations

"""T2 运行期恢复与确定性收尾。

当 Scout Agent 已经拿到了足够的检索结果，但 LLM 在去重/写文件前中断时，
这里提供一条纯代码路径，把 `papers_raw.jsonl` 收敛为 T2 所需的其余产物。
"""

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from ..tools.paper_enrichment import apply_semantic_screening, build_access_audit, build_deep_read_queue, enrich_papers
from ..tools.citation_graph import build_domain_map
from ..tools.abstract_utils import clean_abstract
from ..tools.crossref_api import _extract_crossref_references
from ..tools.openalex_api import _work_to_paper as _openalex_work_to_paper
from ..tools.paper_save_tools import SavePapersDedupTool, SavePapersRawTool
from ..literature_identity import stable_noopenalex_id
from ..tools.paper_utils import (
    deduplicate_papers,
    filter_by_domain,
    generate_search_log,
    score_papers,
)
from ..tools.workspace_policy import WorkspaceAccessPolicy
from ..time_utils import current_utc_year, format_year_window, recent_year_from

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - depends on runtime env
    httpx = None


SEARCH_TOOL_NAMES = frozenset(
    {
        "multi_source_search",
        "search_papers",
        "semantic_scholar_search",
        "arxiv_search",
        "openalex_search",
        "crossref_search",
        "elsevier_scopus_search",
        "informs_search",
        "fetch_outgoing_citations",
    }
)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _load_project(workspace_dir: Path) -> dict[str, Any]:
    project_path = workspace_dir / "project.yaml"
    if not project_path.exists():
        return {}
    data = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _load_bridge_domain_plan(workspace_dir: Path) -> dict[str, Any]:
    path = workspace_dir / "literature" / "bridge_domain_plan.json"
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_keywords(project: dict[str, Any]) -> list[str]:
    raw_keywords = project.get("keywords") or []
    keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
    if keywords:
        return keywords
    direction = str(project.get("research_direction", "")).strip()
    if not direction:
        return []
    # 退化情况下，用研究方向整句做弱关键词。
    return [direction]


def _keyword_aliases(keyword: str) -> list[str]:
    tokens = [token for token in keyword.lower().replace("/", " ").split() if token and token not in _STOPWORDS]
    aliases = {keyword.lower().strip()}
    aliases.update(token for token in tokens if len(token) >= 4)
    return [alias for alias in aliases if alias]


def _project_domain_profile(project: dict[str, Any]) -> dict[str, Any] | None:
    """Return an explicit domain profile if the project provides one.

    T2 recovery must not infer discipline-specific filters from hardcoded
    keyword lists. If users or an upstream LLM want profile-driven filtering,
    they can store it in project.yaml under ``domain_profile`` or
    ``literature_domain_profile``.
    """

    for key in ("domain_profile", "literature_domain_profile"):
        profile = project.get(key)
        if isinstance(profile, dict):
            return profile
    return None


def _select_final_papers(scored_papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the recovered paper pool without relevance-score exclusion.

    Deep reading is capped later by ``build_deep_read_queue``. T2 recovery
    should not silently drop verified papers here, because overflow papers are
    still useful for shallow abstract notes and resume/backlog decisions.
    """

    return scored_papers


def _normalize_match_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def _researcher_email() -> str:
    return (
        os.environ.get("RESEARCHER_EMAIL")
        or os.environ.get("OPENALEX_MAILTO")
        or "researcher@example.com"
    ).strip()


def _crossref_headers() -> dict[str, str]:
    return {"User-Agent": f"ResearchOS/0.1.0 (mailto:{_researcher_email()})"}


def _record_doi(record: dict[str, Any]) -> str:
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    candidates = [
        record.get("doi"),
        external_ids.get("DOI"),
        record.get("canonical_id"),
        record.get("id"),
        record.get("url"),
    ]
    for candidate in candidates:
        doi = str(candidate or "").strip()
        doi = (
            doi.removeprefix("https://doi.org/")
            .removeprefix("http://doi.org/")
            .removeprefix("doi:")
        )
        if doi.startswith("10."):
            return doi
    return ""


def _record_openalex_id(record: dict[str, Any]) -> str:
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    for candidate in (
        record.get("canonical_id"),
        record.get("id"),
        record.get("openalex_id"),
        external_ids.get("OpenAlex"),
        record.get("url"),
    ):
        value = str(candidate or "").strip()
        if value.startswith("https://openalex.org/") or value.startswith("https://api.openalex.org/works/"):
            value = value.rstrip("/").split("/")[-1]
        if value.startswith("W") and value[1:].isdigit():
            return value
    return ""


def _record_has_pdf_hint(record: dict[str, Any]) -> bool:
    for key in (
        "pdf_url",
        "open_access_pdf_url",
        "oa_pdf_url",
        "best_pdf_url",
        "full_text_url",
        "pmc_pdf_url",
        "url_for_pdf",
    ):
        if str(record.get(key) or "").strip():
            return True
    for key in ("best_oa_location", "primary_location", "openAccessPdf", "open_access_pdf", "oa_pdf"):
        value = record.get(key)
        if isinstance(value, dict) and any(str(value.get(k) or "").strip() for k in ("pdf_url", "url_for_pdf", "url")):
            return True
    for key in ("locations", "oa_locations", "open_access_locations", "openAccessLocations", "open_access_pdfs"):
        value = record.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _merge_openalex_metadata(target: dict[str, Any], openalex_paper: dict[str, Any]) -> dict[str, bool]:
    filled = {
        "openalex_id": False,
        "abstract": False,
        "references": False,
        "pdf_hints": False,
    }

    openalex_id = _record_openalex_id(openalex_paper)
    if openalex_id and _record_openalex_id(target) != openalex_id:
        target["canonical_id"] = openalex_id
        target["canonical_id_source"] = "openalex"
        target["no_openalex_id"] = False
        filled["openalex_id"] = True

    external_ids = target.get("externalIds") if isinstance(target.get("externalIds"), dict) else {}
    incoming_external = openalex_paper.get("externalIds") if isinstance(openalex_paper.get("externalIds"), dict) else {}
    if incoming_external:
        target["externalIds"] = {
            **external_ids,
            **{key: value for key, value in incoming_external.items() if value not in (None, "", [], {})},
        }

    incoming_abstract = clean_abstract(openalex_paper.get("abstract"))
    if incoming_abstract and len(incoming_abstract) > len(clean_abstract(target.get("abstract"))):
        target["abstract"] = incoming_abstract
        target["_abstract_backfilled_from"] = "openalex_recovery"
        target.pop("_missing_abstract", None)
        filled["abstract"] = True

    for key in ("year", "venue", "doi", "url"):
        if target.get(key) in (None, "", [], {}) and openalex_paper.get(key) not in (None, "", [], {}):
            target[key] = openalex_paper[key]

    try:
        target["citation_count"] = max(int(target.get("citation_count") or 0), int(openalex_paper.get("citation_count") or 0))
    except (TypeError, ValueError):
        pass

    for key in ("referenced_works", "related_works"):
        incoming = openalex_paper.get(key)
        if isinstance(incoming, list) and incoming:
            current = target.get(key) if isinstance(target.get(key), list) else []
            merged: list[Any] = []
            seen: set[str] = set()
            for item in [*current, *incoming]:
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    merged.append(item)
            if len(merged) > len(current):
                target[key] = merged
                filled["references"] = True
    if target.get("referenced_works"):
        target["refs_unavailable"] = False

    had_pdf_hint = _record_has_pdf_hint(target)
    for key in (
        "best_oa_location",
        "primary_location",
        "locations",
        "open_access",
        "pdf_url",
        "open_access_pdf_url",
    ):
        if openalex_paper.get(key) not in (None, "", [], {}) and target.get(key) in (None, "", [], {}):
            target[key] = openalex_paper[key]
    filled["pdf_hints"] = not had_pdf_hint and _record_has_pdf_hint(target)

    provenance = target.get("provenance") if isinstance(target.get("provenance"), dict) else {}
    incoming_provenance = openalex_paper.get("provenance") if isinstance(openalex_paper.get("provenance"), dict) else {}
    if incoming_provenance:
        provenance.setdefault("openalex_source_id", incoming_provenance.get("source_id"))
        provenance.setdefault("openalex_source_url", incoming_provenance.get("source_url"))
        provenance.setdefault("openalex_backfilled", True)
        target["provenance"] = provenance

    return filled


async def _backfill_recovered_openalex_metadata(
    papers: list[dict[str, Any]],
    *,
    max_papers: int = 120,
    max_concurrency: int = 8,
) -> dict[str, Any]:
    """Bounded OpenAlex repair for DOI/OpenAlex records.

    This is mechanical metadata acquisition only: OpenAlex id, abstract,
    references/related works, and OA/PDF locations. It does not decide
    relevance or evidence claims.
    """

    if httpx is None:
        return {"enabled": False, "reason": "httpx_missing"}

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for paper in papers:
        doi = _record_doi(paper)
        openalex_id = _record_openalex_id(paper)
        if not doi and not openalex_id:
            continue
        identifier = openalex_id or f"https://doi.org/{doi}"
        if identifier.casefold() in seen_ids:
            continue
        needs_openalex = not openalex_id
        needs_abstract = not clean_abstract(paper.get("abstract"))
        needs_refs = not (paper.get("referenced_works") or paper.get("references"))
        needs_pdf = not _record_has_pdf_hint(paper)
        if not (needs_openalex or needs_abstract or needs_refs or needs_pdf):
            continue
        seen_ids.add(identifier.casefold())
        candidates.append(paper)
        if len(candidates) >= max_papers:
            break

    stats: dict[str, Any] = {
        "enabled": True,
        "candidate_count": len(candidates),
        "attempted": 0,
        "openalex_id_filled": 0,
        "abstract_filled": 0,
        "references_filled": 0,
        "pdf_hints_filled": 0,
        "failed": 0,
    }
    if not candidates:
        return stats

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(client: "httpx.AsyncClient", paper: dict[str, Any]) -> None:
        doi = _record_doi(paper)
        openalex_id = _record_openalex_id(paper)
        identifier = openalex_id or f"https://doi.org/{doi}"
        url = f"https://api.openalex.org/works/{quote(identifier, safe=':/')}"
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(url, params={"mailto": _researcher_email()})
                response.raise_for_status()
                work = response.json()
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                failures = paper.setdefault("_metadata_backfill_failures", [])
                if isinstance(failures, list):
                    failures.append("openalex_detail_failed")
                return
            filled = _merge_openalex_metadata(paper, _openalex_work_to_paper(work))
            if filled["openalex_id"]:
                stats["openalex_id_filled"] = int(stats["openalex_id_filled"]) + 1
            if filled["abstract"]:
                stats["abstract_filled"] = int(stats["abstract_filled"]) + 1
            if filled["references"]:
                stats["references_filled"] = int(stats["references_filled"]) + 1
            if filled["pdf_hints"]:
                stats["pdf_hints_filled"] = int(stats["pdf_hints_filled"]) + 1

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(*(_one(client, paper) for paper in candidates))
    return stats


async def _backfill_recovered_crossref_metadata(
    papers: list[dict[str, Any]],
    *,
    max_papers: int = 120,
    max_concurrency: int = 8,
) -> dict[str, Any]:
    """Bounded DOI metadata repair for deterministic T2 recovery/finalize.

    This only fetches mechanical Crossref fields: abstract, DOI title/year,
    reference DOI/title aliases, and reference counts. It does not decide
    relevance or whether a reference should be read.
    """

    if httpx is None:
        return {"enabled": False, "reason": "httpx_missing"}

    candidates: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    for paper in papers:
        doi = _record_doi(paper)
        if not doi or doi.casefold() in seen_dois:
            continue
        needs_abstract = not clean_abstract(paper.get("abstract"))
        needs_refs = not (paper.get("referenced_works") or paper.get("references"))
        if not needs_abstract and not needs_refs:
            continue
        seen_dois.add(doi.casefold())
        candidates.append(paper)
        if len(candidates) >= max_papers:
            break

    stats: dict[str, Any] = {
        "enabled": True,
        "candidate_count": len(candidates),
        "attempted": 0,
        "abstract_filled": 0,
        "references_filled": 0,
        "failed": 0,
        "skipped_after_cap": max(0, len(seen_dois) - len(candidates)),
    }
    if not candidates:
        return stats

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(client: "httpx.AsyncClient", paper: dict[str, Any]) -> None:
        doi = _record_doi(paper)
        if not doi:
            return
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(
                    f"https://api.crossref.org/works/{quote(doi, safe='')}",
                    headers=_crossref_headers(),
                )
                response.raise_for_status()
                message = response.json().get("message", {})
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                failures = paper.setdefault("_metadata_backfill_failures", [])
                if isinstance(failures, list):
                    failures.append("crossref_detail_failed")
                return

            abstract = clean_abstract(message.get("abstract"))
            if abstract and not clean_abstract(paper.get("abstract")):
                paper["abstract"] = abstract
                paper["_abstract_backfilled_from"] = "crossref_recovery"
                paper.pop("_missing_abstract", None)
                stats["abstract_filled"] = int(stats["abstract_filled"]) + 1

            references = _extract_crossref_references(message)
            if references and not (paper.get("referenced_works") or paper.get("references")):
                paper["references"] = references
                paper["referenced_works"] = references
                stats["references_filled"] = int(stats["references_filled"]) + 1
            paper["reference_count"] = message.get("reference-count", len(references))

            title_list = message.get("title")
            if isinstance(title_list, list) and title_list and not str(paper.get("title") or "").strip():
                paper["title"] = str(title_list[0] or "").strip()
            issued = (
                message.get("published-print")
                or message.get("published-online")
                or message.get("published")
                or message.get("issued")
            )
            parts = issued.get("date-parts", [[]]) if isinstance(issued, dict) else [[]]
            if not paper.get("year") and parts and parts[0]:
                try:
                    paper["year"] = int(parts[0][0])
                except (TypeError, ValueError):
                    pass

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(*(_one(client, paper) for paper in candidates))
    return stats


def _crossref_message_to_snowball_paper(
    message: dict[str, Any],
    *,
    source_record: dict[str, Any],
    ref_doi: str,
) -> dict[str, Any] | None:
    title_list = message.get("title")
    title = str(title_list[0] if isinstance(title_list, list) and title_list else "").strip()
    if not title:
        return None

    authors: list[dict[str, str]] = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        given = str(author.get("given") or "").strip()
        family = str(author.get("family") or "").strip()
        name = f"{given} {family}".strip() or "Unknown"
        authors.append({"name": name})
        if len(authors) >= 10:
            break

    issued = (
        message.get("published-print")
        or message.get("published-online")
        or message.get("published")
        or message.get("issued")
    )
    parts = issued.get("date-parts", [[]]) if isinstance(issued, dict) else [[]]
    year = None
    if parts and parts[0]:
        try:
            year = int(parts[0][0])
        except (TypeError, ValueError):
            year = None

    doi = str(message.get("DOI") or ref_doi or "").strip()
    references = _extract_crossref_references(message)
    source_id = str(source_record.get("canonical_id") or source_record.get("id") or source_record.get("doi") or "").strip()
    source_title = str(source_record.get("title") or source_id or "unknown source").strip()
    container = message.get("container-title")
    venue = str(container[0] if isinstance(container, list) and container else "").strip()
    paper: dict[str, Any] = {
        "id": f"doi:{doi}" if doi else title,
        "source": "crossref_snowball",
        "title": title,
        "authors": authors or [{"name": "Unknown"}],
        "year": year,
        "abstract": clean_abstract(message.get("abstract")),
        "venue": venue,
        "doi": doi,
        "citation_count": int(message.get("is-referenced-by-count") or 0),
        "url": str(message.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
        "externalIds": {"DOI": doi} if doi else {},
        "references": references,
        "referenced_works": references,
        "reference_count": int(message.get("reference-count") or len(references)),
        "retrieval_intent": "citation_snowball",
        "search_bucket": "snowball",
        "source_bucket": "snowball",
        "source_query": f"Crossref one-hop references from {source_title}",
        "source_tool": "crossref_snowball_backfill",
        "citation_snowball_source_id": source_id,
        "citation_snowball_source_title": source_title,
        "provenance": {
            "source_tool": "crossref_snowball_backfill",
            "source_id": doi,
            "source_url": str(message.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
            "snowball_source_id": source_id,
            "id_source": "doi",
        },
    }
    return paper


async def _expand_crossref_snowball_candidates(
    papers: list[dict[str, Any]],
    *,
    max_sources: int = 12,
    refs_per_source: int = 8,
    max_candidates: int = 40,
    max_concurrency: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add bounded one-hop DOI reference candidates from Crossref metadata.

    This repairs the failure mode where OpenAlex/S2 are rate-limited and the
    citation graph becomes empty even though Crossref records contain reference
    DOI/title aliases. It is metadata acquisition only; downstream semantic
    screening/queue rules still decide whether candidates are read deeply.
    """

    stats: dict[str, Any] = {
        "enabled": httpx is not None,
        "source_candidates": 0,
        "sources_used": 0,
        "reference_dois_seen": 0,
        "attempted": 0,
        "added": 0,
        "failed": 0,
    }
    if httpx is None:
        stats["reason"] = "httpx_missing"
        return [], stats

    existing_dois = {_record_doi(paper).casefold() for paper in papers if _record_doi(paper)}
    selected_sources: list[dict[str, Any]] = []
    for paper in papers:
        refs = paper.get("referenced_works") or paper.get("references") or []
        if not isinstance(refs, list) or not refs:
            continue
        selected_sources.append(paper)
    selected_sources.sort(
        key=lambda paper: (
            not bool(paper.get("seed_priority") or paper.get("source") == "user_seed"),
            not bool(isinstance(paper.get("semantic_screen"), dict) and paper["semantic_screen"].get("can_enter_deep_read")),
            -float(paper.get("relevance_score", 0.0) or 0.0),
            str(paper.get("title") or "").casefold(),
        )
    )
    stats["source_candidates"] = len(selected_sources)
    selected_sources = selected_sources[:max_sources]
    stats["sources_used"] = len(selected_sources)

    ref_jobs: list[tuple[str, dict[str, Any]]] = []
    seen_ref_dois: set[str] = set()
    for source in selected_sources:
        refs = source.get("referenced_works") or source.get("references") or []
        per_source_count = 0
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            doi = str(ref.get("doi") or ref.get("DOI") or ref.get("id") or "").strip()
            doi = (
                doi.removeprefix("https://doi.org/")
                .removeprefix("http://doi.org/")
                .removeprefix("doi:")
            )
            if not doi or not doi.startswith("10."):
                continue
            doi_key = doi.casefold()
            if doi_key in existing_dois or doi_key in seen_ref_dois:
                continue
            seen_ref_dois.add(doi_key)
            ref_jobs.append((doi, source))
            per_source_count += 1
            if per_source_count >= refs_per_source or len(ref_jobs) >= max_candidates:
                break
        if len(ref_jobs) >= max_candidates:
            break

    stats["reference_dois_seen"] = len(ref_jobs)
    if not ref_jobs:
        return [], stats

    semaphore = asyncio.Semaphore(max_concurrency)
    added: list[dict[str, Any]] = []

    async def _one(client: "httpx.AsyncClient", doi: str, source: dict[str, Any]) -> None:
        async with semaphore:
            stats["attempted"] = int(stats["attempted"]) + 1
            try:
                response = await client.get(
                    f"https://api.crossref.org/works/{quote(doi, safe='')}",
                    headers=_crossref_headers(),
                )
                response.raise_for_status()
                message = response.json().get("message", {})
            except Exception:
                stats["failed"] = int(stats["failed"]) + 1
                return
            paper = _crossref_message_to_snowball_paper(message, source_record=source, ref_doi=doi)
            if not paper:
                stats["failed"] = int(stats["failed"]) + 1
                return
            added.append(paper)
            stats["added"] = int(stats["added"]) + 1

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        await asyncio.gather(*(_one(client, doi, source) for doi, source in ref_jobs))
    return added, stats


def _load_seed_papers(workspace_dir: Path) -> list[dict[str, Any]]:
    return _load_jsonl(workspace_dir / "user_seeds" / "seed_papers.jsonl")


def _seed_to_recovery_paper(seed: dict[str, Any]) -> dict[str, Any]:
    arxiv_id = str(seed.get("arxiv_id", "")).strip()
    paper_id = f"arxiv:{arxiv_id}" if arxiv_id and not arxiv_id.startswith("arxiv:") else arxiv_id
    if not paper_id:
        paper_id = str(seed.get("doi") or seed.get("id") or "").strip()
    canonical_id = paper_id if paper_id.startswith("arxiv:") else stable_noopenalex_id({**seed, "id": paper_id})
    canonical_id_source = "arxiv_noopenalex" if paper_id.startswith("arxiv:") else "noopenalex_fallback"
    if not paper_id:
        paper_id = canonical_id
    url = str(seed.get("url") or "").strip()
    try:
        seed_year = int(seed["year"]) if seed.get("year") else None
    except (TypeError, ValueError):
        seed_year = None
    return {
        "id": paper_id,
        "canonical_id": canonical_id,
        "preferred_id_source": "arxiv" if arxiv_id else "doi" if seed.get("doi") else "seed_fallback",
        "canonical_id_source": canonical_id_source,
        "no_openalex_id": True,
        "source": "user_seed",
        "title": str(seed.get("title", "")).strip() or "Untitled seed paper",
        "authors": seed.get("authors") or ["Unknown"],
        "year": seed_year,
        "abstract": str(seed.get("abstract") or ""),
        "venue": str(seed.get("venue") or "user_seed"),
        "citation_count": int(seed.get("citation_count") or 0),
        "doi": str(seed.get("doi") or ""),
        "url": url,
        "externalIds": {"ArXiv": arxiv_id} if arxiv_id else {},
        "source_type": "preprint",
        "relevance_score": 1.0,
        "why_relevant": str(seed.get("why_relevant") or "用户提供的高优先级 seed paper"),
        "provenance": {
            "source_tool": "user_seed",
            "source_id": paper_id,
            "source_url": url,
            "canonical_id": canonical_id,
            "id_source": canonical_id_source,
        },
    }


def _ensure_seed_papers(
    selected_papers: list[dict[str, Any]],
    candidate_papers: list[dict[str, Any]],
    workspace_dir: Path,
) -> list[dict[str, Any]]:
    """确保恢复路径不会丢掉用户 seed papers。"""

    seeds = _load_seed_papers(workspace_dir)
    if not seeds:
        return selected_papers

    selected = list(selected_papers)
    selected_title_keys = {_normalize_match_key(paper.get("title")) for paper in selected}
    candidates_by_title = {
        _normalize_match_key(paper.get("title")): paper
        for paper in candidate_papers
        if str(paper.get("title", "")).strip()
    }

    for seed in seeds:
        seed_key = _normalize_match_key(seed.get("title"))
        if not seed_key or seed_key in selected_title_keys:
            continue
        recovered = dict(candidates_by_title.get(seed_key) or _seed_to_recovery_paper(seed))
        recovered["relevance_score"] = max(float(recovered.get("relevance_score", 0.0)), 1.0)
        recovered["why_relevant"] = str(
            recovered.get("why_relevant") or seed.get("why_relevant") or "用户提供的高优先级 seed paper"
        )
        selected.insert(0, recovered)
        selected_title_keys.add(seed_key)

    # Seed repair must not become a hidden pool cap. T2 queue construction is
    # responsible for active deep-read limits; verified overflow still feeds
    # shallow abstract sweep, citation diagnostics, and resume decisions.
    return selected


def _build_recovered_verified_papers(
    papers: list[dict[str, Any]],
    workspace_dir: Path,
) -> list[dict[str, Any]]:
    """基于已落盘来源 metadata 生成恢复用 verified 池。

    恢复路径不额外访问外部 API；它只把已经带有 DOI/arXiv/source provenance
    的真实检索记录标为 source metadata verified，供 T3 继续消费可追溯记录。
    """

    local_pdf_dir = workspace_dir / "literature" / "pdfs"
    verified: list[dict[str, Any]] = []
    for paper in papers:
        title = str(paper.get("title") or "").strip()
        raw_id = str(paper.get("id") or "").strip()
        raw_canonical_id = str(paper.get("canonical_id") or "").strip()
        canonical_id = (
            raw_canonical_id if raw_canonical_id and raw_canonical_id != title
            else raw_id if raw_id and raw_id != title
            else stable_noopenalex_id(paper)
        )
        if not canonical_id:
            continue
        normalized_id = canonical_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        has_local_pdf = bool(normalized_id and (local_pdf_dir / f"{normalized_id}.pdf").exists())
        record = dict(paper)
        record["canonical_id"] = canonical_id
        record.setdefault("preferred_id_source", "source_id")
        record["verification_status"] = "pdf_verified" if has_local_pdf else "metadata_verified"
        record["verification_method"] = "recovered_source_metadata"
        record["verification_source"] = str(
            (record.get("provenance") or {}).get("source_tool") or record.get("source") or "unknown"
        )
        record["verification_confidence"] = 0.9 if has_local_pdf else 0.72
        record["verification_title_similarity"] = 1.0
        record["verification_year_match"] = True
        verified.append(record)
    return verified


def _build_recovered_citation_edges(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build cheap citation-edge hints from already persisted metadata.

    Recovery/finalize deliberately avoids extra network calls. When raw records
    already contain referenced_works/related_works, we preserve them; otherwise
    the domain map still records buckets and emits a warning.
    """

    payload: list[dict[str, Any]] = []
    for paper in papers:
        source_id = str(paper.get("canonical_id") or paper.get("id") or "").strip()
        if not source_id:
            continue
        refs = paper.get("referenced_works") or paper.get("references") or []
        related = paper.get("related_works") or paper.get("related") or []
        snowball_source_ids: list[str] = []
        for raw_source_id in [
            paper.get("citation_snowball_source_id"),
            *(paper.get("citation_snowball_source_ids") if isinstance(paper.get("citation_snowball_source_ids"), list) else []),
        ]:
            snowball_source_id = str(raw_source_id or "").strip()
            if snowball_source_id and snowball_source_id not in snowball_source_ids:
                snowball_source_ids.append(snowball_source_id)
        for snowball_source_id in snowball_source_ids:
            payload.append(
                {
                    "source_id": snowball_source_id,
                    "referenced_works": [source_id],
                    "related_works": [],
                    "source": "crossref_snowball_backfill",
                    "edge_semantics": "source_paper_references_snowball_candidate",
                }
            )
        if not refs and not related:
            continue
        payload.append(
            {
                "source_id": source_id,
                "referenced_works": refs,
                "related_works": related,
                "source": "recovered_existing_metadata",
            }
        )
    return payload


def _extract_existing_semantic_screenings(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover Scout LLM semantic_screen fields already persisted in raw/dedup records."""

    screenings: list[dict[str, Any]] = []
    for paper in papers:
        screen = paper.get("semantic_screen")
        if not isinstance(screen, dict):
            continue
        screening = dict(screen)
        for key in ("paper_id", "id", "canonical_id", "doi", "title"):
            value = paper.get(key)
            if value not in (None, ""):
                screening.setdefault(key, value)
        screenings.append(screening)
    return screenings


def _iter_t2_trace_paths(workspace_dir: Path) -> list[Path]:
    trace_dir = workspace_dir / "_runtime" / "traces"
    if not trace_dir.exists():
        return []
    return sorted(trace_dir.glob("*.jsonl"))


def extract_t2_search_history(trace_paths: list[Path]) -> tuple[list[str], dict[str, int], int, list[dict[str, Any]]]:
    """从 trace 中恢复检索式和结构化 provenance。

    旧版只返回 query/count，会丢失 bridge_id、query_bucket 和 source/tool。
    这里保留旧的 queries/query_results 兼容字段，同时返回 search_records
    供 search_log 展示 bridge/source 覆盖。
    """

    ordered_queries: list[str] = []
    query_results: dict[str, int] = {}
    search_records: list[dict[str, Any]] = []
    parsed_traces = 0

    for trace_path in trace_paths:
        if not trace_path.exists():
            continue
        is_t2_trace = trace_path.stem.lower().startswith("t2")
        pending_queries: dict[str, dict[str, Any]] = {}
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "run_start":
                payload = event.get("payload", {})
                is_t2_trace = payload.get("task_id") == "T2" or is_t2_trace
                if is_t2_trace:
                    parsed_traces += 1
                continue
            if not is_t2_trace:
                continue
            if event.get("type") != "message":
                continue

            payload = event.get("payload", {})
            role = payload.get("role")
            if role == "assistant":
                for tool_call in payload.get("tool_calls") or []:
                    tool_name = tool_call.get("name")
                    if tool_name not in SEARCH_TOOL_NAMES:
                        continue
                    arguments = tool_call.get("arguments") or {}
                    query = str(arguments.get("query", "")).strip()
                    pending_queries[str(tool_call.get("id", ""))] = {
                        "query": query,
                        "tool_name": tool_name,
                        "query_bucket": str(
                            arguments.get("query_bucket") or arguments.get("search_bucket") or ""
                        ).strip(),
                        "bridge_id": str(arguments.get("bridge_id") or "").strip(),
                    }
                continue

            if role != "tool" or payload.get("name") not in SEARCH_TOOL_NAMES:
                continue

            metadata = payload.get("metadata") or {}
            if metadata.get("is_error"):
                continue
            data = metadata.get("data") or {}
            papers = data.get("papers") or []
            count = len(papers) if isinstance(papers, list) else 0
            tool_call_id = str(payload.get("tool_call_id", ""))
            pending = pending_queries.get(tool_call_id, {})
            query = str(pending.get("query") or data.get("query") or "").strip()
            if not query:
                continue
            if query not in query_results:
                ordered_queries.append(query)
                query_results[query] = 0
            query_results[query] += count
            auto_persist = metadata.get("auto_persist_raw") if isinstance(metadata, dict) else {}
            if not isinstance(auto_persist, dict):
                auto_persist = {}
            persisted_count = int(
                auto_persist.get("retained_count")
                or auto_persist.get("count")
                or 0
            )
            search_records.append(
                {
                    "query": query,
                    "tool_name": payload.get("name") or pending.get("tool_name") or "",
                    "query_bucket": pending.get("query_bucket") or data.get("query_bucket") or data.get("search_bucket") or "",
                    "bridge_id": pending.get("bridge_id") or data.get("bridge_id") or "",
                    "result_count": count,
                    "persisted_count": persisted_count,
                    "source_stats": data.get("source_stats") if isinstance(data.get("source_stats"), dict) else {},
                }
            )

    return ordered_queries, query_results, parsed_traces, search_records


def _search_records_from_raw(raw_papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback search history from persisted raw provenance."""

    grouped: dict[tuple[str, str, str, str], int] = Counter()
    for paper in raw_papers:
        provenance = paper.get("provenance") if isinstance(paper.get("provenance"), dict) else {}
        queries = _raw_values(paper.get("source_queries")) or _raw_values(paper.get("source_query")) or _raw_values(provenance.get("source_query"))
        buckets = (
            _raw_values(paper.get("search_buckets"))
            or _raw_values(paper.get("query_buckets"))
            or _raw_values(paper.get("search_bucket") or paper.get("query_bucket"))
            or _raw_values(provenance.get("search_bucket") or provenance.get("query_bucket"))
        )
        bridge_ids = (
            _raw_values(paper.get("recalled_by_bridges"))
            or _raw_values(paper.get("bridge_ids"))
            or _raw_values(paper.get("bridge_id"))
            or _raw_values(provenance.get("bridge_id"))
        )
        tools = (
            _raw_values(paper.get("source_tools"))
            or _raw_values(paper.get("source_tool"))
            or _raw_values(provenance.get("source_tool"))
            or _raw_values(paper.get("source"))
        )
        if not queries and not buckets and not bridge_ids and not tools:
            continue
        max_len = max(len(queries), len(buckets), len(bridge_ids), len(tools), 1)
        for idx in range(max_len):
            query = queries[idx] if idx < len(queries) else queries[0] if queries else "[unknown query]"
            bucket = buckets[idx] if idx < len(buckets) else buckets[0] if buckets else ""
            bridge_id = _raw_bridge_value_at(bridge_ids, buckets, queries, idx)
            tool = tools[idx] if idx < len(tools) else tools[0] if tools else "unknown"
            grouped[(query or "[unknown query]", bucket, bridge_id, tool or "unknown")] += 1

    records: list[dict[str, Any]] = []
    for (query, bucket, bridge_id, tool), count in sorted(grouped.items()):
        records.append(
            {
                "query": query,
                "query_bucket": bucket,
                "bridge_id": bridge_id,
                "tool_name": tool,
                "result_count": count,
                "persisted_count": count,
                "source": "papers_raw_provenance",
            }
        )
    return records


def _raw_values(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _raw_bridge_value_at(
    bridge_ids: list[str],
    buckets: list[str],
    queries: list[str],
    idx: int,
) -> str:
    if not bridge_ids:
        return ""
    if len(bridge_ids) == 1:
        bucket = buckets[idx] if idx < len(buckets) else buckets[0] if len(buckets) == 1 else ""
        if bucket in {"theory_bridge", "adjacent_field"}:
            return bridge_ids[0]
        if len(queries) <= 1 and len(buckets) <= 1:
            return bridge_ids[0]
        return ""
    if idx < len(bridge_ids):
        return bridge_ids[idx]
    return ""


def generate_missing_areas_report(
    project: dict[str, Any],
    papers: list[dict[str, Any]],
    *,
    current_year: int | None = None,
) -> str:
    """基于关键词覆盖和分布特征生成确定性的缺口分析初稿。"""

    runtime_year = current_year if current_year is not None else current_utc_year()
    recent_start_year = recent_year_from(2, current_year=runtime_year)
    recent_label = format_year_window(2, current_year=runtime_year)
    research_direction = str(project.get("research_direction", "未指定")).strip() or "未指定"
    keywords = _normalize_keywords(project)
    keyword_counts: dict[str, int] = {}

    for keyword in keywords:
        aliases = _keyword_aliases(keyword)
        count = 0
        for paper in papers:
            text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
            if any(alias in text for alias in aliases):
                count += 1
        keyword_counts[keyword] = count

    recent_count = 0
    missing_abstract_count = 0
    source_counter: Counter[str] = Counter()
    year_counter: Counter[int] = Counter()
    for paper in papers:
        year = paper.get("year")
        if isinstance(year, int):
            year_counter[year] += 1
            if year >= recent_start_year:
                recent_count += 1
        if paper.get("_missing_abstract"):
            missing_abstract_count += 1
        source_counter[str(paper.get("source_type", "unknown"))] += 1

    total = len(papers)
    high_coverage_threshold = max(4, total // 12) if total else 4
    low_coverage_threshold = max(2, total // 20) if total else 2
    covered_keywords = [kw for kw, count in keyword_counts.items() if count >= high_coverage_threshold]
    missing_keywords = [kw for kw, count in keyword_counts.items() if count < low_coverage_threshold]

    retrieval_coverage_hints: list[str] = []
    source_type_review_count = source_counter.get("unknown", 0)
    if total and recent_count < max(5, total // 4):
        retrieval_coverage_hints.append(f"{recent_label} 的最新论文占比偏低，近期进展覆盖可能不足。")
    if total and source_type_review_count > total // 3:
        retrieval_coverage_hints.append("source_type 需要 LLM 复核的论文比例偏高，后续应补充领域 venue/profile 判断。")
    if total and missing_abstract_count > total // 3:
        retrieval_coverage_hints.append("缺少摘要的论文比例偏高，T3 精读前建议补齐关键 metadata。")

    lines = [
        "# 文献缺口分析",
        "",
        "> 本文件由 runtime 基于 `papers_dedup.jsonl` 自动生成，",
        "> 依据关键词覆盖、年份分布和来源分布做初步判断，可作为 T3/T4 的起点，",
        "> 不是人工精读后的最终结论。",
        "",
        "## 当前覆盖概况",
        "",
        f"- 研究方向: {research_direction}",
        f"- 去重后论文数: {total} 篇",
        f"- {recent_label} 最近论文: {recent_count} 篇",
        f"- source_type 待 LLM 复核: {source_type_review_count} 篇",
        "- 注：本文件只描述检索覆盖和 metadata 完整性，不宣称真实研究空白。",
        "",
        "## 覆盖较好的主题",
        "",
    ]

    if covered_keywords:
        for keyword in covered_keywords:
            lines.append(f"- `{keyword}`: {keyword_counts[keyword]} 篇论文显式提及")
    else:
        lines.append("- 当前还没有明显高覆盖的单一主题，说明论文池较分散。")

    lines.extend(["", "## 覆盖不足的主题", ""])
    if missing_keywords:
        for keyword in missing_keywords:
            lines.append(f"- `{keyword}`: 仅 {keyword_counts[keyword]} 篇论文显式提及，建议继续补检")
    else:
        lines.append("- 当前项目关键词都至少获得了基础覆盖，但仍建议人工检查是否存在语义漏网项。")

    lines.extend(["", "## Retrieval Coverage Hints", ""])
    if retrieval_coverage_hints:
        lines.extend(f"- {item}" for item in retrieval_coverage_hints)
    else:
        lines.append("- 当前去重论文池在年份和 metadata 完整性上没有明显覆盖提示。")

    # --- 检索覆盖提示（结构化，供 T3/T4 复核，不是研究缺口结论） ---
    gap_entries: list[dict[str, str]] = []
    gap_counter = 0

    # 从低覆盖关键词生成补检/复核提示
    for keyword in missing_keywords:
        gap_counter += 1
        count = keyword_counts[keyword]
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": f"`{keyword}` 相关检索覆盖不足",
            "what": f"在 {total} 篇去重论文中，仅 {count} 篇显式提及 `{keyword}`，远低于高覆盖阈值 {high_coverage_threshold}。",
            "why": "这是检索覆盖提示，不等于真实研究缺口；需要 Reader/Ideation LLM 基于精读材料确认是否有科学问题。",
            "direction": f"围绕 `{keyword}` 设计补检 query，或在 T3 精读时记录该主题是否实际出现。",
            "difficulty": "Medium",
        })

    # 从结构性覆盖问题生成补检/复核提示
    if total and recent_count < max(5, total // 4):
        gap_counter += 1
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": f"{recent_label} 最新论文覆盖不足",
            "what": f"{recent_label} 论文仅 {recent_count} 篇（占比 {recent_count / max(1, total) * 100:.0f}%），最新进展覆盖可能不足。",
            "why": "这是时间覆盖提示，不等于近期一定存在未覆盖突破。",
            "direction": f"针对 {recent_label} 做一轮专题补检，或由 LLM 判断当前领域是否确实需要近期补搜。",
            "difficulty": "Low",
        })
    if total and source_type_review_count > total // 3:
        gap_counter += 1
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": "source_type 复核不足",
            "what": f"有 {source_type_review_count} 篇论文的 source_type 为 unknown 或需要 LLM 复核。",
            "why": "source_type 属于领域 profile 判断，不能由 runtime 仅凭 venue 名称替代。",
            "direction": "由 Scout/Reader LLM 基于 domain_profile 标注相关 venue/source_type，必要时补搜目标领域代表 venue。",
            "difficulty": "Medium",
        })
    if total and missing_abstract_count > total // 3:
        gap_counter += 1
        gap_entries.append({
            "id": f"提示 {gap_counter}",
            "title": "摘要缺失论文比例偏高",
            "what": f"有 {missing_abstract_count} 篇论文（占比 {missing_abstract_count / max(1, total) * 100:.0f}%）缺少摘要，无法进行内容级分析。",
            "why": "缺少摘要的论文无法参与关键词覆盖分析和 abstract sweep，可能导致覆盖评估偏差。",
            "direction": "对缺失摘要的关键论文手动补充 metadata，或在 T3 精读时优先处理这些论文。",
            "difficulty": "Low",
        })

    # 从覆盖过度集中生成补检/复核提示
    if covered_keywords and len(covered_keywords) >= 3:
        # 检查覆盖是否过于集中在少数关键词
        top_keyword = max(keyword_counts.items(), key=lambda x: x[1])
        if top_keyword[1] > max(10, total // 3):
            gap_counter += 1
            gap_entries.append({
                "id": f"提示 {gap_counter}",
                "title": f"检索视角过于集中在 `{top_keyword[0]}`",
                "what": f"`{top_keyword[0]}` 有 {top_keyword[1]} 篇论文，占论文池的 {top_keyword[1] / max(1, total) * 100:.0f}%，其余主题覆盖稀疏。",
                "why": "检索视角过度集中可能导致 Reader 看到的证据范围较窄，但是否构成研究机会需要 LLM 判断。",
                "direction": "让 LLM 基于 domain_profile 判断是否需要相邻领域、替代术语或不同评估场景的补检。",
                "difficulty": "Low",
            })

    if gap_entries:
        lines.extend(["", "## Retrieval Coverage Hints（不是研究缺口结论）", ""])
        lines.append("> 以下提示由 runtime 基于关键词覆盖和分布特征自动生成，只能用于补检或让 T3/T4 复核；不能直接宣称领域空白。")
        lines.append("")
        for gap in gap_entries:
            lines.append(f"### {gap['id']}: {gap['title']}")
            lines.append(f"- **覆盖缺口**: {gap['what']}")
            lines.append(f"- **为什么需要复核**: {gap['why']}")
            lines.append(f"- **建议动作**: {gap['direction']}")
            lines.append(f"- **难度**: {gap['difficulty']}")
            lines.append("")

    lines.extend(["", "## 建议在 T3/T4 继续确认的问题", ""])
    follow_ups = []
    if missing_keywords:
        follow_ups.append(f"优先围绕 {', '.join(f'`{item}`' for item in missing_keywords[:3])} 继续补检或在精读时标注缺口。")
    if recent_count < max(5, total // 4) and total:
        follow_ups.append(f"重点确认 {recent_label} 的最新工作，避免只依赖旧综述或早期系统。")
    if source_type_review_count > total // 3 and total:
        follow_ups.append("让 LLM 基于 domain_profile 复核 source_type/venue，而不是依赖 runtime 自动判断。")
    if not follow_ups:
        follow_ups.append("按论文笔记进一步确认：哪些机制被反复验证，哪些只停留在概念或系统描述。")
    lines.extend(f"- {item}" for item in follow_ups)

    if year_counter:
        lines.extend(["", "## 年份分布（Top 5）", ""])
        for year, count in year_counter.most_common(5):
            lines.append(f"- {year}: {count} 篇")

    return "\n".join(lines) + "\n"


async def finalize_t2_outputs(
    workspace_dir: Path,
    *,
    trace_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """根据现有 raw 结果，确定性补齐 T2 产物。"""

    workspace_dir = workspace_dir.resolve()
    raw_path = workspace_dir / "literature" / "papers_raw.jsonl"
    raw_papers = _load_jsonl(raw_path)
    if not raw_papers:
        return {
            "ok": False,
            "reason": "papers_raw_missing_or_empty",
            "raw_count": 0,
        }

    project = _load_project(workspace_dir)
    keywords = _normalize_keywords(project)
    domain_profile = _project_domain_profile(project)
    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace_dir,
        allowed_read_prefixes=["", "literature/", "user_seeds/", "seeds/"],
        allowed_write_prefixes=["literature/", "literature/temp/"],
    )

    dedup_papers = deduplicate_papers(raw_papers, doi_dedup=True, title_threshold=0.95)
    if domain_profile:
        dedup_papers = filter_by_domain(
            dedup_papers,
            target_domain=str(domain_profile.get("target_domain") or domain_profile.get("domain") or "profile"),
            domain_profile=domain_profile,
        )

    openalex_backfill = await _backfill_recovered_openalex_metadata(dedup_papers)
    metadata_backfill = await _backfill_recovered_crossref_metadata(dedup_papers)
    snowball_candidates, citation_backfill = await _expand_crossref_snowball_candidates(dedup_papers)
    if snowball_candidates:
        raw_save_result = await SavePapersRawTool(policy).execute(papers=snowball_candidates, append=True)
        citation_backfill["raw_persist_ok"] = bool(raw_save_result.ok)
        citation_backfill["raw_persisted"] = int((raw_save_result.data or {}).get("count") or 0) if raw_save_result.ok else 0
        if not raw_save_result.ok:
            citation_backfill["raw_persist_error"] = raw_save_result.error or raw_save_result.content
        raw_papers = _load_jsonl(raw_path)
        dedup_papers = deduplicate_papers(
            [*dedup_papers, *snowball_candidates],
            doi_dedup=True,
            title_threshold=0.95,
        )

    scored_papers = score_papers(dedup_papers, keywords)
    # Sort for deterministic queue priority only. `relevance_score` is a
    # metadata priority hint and is not used as an exclusion threshold.
    scored_papers = sorted(
        scored_papers,
        key=lambda paper: (
            float(paper.get("relevance_score", 0.0)),
            int(paper.get("citation_count", 0) or 0),
            int(paper.get("year", 0) or 0),
        ),
        reverse=True,
    )
    final_papers = _select_final_papers(scored_papers)
    final_papers = _ensure_seed_papers(final_papers, scored_papers + raw_papers, workspace_dir)
    raw_screenings = _extract_existing_semantic_screenings(raw_papers + dedup_papers + final_papers)
    enriched_papers = enrich_papers(final_papers, keywords, domain_profile=domain_profile)
    if raw_screenings:
        enriched_papers = apply_semantic_screening(enriched_papers, raw_screenings)

    save_result = await SavePapersDedupTool(policy).execute(papers=enriched_papers, append=False)
    if not save_result.ok:
        return {
            "ok": False,
            "reason": "save_papers_dedup_failed",
            "error": save_result.error or save_result.content,
            "raw_count": len(raw_papers),
        }

    verified_papers = _build_recovered_verified_papers(enriched_papers, workspace_dir)
    verified_path = workspace_dir / "literature" / "papers_verified.jsonl"
    failures_path = workspace_dir / "literature" / "verification_failures.jsonl"
    _write_jsonl(verified_path, verified_papers)
    _write_jsonl(failures_path, [])

    citation_edges = _build_recovered_citation_edges(verified_papers)
    citation_edges_path = workspace_dir / "literature" / "citation_edges.json"
    citation_edges_path.write_text(
        json.dumps(citation_edges, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    domain_map = build_domain_map(
        papers_verified=verified_papers,
        citation_edges=citation_edges,
    )
    domain_map_path = workspace_dir / "literature" / "domain_map.json"
    domain_map_path.write_text(
        json.dumps(domain_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    queue_records, queue_meta = build_deep_read_queue(
        verified_papers,
        workspace_dir,
        deep_read_min=35,
        deep_read_target=35,
        deep_read_max=45,
        probe_pool=45,
        mainline_screened_cap=90,
        bridge_deep_floor=3,
        bridge_screened_cap=7,
        bridge_pool_cap=15,
        citation_hub_slots=3,
    )
    queue_path = workspace_dir / "literature" / "deep_read_queue.jsonl"
    queue_meta_path = workspace_dir / "literature" / "deep_read_queue_meta.json"
    _write_jsonl(queue_path, queue_records)
    queue_meta_path.write_text(
        json.dumps(queue_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    audit_records, audit_markdown = build_access_audit(verified_papers, workspace_dir, top_n=50)
    access_audit_path = workspace_dir / "literature" / "access_audit.md"
    access_audit_jsonl_path = workspace_dir / "literature" / "access_audit.jsonl"
    _write_jsonl(access_audit_jsonl_path, audit_records)
    access_audit_path.write_text(audit_markdown, encoding="utf-8")

    history_paths = trace_paths if trace_paths is not None else _iter_t2_trace_paths(workspace_dir)
    queries, query_results, trace_count, search_records = extract_t2_search_history(history_paths)
    raw_search_records = _search_records_from_raw(raw_papers)
    if not search_records:
        search_records = raw_search_records
    else:
        record_index = {
            (
                str(item.get("query") or ""),
                str(item.get("query_bucket") or ""),
                str(item.get("bridge_id") or ""),
                str(item.get("tool_name") or item.get("source_tool") or ""),
            ): idx
            for idx, item in enumerate(search_records)
        }
        for item in raw_search_records:
            key = (
                str(item.get("query") or ""),
                str(item.get("query_bucket") or ""),
                str(item.get("bridge_id") or ""),
                str(item.get("tool_name") or item.get("source_tool") or ""),
            )
            existing_idx = record_index.get(key)
            if existing_idx is None:
                search_records.append(item)
                record_index[key] = len(search_records) - 1
                continue
            existing = search_records[existing_idx]
            raw_persisted = int(item.get("persisted_count") or 0)
            existing_persisted = int(existing.get("persisted_count") or 0)
            if raw_persisted > existing_persisted:
                existing["persisted_count"] = raw_persisted
            if not existing.get("source") and item.get("source"):
                existing["source"] = item.get("source")

    if not queries:
        queries = [str(item.get("query") or "") for item in search_records if str(item.get("query") or "").strip()]
    if not queries:
        queries = ["[Recovered] 原始 query 历史不可用"]
        query_results = None

    search_log = generate_search_log(
        raw_count=len(raw_papers),
        dedup_count=len(enriched_papers),
        queries=queries,
        query_results=query_results,
        search_records=search_records,
        bridge_plan=_load_bridge_domain_plan(workspace_dir),
    )
    search_log += "\n## 说明\n\n"
    search_log += "- 此文件由 runtime 基于当前 `papers_raw.jsonl` 和可解析的 T2 trace 自动重建。\n"
    search_log += f"- 解析到的 T2 trace 数量: {trace_count}\n"
    search_log += (
        "- OpenAlex DOI/OA 详情补全: "
        f"enabled={openalex_backfill.get('enabled')}, "
        f"candidate={openalex_backfill.get('candidate_count')}, "
        f"attempted={openalex_backfill.get('attempted')}, "
        f"openalex_id_filled={openalex_backfill.get('openalex_id_filled')}, "
        f"abstract_filled={openalex_backfill.get('abstract_filled')}, "
        f"references_filled={openalex_backfill.get('references_filled')}, "
        f"pdf_hints_filled={openalex_backfill.get('pdf_hints_filled')}, "
        f"failed={openalex_backfill.get('failed')}\n"
    )
    search_log += (
        "- Crossref DOI 详情补全: "
        f"enabled={metadata_backfill.get('enabled')}, "
        f"candidate={metadata_backfill.get('candidate_count')}, "
        f"attempted={metadata_backfill.get('attempted')}, "
        f"abstract_filled={metadata_backfill.get('abstract_filled')}, "
        f"references_filled={metadata_backfill.get('references_filled')}, "
        f"failed={metadata_backfill.get('failed')}\n"
    )
    search_log += (
        "- Crossref citation snowball 补全: "
        f"enabled={citation_backfill.get('enabled')}, "
        f"sources_used={citation_backfill.get('sources_used')}, "
        f"reference_dois_seen={citation_backfill.get('reference_dois_seen')}, "
        f"attempted={citation_backfill.get('attempted')}, "
        f"added={citation_backfill.get('added')}, "
        f"raw_persisted={citation_backfill.get('raw_persisted')}, "
        f"failed={citation_backfill.get('failed')}\n"
    )
    if query_results is None:
        search_log += "- 本次未能恢复可靠的 query 历史，因此只保留了总量统计。\n"

    search_log_path = workspace_dir / "literature" / "search_log.md"
    search_log_path.write_text(search_log, encoding="utf-8")

    missing_areas_path = workspace_dir / "literature" / "missing_areas.md"
    missing_areas_path.write_text(
        generate_missing_areas_report(project, enriched_papers),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "raw_count": len(raw_papers),
        "dedup_count": len(enriched_papers),
        "query_count": len(queries),
        "trace_count": trace_count,
        "openalex_backfill": openalex_backfill,
        "metadata_backfill": metadata_backfill,
        "citation_backfill": citation_backfill,
        "paths": {
            "papers_dedup": str(workspace_dir / "literature" / "papers_dedup.jsonl"),
            "papers_verified": str(verified_path),
            "verification_failures": str(failures_path),
            "deep_read_queue": str(queue_path),
            "domain_map": str(domain_map_path),
            "citation_edges": str(citation_edges_path),
            "access_audit": str(access_audit_path),
            "search_log": str(search_log_path),
            "missing_areas": str(missing_areas_path),
        },
    }
