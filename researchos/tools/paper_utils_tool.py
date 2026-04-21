"""论文处理工具的 Tool 包装器。

将 paper_utils.py 中的确定性函数包装成 Agent 可调用的 Tool。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .paper_utils import (
    deduplicate_papers,
    score_papers,
    expand_queries,
    filter_by_domain,
    generate_search_log,
)


class DeduplicatePapersParams(BaseModel):
    papers: list[dict] = Field(..., description="论文列表，每个论文是一个字典")
    doi_dedup: bool = Field(True, description="是否进行 DOI 精确去重")
    title_threshold: float = Field(0.95, description="标题相似度阈值（0-1），超过此值视为重复")


class DeduplicatePapersTool(Tool):
    """论文去重工具。"""

    name = "deduplicate_papers"
    description = "对论文列表进行确定性去重（DOI精确去重 + 标题相似度去重）"
    parameters_schema = DeduplicatePapersParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行去重。"""
        params = DeduplicatePapersParams(**kwargs)
        try:
            result = deduplicate_papers(params.papers, params.doi_dedup, params.title_threshold)
            return ToolResult(
                ok=True,
                content=f"去重完成：{len(params.papers)} 篇 → {len(result)} 篇",
                data={
                    "papers": result,
                    "original_count": len(params.papers),
                    "dedup_count": len(result),
                },
            )
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))


class ScorePapersParams(BaseModel):
    papers: list[dict] = Field(..., description="论文列表")
    keywords: list[str] = Field(..., description="关键词列表")
    weights: dict | None = Field(None, description="各维度权重（可选）")


class ScorePapersTool(Tool):
    """论文评分工具。"""

    name = "score_papers"
    description = "为论文列表评分（基于来源类型、年份、引用数、关键词匹配度）"
    parameters_schema = ScorePapersParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行评分。"""
        params = ScorePapersParams(**kwargs)
        try:
            result = score_papers(params.papers, params.keywords, params.weights)
            return ToolResult(
                ok=True,
                content=f"评分完成：{len(result)} 篇论文",
                data={"papers": result},
            )
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))


class ExpandQueriesParams(BaseModel):
    seed_papers: list[dict] = Field(default_factory=list, description="种子论文列表（可选，如果没有则传空列表）")
    topic: str = Field(..., description="研究主题")
    max_queries: int = Field(10, description="最大检索式数量")


class ExpandQueriesTool(Tool):
    """检索式扩展工具。"""

    name = "expand_queries"
    description = "基于种子论文和主题扩展检索式"
    parameters_schema = ExpandQueriesParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行检索式扩展。"""
        params = ExpandQueriesParams(**kwargs)
        try:
            result = expand_queries(params.seed_papers, params.topic, params.max_queries)
            return ToolResult(
                ok=True,
                content=f"生成 {len(result)} 条检索式",
                data={"queries": result},
            )
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))


class FilterByDomainParams(BaseModel):
    papers: list[dict] = Field(..., description="论文列表")
    target_domain: str = Field("cs", description="目标领域")


class FilterByDomainTool(Tool):
    """领域过滤工具。"""

    name = "filter_by_domain"
    description = "按领域过滤论文（避免心理学论文混入 AI 论文）"
    parameters_schema = FilterByDomainParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行领域过滤。"""
        params = FilterByDomainParams(**kwargs)
        try:
            result = filter_by_domain(params.papers, params.target_domain)
            return ToolResult(
                ok=True,
                content=f"领域过滤完成：{len(params.papers)} 篇 → {len(result)} 篇",
                data={
                    "papers": result,
                    "original_count": len(params.papers),
                    "filtered_count": len(result),
                },
            )
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))


class GenerateSearchLogParams(BaseModel):
    raw_count: int = Field(..., description="原始检索结果数量")
    dedup_count: int = Field(..., description="去重后数量")
    queries: list[str] = Field(..., description="使用的检索式列表")
    query_results: dict[str, int] | None = Field(None, description="每个检索式的结果数量（可选）")


class GenerateSearchLogTool(Tool):
    """检索日志生成工具。"""

    name = "generate_search_log"
    description = "生成检索日志（基于实际数据，不允许编造）"
    parameters_schema = GenerateSearchLogParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行日志生成。"""
        params = GenerateSearchLogParams(**kwargs)
        try:
            result = generate_search_log(
                params.raw_count,
                params.dedup_count,
                params.queries,
                params.query_results,
            )
            return ToolResult(
                ok=True,
                content="检索日志生成完成",
                data={"log": result},
            )
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))
