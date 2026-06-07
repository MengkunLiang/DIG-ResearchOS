"""多源论文API工具 - 添加Crossref、Europe PMC、PubMed、INFORMS等免费API

支持的API：
1. arXiv - 预印本（可能有速率限制）
2. Crossref - DOI元数据（无需注册，建议带mailto）
3. Europe PMC - 生物医学论文（无需注册）
4. PubMed/NCBI - 生物医学论文（无需API key，但建议使用）
5. Semantic Scholar - 学术论文（需要API key）
6. INFORMS/Crossref - INFORMS 期刊 DOI prefix 元数据
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

try:
    import httpx
except ModuleNotFoundError:
    httpx = None

from pydantic import BaseModel, Field

from ..runtime.errors import ToolRuntimeError
from .abstract_utils import clean_abstract
from .base import Tool, ToolResult
from .openalex_api import _researcher_email as _openalex_researcher_email
from .openalex_api import _work_to_paper as _openalex_work_to_paper


def _extract_crossref_references(item: dict[str, Any], limit: int = 80) -> list[dict[str, str]]:
    """Extract DOI/title aliases from Crossref references for local graph matching."""

    references: list[dict[str, str]] = []
    for ref in item.get("reference") or []:
        if not isinstance(ref, dict):
            continue
        doi = str(ref.get("DOI") or ref.get("doi") or "").strip()
        title = str(ref.get("article-title") or ref.get("unstructured") or "").strip()
        year = str(ref.get("year") or "").strip()
        if not doi and not title:
            continue
        record: dict[str, str] = {}
        if doi:
            record["doi"] = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
            record["id"] = record["doi"]
        if title:
            record["title"] = title
        if year:
            record["year"] = year
        references.append(record)
        if len(references) >= limit:
            break
    return references


class MultiSourceSearchParams(BaseModel):
    query: str = Field(..., description="搜索关键词")
    max_results: int = Field(20, ge=1, le=100, description="最多返回多少篇论文")
    query_bucket: str | None = Field(
        None,
        description=(
            "可选检索式桶标签，仅作为 ResearchOS 召回意图/provenance；例如 core, baseline, "
            "evaluation, adjacent_field, theory_bridge。不会改变实际检索，也不决定语义角色。"
        ),
    )
    bridge_id: str | None = Field(
        None,
        description="可选 bridge_domain_plan.json 中的 bridge_id；只记录召回意图，不代表语义角色。",
    )
    sources: list[str] = Field(
        default=["openalex", "crossref", "arxiv", "informs", "europepmc"],
        description="要使用的数据源列表，按优先级排序"
    )
    try_all_sources: bool = Field(
        default=True,
        description=(
            "默认尝试所有配置的数据源，再去重截断。设为 false 时允许达到 "
            "max_results 后提前停止，用于显式节省 API 调用。"
        ),
    )


class MultiSourceSearchTool(Tool):
    """多源论文搜索工具 - 支持Crossref、arXiv、INFORMS、Europe PMC等免费API"""

    name = "multi_source_search"
    description = (
        "从多个免费学术数据库搜索论文。"
        "支持Crossref（DOI元数据）、arXiv（预印本）、INFORMS（10.1287 DOI prefix）、Europe PMC（生物医学）等。"
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
        query = _clean_query(params.query)
        if not query:
            return ToolResult(
                ok=False,
                content=(
                    "检索 query 不能为空。请先基于 project.yaml、seed papers/ideas "
                    "和 domain_profile 设计具体检索式，再调用 multi_source_search。"
                ),
                error="empty_query",
                data={"query": params.query},
            )
        params.query = query

        if httpx is None:
            return ToolResult(
                ok=False,
                content="缺少httpx依赖",
                error="dependency_missing"
            )

        all_papers = []
        source_stats = {}

        # 按优先级尝试各个数据源
        for idx, source in enumerate(params.sources):
            try:
                if source == "crossref":
                    papers = await self._search_crossref(params.query, params.max_results)
                elif source == "openalex":
                    papers = await self._search_openalex(params.query, params.max_results)
                elif source == "arxiv":
                    papers = await self._search_arxiv(params.query, params.max_results)
                elif source == "europepmc":
                    papers = await self._search_europepmc(params.query, params.max_results)
                elif source == "pubmed":
                    papers = await self._search_pubmed(params.query, params.max_results)
                elif source == "informs":
                    papers = await self._search_informs(params.query, params.max_results)
                else:
                    continue

                papers = [paper for paper in papers if _is_usable_search_record(paper)]
                source_stats[source] = len(papers)
                all_papers.extend(papers)

                # 默认尝试所有源。这样同一篇论文若在 OpenAlex/Crossref/arXiv/
                # Europe PMC 等多个源出现，后续去重合并能保留 DOI、摘要、
                # PDF/OA hint 和 references，不会因为早停而损失 metadata。
                if not params.try_all_sources and len(all_papers) >= params.max_results:
                    break

                # 避免速率限制：每个数据源之间间隔2秒
                await asyncio.sleep(2)

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
                "source_stats": source_stats,
                "query": params.query,
                "query_bucket": params.query_bucket,
                "bridge_id": params.bridge_id,
            }
        )

    async def _search_openalex(self, query: str, max_results: int) -> list[dict]:
        """搜索 OpenAlex，保留 OpenAlex ID、引用边和开放获取位置。"""
        url = "https://api.openalex.org/works"
        params = {
            "search": query,
            "per-page": max_results,
            "mailto": _openalex_researcher_email(),
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])
            papers = [_openalex_work_to_paper(work) for work in results if isinstance(work, dict)]
            return [paper for paper in papers if _is_usable_search_record(paper)]

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

                paper = {
                    "id": f"doi:{doi}" if doi else item.get("title", [""])[0][:50],
                    "source": "crossref",
                    "title": item.get("title", [""])[0],
                    "authors": authors,
                    "year": year,
                    "abstract": clean_abstract(item.get("abstract")),
                    "venue": item.get("container-title", [""])[0] if item.get("container-title") else "",
                    "doi": doi,
                    "citation_count": item.get("is-referenced-by-count", 0),
                    "url": f"https://doi.org/{doi}" if doi else "",
                    "externalIds": {"DOI": doi} if doi else {}
                }
                references = _extract_crossref_references(item)
                if references:
                    paper["references"] = references
                    paper["referenced_works"] = references
                    paper["reference_count"] = item.get("reference-count", len(references))
                else:
                    paper["reference_count"] = item.get("reference-count", 0)
                if _is_usable_search_record(paper):
                    papers.append(paper)

            return papers

    async def _search_informs(self, query: str, max_results: int) -> list[dict]:
        """搜索 INFORMS/Crossref（10.1287 DOI prefix）。"""
        filters = "prefix:10.1287,type:journal-article"
        url = (
            "https://api.crossref.org/works"
            f"?query={quote_plus(query)}&rows={max_results}&filter={filters}&mailto={self.email}"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            items = data.get("message", {}).get("items", [])

            papers = []
            for item in items:
                authors = []
                for author in item.get("author", []):
                    given = author.get("given", "")
                    family = author.get("family", "")
                    name = f"{given} {family}".strip() or "Unknown"
                    authors.append({"name": name})

                year = None
                published = (
                    item.get("published-print")
                    or item.get("published-online")
                    or item.get("published")
                    or item.get("issued")
                )
                date_parts = (published or {}).get("date-parts", [[]])[0]
                if date_parts:
                    year = date_parts[0]

                doi = item.get("DOI", "")
                title = item.get("title", [""])[0] if item.get("title") else ""
                venue = item.get("container-title", [""])[0] if item.get("container-title") else ""

                paper = {
                    "id": f"doi:{doi}" if doi else title[:50],
                    "source": "informs_crossref",
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "abstract": clean_abstract(item.get("abstract")),
                    "venue": venue,
                    "doi": doi,
                    "citation_count": item.get("is-referenced-by-count", 0),
                    "url": f"https://doi.org/{doi}" if doi else item.get("URL", ""),
                    "externalIds": {"DOI": doi, "CrossrefPrefix": "10.1287"} if doi else {"CrossrefPrefix": "10.1287"},
                }
                references = _extract_crossref_references(item)
                if references:
                    paper["references"] = references
                    paper["referenced_works"] = references
                    paper["reference_count"] = item.get("reference-count", len(references))
                else:
                    paper["reference_count"] = item.get("reference-count", 0)
                if _is_usable_search_record(paper):
                    papers.append(paper)

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
                    return clean_abstract(" ".join(value.split()))

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

                paper = {
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
                    "pdf_url": f"https://arxiv.org/pdf/{identifier}.pdf",
                    "externalIds": {"ArXiv": identifier}
                }
                if _is_usable_search_record(paper):
                    papers.append(paper)

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

                paper = {
                    "id": f"pmc:{paper_id}" if paper_id else item.get("title", "")[:50],
                    "source": "europepmc",
                    "title": item.get("title", ""),
                    "authors": authors,
                    "year": int(item.get("pubYear", 0)) if item.get("pubYear") else None,
                    "abstract": clean_abstract(item.get("abstractText")),
                    "venue": item.get("journalTitle", ""),
                    "doi": doi,
                    "citation_count": int(item.get("citedByCount", 0)),
                    "url": f"https://europepmc.org/article/MED/{pmid}" if pmid else "",
                    "externalIds": {
                        "PMID": pmid,
                        "PMCID": pmcid,
                        "DOI": doi
                    }
                }
                full_text_urls = _extract_europepmc_full_text_urls(item)
                if full_text_urls:
                    paper["full_text_url"] = full_text_urls[0]
                    paper["open_access_locations"] = [{"url": url} for url in full_text_urls]
                if pmcid:
                    paper["pmc_pdf_url"] = f"https://europepmc.org/articles/{pmcid}?pdf=render"
                if _is_usable_search_record(paper):
                    papers.append(paper)

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

                paper = {
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
                }
                if _is_usable_search_record(paper):
                    papers.append(paper)

            return papers

    def _deduplicate_papers(self, papers: list[dict]) -> list[dict]:
        """去重论文（基于 DOI 和标题），并合并后续来源的更完整 metadata。"""

        unique: list[dict] = []
        index: dict[str, int] = {}

        for paper in papers:
            keys = _paper_identity_keys(paper)
            existing_idx = next((index[key] for key in keys if key in index), None)
            if existing_idx is None:
                index_pos = len(unique)
                unique.append(dict(paper))
                for key in keys:
                    index.setdefault(key, index_pos)
                continue

            merged = _merge_paper_records(unique[existing_idx], paper)
            unique[existing_idx] = merged
            for key in _paper_identity_keys(merged):
                index.setdefault(key, existing_idx)

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


def _clean_query(value: Any) -> str:
    return " ".join(str(value or "").split())


def _paper_identity_keys(paper: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    doi = str(paper.get("doi") or (paper.get("externalIds") or {}).get("DOI") or "").strip().casefold()
    doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/").removeprefix("doi:")
    if doi:
        keys.append(f"doi:{doi}")
    arxiv_id = str((paper.get("externalIds") or {}).get("ArXiv") or "").strip().casefold()
    if arxiv_id:
        keys.append(f"arxiv:{arxiv_id.removeprefix('arxiv:')}")
    title = " ".join(str(paper.get("title") or "").casefold().split())
    if title:
        keys.append(f"title:{title}")
    return keys


def _merge_paper_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge duplicate records without losing richer PDF/OA/reference metadata."""

    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, "", []):
            continue
        current = merged.get(key)
        if current in (None, "", []):
            merged[key] = value
            continue
        if key == "externalIds" and isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **{k: v for k, v in value.items() if v not in (None, "", [])}}
        elif key in {
            "references",
            "referenced_works",
            "related_works",
            "locations",
            "oa_locations",
            "open_access_locations",
            "openAccessLocations",
            "open_access_pdfs",
        }:
            if isinstance(current, list) and isinstance(value, list):
                merged[key] = _dedupe_list_payload([*current, *value])
        elif key in {
            "openAccessPdf",
            "open_access_pdf",
            "oa_pdf",
            "open_access",
            "best_oa_location",
            "primary_location",
        } and isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **{k: v for k, v in value.items() if v not in (None, "", [])}}
        elif key == "citation_count":
            try:
                merged[key] = max(int(current or 0), int(value or 0))
            except (TypeError, ValueError):
                pass
        elif key == "abstract":
            if len(str(value)) > len(str(current)):
                merged[key] = value
        elif key == "source":
            sources = [item for item in str(current).split("+") if item]
            incoming_source = str(value)
            if incoming_source and incoming_source not in sources:
                sources.append(incoming_source)
                merged[key] = "+".join(sources)
    return merged


def _dedupe_list_payload(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        key = json_key = str(item)
        if isinstance(item, dict):
            key = str(item.get("doi") or item.get("id") or item.get("url") or item.get("title") or item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _extract_europepmc_full_text_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    raw = item.get("fullTextUrlList")
    if isinstance(raw, dict):
        raw_urls = raw.get("fullTextUrl") or []
    else:
        raw_urls = raw if isinstance(raw, list) else []
    for entry in raw_urls:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


def _is_usable_search_record(paper: dict[str, Any]) -> bool:
    """Drop empty aggregator records before schema validation/persistence.

    Crossref occasionally returns DOI shell records with no title/authors/year.
    Keeping them in the returned batch can make the runtime reject the whole
    raw append. This filter is mechanical metadata hygiene, not relevance
    judgment.
    """

    title = str(paper.get("title") or "").strip()
    paper_id = str(paper.get("id") or paper.get("doi") or "").strip()
    if not title:
        return False
    if title.casefold() in {"unknown", "untitled"} and not paper_id:
        return False
    return True
