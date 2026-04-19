from __future__ import annotations

from .ask_human import AskHumanTool
from .bash_run import BashRunTool
from .echo import EchoTool
from .filesystem import ListFilesTool, ReadFileTool, WriteFileTool
from .finish_task import FinishTaskTool
from .glob_files import GlobFilesTool
from .grep_search import GrepSearchTool
from .registry import ToolRegistry
from .web_fetch import WebFetchTool


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register("read_file", lambda ctx: ReadFileTool(ctx.policy))
    registry.register("write_file", lambda ctx: WriteFileTool(ctx.policy))
    registry.register("list_files", lambda ctx: ListFilesTool(ctx.policy))
    registry.register("finish_task", lambda ctx: FinishTaskTool())
    registry.register("ask_human", lambda ctx: AskHumanTool(ctx.human))
    registry.register("echo", lambda ctx: EchoTool())
    registry.register("bash_run", lambda ctx: BashRunTool(ctx.policy, skill_dir=ctx.skill_dir))
    registry.register("grep_search", lambda ctx: GrepSearchTool(ctx.policy))
    registry.register("glob_files", lambda ctx: GlobFilesTool(ctx.policy))
    registry.register("web_fetch", lambda ctx: WebFetchTool())
