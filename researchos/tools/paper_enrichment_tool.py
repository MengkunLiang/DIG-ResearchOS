"""论文数据增强工具的 Tool 包装器。"""

from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - 依赖是否安装取决于环境
    httpx = None

from pydantic import BaseModel, Field

from ..agents._common import normalize_text_key
from ..literature_identity import find_matching_seed_pdf, normalize_loose_identity_key
from .base import Tool, ToolResult
from .abstract_utils import clean_abstract, abstract_from_openalex_index
from .crossref_api import _extract_crossref_references
from .paper_enrichment import (
    apply_semantic_screening,
    build_access_audit,
    build_deep_read_queue,
    enrich_papers,
    detect_duplicate_queries,
    analyze_dedup_rate,
)
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


def _title_similarity(a: str, b: str) -> float:
    """Loose title similarity for metadata backfill matching only."""

    left = normalize_loose_identity_key(a)
    right = normalize_loose_identity_key(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _researcher_email() -> str:
    """Return a configured contact email, if the user provided one."""

    return (
        os.environ.get("RESEARCHER_EMAIL")
        or os.environ.get("OPENALEX_MAILTO")
        or ""
    ).strip()


def _openalex_params(**extra: Any) -> dict[str, Any]:
    params = {key: value for key, value in extra.items() if value not in (None, "")}
    email = _researcher_email()
    if email:
        params["mailto"] = email
    return params


def _crossref_headers() -> dict[str, str]:
    email = _researcher_email() or "researcher@example.com"
    return {"User-Agent": f"ResearchOS/0.1.0 (mailto:{email})"}


class EnrichPapersParams(BaseModel):
    """enrich_papers 工具的参数。"""
    papers: list[dict[str, Any]] = Field(
        ...,
        description="原始论文列表",
    )
    keywords: list[str] | None = Field(
        None,
        description="关键词列表（可选，用于生成更具体的 why_relevant）",
    )
    domain_profile: dict[str, Any] | None = Field(
        None,
        description="LLM 归纳的领域 profile；工具只用于机械补全和 provenance，不替代领域判断。",
    )
    llm_annotations: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = Field(
        None,
        description=(
            "LLM 对论文的结构化标注。可覆盖 source_type、why_relevant、"
            "method_family、domain_tags、evidence_level 等字段；没有标注时工具只做保守补齐。"
        ),
    )


class EnrichPapersTool(Tool):
    """增强论文数据，自动补充缺失字段。

    功能：
    - 优先应用 LLM 提供的 source_type / why_relevant / method_family / domain_tags 等标注
    - 对缺失字段做保守 schema 补全
    - 转换 authors 格式（对象数组 -> 字符串数组）
    - 补充缺失的必需字段，不替代 LLM 的学术判断
    - 标记数据质量问题

    使用场景：
    - 在保存 papers_dedup.jsonl 之前调用
    - 确保数据符合 schema 要求
    """

    name = "enrich_papers"
    description = "增强论文数据并补齐 schema；学术判断优先来自 LLM annotation，工具只给保守 hint"
    parameters_schema = EnrichPapersParams
    timeout_seconds = 30.0

    async def execute(self, **kwargs) -> ToolResult:
        papers = kwargs["papers"]
        keywords = kwargs.get("keywords")
        domain_profile = kwargs.get("domain_profile")
        llm_annotations = kwargs.get("llm_annotations")

        if not papers:
            return ToolResult(
                ok=False,
                content="❌ 论文列表为空",
                error="empty_papers"
            )

        try:
            enriched = enrich_papers(
                papers,
                keywords=keywords,
                domain_profile=domain_profile,
                llm_annotations=llm_annotations,
            )

            # 统计数据质量
            missing_abstract_count = sum(1 for p in enriched if p.get("_missing_abstract"))

            content_lines = [
                f"✅ 成功增强 {len(enriched)} 篇论文的数据",
                "",
                "增强内容：",
                "- 优先应用 LLM annotation（如提供）",
                "- 对缺失 source_type / why_relevant 做保守补全并标记需复核",
                "- 生成 access_level_hint；不把 metadata 可读性伪装成 FULL/PARTIAL 证据",
                "- 转换 authors 格式（对象数组 -> 字符串数组）",
                "- 补充缺失的必需字段",
            ]

            if missing_abstract_count > 0:
                content_lines.append("")
                content_lines.append(f"⚠️ 数据质量提示：{missing_abstract_count} 篇论文缺少摘要")
                content_lines.append("   建议：使用 MCP Semantic Scholar 工具补充摘要")

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data={"papers": enriched}
            )

        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 数据增强失败: {e}",
                error="enrichment_failed"
            )


class ApplySemanticScreeningParams(BaseModel):
    screenings: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Scout Agent/LLM 产出的逐论文 semantic_screen 判定列表。工具只合并写盘，"
            "不判断论文是否 core/bridge。"
        ),
    )
    papers_path: str = Field(
        default="literature/papers_raw.jsonl",
        description=(
            "要合并 semantic_screen 的论文池。T2 正常检索中建议合并到 papers_raw.jsonl，"
            "runtime 收尾会保留到 papers_dedup/papers_verified；若已完成收尾，可显式传 "
            "literature/papers_verified.jsonl。"
        ),
    )
    screening_path: str = Field(
        default="",
        description=(
            "可选：额外保存原始 LLM screening 判定的 JSONL 路径。默认不写额外文件，"
            "避免产生 screening_plan/excluded_candidates 这类旁路 artifact。"
        ),
    )


class ApplySemanticScreeningTool(Tool):
    name = "apply_semantic_screening"
    description = (
        "把 Scout LLM 显式输出的 semantic_screen 判定合并到 papers_verified.jsonl，"
        "或 papers_raw.jsonl。工具只做合并写盘，不做语义判断，也不会默认新增筛选计划文件。"
    )
    parameters_schema = ApplySemanticScreeningParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = ApplySemanticScreeningParams(**kwargs)
        try:
            screening_output_path: Path | None = None
            if params.screening_path:
                screening_output_path = self.policy.resolve_write(params.screening_path)
                screening_output_path.parent.mkdir(parents=True, exist_ok=True)
                screening_output_path.write_text(
                    "\n".join(json.dumps(item, ensure_ascii=False) for item in params.screenings)
                    + ("\n" if params.screenings else ""),
                    encoding="utf-8",
                )

            papers_path = self.policy.resolve_write(params.papers_path)
            if not papers_path.exists():
                return ToolResult(
                    ok=False,
                    content=f"❌ semantic screening 合并失败: 论文池不存在 {params.papers_path}",
                    error="papers_path_missing",
                )
            merged_count = 0
            papers = _load_jsonl_local(papers_path)
            merged = apply_semantic_screening(papers, params.screenings)
            merged_count = sum(1 for item in merged if isinstance(item.get("semantic_screen"), dict))
            papers_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in merged)
                + ("\n" if merged else ""),
                encoding="utf-8",
            )
            return ToolResult(
                ok=True,
                content=(
                    f"✅ 已合并 {len(params.screenings)} 条 LLM semantic screening；"
                    f"论文池中带 semantic_screen 的记录 {merged_count} 条。"
                ),
                data={
                    "screening_path": params.screening_path if screening_output_path else None,
                    "papers_path": params.papers_path,
                    "screening_count": len(params.screenings),
                    "merged_count": merged_count,
                },
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"❌ semantic screening 合并失败: {exc}",
                error="apply_semantic_screening_failed",
            )


class BackfillPaperAbstractsParams(BaseModel):
    """backfill_paper_abstracts 工具的参数。"""

    papers_path: str = Field(
        default="literature/papers_raw.jsonl",
        description=(
            "要回填摘要的论文池。T2 正常流程应在 semantic_screen 之前对 "
            "papers_raw.jsonl 调用，让 LLM 筛选看到更完整的摘要证据。"
        ),
    )
    title_match_threshold: float = Field(
        default=0.88,
        ge=0.0,
        le=1.0,
        description="无 DOI/arXiv 时，用标题匹配回填摘要所需的最低相似度。",
    )
    max_concurrency: int = Field(default=6, ge=1, le=20, description="逐条回填并发上限。")
    enable_title_fallback: bool = Field(
        default=True,
        description="是否允许在无 DOI/arXiv 时使用标题匹配兜底；带阈值防止错配。",
    )


class BackfillPaperAbstractsTool(Tool):
    """在 semantic_screen 之前批量回填缺失摘要。

    工具只做机械 metadata 补全：清洗已有摘要，尽量从多源 API 找回缺失摘要，
    并写入 `_abstract_backfilled_from`。它不判断相关性、证据强度或是否应进入
    deep-read。
    """

    name = "backfill_paper_abstracts"
    description = (
        "在 semantic_screen 之前批量回填 papers_raw.jsonl 中缺失的摘要；"
        "多源容错，只补 abstract，不做学术判断。"
    )
    parameters_schema = BackfillPaperAbstractsParams
    timeout_seconds = 180.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BackfillPaperAbstractsParams(**kwargs)
        if httpx is None:
            return ToolResult(
                ok=False,
                content="❌ 缺少 httpx 依赖，无法执行摘要回填",
                error="dependency_missing",
            )

        try:
            papers_path = self.policy.resolve_write(params.papers_path)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        if not papers_path.exists():
            return ToolResult(
                ok=False,
                content=f"❌ 论文池不存在: {params.papers_path}",
                error="papers_path_missing",
            )

        papers = _load_jsonl_local(papers_path)
        cleaned_existing = 0
        for paper in papers:
            existing = clean_abstract(paper.get("abstract"))
            if existing:
                if existing != paper.get("abstract"):
                    cleaned_existing += 1
                paper["abstract"] = existing
                paper.pop("_missing_abstract", None)

        missing = [paper for paper in papers if not clean_abstract(paper.get("abstract"))]
        by_source: dict[str, int] = {}

        if missing:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                await self._s2_batch_backfill(client, missing, by_source)

                still_missing = [
                    paper for paper in missing if not clean_abstract(paper.get("abstract"))
                ]
                semaphore = asyncio.Semaphore(params.max_concurrency)

                async def _one(paper: dict[str, Any]) -> None:
                    async with semaphore:
                        await self._backfill_single(
                            client,
                            paper,
                            by_source,
                            title_threshold=params.title_match_threshold,
                            enable_title_fallback=params.enable_title_fallback,
                        )

                await asyncio.gather(*(_one(paper) for paper in still_missing))

        papers_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in papers)
            + ("\n" if papers else ""),
            encoding="utf-8",
        )

        filled = sum(by_source.values())
        remaining = sum(1 for paper in papers if not clean_abstract(paper.get("abstract")))
        return ToolResult(
            ok=True,
            content=(
                f"✅ 摘要回填完成：总计 {len(papers)} 篇，初始缺失 {len(missing)} 篇，"
                f"补全 {filled} 篇，仍缺 {remaining} 篇；清洗已有摘要 {cleaned_existing} 篇。"
            ),
            data={
                "papers_path": params.papers_path,
                "total": len(papers),
                "missing": len(missing),
                "filled": filled,
                "remaining": remaining,
                "cleaned_existing": cleaned_existing,
                "by_source": by_source,
            },
        )

    async def _s2_batch_backfill(
        self,
        client: "httpx.AsyncClient",
        missing: list[dict[str, Any]],
        by_source: dict[str, int],
    ) -> None:
        id_to_paper: dict[str, dict[str, Any]] = {}
        for paper in missing:
            external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
            doi = self._record_doi(paper, {})
            arxiv_id = str(external_ids.get("ArXiv") or paper.get("arxiv_id") or "").strip()
            raw_id = str(paper.get("id") or paper.get("canonical_id") or "").strip()
            if not arxiv_id and raw_id.lower().startswith("arxiv:"):
                arxiv_id = raw_id.split(":", 1)[1].strip()
            s2_id = raw_id if str(paper.get("source") or "").casefold() == "semantic_scholar" else ""

            candidate_ids = []
            if doi:
                candidate_ids.append(f"DOI:{doi}")
            if arxiv_id:
                candidate_ids.append(f"ARXIV:{arxiv_id.removeprefix('arxiv:')}")
            if s2_id:
                candidate_ids.append(s2_id)
            for candidate_id in candidate_ids:
                id_to_paper.setdefault(candidate_id, paper)

        ids = list(id_to_paper.keys())
        if not ids:
            return
        headers = {"x-api-key": os.environ.get("S2_API_KEY", "")}
        headers = {key: value for key, value in headers.items() if value}
        for batch_start in range(0, len(ids), 100):
            batch_ids = ids[batch_start : batch_start + 100]
            try:
                response = await client.post(
                    "https://api.semanticscholar.org/graph/v1/paper/batch",
                    params={"fields": "abstract,title,externalIds"},
                    json={"ids": batch_ids},
                    headers=headers,
                )
                response.raise_for_status()
                results = response.json()
            except Exception:
                continue
            if not isinstance(results, list):
                continue
            for candidate_id, item in zip(batch_ids, results):
                if not isinstance(item, dict):
                    continue
                paper = id_to_paper[candidate_id]
                if clean_abstract(paper.get("abstract")):
                    continue
                abstract = clean_abstract(item.get("abstract"))
                if abstract:
                    self._apply(paper, abstract, "semantic_scholar_batch", by_source)

    async def _backfill_single(
        self,
        client: "httpx.AsyncClient",
        paper: dict[str, Any],
        by_source: dict[str, int],
        *,
        title_threshold: float,
        enable_title_fallback: bool,
    ) -> None:
        external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
        raw_id = str(paper.get("id") or paper.get("canonical_id") or "").strip()
        arxiv_id = str(external_ids.get("ArXiv") or paper.get("arxiv_id") or "").strip()
        if not arxiv_id and raw_id.lower().startswith("arxiv:"):
            arxiv_id = raw_id.split(":", 1)[1].strip()
        doi = self._record_doi(paper, {})
        title = str(paper.get("title") or "").strip()

        if arxiv_id:
            if self._apply(paper, await self._try_arxiv(client, arxiv_id), "arxiv", by_source):
                return

        if doi:
            abstract = await self._try_openalex_by_id(client, f"https://doi.org/{doi}")
            if self._apply(paper, abstract, "openalex", by_source):
                return
            abstract = await self._try_crossref(client, doi)
            if self._apply(paper, abstract, "crossref", by_source):
                return
            abstract = await self._try_s2_by_id(client, f"DOI:{doi}")
            if self._apply(paper, abstract, "semantic_scholar", by_source):
                return

        if doi or title:
            abstract = await self._try_europepmc(
                client,
                doi=doi,
                title=title,
                title_threshold=title_threshold,
            )
            if self._apply(paper, abstract, "europepmc", by_source):
                return

        if enable_title_fallback and title:
            abstract = await self._try_openalex_by_title(client, title, title_threshold)
            if self._apply(paper, abstract, "openalex_title", by_source):
                return
            abstract = await self._try_s2_by_title(client, title, title_threshold)
            if self._apply(paper, abstract, "semantic_scholar_title", by_source):
                return

    @staticmethod
    def _apply(
        paper: dict[str, Any],
        abstract: str,
        source: str,
        by_source: dict[str, int],
    ) -> bool:
        abstract = clean_abstract(abstract)
        if not abstract:
            return False
        paper["abstract"] = abstract
        paper["_abstract_backfilled_from"] = source
        paper.pop("_missing_abstract", None)
        by_source[source] = by_source.get(source, 0) + 1
        return True

    async def _try_arxiv(self, client: "httpx.AsyncClient", arxiv_id: str) -> str:
        try:
            response = await client.get(
                f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id.removeprefix('arxiv:'), safe='')}"
            )
            response.raise_for_status()
            root = ET.fromstring(response.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            return clean_abstract(root.findtext(".//atom:summary", default="", namespaces=ns))
        except Exception:
            return ""

    async def _try_openalex_by_id(self, client: "httpx.AsyncClient", lookup: str) -> str:
        try:
            response = await client.get(
                f"https://api.openalex.org/works/{quote(lookup, safe=':/')}",
                params=_openalex_params(),
            )
            response.raise_for_status()
            return abstract_from_openalex_index(response.json().get("abstract_inverted_index"))
        except Exception:
            return ""

    async def _try_crossref(self, client: "httpx.AsyncClient", doi: str) -> str:
        try:
            response = await client.get(
                f"https://api.crossref.org/works/{quote(doi, safe='')}",
                headers=_crossref_headers(),
            )
            response.raise_for_status()
            return clean_abstract(response.json().get("message", {}).get("abstract"))
        except Exception:
            return ""

    async def _try_s2_by_id(self, client: "httpx.AsyncClient", paper_id: str) -> str:
        headers = {"x-api-key": os.environ.get("S2_API_KEY", "")}
        headers = {key: value for key, value in headers.items() if value}
        try:
            response = await client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/{quote(paper_id, safe='')}",
                params={"fields": "abstract,title,externalIds"},
                headers=headers,
            )
            response.raise_for_status()
            return clean_abstract(response.json().get("abstract"))
        except Exception:
            return ""

    async def _try_europepmc(
        self,
        client: "httpx.AsyncClient",
        *,
        doi: str,
        title: str,
        title_threshold: float,
    ) -> str:
        try:
            query = f'DOI:"{doi}"' if doi else f'TITLE:"{title}"'
            response = await client.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": 1,
                },
            )
            response.raise_for_status()
            results = response.json().get("resultList", {}).get("result", [])
            if not results:
                return ""
            hit = results[0]
            if not doi and _title_similarity(title, str(hit.get("title") or "")) < title_threshold:
                return ""
            return clean_abstract(hit.get("abstractText"))
        except Exception:
            return ""

    async def _try_openalex_by_title(
        self,
        client: "httpx.AsyncClient",
        title: str,
        threshold: float,
    ) -> str:
        try:
            response = await client.get(
                "https://api.openalex.org/works",
                params=_openalex_params(search=title, **{"per-page": 3}),
            )
            response.raise_for_status()
            for work in response.json().get("results", []):
                if _title_similarity(title, str(work.get("title") or "")) >= threshold:
                    return abstract_from_openalex_index(work.get("abstract_inverted_index"))
        except Exception:
            return ""
        return ""

    async def _try_s2_by_title(
        self,
        client: "httpx.AsyncClient",
        title: str,
        threshold: float,
    ) -> str:
        headers = {"x-api-key": os.environ.get("S2_API_KEY", "")}
        headers = {key: value for key, value in headers.items() if value}
        try:
            response = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": title, "limit": 3, "fields": "title,abstract"},
                headers=headers,
            )
            response.raise_for_status()
            for item in response.json().get("data", []):
                if _title_similarity(title, str(item.get("title") or "")) >= threshold:
                    return clean_abstract(item.get("abstract"))
        except Exception:
            return ""
        return ""

    @staticmethod
    def _record_doi(record: dict[str, Any], reference: dict[str, Any]) -> str:
        external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
        doi = str(
            record.get("doi")
            or reference.get("doi")
            or external_ids.get("DOI")
            or ""
        ).strip()
        if doi:
            return (
                doi.removeprefix("https://doi.org/")
                .removeprefix("http://doi.org/")
                .removeprefix("doi:")
            )
        canonical_id = str(record.get("canonical_id") or record.get("id") or "").strip()
        return canonical_id if canonical_id.startswith("10.") else ""


class DetectDuplicateQueriesParams(BaseModel):
    """detect_duplicate_queries 工具的参数。"""
    queries: list[str] = Field(
        ...,
        description="检索式列表",
    )
    threshold: float = Field(
        default=0.7,
        description="相似度阈值（0-1），默认 0.7",
    )


class DetectDuplicateQueriesTool(Tool):
    """检测检索式之间的重复度。

    功能：
    - 计算检索式之间的相似度
    - 识别重复的检索式对
    - 给出结构化重复度 hint；是否重写 query 由 Scout LLM 判断

    使用场景：
    - 在执行检索之前调用
    - 确保检索式多样性
    """

    name = "detect_duplicate_queries"
    description = "检测检索式之间的重复度，给出改进建议"
    parameters_schema = DetectDuplicateQueriesParams
    timeout_seconds = 10.0

    async def execute(self, **kwargs) -> ToolResult:
        raw_queries = kwargs["queries"]
        queries = [" ".join(str(query or "").split()) for query in raw_queries]
        queries = [query for query in queries if query]
        threshold = kwargs.get("threshold", 0.7)

        if not queries:
            return ToolResult(
                ok=False,
                content=(
                    "❌ 检索式列表为空或全是空白。请先基于 project.yaml、真实 seed "
                    "和 Scout LLM 的 domain_profile 生成非空 query；必要时调用 ask_human 补充研究边界。"
                ),
                error="empty_query_plan",
                data={"queries": [], "raw_query_count": len(raw_queries)},
            )

        if len(queries) < 2:
            return ToolResult(
                ok=True,
                content="✅ 检索式数量少于 2 条，无需检测重复度",
                data={"is_high_duplicate": False, "queries": queries}
            )

        try:
            result = detect_duplicate_queries(queries, threshold)

            content_lines = [
                f"检索式重复度分析（共 {len(queries)} 条）：",
                "",
                f"平均相似度: {result['avg_similarity']*100:.1f}%",
            ]

            if result["duplicate_pairs"]:
                content_lines.append("")
                content_lines.append(f"发现 {len(result['duplicate_pairs'])} 对高度相似的检索式：")
                for q1, q2, sim in result["duplicate_pairs"][:5]:  # 只显示前 5 对
                    content_lines.append(f"  - \"{q1}\" ≈ \"{q2}\" (相似度: {sim*100:.0f}%)")

            if result["warning"]:
                content_lines.append("")
                content_lines.append(f"⚠️ {result['warning']}")
                content_lines.append("")
                content_lines.append("LLM 复核提示：")
                content_lines.append("- 重新检查 domain_profile 是否覆盖了不同机制、任务、评估场景或相邻领域")
                content_lines.append("- 只保留有明确语义差异的 query；不要为了数量硬凑同义句")
            else:
                content_lines.append("")
                content_lines.append("✅ 检索式多样性良好")

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data=result
            )

        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 重复度检测失败: {e}",
                error="detection_failed"
            )


class AnalyzeDedupRateParams(BaseModel):
    """analyze_dedup_rate 工具的参数。"""
    raw_count: int = Field(
        ...,
        description="原始结果数量",
    )
    dedup_count: int = Field(
        ...,
        description="去重后数量",
    )


class AnalyzeDedupRateTool(Tool):
    """分析去重率，给出覆盖 hint。

    功能：
    - 计算去重率
    - 给出 raw 覆盖是否可能过窄的机械提示

    使用场景：
    - 在去重之后调用
    - 评估检索策略效果
    """

    name = "analyze_dedup_rate"
    description = "分析去重率，评估检索式质量"
    parameters_schema = AnalyzeDedupRateParams
    timeout_seconds = 5.0

    async def execute(self, **kwargs) -> ToolResult:
        raw_count = kwargs["raw_count"]
        dedup_count = kwargs["dedup_count"]

        try:
            result = analyze_dedup_rate(raw_count, dedup_count)

            content_lines = [
                "去重率分析：",
                "",
                f"原始结果: {raw_count} 篇",
                f"去重后: {dedup_count} 篇",
                f"去重率: {result['dedup_rate']*100:.1f}%",
                "",
            ]

            if result["status"] == "good":
                content_lines.append(f"✅ {result['message']}")
            elif result["status"] == "warning":
                content_lines.append(f"⚠️ {result['message']}")
            elif result["status"] == "critical":
                content_lines.append(f"❌ {result['message']}")
                content_lines.append("")
                content_lines.append("LLM 复核提示：")
                content_lines.append("- 回到 domain_profile，判断是否遗漏了机制、任务、数据、baseline 或相邻领域角度")
                content_lines.append("- 重新设计语义不同的 query，而不是机械替换同义词")

            return ToolResult(
                ok=True,
                content="\n".join(content_lines),
                data=result
            )

        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 去重率分析失败: {e}",
                error="analysis_failed"
            )


class BuildVerifiedPapersParams(BaseModel):
    papers: list[dict[str, Any]] = Field(..., description="去重并增强后的论文列表")
    title_similarity_threshold: float = Field(
        0.84,
        description="标题相似度阈值；低于该值视为 metadata verification 失败",
    )


class BuildVerifiedPapersTool(Tool):
    """对论文做确定性 metadata verification，并落盘 verified / failure artifacts。"""

    name = "build_verified_papers"
    description = (
        "基于 arXiv / DOI / OpenAlex / Semantic Scholar 对论文做确定性校验，"
        "生成 literature/papers_verified.jsonl 和 literature/verification_failures.jsonl。"
    )
    parameters_schema = BuildVerifiedPapersParams
    timeout_seconds = 90.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs):
        params = BuildVerifiedPapersParams(**kwargs)

        if httpx is None:
            return ToolResult(
                ok=False,
                content="❌ 缺少 httpx 依赖，无法执行 metadata verification",
                error="dependency_missing",
            )

        try:
            verified_records: list[dict[str, Any]] = []
            failure_records: list[dict[str, Any]] = []

            # 这里集中做真实性核验，避免把“长得像论文”的记录直接送进 T3。
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                for paper in params.papers:
                    verified, failure = await self._verify_one_paper(
                        client,
                        paper,
                        title_similarity_threshold=params.title_similarity_threshold,
                    )
                    if verified is not None:
                        verified_records.append(verified)
                    elif failure is not None:
                        failure_records.append(failure)

            verified_path = self.policy.resolve_write("literature/papers_verified.jsonl")
            failure_path = self.policy.resolve_write("literature/verification_failures.jsonl")
            verified_path.parent.mkdir(parents=True, exist_ok=True)
            verified_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in verified_records) + ("\n" if verified_records else ""),
                encoding="utf-8",
            )
            failure_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in failure_records) + ("\n" if failure_records else ""),
                encoding="utf-8",
            )

            return ToolResult(
                ok=True,
                content=(
                    f"✅ metadata verification 完成：verified={len(verified_records)}，"
                    f"failed={len(failure_records)}"
                ),
                data={
                    "verified_path": "literature/papers_verified.jsonl",
                    "failure_path": "literature/verification_failures.jsonl",
                    "verified_count": len(verified_records),
                    "failure_count": len(failure_records),
                    "verified_papers": verified_records,
                    "verification_failures": failure_records,
                },
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 构建 verified papers 失败: {e}",
                error="build_verified_papers_failed",
            )

    async def _verify_one_paper(
        self,
        client: "httpx.AsyncClient",
        paper: dict[str, Any],
        *,
        title_similarity_threshold: float,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """按最佳可用标识做单篇校验。"""

        canonical_id = str(paper.get("canonical_id") or paper.get("id") or "").strip()
        title = str(paper.get("title", "")).strip()

        seed_pdf_path = find_matching_seed_pdf(paper, self.policy.workspace_dir / "user_seeds" / "pdfs")
        if seed_pdf_path is not None:
            verified_record = dict(paper)
            verified_record["canonical_id"] = canonical_id or str(paper.get("id", "")).strip()
            verified_record["verification_status"] = "pdf_verified"
            verified_record["verification_method"] = "seed_pdf_local_identity_match"
            verified_record["verification_source"] = "user_seeds/pdfs"
            verified_record["verification_confidence"] = 1.0
            verified_record["verification_title_similarity"] = 1.0
            verified_record["verification_year_match"] = True
            verified_record["has_seed_pdf"] = True
            verified_record["has_local_pdf"] = True
            verified_record["seed_pdf_path"] = str(seed_pdf_path.relative_to(self.policy.workspace_dir))
            verified_record["access_level_hint"] = "FULL_TEXT_LOCAL"
            verified_record["access_score"] = 1.0
            verified_record["access_score_estimate"] = 1.0
            await self._backfill_verified_abstract(client, verified_record, {})
            return verified_record, None

        reference = None
        method = "none"
        try:
            if canonical_id.startswith("arxiv:"):
                method = "arxiv"
                reference = await self._fetch_arxiv_metadata(client, canonical_id.removeprefix("arxiv:"))
            elif str(paper.get("doi", "")).strip() or canonical_id.startswith("10."):
                method = "crossref"
                reference = await self._fetch_crossref_metadata(
                    client,
                    str(paper.get("doi", "")).strip() or canonical_id,
                )
            elif canonical_id.startswith("W") and canonical_id[1:].isdigit():
                method = "openalex"
                reference = await self._fetch_openalex_metadata(client, canonical_id)
            else:
                method = "semantic_scholar"
                reference = await self._fetch_semantic_scholar_metadata(client, str(paper.get("id", "")).strip())
        except Exception as exc:
            return None, self._build_failure_record(
                paper,
                method=method,
                reason="verification_request_failed",
                error=str(exc),
            )

        if not reference:
            return None, self._build_failure_record(
                paper,
                method=method,
                reason="no_reference_metadata",
            )

        similarity = SequenceMatcher(
            None,
            normalize_text_key(title),
            normalize_text_key(str(reference.get("title", ""))),
        ).ratio()
        source_year = paper.get("year")
        ref_year = reference.get("year")
        year_match = self._year_matches(source_year, ref_year)

        if similarity < title_similarity_threshold or not year_match:
            return None, self._build_failure_record(
                paper,
                method=method,
                reason="metadata_mismatch",
                error=f"title_similarity={similarity:.2f}, year_match={year_match}",
                confidence=round(similarity * (1.0 if year_match else 0.6), 2),
            )

        # 本地 PDF 是最强的二级证据，因此在 verified 层里明确升成 pdf_verified。
        normalized_id = str(paper.get("canonical_id") or paper.get("id") or "").replace(":", "_").replace("/", "_")
        has_local_pdf = bool(normalized_id and (self.policy.workspace_dir / "literature" / "pdfs" / f"{normalized_id}.pdf").exists())
        verified_record = dict(paper)
        verified_record["canonical_id"] = canonical_id or str(paper.get("id", "")).strip()
        verified_record["verification_status"] = "pdf_verified" if has_local_pdf else "metadata_verified"
        verified_record["verification_method"] = method
        verified_record["verification_source"] = str(reference.get("source", method))
        verified_record["verification_confidence"] = round(min(1.0, max(similarity, 0.9 if year_match else similarity)), 2)
        verified_record["verification_title_similarity"] = round(similarity, 2)
        verified_record["verification_year_match"] = year_match
        self._merge_reference_metadata(verified_record, reference)
        await self._backfill_verified_abstract(client, verified_record, reference)
        return verified_record, None

    @staticmethod
    def _merge_reference_metadata(
        verified_record: dict[str, Any],
        reference: dict[str, Any],
    ) -> None:
        """Preserve mechanical metadata discovered during verification."""

        if not reference:
            return
        for key in (
            "referenced_works",
            "related_works",
            "references",
            "reference_count",
            "refs_unavailable",
            "pdf_url",
            "open_access_pdf_url",
            "oa_pdf_url",
            "best_pdf_url",
            "full_text_url",
            "pmc_pdf_url",
            "url_for_pdf",
            "landing_page_url",
            "openAccessPdf",
            "open_access_pdf",
            "oa_pdf",
            "best_oa_location",
            "primary_location",
            "locations",
            "oa_locations",
            "open_access_locations",
            "openAccessLocations",
            "open_access_pdfs",
            "openalex_id",
            "semantic_scholar_id",
            "arxiv_id",
        ):
            value = reference.get(key)
            if value not in (None, "", []):
                verified_record.setdefault(key, value)
        external_ids = reference.get("externalIds")
        if isinstance(external_ids, dict) and external_ids:
            merged = dict(verified_record.get("externalIds") or {})
            for key, value in external_ids.items():
                if value not in (None, "", []):
                    merged.setdefault(key, value)
            verified_record["externalIds"] = merged
        doi = str(reference.get("doi") or "").strip()
        if doi and not str(verified_record.get("doi") or "").strip():
            verified_record["doi"] = doi

    async def _backfill_verified_abstract(
        self,
        client: "httpx.AsyncClient",
        verified_record: dict[str, Any],
        reference: dict[str, Any],
    ) -> None:
        """Preserve or backfill abstracts after identity verification succeeds."""

        existing = clean_abstract(verified_record.get("abstract"))
        if existing:
            verified_record["abstract"] = existing
            verified_record.pop("_missing_abstract", None)
            return

        abstract = clean_abstract(reference.get("abstract"))
        abstract_source = str(reference.get("source") or verified_record.get("verification_source") or "")

        backfiller = BackfillPaperAbstractsTool(self.policy)
        doi = self._record_doi(verified_record, reference)
        if not abstract and doi:
            for fetcher_name in ("openalex", "crossref", "semantic_scholar"):
                try:
                    if fetcher_name == "openalex":
                        fallback_reference = await self._fetch_openalex_metadata(
                            client, f"https://doi.org/{doi}"
                        )
                    elif fetcher_name == "crossref":
                        fallback_reference = await self._fetch_crossref_metadata(client, doi)
                    else:
                        fallback_reference = await self._fetch_semantic_scholar_metadata(
                            client, f"DOI:{doi}"
                        )
                except Exception:
                    continue
                abstract = clean_abstract((fallback_reference or {}).get("abstract"))
                if abstract:
                    abstract_source = fetcher_name
                    break

        if not abstract:
            arxiv_id = self._record_arxiv_id(verified_record, reference)
            if arxiv_id:
                abstract = await backfiller._try_arxiv(client, arxiv_id)
                if abstract:
                    abstract_source = "arxiv"

        if not abstract:
            title = str(verified_record.get("title") or reference.get("title") or "").strip()
            if title:
                abstract = await backfiller._try_openalex_by_title(client, title, 0.88)
                if abstract:
                    abstract_source = "openalex_title"
                else:
                    abstract = await backfiller._try_s2_by_title(client, title, 0.88)
                    if abstract:
                        abstract_source = "semantic_scholar_title"

        if abstract:
            verified_record["abstract"] = abstract
            verified_record["_abstract_backfilled_from"] = abstract_source or "metadata_reference"
            verified_record.pop("_missing_abstract", None)

    @staticmethod
    def _record_doi(record: dict[str, Any], reference: dict[str, Any]) -> str:
        external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
        doi = str(record.get("doi") or reference.get("doi") or external_ids.get("DOI") or "").strip()
        if doi:
            return (
                doi.removeprefix("https://doi.org/")
                .removeprefix("http://doi.org/")
                .removeprefix("doi:")
            )
        canonical_id = str(record.get("canonical_id") or record.get("id") or "").strip()
        return canonical_id if canonical_id.startswith("10.") else ""

    @staticmethod
    def _record_arxiv_id(record: dict[str, Any], reference: dict[str, Any]) -> str:
        external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
        for value in (
            record.get("arxiv_id"),
            external_ids.get("ArXiv"),
            reference.get("arxiv_id"),
            str(record.get("canonical_id") or ""),
            str(record.get("id") or ""),
        ):
            text = str(value or "").strip()
            if not text:
                continue
            lower = text.lower()
            if lower.startswith("arxiv:"):
                return text.split(":", 1)[1].strip()
            if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", text):
                return text
        return ""

    @staticmethod
    def _year_matches(source_year: Any, ref_year: Any) -> bool:
        source = BuildVerifiedPapersTool._parse_year(source_year)
        reference = BuildVerifiedPapersTool._parse_year(ref_year)
        return source is None or reference is None or abs(source - reference) <= 1

    @staticmethod
    def _parse_year(value: Any) -> int | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        match = re.search(r"\d{4}", text)
        return int(match.group(0)) if match else None

    @staticmethod
    def _build_failure_record(
        paper: dict[str, Any],
        *,
        method: str,
        reason: str,
        error: str = "",
        confidence: float = 0.0,
    ) -> dict[str, Any]:
        """把失败原因结构化落盘，便于后续 audit。"""

        return {
            "paper_id": str(paper.get("id", "")).strip(),
            "canonical_id": str(paper.get("canonical_id") or paper.get("id") or "").strip(),
            "title": str(paper.get("title", "")).strip(),
            "source": str(paper.get("source", "")).strip(),
            "doi": str(paper.get("doi", "")).strip(),
            "verification_status": "failed_verification",
            "verification_method": method,
            "failure_reason": reason,
            "verification_error": error,
            "verification_confidence": round(confidence, 2),
        }

    async def _fetch_crossref_metadata(
        self,
        client: "httpx.AsyncClient",
        doi: str,
    ) -> dict[str, Any] | None:
        response = await client.get(
            f"https://api.crossref.org/works/{quote(doi, safe='')}",
            headers=_crossref_headers(),
        )
        response.raise_for_status()
        item = response.json().get("message", {})
        title = item.get("title", [""])
        published = (
            item.get("published-print")
            or item.get("published-online")
            or item.get("published")
            or item.get("issued")
            or item.get("created")
        )
        date_parts = (published or {}).get("date-parts", [[]]) if isinstance(published, dict) else [[]]
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        references = _extract_crossref_references(item)
        payload = {
            "source": "crossref",
            "title": title[0] if title else "",
            "year": year,
            "abstract": clean_abstract(item.get("abstract", "")),
            "doi": item.get("DOI", doi),
            "reference_count": item.get("reference-count", len(references)),
        }
        if references:
            payload["references"] = references
            payload["referenced_works"] = references
        return payload

    @staticmethod
    def _openalex_location_metadata(item: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in ("best_oa_location", "primary_location", "locations", "open_access"):
            value = item.get(key)
            if value not in (None, "", []):
                metadata[key] = value
        for location_key in ("best_oa_location", "primary_location"):
            location = item.get(location_key)
            if not isinstance(location, dict):
                continue
            pdf_url = (
                location.get("pdf_url")
                or location.get("url_for_pdf")
                or location.get("pdfUrl")
                or location.get("pdfURL")
            )
            if pdf_url and not metadata.get("open_access_pdf_url"):
                metadata["open_access_pdf_url"] = pdf_url
            landing = location.get("landing_page_url") or location.get("url")
            if landing and not metadata.get("landing_page_url"):
                metadata["landing_page_url"] = landing
        return metadata

    @staticmethod
    def _s2_reference_aliases(items: Any, limit: int = 80) -> list[dict[str, str]]:
        references: list[dict[str, str]] = []
        if not isinstance(items, list):
            return references
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
            doi = str(external_ids.get("DOI") or item.get("doi") or "").strip()
            title = str(item.get("title") or "").strip()
            paper_id = str(item.get("paperId") or item.get("id") or "").strip()
            if not doi and not title and not paper_id:
                continue
            record: dict[str, str] = {}
            if doi:
                record["doi"] = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
                record["id"] = record["doi"]
            elif paper_id:
                record["id"] = paper_id
            if title:
                record["title"] = title
            references.append(record)
        return references

    @staticmethod
    def _openalex_external_ids(item: dict[str, Any], doi: str) -> dict[str, str]:
        external_ids: dict[str, str] = {}
        openalex_id = str(item.get("id") or "").strip()
        if openalex_id:
            external_ids["OpenAlex"] = openalex_id.rstrip("/").split("/")[-1]
        if doi:
            external_ids["DOI"] = doi
        return external_ids

    @staticmethod
    def _normalize_openalex_refs(values: Any) -> list[str]:
        refs: list[str] = []
        if not isinstance(values, list):
            return refs
        for item in values:
            text = str(item or "").strip()
            if not text:
                continue
            if text.startswith("https://openalex.org/") or text.startswith("https://api.openalex.org/works/"):
                text = text.rstrip("/").split("/")[-1]
            refs.append(text)
        return refs

    async def _fetch_openalex_metadata(
        self,
        client: "httpx.AsyncClient",
        work_id: str,
    ) -> dict[str, Any] | None:
        response = await client.get(
            f"https://api.openalex.org/works/{quote(work_id, safe=':/')}",
            params=_openalex_params(),
        )
        response.raise_for_status()
        item = response.json()
        doi = str(item.get("doi") or "").removeprefix("https://doi.org/")
        referenced_works = self._normalize_openalex_refs(item.get("referenced_works"))
        related_works = self._normalize_openalex_refs(item.get("related_works"))
        payload = {
            "source": "openalex",
            "title": item.get("title", ""),
            "year": item.get("publication_year"),
            "abstract": abstract_from_openalex_index(item.get("abstract_inverted_index")),
            "doi": doi,
            "referenced_works": referenced_works,
            "related_works": related_works,
            "refs_unavailable": not bool(referenced_works),
            "externalIds": self._openalex_external_ids(item, doi),
            **self._openalex_location_metadata(item),
        }
        return payload

    async def _fetch_semantic_scholar_metadata(
        self,
        client: "httpx.AsyncClient",
        paper_id: str,
    ) -> dict[str, Any] | None:
        if not paper_id:
            return None
        headers = {"x-api-key": os.environ.get("S2_API_KEY", "")}
        headers = {key: value for key, value in headers.items() if value}
        response = await client.get(
            f"https://api.semanticscholar.org/graph/v1/paper/{quote(paper_id, safe='')}",
            params={
                "fields": (
                    "title,year,abstract,externalIds,openAccessPdf,"
                    "references.paperId,references.title,references.externalIds,"
                    "citations.paperId,citations.title,citations.externalIds"
                )
            },
            headers=headers,
        )
        response.raise_for_status()
        item = response.json()
        external_ids = item.get("externalIds") or {}
        references = self._s2_reference_aliases(item.get("references"))
        citations = self._s2_reference_aliases(item.get("citations"), limit=40)
        return {
            "source": "semantic_scholar",
            "title": item.get("title", ""),
            "year": item.get("year"),
            "abstract": clean_abstract(item.get("abstract", "")),
            "doi": external_ids.get("DOI", ""),
            "externalIds": external_ids,
            "references": references,
            "referenced_works": references,
            "related_works": citations,
            "openAccessPdf": item.get("openAccessPdf") or {},
        }

    async def _fetch_arxiv_metadata(
        self,
        client: "httpx.AsyncClient",
        arxiv_id: str,
    ) -> dict[str, Any] | None:
        response = await client.get(
            f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id, safe='')}"
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return None

        title = clean_abstract(" ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split()))
        abstract = clean_abstract(" ".join((entry.findtext("atom:summary", default="", namespaces=ns) or "").split()))
        published = entry.findtext("atom:published", default="", namespaces=ns) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        return {
            "source": "arxiv",
            "title": title,
            "year": year,
            "abstract": abstract,
        }


class BuildDeepReadQueueParams(BaseModel):
    papers: list[dict[str, Any]] = Field(..., description="去重并增强后的论文列表")
    deep_read_min: int = Field(35, description="最低有效 deep-read 数")
    deep_read_target: int = Field(35, description="目标 deep-read 数")
    deep_read_max: int = Field(45, description="最大 deep-read/probe 队列数")
    probe_pool: int = Field(45, description="实际先 probe 的候选数")
    mainline_screened_cap: int = Field(90, description="主线筛读候选 cap；只用于 coverage/triage 记账，不等于 T3 必读数。")
    bridge_deep_floor: int = Field(3, description="每个 must_explore bridge 通过 screen 后的 deep-read 保底数。")
    bridge_screened_cap: int = Field(7, description="每个 bridge 保留的 screened triage 记录上限。")
    bridge_pool_cap: int = Field(15, description="每个 bridge 在 deep_read_queue 中保留的候选上限。")
    cross_domain_slots: int | None = Field(
        default=None,
        description="由 semantic_screen 允许的跨领域/theory_bridge 候选整体保护名额；None 时按 runtime 默认值自动计算。",
    )
    citation_hub_slots: int | None = Field(
        default=None,
        description=(
            "citation graph 枢纽节点保护名额；只保护池内 citation edge 识别出的枢纽，"
            "且仍要求 seed 或 Scout semantic_screen 允许进入 deep-read。"
        ),
    )


class BuildAccessAuditParams(BaseModel):
    papers: list[dict[str, Any]] = Field(..., description="去重并增强后的论文列表")
    top_n: int = Field(50, description="在 markdown 里展示前多少篇")


class BuildDeepReadQueueTool(Tool):
    """构建 deep-read 队列并直接写入 artifact。"""

    name = "build_deep_read_queue"
    description = (
        "基于 seed priority、可读性 metadata 和 Scout LLM 的 semantic_screen 构建 "
        "literature/deep_read_queue.jsonl；bucket/retrieval_intent 只作 provenance。"
    )
    parameters_schema = BuildDeepReadQueueParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs):
        params = BuildDeepReadQueueParams(**kwargs)

        try:
            queue_records, metadata = build_deep_read_queue(
                params.papers,
                self.policy.workspace_dir,
                deep_read_min=params.deep_read_min,
                deep_read_target=params.deep_read_target,
                deep_read_max=params.deep_read_max,
                probe_pool=params.probe_pool,
                cross_domain_slots=params.cross_domain_slots,
                citation_hub_slots=params.citation_hub_slots,
                mainline_screened_cap=params.mainline_screened_cap,
                bridge_deep_floor=params.bridge_deep_floor,
                bridge_screened_cap=params.bridge_screened_cap,
                bridge_pool_cap=params.bridge_pool_cap,
            )
            queue_path = self.policy.resolve_write("literature/deep_read_queue.jsonl")
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.write_text(
                "\n".join(__import__("json").dumps(item, ensure_ascii=False) for item in queue_records) + "\n",
                encoding="utf-8",
            )
            meta_path = self.policy.resolve_write("literature/deep_read_queue_meta.json")
            meta_path.write_text(__import__("json").dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            return ToolResult(
                ok=True,
                content=(
                    f"✅ 已生成 deep-read 队列 {len(queue_records)} 篇，"
                    f"target={metadata['deep_read_target']}, probe_pool={metadata['probe_pool']}"
                ),
                data={
                    "queue_path": "literature/deep_read_queue.jsonl",
                    "meta_path": "literature/deep_read_queue_meta.json",
                    "queue_count": len(queue_records),
                    "metadata": metadata,
                },
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 构建 deep-read 队列失败: {e}",
                error="build_queue_failed",
            )


class BuildAccessAuditTool(Tool):
    """构建 access audit 清单并直接写入 artifact。"""

    name = "build_access_audit"
    description = "生成 literature/access_audit.md，汇总论文可读性与 PDF 可得性。"
    parameters_schema = BuildAccessAuditParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs):
        params = BuildAccessAuditParams(**kwargs)

        try:
            records, markdown = build_access_audit(
                params.papers,
                self.policy.workspace_dir,
                top_n=params.top_n,
            )
            audit_md_path = self.policy.resolve_write("literature/access_audit.md")
            audit_jsonl_path = self.policy.resolve_write("literature/access_audit.jsonl")
            audit_md_path.parent.mkdir(parents=True, exist_ok=True)
            audit_md_path.write_text(markdown, encoding="utf-8")
            audit_jsonl_path.write_text(
                "\n".join(__import__("json").dumps(item, ensure_ascii=False) for item in records) + "\n",
                encoding="utf-8",
            )

            return ToolResult(
                ok=True,
                content=f"✅ 已生成 access audit，共 {len(records)} 篇候选论文",
                data={
                    "audit_md_path": "literature/access_audit.md",
                    "audit_jsonl_path": "literature/access_audit.jsonl",
                    "count": len(records),
                },
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"❌ 构建 access audit 失败: {e}",
                error="build_access_audit_failed",
            )


def _load_jsonl_local(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records
