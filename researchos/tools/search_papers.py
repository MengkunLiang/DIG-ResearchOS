from __future__ import annotations

"""学术论文搜索工具。

实现策略：
- 优先调用 Semantic Scholar；
- auto 模式下若 S2 失败或无结果，则自动降级到 arXiv；
- 返回给 LLM 的内容尽量紧凑，但 `data` 中保留结构化字段，方便后续 agent 写文件。
"""

from datetime import datetime
import os
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

    async def execute(self, **kwargs: Any) -> ToolResult:
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

        if params.source in {"auto", "semantic_scholar"}:
            try:
                papers = await self._s2_search(params)
            except ModuleNotFoundError:
                return ToolResult(
                    ok=False,
                    content="缺少 httpx 依赖，无法执行 search_papers 网络检索。",
                    error="dependency_missing",
                )
            except Exception as exc:
                is_http_error = httpx is not None and isinstance(exc, httpx.HTTPError)
                if params.source == "semantic_scholar" or not is_http_error:
                    raise ToolRuntimeError(self.name, exc) from exc
            if papers:
                source_used = "semantic_scholar"

        if not papers and params.source in {"auto", "arxiv"}:
            try:
                papers = await self._arxiv_search(params)
            except ModuleNotFoundError:
                return ToolResult(
                    ok=False,
                    content="缺少 httpx 依赖，无法执行 search_papers 网络检索。",
                    error="dependency_missing",
                )
            except Exception as exc:
                raise ToolRuntimeError(self.name, exc) from exc
            source_used = "arxiv"

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
            "authors": [author.get("name", "") for author in item.get("authors", [])],
            "year": item.get("year"),
            "abstract": clean_abstract(item.get("abstract")),
            "venue": item.get("venue", ""),
            "citationCount": item.get("citationCount", 0),
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
            "externalIds": {"ArXiv": identifier},
            "url": text("atom:id"),
        }

    @staticmethod
    def _format_papers(papers: list[dict[str, Any]]) -> str:
        if not papers:
            return "未检索到论文结果。"
        lines: list[str] = []
        for index, paper in enumerate(papers, start=1):
            authors = ", ".join(str(author) for author in paper.get("authors", [])[:3])
            if len(paper.get("authors", [])) > 3:
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
        try:
            if params.source == "arxiv" or (
                params.source == "auto" and _looks_like_arxiv_id(params.id)
            ):
                paper = await self._fetch_arxiv(params.id)
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
                return ToolResult(
                    ok=False,
                    content=f"Paper not found: {params.id} ({exc.response.status_code})",
                    error="not_found",
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


def _looks_like_arxiv_id(identifier: str) -> bool:
    normalized = identifier.replace("arXiv:", "")
    return "." in normalized and normalized.replace(".", "").replace("v", "").isdigit()


def _require_httpx():
    """按需返回 httpx 模块，缺失时显式抛错给上层转换。"""

    if httpx is None:
        raise ModuleNotFoundError("httpx")
    return httpx
