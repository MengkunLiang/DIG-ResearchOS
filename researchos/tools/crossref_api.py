"""CrossRef API 工具。

直接调用 CrossRef API，提供 DOI 元数据查询功能。
完全免费，覆盖 1.4 亿+ DOI，是 DOI 元数据的权威来源。
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult


class CrossRefSearchParams(BaseModel):
    """搜索 CrossRef 的参数"""
    query: str = Field(..., description="搜索查询字符串")
    rows: int = Field(default=10, description="返回结果数量（最多1000）", ge=1, le=1000)
    sort: str = Field(default="relevance", description="排序方式：relevance, score, updated, deposited, published")


class CrossRefGetWorkParams(BaseModel):
    """获取 CrossRef DOI 详情的参数"""
    doi: str = Field(..., description="DOI（如 10.1234/example）")


class CrossRefSearchTool(Tool):
    """搜索 CrossRef DOI 元数据。

    使用 CrossRef API 搜索学术论文的 DOI 元数据。
    完全免费，覆盖 1.4 亿+ DOI，是 DOI 元数据的权威来源。
    """

    name = "crossref_search"
    description = "搜索 CrossRef DOI 元数据数据库（1.4亿+ DOI）"
    parameters_schema = CrossRefSearchParams
    timeout_seconds = 30.0

    def __init__(self):
        self.base_url = "https://api.crossref.org"

    async def execute(self, **kwargs) -> ToolResult:
        query = kwargs["query"]
        rows = kwargs.get("rows", 10)
        sort = kwargs.get("sort", "relevance")

        params = {
            "query": query,
            "rows": rows,
            "sort": sort,
        }

        headers = {
            "User-Agent": "ResearchOS/0.1.0 (mailto:researchos@example.com)"  # 礼貌使用
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/works",
                    params=params,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()

            message = data.get("message", {})
            items = message.get("items", [])
            total_results = message.get("total-results", 0)

            papers = []
            for item in items:
                # 提取基本信息
                title_list = item.get("title", [])
                title = title_list[0] if title_list else "Unknown"

                # 提取作者
                authors = []
                author_list = item.get("author", [])
                for author in author_list[:10]:  # 最多取前10个作者
                    given = author.get("given", "")
                    family = author.get("family", "")
                    name = f"{given} {family}".strip() if given or family else "Unknown"
                    authors.append(name)

                # 提取年份
                published = item.get("published-print") or item.get("published-online") or item.get("created")
                year = None
                if published and "date-parts" in published:
                    date_parts = published["date-parts"][0]
                    if date_parts:
                        year = date_parts[0]

                # 提取摘要（CrossRef 通常不提供摘要）
                abstract = item.get("abstract", "")

                # 提取 venue
                container_title = item.get("container-title", [])
                venue = container_title[0] if container_title else "Unknown"

                # 提取引用数（CrossRef 不直接提供，但有 is-referenced-by-count）
                citation_count = item.get("is-referenced-by-count", 0)

                # 提取 DOI
                doi = item.get("DOI", "")

                # 提取 URL
                url = item.get("URL", f"https://doi.org/{doi}" if doi else "")

                # 提取类型
                item_type = item.get("type", "")

                paper = {
                    "id": doi,
                    "source": "crossref",
                    "title": title,
                    "authors": authors if authors else ["Unknown"],
                    "year": year,
                    "abstract": abstract,
                    "venue": venue,
                    "url": url,
                    "citation_count": citation_count,
                    "doi": doi,
                    "type": item_type,
                }
                papers.append(paper)

            # 格式化输出
            content_lines = [
                f"找到 {total_results} 篇相关论文（返回前 {len(papers)} 篇）：",
                ""
            ]

            for i, paper in enumerate(papers, 1):
                title = paper["title"]
                authors = paper["authors"][:3]
                year = paper["year"]
                citations = paper["citation_count"]

                content_lines.append(f"{i}. {title}")
                content_lines.append(f"   作者: {', '.join(authors)}")
                content_lines.append(f"   年份: {year if year is not None else 'unknown'} | 引用数: {citations}")
                if paper["doi"]:
                    content_lines.append(f"   DOI: {paper['doi']}")
                content_lines.append("")

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data={"papers": papers, "total": total_results}
            )

        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ CrossRef 搜索失败: {e}",
                error="search_failed"
            )


class CrossRefGetWorkTool(Tool):
    """获取 CrossRef DOI 详情。

    根据 DOI 获取完整的元数据。
    """

    name = "crossref_get_work"
    description = "获取 CrossRef DOI 的详细元数据"
    parameters_schema = CrossRefGetWorkParams
    timeout_seconds = 30.0

    def __init__(self):
        self.base_url = "https://api.crossref.org"

    async def execute(self, **kwargs) -> ToolResult:
        doi = kwargs["doi"]

        headers = {
            "User-Agent": "ResearchOS/0.1.0 (mailto:researchos@example.com)"
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/works/{doi}",
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()

            message = data.get("message", {})

            # 提取信息
            title_list = message.get("title", [])
            title = title_list[0] if title_list else "Unknown"

            author_list = message.get("author", [])
            authors = []
            for author in author_list:
                given = author.get("given", "")
                family = author.get("family", "")
                name = f"{given} {family}".strip() if given or family else "Unknown"
                authors.append(name)

            published = message.get("published-print") or message.get("published-online") or message.get("created")
            year = None
            if published and "date-parts" in published:
                date_parts = published["date-parts"][0]
                if date_parts:
                    year = date_parts[0]

            abstract = message.get("abstract", "")

            container_title = message.get("container-title", [])
            venue = container_title[0] if container_title else "Unknown"

            citation_count = message.get("is-referenced-by-count", 0)

            url = message.get("URL", f"https://doi.org/{doi}")

            # 格式化输出
            content_lines = [
                f"标题: {title}",
                f"作者: {', '.join(authors)}",
                f"年份: {year if year is not None else 'unknown'}",
                f"发表于: {venue}",
                f"引用数: {citation_count}",
                f"DOI: {doi}",
                "",
            ]

            if abstract:
                content_lines.append("摘要:")
                content_lines.append(abstract[:500] + "..." if len(abstract) > 500 else abstract)
            else:
                content_lines.append("摘要: 无（CrossRef 通常不提供摘要）")

            paper = {
                "id": doi,
                "source": "crossref",
                "title": title,
                "authors": authors,
                "year": year,
                "abstract": abstract,
                "venue": venue,
                "url": url,
                "citation_count": citation_count,
                "doi": doi,
            }

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data={"paper": paper}
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ToolResult(
                    ok=False,
                    content=f"❌ DOI 未找到: {doi}",
                    error="doi_not_found"
                )
            return ToolResult(
                ok=False,
                content=f"❌ API 请求失败: HTTP {e.response.status_code}",
                error=f"http_{e.response.status_code}"
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 获取 DOI 失败: {e}",
                error="get_work_failed"
            )
