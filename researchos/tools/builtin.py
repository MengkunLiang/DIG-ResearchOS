from __future__ import annotations

"""运行时内置工具注册。

这里集中注册“所有进程默认可用”的工具工厂。
后续 skill 自带工具、MCP 工具等扩展，会在 CLI 启动时追加注册到同一个 registry。
"""

import os

from ..runtime.config import RuntimeSettings
from .ask_human import AskHumanTool
from .bash_run import BashRunTool
from .clone_repo import CloneRepoTool
from .docker_exec import DockerExecTool, load_project_config
from .echo import EchoTool
from .filesystem import ListFilesTool, ReadFileTool, WriteFileTool
from .finish_task import FinishTaskTool
from .structured_file import WriteStructuredFileTool
from .glob_files import GlobFilesTool
from .grep_search import GrepSearchTool
from .latex_compile import LatexCompileTool
from .literature_synthesis import BuildSynthesisWorkbenchTool
from .multi_source_search import MultiSourceSearchTool
from .paper_processing import ExtractSectionsTool
from .paper_fetch import AppendFileTool, FetchPaperPdfTool, ExtractPdfTextTool
from .paper_lookup import LookupPaperRecordTool
from .registry import ToolRegistry
from .search_papers import FetchPaperMetadataTool, SearchPapersTool
from .seed_paper_processor import ProcessSeedPaperTool
from .upload_seed_materials import UploadSeedCodeTool, UploadSeedDataTool, UploadSeedPdfTool
from .web_fetch import WebFetchAllowlist, WebFetchTool
from .paper_utils_tool import (
    DeduplicatePapersTool,
    ScorePapersTool,
    ExpandQueriesTool,
    FilterByDomainTool,
    GenerateSearchLogTool,
    LogScoutProgressTool,
)
from .paper_enrichment_tool import (
    EnrichPapersTool,
    DetectDuplicateQueriesTool,
    AnalyzeDedupRateTool,
    BuildAccessAuditTool,
    BuildDeepReadQueueTool,
    BuildVerifiedPapersTool,
)
from .paper_save_tools import (
    AppendPapersRawTool,
    ProcessPapersRawTool,
    SavePapersRawTool,
    SavePapersDedupTool,
)
from .semantic_scholar import SemanticScholarSearchTool, SemanticScholarGetPaperTool
from .arxiv_api import ArxivSearchTool
from .openalex_api import OpenAlexSearchTool, OpenAlexGetWorkTool
from .crossref_api import CrossRefSearchTool, CrossRefGetWorkTool
from .publisher_search import ElsevierScopusSearchTool, InformsSearchTool


def register_builtin_tools(
    registry: ToolRegistry,
    runtime_settings: RuntimeSettings | None = None,
) -> None:
    """注册 runtime 默认内置工具。"""
    runtime_settings = runtime_settings or RuntimeSettings()
    registry.register("read_file", lambda ctx: ReadFileTool(ctx.policy))
    registry.register("write_file", lambda ctx: WriteFileTool(ctx.policy))
    registry.register("write_structured_file", lambda ctx: WriteStructuredFileTool(ctx.policy))
    registry.register("append_file", lambda ctx: AppendFileTool(ctx.policy))
    registry.register("list_files", lambda ctx: ListFilesTool(ctx.policy))
    registry.register("finish_task", lambda ctx: FinishTaskTool())
    registry.register("ask_human", lambda ctx: AskHumanTool(ctx.human))
    registry.register("echo", lambda ctx: EchoTool())
    registry.register("bash_run", lambda ctx: BashRunTool(ctx.policy, skill_dir=ctx.skill_dir))
    registry.register("grep_search", lambda ctx: GrepSearchTool(ctx.policy))
    registry.register("glob_files", lambda ctx: GlobFilesTool(ctx.policy))
    registry.register(
        "web_fetch",
        lambda ctx: WebFetchTool(
            allowlist=WebFetchAllowlist.from_runtime_settings(runtime_settings),
        ),
    )
    registry.register("clone_repo", lambda ctx: CloneRepoTool(ctx.policy))
    # Reader / Reviewer 等后续 agent 需要按 section 粒度读取 PDF；
    # 这里直接放进 builtin，避免到 agent 落地时还要回头补 runtime 注册链。
    registry.register("extract_paper_sections", lambda ctx: ExtractSectionsTool(ctx.policy))
    registry.register("fetch_paper_pdf", lambda ctx: FetchPaperPdfTool(ctx.policy))
    registry.register("extract_pdf_text", lambda ctx: ExtractPdfTextTool(ctx.policy))
    registry.register("lookup_paper_record", lambda ctx: LookupPaperRecordTool(ctx.policy))
    registry.register("build_synthesis_workbench", lambda ctx: BuildSynthesisWorkbenchTool(ctx.policy))
    registry.register(
        "multi_source_search",
        lambda _ctx: MultiSourceSearchTool(os.environ.get("RESEARCHER_EMAIL")),
    )
    registry.register(
        "search_papers",
        lambda _ctx: SearchPapersTool(os.environ.get("S2_API_KEY")),
    )
    registry.register(
        "fetch_paper_metadata",
        lambda _ctx: FetchPaperMetadataTool(os.environ.get("S2_API_KEY")),
    )
    registry.register(
        "docker_exec",
        lambda ctx: DockerExecTool(
            ctx.policy,
            project_config=load_project_config(ctx.policy.workspace_dir),
        ),
    )
    registry.register(
        "latex_compile",
        lambda ctx: LatexCompileTool(
            DockerExecTool(
                ctx.policy,
                project_config=load_project_config(ctx.policy.workspace_dir),
            )
        ),
    )
    registry.register("process_seed_paper", lambda ctx: ProcessSeedPaperTool(ctx.policy))
    registry.register("upload_seed_pdf", lambda ctx: UploadSeedPdfTool(ctx.policy))
    registry.register("upload_seed_data", lambda ctx: UploadSeedDataTool(ctx.policy))
    registry.register("upload_seed_code", lambda ctx: UploadSeedCodeTool(ctx.policy))
    # 新增：确定性论文处理工具
    registry.register("deduplicate_papers", lambda ctx: DeduplicatePapersTool())
    registry.register("score_papers", lambda ctx: ScorePapersTool())
    registry.register("expand_queries", lambda ctx: ExpandQueriesTool())
    registry.register("filter_by_domain", lambda ctx: FilterByDomainTool())
    registry.register("generate_search_log", lambda ctx: GenerateSearchLogTool(workspace_dir=str(ctx.policy.workspace_dir)))
    # 论文数据增强工具
    registry.register("enrich_papers", lambda ctx: EnrichPapersTool())
    registry.register("detect_duplicate_queries", lambda ctx: DetectDuplicateQueriesTool())
    registry.register("analyze_dedup_rate", lambda ctx: AnalyzeDedupRateTool())
    registry.register("build_verified_papers", lambda ctx: BuildVerifiedPapersTool(ctx.policy))
    registry.register("build_access_audit", lambda ctx: BuildAccessAuditTool(ctx.policy))
    registry.register("build_deep_read_queue", lambda ctx: BuildDeepReadQueueTool(ctx.policy))
    # Semantic Scholar 工具（直接 API 调用，不依赖 MCP）
    registry.register("semantic_scholar_search", lambda ctx: SemanticScholarSearchTool())
    registry.register("semantic_scholar_get_paper", lambda ctx: SemanticScholarGetPaperTool())
    # arXiv 工具（预印本搜索）
    registry.register("arxiv_search", lambda ctx: ArxivSearchTool())
    # OpenAlex 工具（综合学术搜索）
    registry.register("openalex_search", lambda ctx: OpenAlexSearchTool())
    registry.register("openalex_get_work", lambda ctx: OpenAlexGetWorkTool())
    # CrossRef 工具（DOI 元数据）
    registry.register("crossref_search", lambda ctx: CrossRefSearchTool())
    registry.register("crossref_get_work", lambda ctx: CrossRefGetWorkTool())
    # Publisher-specific literature databases.
    registry.register("elsevier_scopus_search", lambda ctx: ElsevierScopusSearchTool())
    registry.register(
        "informs_search",
        lambda _ctx: InformsSearchTool(os.environ.get("RESEARCHER_EMAIL")),
    )
    # Scout Agent 进度日志工具（工具层追加，无需用户手动调用）
    registry.register(
        "log_scout_progress",
        lambda ctx: _build_log_scout_progress_tool(str(ctx.policy.workspace_dir)),
    )
    # 论文数据保存工具
    # 流式写入：LLM 检索到论文后立即追加原始数据（不转换）
    registry.register("append_papers_raw", lambda ctx: AppendPapersRawTool(ctx.policy))
    # 批量处理：LLM 完成所有检索后一次性转换和验证
    registry.register("process_papers_raw", lambda ctx: ProcessPapersRawTool(ctx.policy))
    # 兼容旧接口（保留）
    registry.register("save_papers_raw", lambda ctx: SavePapersRawTool(ctx.policy))
    registry.register("save_papers_dedup", lambda ctx: SavePapersDedupTool(ctx.policy))


def _build_log_scout_progress_tool(workspace_dir: str) -> LogScoutProgressTool:
    tool = LogScoutProgressTool()
    tool.set_workspace_dir(workspace_dir)
    return tool
