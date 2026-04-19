"""多源论文API工具 - 添加Crossref、Europe PMC、PubMed等免费API

支持的API：
1. arXiv - 预印本（可能有速率限制）
2. Crossref - DOI元数据（无需注册，建议带mailto）
3. Europe PMC - 生物医学论文（无需注册）
4. PubMed/NCBI - 生物医学论文（无需API key，但建议使用）
5. Semantic Scholar - 学术论文（需要API key）
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from html import unescape
from typing import Any, Literal
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

try:
    import httpx
except ModuleNotFoundError:
    httpx = None

from pydantic import BaseModel, Field

from ..runtime.errors import ToolRuntimeError
from .base import Tool, ToolResult


class MultiSourceSearchParams(BaseModel):
    query: str = Field(..., min_length=1, description="搜索关键词")
    max_results: int = Field(20, ge=1, le=100, description="最多返回多少篇论文")
    sources: list[str] = Field(
        default=["crossref", "arxiv", "europepmc"],
        description="要使用的数据源列表，按优先级排序"
    )


class MultiSourceSearchTool(Tool):
    """多源论文搜索工具 - 支持Crossref、arXiv、Europe PMC等免费API"""

    name = "multi_source_search"
    description = (
        "从多个免费学术数据库搜索论文。"
        "支持Crossref（DOI元数据）、arXiv（预印本）、Europe PMC（生物医学）等。"
        "自动处理速率限制和API失败，返回真实可验证的论文数据。"
    )
    parameters_schema = MultiSourceSearchParams
    timeout_seconds = 90.0

    def __init__(self, email: str | None = None):
        """
        Args:
            email: 用于Crossref polite pool的邮箱（可选但推荐）
        """
        self.email = email or os.environ.get("RESEARCHER_EMAIL", "researcher@example.com")

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MultiSourceSearchParams(**kwargs)

        if httpx is None:
            return ToolResult(
                ok=False,
                content="缺少httpx依赖",
                error="dependency_missing"
            )

        all_papers = []
        source_stats = {}

        # 按优先级尝试各个数据源
        for source in params.sources:
            try:
                if source == "crossref":
                    papers = await self._search_crossref(params.query, params.max_results)
                elif source == "arxiv":
                    papers = await self._search_arxiv(params.query, params.max_results)
                elif source == "europepmc":
                    papers = await self._search_europepmc(params.query, params.max_results)
                elif source == "pubmed":
                    papers = await self._search_pubmed(params.query, params.max_results)
                else:
                    continue

                source_stats[source] = len(papers)
                all_papers.extend(papers)

                # 如果已经获取足够的论文，可以提前停止
                if len(all_papers) >= params.max_results:
                    break

                # 避免速率限制
                await asyncio.sleep(1)

            except Exception as e:
                source_stats[source] = f"failed: {str(e)[:50]}"
                continue

        # 去重（基于DOI和标题）
        unique_papers = self._deduplicate_papers(all_papers)

        # 限制数量
        unique_papers = unique_papers[:params.max_results]

        return ToolResult(
            ok=True,
            content=self._format_papers(unique_papers, source_stats),
            data={
                "papers": unique_papers,
                "count": len(unique_papers),
                "source_stats": source_stats
            }
        )

    async def _search_crossref(self, query: str, max_results: int) -> list[dict]:
        """搜索Crossref（DOI元数据）"""
        url = f"https://api.crossref.org/works?query={quote_plus(query)}&rows={max_results}&mailto={self.email}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            items = data.get("message", {}).get("items", [])

            papers = []
            for item in items:
                # 提取作者
                authors = []
                for author in item.get("author", []):
                    given = author.get("given", "")
                    family = author.get("family", "")
                    name = f"{given} {family}".strip() or "Unknown"
                    authors.append({"name": name})

                # 提取年份
                year = None
                if "published" in item:
                    date_parts = item["published"].get("date-parts", [[]])[0]
                    if date_parts:
                        year = date_parts[0]

                # 提取DOI
                doi = item.get("DOI", "")

                papers.append({
                    "id": f"doi:{doi}" if doi else item.get("title", [""])[0][:50],
                    "source": "crossref",
                    "title": item.get("title", [""])[0],
                    "authors": authors,
                    "year": year,
                    "abstract": item.get("abstract", ""),
                    "venue": item.get("container-title", [""])[0] if item.get("container-title") else "",
                    "doi": doi,
                    "citation_count": item.get("is-referenced-by-count", 0),
                    "url": f"https://doi.org/{doi}" if doi else "",
                    "externalIds": {"DOI": doi} if doi else {}
                })

            return papers

    async def _search_arxiv(self, query: str, max_results: int) -> list[dict]:
        """搜索arXiv（预印本）"""
        url = f"https://export.arxiv.org/api/query?search_query=all:{quote_plus(query)}&start=0&max_results={max_results}&sortBy=relevance"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)

            # arXiv可能返回429，这是正常的
            if response.status_code == 429:
                return []

            response.raise_for_status()

            root = ET.fromstring(response.text)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}

            papers = []
            for entry in root.findall('atom:entry', ns):
                def text(tag: str) -> str:
                    value = entry.findtext(tag, default="", namespaces=ns)
                    return unescape(" ".join(value.split()))

                authors = [
                    {"name": author.findtext("atom:name", default="", namespaces=ns)}
                    for author in entry.findall("atom:author", ns)
                ]

                published = text("atom:published")
                year = None
                if published:
                    year = datetime.fromisoformat(published.replace("Z", "+00:00")).year

                identifier = text("atom:id").rstrip("/").split("/")[-1]
                summary = text("atom:summary")

                papers.append({
                    "id": f"arxiv:{identifier}",
                    "source": "arxiv",
                    "title": text("atom:title"),
                    "authors": authors,
                    "year": year,
                    "abstract": summary,
                    "venue": "arXiv",
                    "doi": "",
                    "citation_count": 0,
                    "url": text("atom:id"),
                    "externalIds": {"ArXiv": identifier}
                })

            return papers

    async def _search_europepmc(self, query: str, max_results: int) -> list[dict]:
        """搜索Europe PMC（生物医学论文）"""
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={quote_plus(query)}&format=json&pageSize={max_results}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            results = data.get("resultList", {}).get("result", [])

            papers = []
            for item in results:
                # 提取作者
                author_string = item.get("authorString", "")
                authors = [{"name": name.strip()} for name in author_string.split(",") if name.strip()]

                # 提取DOI
                doi = item.get("doi", "")

                # 提取ID
                pmid = item.get("pmid", "")
                pmcid = item.get("pmcid", "")
                paper_id = pmcid or pmid or doi

                papers.append({
                    "id": f"pmc:{paper_id}" if paper_id else item.get("title", "")[:50],
                    "source": "europepmc",
                    "title": item.get("title", ""),
                    "authors": authors,
                    "year": int(item.get("pubYear", 0)) if item.get("pubYear") else None,
                    "abstract": item.get("abstractText", ""),
                    "venue": item.get("journalTitle", ""),
                    "doi": doi,
                    "citation_count": int(item.get("citedByCount", 0)),
                    "url": f"https://europepmc.org/article/MED/{pmid}" if pmid else "",
                    "externalIds": {
                        "PMID": pmid,
                        "PMCID": pmcid,
                        "DOI": doi
                    }
                })

            return papers

    async def _search_pubmed(self, query: str, max_results: int) -> list[dict]:
        """搜索PubMed（生物医学论文）"""
        # 第一步：搜索获取ID列表
        search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={quote_plus(query)}&retmax={max_results}&retmode=json"

        async with httpx.AsyncClient(timeout=30.0) as client:
            search_response = await client.get(search_url)
            search_response.raise_for_status()

            search_data = search_response.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                return []

            # 第二步：获取详细信息
            ids = ",".join(id_list)
            fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={ids}&retmode=json"

            fetch_response = await client.get(fetch_url)
            fetch_response.raise_for_status()

            fetch_data = fetch_response.json()
            results = fetch_data.get("result", {})

            papers = []
            for pmid in id_list:
                item = results.get(pmid, {})
                if not item:
                    continue

                # 提取作者
                authors = [{"name": author.get("name", "")} for author in item.get("authors", [])]

                # 提取DOI
                article_ids = item.get("articleids", [])
                doi = ""
                for aid in article_ids:
                    if aid.get("idtype") == "doi":
                        doi = aid.get("value", "")
                        break

                papers.append({
                    "id": f"pubmed:{pmid}",
                    "source": "pubmed",
                    "title": item.get("title", ""),
                    "authors": authors,
                    "year": int(item.get("pubdate", "").split()[0]) if item.get("pubdate") else None,
                    "abstract": "",  # PubMed summary不包含abstract
                    "venue": item.get("source", ""),
                    "doi": doi,
                    "citation_count": 0,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "externalIds": {"PMID": pmid, "DOI": doi}
                })

            return papers

    def _deduplicate_papers(self, papers: list[dict]) -> list[dict]:
        """去重论文（基于DOI和标题）"""
        seen_dois = set()
        seen_titles = set()
        unique = []

        for paper in papers:
            # DOI去重
            doi = paper.get("doi", "").strip().lower()
            if doi and doi in seen_dois:
                continue
            if doi:
                seen_dois.add(doi)

            # 标题去重
            title = paper.get("title", "").strip().lower()
            if title in seen_titles:
                continue
            if title:
                seen_titles.add(title)

            unique.append(paper)

        return unique

    @staticmethod
    def _format_papers(papers: list[dict], source_stats: dict) -> str:
        """格式化论文列表"""
        if not papers:
            return f"未检索到论文。数据源统计: {source_stats}"

        lines = [f"检索到 {len(papers)} 篇论文（数据源统计: {source_stats}）\n"]

        for i, paper in enumerate(papers, 1):
            authors = ", ".join(a.get("name", "") for a in paper.get("authors", [])[:3])
            if len(paper.get("authors", [])) > 3:
                authors += " et al."

            lines.append(
                f"[{i}] {paper.get('title', '?')} "
                f"({authors or 'Unknown'}, {paper.get('year', '?')}) "
                f"- {paper.get('source', '?')}, citations={paper.get('citation_count', 0)}"
            )

        return "\n".join(lines)
