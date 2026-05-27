"""论文数据增强工具的 Tool 包装器。"""

from __future__ import annotations

from difflib import SequenceMatcher
from html import unescape
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - 依赖是否安装取决于环境
    httpx = None

from pydantic import BaseModel, Field

from ..agents._common import normalize_text_key
from .base import Tool, ToolResult
from .paper_enrichment import (
    build_access_audit,
    build_deep_read_queue,
    enrich_papers,
    detect_duplicate_queries,
    analyze_dedup_rate,
)
from .workspace_policy import WorkspaceAccessPolicy


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
        queries = kwargs["queries"]
        threshold = kwargs.get("threshold", 0.7)

        if len(queries) < 2:
            return ToolResult(
                ok=True,
                content="✅ 检索式数量少于 2 条，无需检测重复度",
                data={"is_high_duplicate": False}
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
        year_match = source_year is None or ref_year is None or abs(int(source_year) - int(ref_year)) <= 1

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
        return verified_record, None

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
        response = await client.get(f"https://api.crossref.org/works/{quote(doi, safe='')}")
        response.raise_for_status()
        item = response.json().get("message", {})
        title = item.get("title", [""])
        date_parts = item.get("published", {}).get("date-parts", [[]])
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        return {
            "source": "crossref",
            "title": title[0] if title else "",
            "year": year,
        }

    async def _fetch_openalex_metadata(
        self,
        client: "httpx.AsyncClient",
        work_id: str,
    ) -> dict[str, Any] | None:
        response = await client.get(f"https://api.openalex.org/works/{quote(work_id, safe=':/')}")
        response.raise_for_status()
        item = response.json()
        return {
            "source": "openalex",
            "title": item.get("title", ""),
            "year": item.get("publication_year"),
        }

    async def _fetch_semantic_scholar_metadata(
        self,
        client: "httpx.AsyncClient",
        paper_id: str,
    ) -> dict[str, Any] | None:
        if not paper_id:
            return None
        response = await client.get(
            f"https://api.semanticscholar.org/graph/v1/paper/{quote(paper_id, safe='')}",
            params={"fields": "title,year"},
        )
        response.raise_for_status()
        item = response.json()
        return {
            "source": "semantic_scholar",
            "title": item.get("title", ""),
            "year": item.get("year"),
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

        title = unescape(" ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split()))
        published = entry.findtext("atom:published", default="", namespaces=ns) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        return {
            "source": "arxiv",
            "title": title,
            "year": year,
        }


class BuildDeepReadQueueParams(BaseModel):
    papers: list[dict[str, Any]] = Field(..., description="去重并增强后的论文列表")
    deep_read_min: int = Field(18, description="最低有效 deep-read 数")
    deep_read_target: int = Field(24, description="目标 deep-read 数")
    deep_read_max: int = Field(30, description="最大 deep-read 数")
    probe_pool: int = Field(45, description="实际先 probe 的候选数")


class BuildAccessAuditParams(BaseModel):
    papers: list[dict[str, Any]] = Field(..., description="去重并增强后的论文列表")
    top_n: int = Field(50, description="在 markdown 里展示前多少篇")


class BuildDeepReadQueueTool(Tool):
    """构建 deep-read 队列并直接写入 artifact。"""

    name = "build_deep_read_queue"
    description = (
        "基于 relevance_score、access_score_estimate 和 seed priority 构建 "
        "literature/deep_read_queue.jsonl。"
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
