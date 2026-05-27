"""Semantic Scholar API 工具。

直接调用 Semantic Scholar API，提供论文搜索和元数据获取功能。
不依赖 MCP，更简单可靠。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult


def _normalize_paper(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize Semantic Scholar output to the common paper shape."""

    external_ids = item.get("externalIds") or {}
    return {
        "id": item.get("paperId") or external_ids.get("CorpusId") or item.get("title"),
        "source": "semantic_scholar",
        "title": item.get("title", ""),
        "authors": [author.get("name", "") for author in item.get("authors", [])],
        "year": item.get("year"),
        "abstract": item.get("abstract", ""),
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


class SemanticScholarSearchParams(BaseModel):
    """搜索论文的参数"""
    query: str = Field(..., description="搜索查询字符串")
    limit: int = Field(default=10, description="返回结果数量（最多100）", ge=1, le=100)
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

    async def execute(self, **kwargs) -> ToolResult:
        query = kwargs["query"]
        limit = kwargs.get("limit", 10)
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
                        if attempt < max_retries - 1:
                            wait_time = 2 ** attempt  # 指数退避：1s, 2s, 4s
                            await asyncio.sleep(wait_time)
                            continue
                        return ToolResult(
                            ok=False,
                            content="❌ API 速率限制，请稍后重试或设置 S2_API_KEY 环境变量",
                            error="rate_limit"
                        )

                    response.raise_for_status()
                    data = response.json()

                papers = [_normalize_paper(item) for item in data.get("data", [])]
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
                    data={"papers": papers, "total": total}
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                return ToolResult(
                    ok=False,
                    content=f"❌ API 请求失败: HTTP {e.response.status_code}",
                    error=f"http_{e.response.status_code}"
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                return ToolResult(
                    ok=False,
                    content=f"❌ 搜索失败: {e}",
                    error="search_failed"
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
