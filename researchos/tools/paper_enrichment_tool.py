"""论文数据增强工具的 Tool 包装器。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .paper_enrichment import (
    enrich_papers,
    detect_duplicate_queries,
    analyze_dedup_rate,
)


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


class EnrichPapersTool(Tool):
    """增强论文数据，自动补充缺失字段。

    功能：
    - 自动推断 source_type（根据 venue）
    - 自动生成 why_relevant（基于 relevance_score 和关键词匹配）
    - 转换 authors 格式（对象数组 -> 字符串数组）
    - 补充缺失的必需字段
    - 标记数据质量问题

    使用场景：
    - 在保存 papers_dedup.jsonl 之前调用
    - 确保数据符合 schema 要求
    """

    name = "enrich_papers"
    description = "增强论文数据，自动补充缺失字段（source_type、why_relevant 等）"
    parameters_schema = EnrichPapersParams
    timeout_seconds = 30.0

    async def execute(self, **kwargs) -> ToolResult:
        papers = kwargs["papers"]
        keywords = kwargs.get("keywords")

        if not papers:
            return ToolResult(
                ok=False,
                content="❌ 论文列表为空",
                error="empty_papers"
            )

        try:
            enriched = enrich_papers(papers, keywords=keywords)

            # 统计数据质量
            missing_abstract_count = sum(1 for p in enriched if p.get("_missing_abstract"))

            content_lines = [
                f"✅ 成功增强 {len(enriched)} 篇论文的数据",
                "",
                "增强内容：",
                "- 自动推断 source_type（根据 venue）",
                "- 自动生成 why_relevant（基于 relevance_score 和关键词匹配）",
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
    - 给出改进建议

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
                content_lines.append("建议：")
                content_lines.append("- 使用不同的同义词和相关概念")
                content_lines.append("- 从不同角度覆盖研究主题（理论、技术、应用）")
                content_lines.append("- 包含上下游技术和相关领域")
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
    """分析去重率，给出建议。

    功能：
    - 计算去重率
    - 评估检索式质量
    - 给出改进建议

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
                content_lines.append("建议：")
                content_lines.append("- 重新设计检索式，使用更多样化的关键词")
                content_lines.append("- 从不同角度覆盖研究主题")
                content_lines.append("- 避免检索式之间关键词重复")

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
