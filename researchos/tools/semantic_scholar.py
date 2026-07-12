"""Semantic Scholar API 工具。

直接调用 Semantic Scholar API，提供论文搜索和元数据获取功能。
不依赖 MCP，更简单可靠。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .abstract_utils import clean_abstract
from .base import Tool, ToolResult
from .http_outcomes import bounded_retry_sleep, provider_cooldown_result, retry_after_hint_seconds, scholarly_http_failure
from .search_validation import clean_search_query, empty_query_result, filter_usable_papers


def _normalize_paper(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize Semantic Scholar output to the common paper shape."""

    external_ids = item.get("externalIds") or {}
    normalized = {
        "id": item.get("paperId") or external_ids.get("CorpusId") or item.get("title"),
        "source": "semantic_scholar",
        "title": item.get("title", ""),
        "authors": _normalize_author_names(item.get("authors", [])),
        "year": item.get("year"),
        "abstract": clean_abstract(item.get("abstract")),
        "venue": item.get("venue", ""),
        "citationCount": item.get("citationCount", 0),
        "externalIds": external_ids,
        "url": item.get("url"),
        "doi": external_ids.get("DOI", ""),
        "provenance": {
            "source_tool": "semantic_scholar_search",
            "source_id": item.get("paperId") or "",
            "source_url": item.get("url") or "",
        },
    }
    references = _normalize_s2_edges(item.get("references"))
    citations = _normalize_s2_edges(item.get("citations"))
    if references:
        normalized["references"] = references
        normalized["referenced_works"] = references
        normalized["reference_count"] = len(references)
    if citations:
        normalized["citations"] = citations
        normalized["citation_edges_inbound_hints"] = citations
    return normalized


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


def _normalize_s2_edges(items: Any, limit: int = 80) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return edges
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
        paper_id = str(item.get("paperId") or external_ids.get("OpenAlex") or external_ids.get("DOI") or "").strip()
        title = str(item.get("title") or "").strip()
        if not paper_id and not title:
            continue
        edge: dict[str, Any] = {}
        if paper_id:
            edge["id"] = paper_id
        if title:
            edge["title"] = title
        doi = str(external_ids.get("DOI") or "").strip()
        if doi:
            edge["doi"] = doi
        openalex = str(external_ids.get("OpenAlex") or "").strip()
        if openalex:
            edge["openalex_id"] = openalex
        edges.append(edge)
    return edges


class SemanticScholarSearchParams(BaseModel):
    """搜索论文的参数"""
    query: str = Field(..., description="搜索查询字符串")
    limit: int = Field(default=10, description="返回结果数量（最多100）", ge=1, le=100)
    query_bucket: str | None = Field(
        default=None,
        description="可选检索式桶标签，仅作为 ResearchOS 召回意图/provenance，不发送给 Semantic Scholar，也不决定语义角色。",
    )
    bridge_id: str | None = Field(
        default=None,
        description="可选 bridge_domain_plan.json 中的 bridge_id；只记录召回意图，不代表语义角色。",
    )
    fields: str = Field(
        default="paperId,title,abstract,authors,year,venue,citationCount,url,externalIds",
        description="返回的字段列表（逗号分隔）"
    )


class SemanticScholarGetPaperParams(BaseModel):
    """获取论文详情的参数"""
    paper_id: str = Field(..., description="论文ID（S2 ID、DOI、arXiv ID等）")
    fields: str = Field(
        default="paperId,title,abstract,authors,year,venue,citationCount,url,externalIds,references,citations",
        description="返回的字段列表（逗号分隔）"
    )


class SemanticScholarSearchTool(Tool):
    """搜索 Semantic Scholar 论文。

    使用 Semantic Scholar API 搜索学术论文。
    支持自然语言查询，返回相关论文列表。
    """

    name = "semantic_scholar_search"
    description = "搜索 Semantic Scholar 学术论文数据库"
    parameters_schema = SemanticScholarSearchParams
    timeout_seconds = 30.0

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("S2_API_KEY")
        self.base_url = "https://api.semanticscholar.org/graph/v1"
        self._cooldown_until = 0.0

    async def execute(self, **kwargs) -> ToolResult:
        remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            return provider_cooldown_result(source="Semantic Scholar", retry_after_seconds=remaining)
        query = clean_search_query(kwargs["query"])
        if not query:
            return empty_query_result(self.name, kwargs.get("query"))
        limit = kwargs.get("limit", 10)
        query_bucket = kwargs.get("query_bucket")
        bridge_id = kwargs.get("bridge_id")
        fields = kwargs.get("fields", "paperId,title,abstract,authors,year,venue,citationCount,url,externalIds")

        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        url = f"{self.base_url}/paper/search"
        params = {
            "query": query,
            "limit": limit,
            "fields": fields
        }

        # 重试逻辑（最多3次）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url, params=params, headers=headers)

                    # 处理速率限制
                    if response.status_code == 429:
                        retry_after = retry_after_hint_seconds(response)
                        if retry_after is not None and retry_after > 10:
                            self._cooldown_until = time.monotonic() + retry_after
                            return scholarly_http_failure(
                                source="Semantic Scholar",
                                exc=httpx.HTTPStatusError("rate limited", request=response.request, response=response),
                                attempts=attempt + 1,
                                action="搜索",
                                response=response,
                            )
                        if attempt < max_retries - 1:
                            await bounded_retry_sleep(response, attempt=attempt)
                            continue
                        cooldown = retry_after if retry_after is not None else 60.0
                        self._cooldown_until = time.monotonic() + cooldown
                        return ToolResult(
                            ok=False,
                            content="Semantic Scholar 暂时触发速率限制；其他可用来源会继续。",
                            error="rate_limited",
                            data={
                                "source": "semantic_scholar",
                                "failure_class": "rate_limited",
                                "retriable": True,
                                "fallback_available": True,
                                "attempts": attempt + 1,
                                "retry_after_seconds": retry_after,
                                "cooldown_seconds": cooldown,
                            },
                        )

                    response.raise_for_status()
                    data = response.json()

                papers = filter_usable_papers([_normalize_paper(item) for item in data.get("data", [])])
                total = data.get("total", 0)

                # 格式化输出
                content_lines = [
                    f"找到 {total} 篇相关论文（返回前 {len(papers)} 篇）：",
                    ""
                ]

                for i, paper in enumerate(papers, 1):
                    title = paper.get("title", "Unknown")
                    authors = paper.get("authors", [])
                    author_names = [str(author or "Unknown") for author in authors[:3]]
                    year = paper.get("year")
                    citations = paper.get("citationCount", 0)

                    content_lines.append(f"{i}. {title}")
                    content_lines.append(f"   作者: {', '.join(author_names)}")
                    content_lines.append(f"   年份: {year if year is not None else 'unknown'} | 引用数: {citations}")
                    content_lines.append("")

                return ToolResult(
                    ok=True,
                    content="\n".join(content_lines),
                    data={
                        "papers": papers,
                        "total": total,
                        "query": query,
                        "query_bucket": query_bucket,
                        "bridge_id": bridge_id,
                    }
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    await bounded_retry_sleep(e.response, attempt=attempt)
                    continue
                return scholarly_http_failure(
                    source="Semantic Scholar",
                    exc=e,
                    attempts=attempt + 1,
                    action="搜索",
                )
            except httpx.RequestError as e:
                if attempt < max_retries - 1:
                    await bounded_retry_sleep(None, attempt=attempt)
                    continue
                return scholarly_http_failure(
                    source="Semantic Scholar",
                    exc=e,
                    attempts=attempt + 1,
                    action="搜索",
                )

        return ToolResult(
            ok=False,
            content="❌ 搜索失败：超过最大重试次数",
            error="max_retries_exceeded"
        )


class SemanticScholarGetPaperTool(Tool):
    """获取 Semantic Scholar 论文详情。

    根据论文 ID 获取完整的论文元数据。
    支持多种 ID 格式：S2 ID、DOI、arXiv ID 等。
    """

    name = "semantic_scholar_get_paper"
    description = "获取 Semantic Scholar 论文的详细信息"
    parameters_schema = SemanticScholarGetPaperParams
    timeout_seconds = 30.0

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("S2_API_KEY")
        self.base_url = "https://api.semanticscholar.org/graph/v1"

    async def execute(self, **kwargs) -> ToolResult:
        paper_id = kwargs["paper_id"]
        fields = kwargs.get("fields", "paperId,title,abstract,authors,year,venue,citationCount,url,externalIds,references,citations")

        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        url = f"{self.base_url}/paper/{paper_id}"
        params = {"fields": fields}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                paper = response.json()

            # 格式化输出
            title = paper.get("title", "Unknown")
            normalized_paper = _normalize_paper(paper)
            authors = normalized_paper.get("authors", [])
            author_names = [str(author or "Unknown") for author in authors]
            year = normalized_paper.get("year")
            venue = normalized_paper.get("venue", "Unknown")
            citations = normalized_paper.get("citationCount", 0)
            abstract = normalized_paper.get("abstract", "")

            content_lines = [
                f"标题: {title}",
                f"作者: {', '.join(author_names)}",
                f"年份: {year if year is not None else 'unknown'}",
                f"发表于: {venue}",
                f"引用数: {citations}",
                "",
                "摘要:",
                abstract[:500] + "..." if len(abstract) > 500 else abstract
            ]

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data={"paper": normalized_paper}
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ToolResult(
                    ok=False,
                    content=f"❌ 论文未找到: {paper_id}",
                    error="paper_not_found"
                )
            return ToolResult(
                ok=False,
                content=f"❌ API 请求失败: HTTP {e.response.status_code}",
                error=f"http_{e.response.status_code}"
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 获取论文失败: {e}",
                error="get_paper_failed"
            )
