"""论文处理工具的 Tool 包装器。

将 paper_utils.py 中的确定性函数包装成 Agent 可调用的 Tool。
"""

from __future__ import annotations

import json
from pathlib import Path
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
from .scout_progress import ScoutProgressLogger


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
    current_year: int | None = Field(
        None,
        description="可选：用于可复现测试的当前年份；默认使用运行时 UTC 年份",
    )


class ScorePapersTool(Tool):
    """论文评分工具。"""

    name = "score_papers"
    description = (
        "为论文列表生成 metadata/search priority hint（基于来源类型、年份、"
        "引用数、关键词匹配度等机械信号；不是最终学术相关性判断）"
    )
    parameters_schema = ScorePapersParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行评分。"""
        params = ScorePapersParams(**kwargs)
        try:
            result = score_papers(params.papers, params.keywords, params.weights, params.current_year)
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
    current_year: int | None = Field(
        None,
        description="可选：用于可复现测试的当前年份；默认使用运行时 UTC 年份",
    )
    domain_profile: dict[str, Any] | None = Field(
        None,
        description=(
            "LLM 先归纳的领域 profile；可包含 include_keywords、exclude_keywords、"
            "query_prefixes、query_variants、related_concepts、venue_terms 等。"
        ),
    )
    llm_queries: list[str] | None = Field(
        None,
        description="LLM 已经设计好的检索式；工具只合并去重，不替代 LLM 的领域判断。",
    )
    domain_hints: list[str] | None = Field(
        None,
        description="LLM 给出的短领域限定词或概念，工具会与 topic 组合。",
    )


class ExpandQueriesTool(Tool):
    """检索式扩展工具。"""

    name = "expand_queries"
    description = "基于种子论文和主题扩展检索式"
    parameters_schema = ExpandQueriesParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行检索式扩展。"""
        params = ExpandQueriesParams(**kwargs)
        try:
            result = expand_queries(
                params.seed_papers,
                params.topic,
                params.max_queries,
                params.current_year,
                params.domain_profile,
                params.llm_queries,
                params.domain_hints,
            )
            return ToolResult(
                ok=True,
                content=f"生成 {len(result)} 条检索式",
                data={"queries": result},
            )
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))


class FilterByDomainParams(BaseModel):
    papers: list[dict] = Field(..., description="论文列表")
    target_domain: str = Field("general", description="目标领域标签，仅用于记录/兼容")
    domain_profile: dict[str, Any] | None = Field(
        None,
        description=(
            "LLM 归纳的过滤 profile。可包含 include_keywords/include_venues/"
            "exclude_keywords/exclude_venues/keep_if_uncertain/min_include_matches。"
            "如果不提供，工具不做领域过滤。"
        ),
    )


class FilterByDomainTool(Tool):
    """领域过滤工具。"""

    name = "filter_by_domain"
    description = "按 LLM 提供的领域 profile 做保守过滤；不内置任何固定学科知识"
    parameters_schema = FilterByDomainParams

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行领域过滤。"""
        params = FilterByDomainParams(**kwargs)
        try:
            result = filter_by_domain(params.papers, params.target_domain, params.domain_profile)
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
    """检索日志生成工具。

    直接写入 `literature/search_log.md`，无需 LLM 额外调用 write_file。
    """

    name = "generate_search_log"
    description = "生成检索日志并写入 literature/search_log.md（基于实际数据，不允许编造）"
    parameters_schema = GenerateSearchLogParams

    def __init__(self, workspace_dir: str | None = None) -> None:
        """构造函数，接收 workspace_dir。"""
        super().__init__()
        self.workspace_dir = workspace_dir

    def set_workspace_dir(self, workspace_dir: str | None) -> None:
        """由 runtime 在工具初始化时注入 workspace_dir。"""
        self.workspace_dir = workspace_dir

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行日志生成并写入文件。"""
        params = GenerateSearchLogParams(**kwargs)
        try:
            result = generate_search_log(
                params.raw_count,
                params.dedup_count,
                params.queries,
                params.query_results,
            )
            # 直接写入文件
            if self.workspace_dir:
                from pathlib import Path
                log_path = Path(self.workspace_dir) / "literature" / "search_log.md"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(result, encoding="utf-8")
                return ToolResult(
                    ok=True,
                    content=f"检索日志已写入 {log_path}",
                    data={"log": result, "path": str(log_path)},
                )
            else:
                # 降级：只返回内容
                return ToolResult(
                    ok=True,
                    content="检索日志生成完成（未设置 workspace_dir，无法写入文件）",
                    data={"log": result},
                )
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))


class LogScoutProgressParams(BaseModel):
    action: str = Field(..., description="操作类型: init|queries|search|search_result|dedup|score|write|finish")
    detail: str = Field(..., description="操作详情")
    query: str | None = Field(None, description="检索式（search 时使用）")
    count: int | None = Field(None, description="论文数量")
    source: str | None = Field(None, description="数据源（search 时使用）")
    before: int | None = Field(None, description="去重前数量（dedup 时使用）")
    after: int | None = Field(None, description="去重后数量（dedup 时使用）")
    topic: str | None = Field(None, description="研究主题（init 时使用）")
    queries: list[str] | None = Field(None, description="检索式列表（queries 时使用）")


class LogScoutProgressTool(Tool):
    """Scout Agent 进度日志工具。

    此工具自动记录 Scout Agent 的中间执行进度。
    工具层追加日志到 `literature/temp/scout_progress.md`，无需用户手动调用。
    """

    name = "log_scout_progress"
    description = (
        "记录 Scout Agent 执行进度到日志文件。工具层追加，用户可随时查看。"
        "此工具不会影响核心逻辑，仅用于进度可视化。"
    )
    parameters_schema = LogScoutProgressParams
    workspace_dir: str | None = None

    def set_workspace_dir(self, workspace_dir: str | None) -> None:
        """由 runtime 在工具初始化时注入 workspace_dir。"""
        self.workspace_dir = workspace_dir

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = LogScoutProgressParams(**kwargs)
        if not self.workspace_dir:
            return ToolResult(ok=False, content="未设置 workspace_dir", error="no_workspace")
        try:
            logger = ScoutProgressLogger(Path(self.workspace_dir))
            action = params.action
            if action == "init":
                logger.log_init(params.count, params.topic)
            elif action == "queries":
                if params.queries:
                    logger.log_queries_expanded(params.queries)
                else:
                    return ToolResult(ok=False, content="queries 参数缺失", error="missing_param")
            elif action == "search":
                logger.log_search_start(params.query or "", params.source)
            elif action == "search_result":
                logger.log_search_result(
                    params.query or "",
                    params.count or 0,
                    params.source or "",
                )
            elif action == "search_error":
                logger.log_search_error(params.query or "", params.detail)
            elif action == "dedup":
                logger.log_dedup(params.before or 0, params.after or 0)
            elif action == "score":
                logger.log_score(params.count or 0)
            elif action == "write":
                logger.log_write_file(params.detail, params.count)
            elif action == "finish":
                logger.log_finish(params.count or 0, params.after or 0)
            else:
                logger.log_step(action, params.detail)
            content = logger.read_progress() or ""
            return ToolResult(ok=True, content=f"进度已记录，当前日志：\n{content[-500:]}")
        except Exception as e:
            return ToolResult(ok=False, content="", error=str(e))
