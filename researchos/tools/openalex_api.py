"""OpenAlex API 工具。

直接调用 OpenAlex API，提供学术论文搜索和元数据获取功能。
完全免费，无速率限制（礼貌使用），覆盖 2.5 亿+ 论文。
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult


class OpenAlexSearchParams(BaseModel):
    """搜索 OpenAlex 论文的参数"""
    query: str = Field(..., description="搜索查询字符串")
    per_page: int = Field(default=10, description="每页结果数量（最多200）", ge=1, le=200)
    filter_params: str | None = Field(default=None, description="过滤参数（如 publication_year:>2020）")


class OpenAlexGetWorkParams(BaseModel):
    """获取 OpenAlex 论文详情的参数"""
    work_id: str = Field(..., description="论文ID（OpenAlex ID、DOI 等）")


class OpenAlexSearchTool(Tool):
    """搜索 OpenAlex 学术论文。

    使用 OpenAlex API 搜索学术论文。
    完全免费，无速率限制，覆盖 2.5 亿+ 论文。
    """

    name = "openalex_search"
    description = "搜索 OpenAlex 学术论文数据库（2.5亿+ 论文）"
    parameters_schema = OpenAlexSearchParams
    timeout_seconds = 30.0

    def __init__(self):
        self.base_url = "https://api.openalex.org"

    async def execute(self, **kwargs) -> ToolResult:
        query = kwargs["query"]
        per_page = kwargs.get("per_page", 10)
        filter_params = kwargs.get("filter_params")

        params = {
            "search": query,
            "per-page": per_page,
            "mailto": "researchos@example.com",  # 礼貌使用
        }

        if filter_params:
            params["filter"] = filter_params

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.base_url}/works", params=params)
                response.raise_for_status()
                data = response.json()

            results = data.get("results", [])
            meta = data.get("meta", {})
            total_count = meta.get("count", 0)

            papers = []
            for work in results:
                # 提取基本信息
                title = work.get("title", "Unknown")

                # 提取作者
                authorships = work.get("authorships", [])
                authors = []
                for authorship in authorships[:10]:  # 最多取前10个作者
                    author = authorship.get("author")
                    if author and isinstance(author, dict):
                        name = author.get("display_name", "Unknown")
                        authors.append(name)

                # 提取年份
                publication_year = work.get("publication_year")

                # 提取摘要
                abstract = None
                abstract_inverted = work.get("abstract_inverted_index")
                if abstract_inverted:
                    # 重建摘要（从倒排索引）
                    words = [""] * 1000  # 预分配空间
                    for word, positions in abstract_inverted.items():
                        for pos in positions:
                            if pos < len(words):
                                words[pos] = word
                    abstract = " ".join([w for w in words if w]).strip()

                # 提取 venue
                primary_location = work.get("primary_location", {})
                if primary_location:
                    source = primary_location.get("source", {})
                    venue = source.get("display_name", "Unknown") if source else "Unknown"
                else:
                    venue = "Unknown"

                # 提取引用数
                cited_by_count = work.get("cited_by_count", 0)

                # 提取 DOI
                doi = work.get("doi", "")
                if doi and doi.startswith("https://doi.org/"):
                    doi = doi.replace("https://doi.org/", "")

                # 提取 OpenAlex ID
                openalex_id = work.get("id", "")

                # 提取 URL
                url = doi if doi else openalex_id

                paper = {
                    "id": openalex_id.split("/")[-1] if openalex_id else "",
                    "source": "openalex",
                    "title": title,
                    "authors": authors if authors else ["Unknown"],
                    "year": publication_year or 2024,
                    "abstract": abstract or "",
                    "venue": venue,
                    "url": f"https://doi.org/{doi}" if doi else openalex_id,
                    "citation_count": cited_by_count,
                    "doi": doi,
                }
                papers.append(paper)

            # 格式化输出
            content_lines = [
                f"找到 {total_count} 篇相关论文（返回前 {len(papers)} 篇）：",
                ""
            ]

            for i, paper in enumerate(papers, 1):
                title = paper["title"]
                authors = paper["authors"][:3]
                year = paper["year"]
                citations = paper["citation_count"]
                abstract = paper.get("abstract", "") or ""
                venue = paper.get("venue", "")

                # 摘要截断到 300 字符
                if len(abstract) > 300:
                    abstract = abstract[:300] + "..."

                content_lines.append(
                    f"{i}. {title}\n"
                    f"   作者: {', '.join(authors)}, 年份: {year}, 发表于: {venue}, 引用: {citations}\n"
                    f"   摘要: {abstract or '无'}"
                )
                content_lines.append("")

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data={"papers": papers, "total": total_count}
            )

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            return ToolResult(
                ok=False,
                content=f"❌ OpenAlex 搜索失败: {e}\n详细错误:\n{error_details}",
                error="search_failed"
            )


class OpenAlexGetWorkTool(Tool):
    """获取 OpenAlex 论文详情。

    根据论文 ID 获取完整的论文元数据。
    支持 OpenAlex ID、DOI 等格式。
    """

    name = "openalex_get_work"
    description = "获取 OpenAlex 论文的详细信息"
    parameters_schema = OpenAlexGetWorkParams
    timeout_seconds = 30.0

    def __init__(self):
        self.base_url = "https://api.openalex.org"

    async def execute(self, **kwargs) -> ToolResult:
        work_id = kwargs["work_id"]

        # 处理不同格式的 ID
        if work_id.startswith("W"):
            url = f"{self.base_url}/works/{work_id}"
        elif work_id.startswith("10."):
            url = f"{self.base_url}/works/https://doi.org/{work_id}"
        else:
            url = f"{self.base_url}/works/{work_id}"

        params = {"mailto": "researchos@example.com"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                work = response.json()

            # 提取信息（与搜索类似）
            title = work.get("title", "Unknown")

            authorships = work.get("authorships", [])
            authors = []
            for a in authorships:
                author = a.get("author")
                if author and isinstance(author, dict):
                    name = author.get("display_name", "Unknown")
                    authors.append(name)

            publication_year = work.get("publication_year")

            abstract = None
            abstract_inverted = work.get("abstract_inverted_index")
            if abstract_inverted:
                words = [""] * 1000
                for word, positions in abstract_inverted.items():
                    for pos in positions:
                        if pos < len(words):
                            words[pos] = word
                abstract = " ".join([w for w in words if w]).strip()

            primary_location = work.get("primary_location", {})
            if primary_location:
                source = primary_location.get("source", {})
                venue = source.get("display_name", "Unknown") if source else "Unknown"
            else:
                venue = "Unknown"

            cited_by_count = work.get("cited_by_count", 0)

            doi = work.get("doi", "")
            if doi and doi.startswith("https://doi.org/"):
                doi = doi.replace("https://doi.org/", "")

            # 格式化输出
            content_lines = [
                f"标题: {title}",
                f"作者: {', '.join(authors)}",
                f"年份: {publication_year}",
                f"发表于: {venue}",
                f"引用数: {cited_by_count}",
                "",
                "摘要:",
                abstract[:500] + "..." if abstract and len(abstract) > 500 else (abstract or "无摘要")
            ]

            paper = {
                "id": work.get("id", "").split("/")[-1],
                "source": "openalex",
                "title": title,
                "authors": authors,
                "year": publication_year or 2024,
                "abstract": abstract or "",
                "venue": venue,
                "url": f"https://doi.org/{doi}" if doi else work.get("id", ""),
                "citation_count": cited_by_count,
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
                    content=f"❌ 论文未找到: {work_id}",
                    error="work_not_found"
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
                error="get_work_failed"
            )
