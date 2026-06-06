"""OpenAlex API 工具。

直接调用 OpenAlex API，提供学术论文搜索和元数据获取功能。
完全免费，无速率限制（礼貌使用），覆盖 2.5 亿+ 论文。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .abstract_utils import abstract_from_openalex_index
from .base import Tool, ToolResult
from .search_validation import clean_search_query, empty_query_result, filter_usable_papers


def _normalize_openalex_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("https://openalex.org/") or text.startswith("https://api.openalex.org/works/"):
        return text.rstrip("/").split("/")[-1]
    return text


def _researcher_email() -> str:
    return (
        os.environ.get("RESEARCHER_EMAIL")
        or os.environ.get("OPENALEX_MAILTO")
        or "researcher@example.com"
    ).strip()


def _work_to_paper(work: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAlex work payload while preserving graph fields."""

    title = work.get("title", "Unknown")
    authors = []
    for authorship in (work.get("authorships") or [])[:10]:
        author = authorship.get("author")
        if isinstance(author, dict):
            name = author.get("display_name", "Unknown")
            if name:
                authors.append(name)

    primary_location = work.get("primary_location", {})
    if isinstance(primary_location, dict):
        source = primary_location.get("source", {})
        venue = source.get("display_name", "Unknown") if isinstance(source, dict) else "Unknown"
    else:
        venue = "Unknown"

    doi = str(work.get("doi") or "")
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")

    openalex_url = str(work.get("id") or "")
    openalex_id = _normalize_openalex_id(openalex_url)
    refs = [_normalize_openalex_id(item) for item in work.get("referenced_works") or [] if item]
    related = [_normalize_openalex_id(item) for item in work.get("related_works") or [] if item]

    paper = {
        "id": openalex_id,
        "canonical_id": openalex_id,
        "canonical_id_source": "openalex",
        "no_openalex_id": False,
        "source": "openalex",
        "title": title,
        "authors": authors if authors else ["Unknown"],
        "year": work.get("publication_year"),
        "abstract": abstract_from_openalex_index(work.get("abstract_inverted_index")),
        "venue": venue,
        "url": f"https://doi.org/{doi}" if doi else openalex_url,
        "citation_count": int(work.get("cited_by_count") or 0),
        "doi": doi,
        "externalIds": {"OpenAlex": openalex_id, **({"DOI": doi} if doi else {})},
        "referenced_works": refs,
        "related_works": related,
        "refs_unavailable": not bool(refs),
        "provenance": {
            "source_tool": "openalex",
            "source_id": openalex_id,
            "source_url": openalex_url,
            "canonical_id": openalex_id,
            "id_source": "openalex",
        },
    }
    for key in ("best_oa_location", "primary_location", "locations", "open_access"):
        value = work.get(key)
        if value not in (None, "", []):
            paper[key] = value
    best_oa = work.get("best_oa_location")
    if isinstance(best_oa, dict):
        pdf_url = str(best_oa.get("pdf_url") or best_oa.get("url_for_pdf") or "").strip()
        if pdf_url:
            paper["pdf_url"] = pdf_url
            paper["open_access_pdf_url"] = pdf_url
    return paper


class OpenAlexSearchParams(BaseModel):
    """搜索 OpenAlex 论文的参数"""
    query: str = Field(..., description="搜索查询字符串")
    per_page: int = Field(default=10, description="每页结果数量（最多200）", ge=1, le=200)
    filter_params: str | None = Field(default=None, description="过滤参数（如 publication_year:>2020）")
    query_bucket: str | None = Field(
        default=None,
        description="可选检索式桶标签，仅作为 ResearchOS 召回意图/provenance，不发送给 OpenAlex，也不决定语义角色。",
    )
    bridge_id: str | None = Field(
        default=None,
        description="可选 bridge_domain_plan.json 中的 bridge_id；只记录召回意图，不代表语义角色。",
    )


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
        query = clean_search_query(kwargs["query"])
        if not query:
            return empty_query_result(self.name, kwargs.get("query"))
        per_page = kwargs.get("per_page", 10)
        filter_params = kwargs.get("filter_params")
        query_bucket = kwargs.get("query_bucket")
        bridge_id = kwargs.get("bridge_id")

        params = {
            "search": query,
            "per-page": per_page,
            "mailto": _researcher_email(),  # 礼貌使用
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

            papers = filter_usable_papers([_work_to_paper(work) for work in results])

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
                    f"   作者: {', '.join(authors)}, 年份: {year if year is not None else 'unknown'}, 发表于: {venue}, 引用: {citations}\n"
                    f"   摘要: {abstract or '无'}"
                )
                content_lines.append("")

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data={
                    "papers": papers,
                    "total": total_count,
                    "query": query,
                    "query_bucket": query_bucket,
                    "bridge_id": bridge_id,
                }
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

        params = {"mailto": _researcher_email()}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                work = response.json()

            paper = _work_to_paper(work)

            # 格式化输出
            content_lines = [
                f"标题: {paper['title']}",
                f"作者: {', '.join(paper['authors'])}",
                f"年份: {paper['year']}",
                f"发表于: {paper['venue']}",
                f"引用数: {paper['citation_count']}",
                "",
                "摘要:",
                paper["abstract"][:500] + "..." if paper["abstract"] and len(paper["abstract"]) > 500 else (paper["abstract"] or "无摘要")
            ]

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
