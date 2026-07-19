from __future__ import annotations

"""学术论文搜索工具。

实现策略：
- 优先调用 Semantic Scholar；
- auto 模式下若 S2 失败或无结果，则自动降级到 arXiv；
- 返回给 LLM 的内容尽量紧凑，但 `data` 中保留结构化字段，方便后续 agent 写文件。
"""

import asyncio
from datetime import datetime
import os
import re
from typing import Any, Literal
from urllib.parse import quote, quote_plus
import xml.etree.ElementTree as ET

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - 依赖是否安装取决于环境
    httpx = None
from pydantic import BaseModel, Field

from ..runtime.errors import ToolRuntimeError
from .abstract_utils import clean_abstract
from .base import Tool, ToolResult
from .search_validation import clean_search_query, empty_query_result, filter_usable_papers


class SearchPapersParams(BaseModel):
    query: str = Field(..., description="搜索关键词")
    year_from: int | None = Field(None, description="起始年份，如 2022")
    year_to: int | None = Field(None, description="截止年份")
    max_results: int = Field(20, ge=1, le=100, description="最多返回多少篇论文")
    query_bucket: str | None = Field(
        None,
        description="可选检索式桶标签，仅作为召回意图/provenance，例如 core/baseline/adjacent_field/theory_bridge；不决定 core/bridge/target。",
    )
    bridge_id: str | None = Field(
        None,
        description="可选 bridge_domain_plan.json 中的 bridge_id；只作为召回意图记录，不代表论文语义角色。",
    )
    source: Literal["semantic_scholar", "arxiv", "auto"] = Field(
        "auto",
        description="优先使用的来源",
    )


class FetchPaperMetadataParams(BaseModel):
    id: str = Field(..., min_length=1, description="arXiv ID / DOI / S2 paper ID")
    source: Literal["semantic_scholar", "arxiv", "auto"] = Field(
        "auto",
        description="论文来源；auto 会按 ID 特征自动判断",
    )


class SearchPapersTool(Tool):
    name = "search_papers"
    description = (
        "跨源搜索学术论文。返回标题、作者、年份、摘要、DOI、citation_count 等元数据。"
        "优先 Semantic Scholar，失败时降级到 arXiv。"
    )
    parameters_schema = SearchPapersParams
    timeout_seconds = 60.0

    def __init__(self, s2_api_key: str | None = None):
        self.s2_api_key = s2_api_key or os.environ.get("S2_API_KEY")
        # Tool instances are built once per Agent run.  Keep provider health
        # in that narrow scope so a temporary public API outage does not make
        # the model spend the rest of the same run issuing paraphrased queries.
        self._temporarily_unavailable_sources: set[str] = set()
        # Tool calls from one LLM response are scheduled concurrently.  A
        # narrow lock lets the first failing request establish provider health
        # before queued paraphrases can start their own network waits.
        self._search_lock = asyncio.Lock()

    async def execute(self, **kwargs: Any) -> ToolResult:
        async with self._search_lock:
            return await self._execute_serialized(**kwargs)

    async def _execute_serialized(self, **kwargs: Any) -> ToolResult:
        params = SearchPapersParams(**kwargs)
        query = clean_search_query(params.query)
        if not query:
            return empty_query_result(self.name, params.query)
        params.query = query
        query_bucket = params.query_bucket
        bridge_id = params.bridge_id
        params.query_bucket = None
        params.bridge_id = None
        papers: list[dict[str, Any]] = []
        source_used = params.source
        source_failures: list[dict[str, Any]] = []
        completed_sources: list[str] = []

        requested_sources = self._requested_sources(params.source)
        available_sources = [
            source
            for source in requested_sources
            if source not in self._temporarily_unavailable_sources
        ]
        if not available_sources:
            return self._provider_circuit_open_result(
                params=params,
                query_bucket=query_bucket,
                bridge_id=bridge_id,
            )

        if (
            params.source in {"auto", "semantic_scholar"}
            and "semantic_scholar" in available_sources
        ):
            try:
                papers = await self._s2_search(params)
                completed_sources.append("semantic_scholar")
            except ModuleNotFoundError:
                return ToolResult(
                    ok=False,
                    content="缺少 httpx 依赖，无法执行 search_papers 网络检索。",
                    error="dependency_missing",
                )
            except Exception as exc:
                if not self._is_expected_http_failure(exc):
                    raise ToolRuntimeError(self.name, exc) from exc
                failure = self._provider_failure("semantic_scholar", exc)
                source_failures.append(failure)
                if bool(failure.get("retriable")):
                    self._temporarily_unavailable_sources.add("semantic_scholar")
                if params.source == "semantic_scholar":
                    return self._search_failure_result(
                        params=params,
                        query_bucket=query_bucket,
                        bridge_id=bridge_id,
                        source_failures=source_failures,
                    )
            if papers:
                source_used = "semantic_scholar"

        if (
            not papers
            and params.source in {"auto", "arxiv"}
            and "arxiv" in available_sources
        ):
            try:
                papers = await self._arxiv_search(params)
                completed_sources.append("arxiv")
            except ModuleNotFoundError:
                return ToolResult(
                    ok=False,
                    content="缺少 httpx 依赖，无法执行 search_papers 网络检索。",
                    error="dependency_missing",
                )
            except Exception as exc:
                if not self._is_expected_http_failure(exc):
                    raise ToolRuntimeError(self.name, exc) from exc
                failure = self._provider_failure("arxiv", exc)
                source_failures.append(failure)
                if bool(failure.get("retriable")):
                    self._temporarily_unavailable_sources.add("arxiv")
                return self._search_failure_result(
                    params=params,
                    query_bucket=query_bucket,
                    bridge_id=bridge_id,
                    source_failures=source_failures,
                )
            source_used = "arxiv"

        # A provider can have been circuit-open before this invocation while
        # the other provider fails now.  Do not turn that dual outage into a
        # misleading successful empty result.
        if source_failures and not completed_sources:
            return self._search_failure_result(
                params=params,
                query_bucket=query_bucket,
                bridge_id=bridge_id,
                source_failures=source_failures,
            )

        papers = filter_usable_papers(papers)

        return ToolResult(
            ok=True,
            content=self._format_papers(papers),
            data={
                "source": source_used,
                "papers": papers,
                "count": len(papers),
                "query": params.query,
                "query_bucket": query_bucket,
                "bridge_id": bridge_id,
                "source_failures": source_failures,
                "completed_sources": completed_sources,
            },
        )

    @staticmethod
    def _requested_sources(source: Literal["semantic_scholar", "arxiv", "auto"]) -> list[str]:
        if source == "auto":
            return ["semantic_scholar", "arxiv"]
        return [source]

    @staticmethod
    def _is_expected_http_failure(exc: Exception) -> bool:
        return httpx is not None and isinstance(exc, httpx.HTTPError)

    @staticmethod
    def _provider_label(provider: str) -> str:
        return "Semantic Scholar" if provider == "semantic_scholar" else "arXiv"

    def _provider_failure(self, provider: str, exc: Exception) -> dict[str, Any]:
        """Describe an expected public-API failure without exposing a traceback.

        ``scholarly_http_failure`` is also used by metadata retrieval.  Reusing
        its classification keeps retry and provenance semantics identical while
        allowing a multi-source search to retain both attempted providers.
        """

        from .http_outcomes import scholarly_http_failure

        result = scholarly_http_failure(
            source=self._provider_label(provider),
            exc=exc,
            attempts=1,
            fallback_available=provider == "semantic_scholar",
            action="论文检索",
        )
        return {
            "provider": provider,
            "display_source": self._provider_label(provider),
            **result.data,
        }

    def _search_failure_result(
        self,
        *,
        params: SearchPapersParams,
        query_bucket: str | None,
        bridge_id: str | None,
        source_failures: list[dict[str, Any]],
    ) -> ToolResult:
        providers = [str(item.get("provider") or "") for item in source_failures]
        temporary_all_source_failure = (
            params.source == "auto"
            and set(self._requested_sources("auto")).issubset(providers)
            and all(bool(item.get("retriable")) for item in source_failures)
        )
        if temporary_all_source_failure:
            error = "retrieval_temporarily_unavailable"
            content = (
                "外部论文检索暂时不可用，本轮未新增论文。Semantic Scholar 与 arXiv 均未能完成请求；"
                "请停止本轮同义或近似查询，使用已有的本地论文卡、阅读笔记和比较表继续审计，"
                "并在报告中明确近期外部覆盖未完成。"
            )
        else:
            error = str(source_failures[-1].get("failure_class") or "external_search_failed")
            content = (
                "外部论文检索未完成，本轮未新增论文。请记录该来源覆盖限制；"
                "不要把未检出论文表述为已完成的近期文献覆盖。"
            )
        return ToolResult(
            ok=False,
            content=content,
            error=error,
            data={
                "source": params.source,
                "query": params.query,
                "query_bucket": query_bucket,
                "bridge_id": bridge_id,
                "attempted_sources": providers,
                "source_failures": source_failures,
                "failure_class": error,
                "retriable": any(bool(item.get("retriable")) for item in source_failures),
                "fallback_available": False,
            },
        )

    def _provider_circuit_open_result(
        self,
        *,
        params: SearchPapersParams,
        query_bucket: str | None,
        bridge_id: str | None,
    ) -> ToolResult:
        requested_sources = self._requested_sources(params.source)
        source_failures = [
            {
                "provider": provider,
                "display_source": self._provider_label(provider),
                "failure_class": "provider_circuit_open",
                "retriable": True,
                "circuit_open": True,
            }
            for provider in requested_sources
        ]
        return ToolResult(
            ok=False,
            content=(
                "外部论文检索在本轮已确认暂时不可用；为避免重复等待，未发起相近查询。"
                "请基于已有本地证据继续，并在审计中把近期外部覆盖标记为未完成。"
            ),
            error="retrieval_temporarily_unavailable",
            data={
                "source": params.source,
                "query": params.query,
                "query_bucket": query_bucket,
                "bridge_id": bridge_id,
                "attempted_sources": requested_sources,
                "source_failures": source_failures,
                "failure_class": "retrieval_temporarily_unavailable",
                "retriable": True,
                "fallback_available": False,
                "circuit_open": True,
            },
        )

    async def _s2_search(self, params: SearchPapersParams) -> list[dict[str, Any]]:
        httpx_mod = _require_httpx()
        headers = {"x-api-key": self.s2_api_key} if self.s2_api_key else {}
        query_params: dict[str, Any] = {
            "query": params.query,
            "limit": params.max_results,
            "fields": (
                "paperId,title,authors,year,abstract,venue,citationCount,externalIds,url"
            ),
        }
        if params.year_from or params.year_to:
            query_params["year"] = f"{params.year_from or ''}-{params.year_to or ''}".strip("-")

        async with httpx_mod.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=query_params,
                headers=headers,
            )
            response.raise_for_status()
            items = response.json().get("data", [])
        return [self._normalize_s2_paper(item) for item in items]

    async def _arxiv_search(self, params: SearchPapersParams) -> list[dict[str, Any]]:
        httpx_mod = _require_httpx()
        url = (
            "http://export.arxiv.org/api/query?"
            f"search_query=all:{quote_plus(params.query)}&start=0&max_results={params.max_results}"
            "&sortBy=relevance&sortOrder=descending"
        )
        async with httpx_mod.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        feed = ET.fromstring(response.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        out: list[dict[str, Any]] = []
        for entry in feed.findall("atom:entry", ns):
            paper = self._normalize_arxiv_entry(entry, ns)
            year = paper.get("year")
            if params.year_from and isinstance(year, int) and year < params.year_from:
                continue
            if params.year_to and isinstance(year, int) and year > params.year_to:
                continue
            out.append(paper)
        return out

    @staticmethod
    def _normalize_s2_paper(item: dict[str, Any]) -> dict[str, Any]:
        external_ids = item.get("externalIds") or {}
        return {
            "id": item.get("paperId") or external_ids.get("CorpusId") or item.get("title"),
            "source": "semantic_scholar",
            "title": item.get("title", ""),
            "authors": _normalize_author_names(item.get("authors", [])),
            "year": item.get("year"),
            "abstract": clean_abstract(item.get("abstract")),
            "venue": item.get("venue", ""),
            "citationCount": item.get("citationCount", 0),
            "doi": external_ids.get("DOI", ""),
            "externalIds": external_ids,
            "url": item.get("url"),
        }

    @staticmethod
    def _normalize_arxiv_entry(entry: ET.Element, ns: dict[str, str]) -> dict[str, Any]:
        def text(tag: str) -> str:
            value = entry.findtext(tag, default="", namespaces=ns)
            return clean_abstract(" ".join(value.split()))

        authors = [
            author.findtext("atom:name", default="", namespaces=ns)
            for author in entry.findall("atom:author", ns)
        ]
        published = text("atom:published")
        year = None
        if published:
            year = datetime.fromisoformat(published.replace("Z", "+00:00")).year
        identifier = text("atom:id").rstrip("/").split("/")[-1]
        summary = text("atom:summary")
        return {
            "id": identifier,
            "source": "arxiv",
            "title": text("atom:title"),
            "authors": authors,
            "year": year,
            "abstract": summary,
            "venue": "arXiv",
            "citationCount": 0,
            "doi": "",
            "pdf_url": f"https://arxiv.org/pdf/{identifier}.pdf",
            "externalIds": {"ArXiv": identifier},
            "url": text("atom:id"),
        }

    @staticmethod
    def _format_papers(papers: list[dict[str, Any]]) -> str:
        if not papers:
            return "未检索到论文结果。"
        lines: list[str] = []
        for index, paper in enumerate(papers, start=1):
            author_list = _normalize_author_names(paper.get("authors", []))
            authors = ", ".join(author_list[:3])
            if len(author_list) > 3:
                authors += " et al."
            title = paper.get("title", "?")
            year = paper.get("year")
            source = paper.get("source", "?")
            citations = paper.get("citationCount", 0)
            abstract = paper.get("abstract", "")
            # 摘要截断到 300 字符，避免输出过长
            if abstract and len(abstract) > 300:
                abstract = abstract[:300] + "..."
            venue = paper.get("venue", "")
            lines.append(
                f"[{index}] {title}\n"
                f"    作者: {authors}, 年份: {year if year is not None else 'unknown'}, 出版地: {venue}, 引用: {citations}, 来源: {source}\n"
                f"    摘要: {abstract or '无'}"
            )
        return "\n".join(lines)


class FetchPaperMetadataTool(Tool):
    name = "fetch_paper_metadata"
    description = "获取单篇论文的较完整元数据，包括引用/被引用等信息。"
    parameters_schema = FetchPaperMetadataParams
    timeout_seconds = 30.0

    def __init__(self, s2_api_key: str | None = None):
        self.s2_api_key = s2_api_key or os.environ.get("S2_API_KEY")

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = FetchPaperMetadataParams(**kwargs)
        use_arxiv = params.source == "arxiv" or (
            params.source == "auto" and _looks_like_arxiv_id(params.id)
        )
        canonical_arxiv_id = _normalize_arxiv_id(params.id) if use_arxiv else ""
        try:
            if use_arxiv:
                paper = await self._fetch_arxiv(canonical_arxiv_id)
            else:
                paper = await self._fetch_s2(params.id)
        except ModuleNotFoundError:
            return ToolResult(
                ok=False,
                content="缺少 httpx 依赖，无法获取论文元数据。",
                error="dependency_missing",
            )
        except Exception as exc:
            if httpx is not None and isinstance(exc, httpx.HTTPStatusError):
                source_label = "arXiv" if use_arxiv else "论文元数据服务"
                fallback_detail = (
                    "已规范化 arXiv 标识后仍未返回条目；保留已有检索候选和本地阅读材料（如有），"
                    "不将其视为论文不存在，也不升级证据等级。"
                    if use_arxiv
                    else "保留已有检索候选和本地阅读材料（如有），不将其视为论文不存在，也不升级证据等级。"
                )
                return ToolResult(
                    ok=False,
                    content=f"{source_label} 补充元数据未命中：{fallback_detail}",
                    error="not_found",
                    data={
                        "display_disposition": "auto_fallback",
                        "fallback_available": True,
                        "fallback_action": "retain_existing_search_candidate_without_metadata_enrichment",
                        "source": source_label,
                        "requested_identifier": params.id,
                        "canonical_identifier": canonical_arxiv_id or params.id,
                        "failure_class": "metadata_not_found",
                    },
                )
            if httpx is not None and isinstance(exc, httpx.RequestError):
                from .http_outcomes import scholarly_http_failure

                return scholarly_http_failure(
                    source="论文元数据服务",
                    exc=exc,
                    attempts=1,
                    action="元数据获取",
                )
            raise ToolRuntimeError(self.name, exc) from exc

        return ToolResult(
            ok=True,
            content=SearchPapersTool._format_papers([paper]),
            data={"paper": paper},
        )

    async def _fetch_s2(self, identifier: str) -> dict[str, Any]:
        httpx_mod = _require_httpx()
        headers = {"x-api-key": self.s2_api_key} if self.s2_api_key else {}
        fields = (
            "paperId,title,authors,year,abstract,venue,citationCount,externalIds,url,"
            "references.title,references.paperId,references.year,citations.title,citations.paperId,citations.year"
        )
        encoded_id = quote(identifier, safe="")
        async with httpx_mod.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/{encoded_id}",
                params={"fields": fields},
                headers=headers,
            )
            response.raise_for_status()
            item = response.json()
        paper = SearchPapersTool._normalize_s2_paper(item)
        paper["references"] = item.get("references", [])
        paper["citations"] = item.get("citations", [])
        return paper

    async def _fetch_arxiv(self, identifier: str) -> dict[str, Any]:
        httpx_mod = _require_httpx()
        url = (
            "http://export.arxiv.org/api/query?"
            f"id_list={identifier}"
        )
        async with httpx_mod.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        feed = ET.fromstring(response.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = feed.find("atom:entry", ns)
        if entry is None:
            raise httpx_mod.HTTPStatusError(
                "Not found",
                request=response.request,
                response=httpx_mod.Response(404, request=response.request),
            )
        paper = SearchPapersTool._normalize_arxiv_entry(entry, ns)
        paper["references"] = []
        paper["citations"] = []
        return paper


_ARXIV_IDENTIFIER_RE = re.compile(
    r"(?:https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?$",
    re.IGNORECASE,
)


def _normalize_arxiv_id(identifier: str) -> str:
    """Return the bare arXiv identifier required by the Atom API."""

    raw = str(identifier or "").strip()
    match = _ARXIV_IDENTIFIER_RE.fullmatch(raw)
    return match.group(1) if match else raw


def _looks_like_arxiv_id(identifier: str) -> bool:
    normalized = _normalize_arxiv_id(identifier)
    return bool(re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", normalized))


def _require_httpx():
    """按需返回 httpx 模块，缺失时显式抛错给上层转换。"""

    if httpx is None:
        raise ModuleNotFoundError("httpx")
    return httpx


def _normalize_author_names(authors: Any) -> list[str]:
    if not authors:
        return []
    if isinstance(authors, str):
        return [authors.strip()] if authors.strip() else []
    if not isinstance(authors, list):
        text = str(authors).strip()
        return [text] if text else []
    names: list[str] = []
    for author in authors:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            name = str(
                author.get("name")
                or author.get("display_name")
                or author.get("author_name")
                or author.get("full_name")
                or ""
            ).strip()
        else:
            name = str(author).strip()
        if name:
            names.append(name)
    return names
