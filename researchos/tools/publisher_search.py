"""Publisher-specific literature search tools.

These tools complement the open aggregators used by T2.  They return the same
`data.papers` shape as the existing search tools so the runtime can persist
their results to `literature/papers_raw.jsonl` without special cases.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult


def _first(items: Any, default: str = "") -> str:
    if isinstance(items, list) and items:
        return html.unescape(str(items[0] or default))
    if isinstance(items, str):
        return html.unescape(items)
    return html.unescape(default)


def _clean_abstract(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text).strip())


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _year_from_crossref(item: dict[str, Any]) -> int | None:
    published = (
        item.get("published-print")
        or item.get("published-online")
        or item.get("published")
        or item.get("issued")
        or item.get("created")
    )
    if isinstance(published, dict):
        date_parts = published.get("date-parts")
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            return _parse_int(date_parts[0][0], default=0) or None
    return None


def _format_paper_summary(papers: list[dict[str, Any]], total: int, source_label: str) -> str:
    lines = [f"{source_label} 找到 {total} 条记录（返回 {len(papers)} 条）：", ""]
    for index, paper in enumerate(papers, 1):
        authors = paper.get("authors") or ["Unknown"]
        lines.append(f"{index}. {paper.get('title') or 'Unknown'}")
        lines.append(f"   作者: {', '.join(authors[:3])}")
        lines.append(
            "   年份: "
            f"{paper.get('year') if paper.get('year') is not None else 'unknown'} | 期刊/会议: {paper.get('venue') or 'Unknown'}"
        )
        if paper.get("doi"):
            lines.append(f"   DOI: {paper['doi']}")
        lines.append("")
    return "\n".join(lines)


class ElsevierScopusSearchParams(BaseModel):
    """Scopus search parameters."""

    query: str = Field(..., description="Scopus 查询字符串，可使用自然语言或 Scopus query syntax")
    count: int = Field(default=10, description="返回结果数量，Scopus API 单次建议不超过 25", ge=1, le=25)
    start: int = Field(default=0, description="分页起点，从 0 开始", ge=0)
    year_from: int | None = Field(default=None, description="起始发表年份，例如 2020")
    year_to: int | None = Field(default=None, description="结束发表年份，例如 2026")
    sort: str | None = Field(default=None, description="Scopus 排序参数，例如 -citedby-count")
    query_bucket: str | None = Field(
        default=None,
        description="可选检索式桶标签，仅用于 ResearchOS 队列保护，不发送给 Scopus。",
    )


class InformsSearchParams(BaseModel):
    """INFORMS metadata search parameters."""

    query: str = Field(..., description="检索关键词")
    rows: int = Field(default=10, description="返回结果数量", ge=1, le=100)
    year_from: int | None = Field(default=None, description="起始发表年份，例如 2020")
    year_to: int | None = Field(default=None, description="结束发表年份，例如 2026")
    sort: str = Field(default="relevance", description="Crossref 排序方式")
    query_bucket: str | None = Field(
        default=None,
        description="可选检索式桶标签，仅用于 ResearchOS 队列保护，不发送给 Crossref/INFORMS。",
    )
    journal_only: bool = Field(
        default=True,
        description="是否只返回 Crossref type=journal-article，默认过滤掉新闻、杂志、教学案例等非期刊论文记录",
    )


class ElsevierScopusSearchTool(Tool):
    """Search Elsevier Scopus metadata.

    Requires `ELSEVIER_API_KEY`.  `ELSEVIER_INSTTOKEN` and
    `ELSEVIER_ACCESS_TOKEN` are optional and used when available.
    """

    name = "elsevier_scopus_search"
    description = (
        "搜索 Elsevier Scopus 文摘与引文数据库。需要 ELSEVIER_API_KEY；"
        "可选 ELSEVIER_INSTTOKEN/ELSEVIER_ACCESS_TOKEN。"
    )
    parameters_schema = ElsevierScopusSearchParams
    timeout_seconds = 30.0

    def __init__(
        self,
        api_key: str | None = None,
        insttoken: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ELSEVIER_API_KEY")
        self.insttoken = insttoken or os.environ.get("ELSEVIER_INSTTOKEN")
        self.access_token = access_token or os.environ.get("ELSEVIER_ACCESS_TOKEN")
        self.base_url = "https://api.elsevier.com/content/search/scopus"

    async def execute(self, **kwargs: Any) -> ToolResult:
        if not self.api_key:
            return ToolResult(
                ok=False,
                content=(
                    "未配置 ELSEVIER_API_KEY，跳过 Elsevier Scopus 搜索。"
                    "请在 .env 中设置 ELSEVIER_API_KEY；如需机构授权，可同时设置 ELSEVIER_INSTTOKEN。"
                ),
                error="missing_api_key",
            )

        params = ElsevierScopusSearchParams(**kwargs)
        request_params: dict[str, Any] = {
            "query": params.query,
            "count": params.count,
            "start": params.start,
            "field": (
                "dc:identifier,eid,dc:title,dc:creator,prism:publicationName,"
                "prism:coverDate,prism:doi,citedby-count,prism:url,subtypeDescription"
            ),
        }
        if params.year_from or params.year_to:
            start_year = params.year_from or 1800
            end_year = params.year_to or 2100
            request_params["date"] = f"{start_year}-{end_year}"
        if params.sort:
            request_params["sort"] = params.sort

        headers = {
            "Accept": "application/json",
            "X-ELS-APIKey": self.api_key,
        }
        if self.insttoken:
            headers["X-ELS-Insttoken"] = self.insttoken
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self.base_url, params=request_params, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                ok=False,
                content=f"Elsevier Scopus 搜索失败: HTTP {exc.response.status_code}",
                error=f"http_{exc.response.status_code}",
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"Elsevier Scopus 搜索失败: {exc}",
                error="search_failed",
            )

        results = payload.get("search-results", {})
        entries = results.get("entry", [])
        if not isinstance(entries, list):
            entries = []
        papers = [self._normalize_entry(entry) for entry in entries if isinstance(entry, dict)]
        papers = [paper for paper in papers if paper.get("title")]
        total = _parse_int(results.get("opensearch:totalResults"), default=len(papers))
        return ToolResult(
            ok=True,
            content=_format_paper_summary(papers, total, "Elsevier Scopus"),
            data={
                "papers": papers,
                "total": total,
                "source": "elsevier_scopus",
                "query": params.query,
                "query_bucket": params.query_bucket,
            },
        )

    @staticmethod
    def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
        doi = str(entry.get("prism:doi") or "").strip()
        scopus_identifier = str(entry.get("dc:identifier") or "").strip()
        eid = str(entry.get("eid") or "").strip()
        title = html.unescape(str(entry.get("dc:title") or "").strip())
        cover_date = str(entry.get("prism:coverDate") or "")
        year = _parse_int(cover_date[:4], default=0) or None
        url = (
            str(entry.get("prism:url") or "").strip()
            or _link_from_scopus_entry(entry)
            or (f"https://doi.org/{doi}" if doi else "")
        )
        creator = str(entry.get("dc:creator") or "").strip()
        authors = [creator] if creator else ["Unknown"]
        external_ids = {"Scopus": scopus_identifier, "EID": eid}
        if doi:
            external_ids["DOI"] = doi
        return {
            "id": doi or eid or scopus_identifier or title,
            "source": "elsevier_scopus",
            "title": title,
            "authors": authors,
            "year": year,
            "abstract": "",
            "venue": html.unescape(str(entry.get("prism:publicationName") or "").strip()) or "Unknown",
            "url": url,
            "citation_count": _parse_int(entry.get("citedby-count"), default=0),
            "doi": doi,
            "type": str(entry.get("subtypeDescription") or "").strip(),
            "externalIds": external_ids,
        }


class InformsSearchTool(Tool):
    """Search INFORMS publications metadata through Crossref.

    INFORMS PubsOnline does not expose a stable public JSON search API in this
    runtime.  Crossref is the durable metadata route because INFORMS registers
    article DOIs with Crossref and its journals use the `10.1287` DOI prefix.
    """

    name = "informs_search"
    description = "搜索 INFORMS PubsOnline 论文元数据（Crossref DOI prefix: 10.1287）"
    parameters_schema = InformsSearchParams
    timeout_seconds = 30.0

    def __init__(self, email: str | None = None) -> None:
        self.email = email or os.environ.get("RESEARCHER_EMAIL") or "researchos@example.com"
        self.base_url = "https://api.crossref.org/works"

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = InformsSearchParams(**kwargs)
        filters = ["prefix:10.1287"]
        if params.journal_only:
            filters.append("type:journal-article")
        if params.year_from:
            filters.append(f"from-pub-date:{params.year_from}-01-01")
        if params.year_to:
            filters.append(f"until-pub-date:{params.year_to}-12-31")

        request_params = {
            "query": params.query,
            "rows": params.rows,
            "sort": params.sort,
            "filter": ",".join(filters),
            "mailto": self.email,
        }
        headers = {
            "User-Agent": f"ResearchOS/0.1.0 (mailto:{self.email})",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self.base_url, params=request_params, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                ok=False,
                content=f"INFORMS 搜索失败: Crossref HTTP {exc.response.status_code}",
                error=f"http_{exc.response.status_code}",
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"INFORMS 搜索失败: {exc}",
                error="search_failed",
            )

        message = payload.get("message", {})
        items = message.get("items", [])
        if not isinstance(items, list):
            items = []
        papers = [self._normalize_item(item) for item in items if isinstance(item, dict)]
        papers = [paper for paper in papers if paper.get("title")]
        total = _parse_int(message.get("total-results"), default=len(papers))
        return ToolResult(
            ok=True,
            content=_format_paper_summary(papers, total, "INFORMS/Crossref"),
            data={
                "papers": papers,
                "total": total,
                "source": "informs_crossref",
                "query": params.query,
                "query_bucket": params.query_bucket,
            },
        )

    @staticmethod
    def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
        doi = str(item.get("DOI") or "").strip()
        title = _first(item.get("title"), "Unknown").strip()
        authors = []
        for author in item.get("author") or []:
            if not isinstance(author, dict):
                continue
            given = str(author.get("given") or "").strip()
            family = str(author.get("family") or "").strip()
            name = f"{given} {family}".strip()
            if name:
                authors.append(name)
        venue = _first(item.get("container-title"), "Unknown").strip()
        return {
            "id": doi or title,
            "source": "informs_crossref",
            "title": title,
            "authors": authors or ["Unknown"],
            "year": _year_from_crossref(item),
            "abstract": _clean_abstract(item.get("abstract")),
            "venue": venue or "Unknown",
            "url": str(item.get("URL") or (f"https://doi.org/{doi}" if doi else "")).strip(),
            "citation_count": _parse_int(item.get("is-referenced-by-count"), default=0),
            "doi": doi,
            "type": str(item.get("type") or "").strip(),
            "publisher": str(item.get("publisher") or "INFORMS").strip(),
            "externalIds": {"DOI": doi, "CrossrefPrefix": "10.1287"},
        }


def _link_from_scopus_entry(entry: dict[str, Any]) -> str:
    links = entry.get("link")
    if not isinstance(links, list):
        return ""
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("@ref") in {"scopus", "self"} and link.get("@href"):
            return str(link["@href"])
    for link in links:
        if isinstance(link, dict) and link.get("@href"):
            return str(link["@href"])
    return ""
