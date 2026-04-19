from __future__ import annotations

"""运行时内置工具注册。

这里集中注册“所有进程默认可用”的工具工厂。
后续 skill 自带工具、MCP 工具等扩展，会在 CLI 启动时追加注册到同一个 registry。
"""

import os

from .ask_human import AskHumanTool
from .bash_run import BashRunTool
from .docker_exec import DockerExecTool, load_project_config
from .echo import EchoTool
from .filesystem import ListFilesTool, ReadFileTool, WriteFileTool
from .finish_task import FinishTaskTool
from .glob_files import GlobFilesTool
from .grep_search import GrepSearchTool
from .latex_compile import LatexCompileTool
from .multi_source_search import MultiSourceSearchTool
from .paper_processing import ExtractSectionsTool
from .paper_fetch import AppendFileTool, FetchPaperPdfTool, ExtractPdfTextTool
from .registry import ToolRegistry
from .search_papers import FetchPaperMetadataTool, SearchPapersTool
from .web_fetch import WebFetchTool


def register_builtin_tools(registry: ToolRegistry) -> None:
    """注册 runtime 默认内置工具。"""
    registry.register("read_file", lambda ctx: ReadFileTool(ctx.policy))
    registry.register("write_file", lambda ctx: WriteFileTool(ctx.policy))
    registry.register("append_file", lambda ctx: AppendFileTool(ctx.policy))
    registry.register("list_files", lambda ctx: ListFilesTool(ctx.policy))
    registry.register("finish_task", lambda ctx: FinishTaskTool())
    registry.register("ask_human", lambda ctx: AskHumanTool(ctx.human))
    registry.register("echo", lambda ctx: EchoTool())
    registry.register("bash_run", lambda ctx: BashRunTool(ctx.policy, skill_dir=ctx.skill_dir))
    registry.register("grep_search", lambda ctx: GrepSearchTool(ctx.policy))
    registry.register("glob_files", lambda ctx: GlobFilesTool(ctx.policy))
    registry.register("web_fetch", lambda ctx: WebFetchTool())
    # Reader / Reviewer 等后续 agent 需要按 section 粒度读取 PDF；
    # 这里直接放进 builtin，避免到 agent 落地时还要回头补 runtime 注册链。
    registry.register("extract_paper_sections", lambda ctx: ExtractSectionsTool(ctx.policy))
    registry.register("fetch_paper_pdf", lambda ctx: FetchPaperPdfTool(ctx.policy))
    registry.register("extract_pdf_text", lambda ctx: ExtractPdfTextTool(ctx.policy))
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
