from __future__ import annotations

from .ask_human import AskHumanTool
from .echo import EchoTool
from .filesystem import ListFilesTool, ReadFileTool, WriteFileTool
from .finish_task import FinishTaskTool
from .registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register("read_file", lambda ctx: ReadFileTool(ctx.policy))
    registry.register("write_file", lambda ctx: WriteFileTool(ctx.policy))
    registry.register("list_files", lambda ctx: ListFilesTool(ctx.policy))
    registry.register("finish_task", lambda ctx: FinishTaskTool())
    registry.register("ask_human", lambda ctx: AskHumanTool(ctx.human))
    registry.register("echo", lambda ctx: EchoTool())
