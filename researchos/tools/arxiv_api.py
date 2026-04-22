"""arXiv API 工具。

直接调用 arXiv API，提供预印本搜索和下载功能。
完全免费，无速率限制。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult


class ArxivSearchParams(BaseModel):
    """搜索 arXiv 论文的参数"""
    query: str = Field(..., description="搜索查询字符串")
    max_results: int = Field(default=10, description="返回结果数量（最多100）", ge=1, le=100)
    sort_by: str = Field(default="relevance", description="排序方式：relevance, lastUpdatedDate, submittedDate")


class ArxivSearchTool(Tool):
    """搜索 arXiv 预印本。

    使用 arXiv API 搜索预印本论文。
    完全免费，无速率限制，主要覆盖物理、数学、计算机科学等领域。
    """

    name = "arxiv_search"
    description = "搜索 arXiv 预印本论文数据库（物理、数学、CS 等）"
    parameters_schema = ArxivSearchParams
    timeout_seconds = 30.0

    def __init__(self):
        self.base_url = "https://export.arxiv.org/api/query"

    async def execute(self, **kwargs) -> ToolResult:
        query = kwargs["query"]
        max_results = kwargs.get("max_results", 10)
        sort_by = kwargs.get("sort_by", "relevance")

        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": "descending"
        }

        # 重试逻辑（arXiv 有速率限制）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(self.base_url, params=params)

                    # 处理速率限制
                    if response.status_code == 429:
                        if attempt < max_retries - 1:
                            wait_time = 3 * (2 ** attempt)  # 3s, 6s, 12s
                            await asyncio.sleep(wait_time)
                            continue
                        return ToolResult(
                            ok=False,
                            content="❌ arXiv API 速率限制，请稍后重试",
                            error="rate_limit"
                        )

                    response.raise_for_status()

                # 解析 XML 响应
                root = ET.fromstring(response.content)
                ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

                entries = root.findall("atom:entry", ns)
                papers = []

                for entry in entries:
                    # 提取基本信息
                    title = entry.find("atom:title", ns)
                    title_text = title.text.strip().replace("\n", " ") if title is not None else "Unknown"

                    summary = entry.find("atom:summary", ns)
                    abstract = summary.text.strip().replace("\n", " ") if summary is not None else ""

                    # 提取作者
                    authors = []
                    for author in entry.findall("atom:author", ns):
                        name = author.find("atom:name", ns)
                        if name is not None:
                            authors.append(name.text.strip())

                    # 提取日期
                    published = entry.find("atom:published", ns)
                    year = None
                    if published is not None:
                        year_match = re.search(r"(\d{4})", published.text)
                        if year_match:
                            year = int(year_match.group(1))

                    # 提取 arXiv ID 和链接
                    arxiv_id = entry.find("atom:id", ns)
                    arxiv_id_text = arxiv_id.text.strip() if arxiv_id is not None else ""
                    # 从 URL 中提取 ID（如 http://arxiv.org/abs/2301.12345v1 -> 2301.12345）
                    id_match = re.search(r"arxiv\.org/abs/(\d+\.\d+)", arxiv_id_text)
                    clean_id = id_match.group(1) if id_match else arxiv_id_text

                    # PDF 链接
                    pdf_url = f"https://arxiv.org/pdf/{clean_id}.pdf"

                    # 提取分类
                    categories = []
                    for category in entry.findall("atom:category", ns):
                        term = category.get("term")
                        if term:
                            categories.append(term)

                    paper = {
                        "id": clean_id,
                        "source": "arxiv",
                        "title": title_text,
                        "authors": authors,
                        "year": year or 2024,
                        "abstract": abstract,
                        "venue": "arXiv",
                        "url": arxiv_id_text,
                        "pdf_url": pdf_url,
                        "citation_count": 0,  # arXiv 不提供引用数
                        "categories": categories,
                        "doi": "",  # arXiv 论文可能没有 DOI
                    }
                    papers.append(paper)

                # 格式化输出
                content_lines = [
                    f"找到 {len(papers)} 篇 arXiv 预印本：",
                    ""
                ]

                for i, paper in enumerate(papers, 1):
                    title = paper["title"]
                    authors = paper["authors"][:3]
                    year = paper["year"]

                    content_lines.append(f"{i}. {title}")
                    content_lines.append(f"   作者: {', '.join(authors)}")
                    content_lines.append(f"   年份: {year} | arXiv ID: {paper['id']}")
                    content_lines.append("")

                return ToolResult(
                    ok=True,
                    content="\n".join(content_lines),
                    data={"papers": papers}
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    wait_time = 3 * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                    continue
                return ToolResult(
                    ok=False,
                    content=f"❌ arXiv API 请求失败: HTTP {e.response.status_code}",
                    error=f"http_{e.response.status_code}"
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return ToolResult(
                    ok=False,
                    content=f"❌ arXiv 搜索失败: {e}",
                    error="search_failed"
                )

        return ToolResult(
            ok=False,
            content="❌ arXiv 搜索失败：超过最大重试次数",
            error="max_retries_exceeded"
        )
