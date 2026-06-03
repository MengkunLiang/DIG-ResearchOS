from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .base import Tool
from .human_gate import HumanInterface
from .workspace_policy import WorkspaceAccessPolicy


ToolFactory = Callable[["ToolBuildContext"], Tool]


@dataclass
class ToolBuildContext:
    """构造工具实例时传入的运行期上下文。

    说明：
    - 工具注册时只注册“工厂”，不直接创建实例。
    - 真正创建实例发生在 agent run 开始时，此时才知道当前 workspace 的权限策略、
      human interface、skill 目录等运行期信息。
    """

    policy: WorkspaceAccessPolicy
    human: HumanInterface
    skill_dir: Path | None = None
    task_id: str | None = None
    run_id: str | None = None


class ToolRegistry:
    """运行期工具注册表。

    设计约束：
    - registry 只保存“名字 -> 工厂”的映射，不保存运行中的具体 Tool 实例；
    - 这样不同 task 可以在同一进程中用不同的 workspace policy 构造各自工具；
    - 也便于在 skill / future MCP / agent-specific tool 场景下做按需注入。
    """

    def __init__(self) -> None:
        self._factories: dict[str, ToolFactory] = {}

    def register(self, name: str, factory: ToolFactory) -> None:
        if name in self._factories:
            raise ValueError(f"Tool '{name}' already registered")
        self._factories[name] = factory

    def register_instance(self, tool: Tool) -> None:
        """把一个现成实例包装成工厂注册。

        这主要用于 skill 自带工具这类“天然就是实例”的场景。
        """
        self.register(tool.name, lambda _ctx, tool=tool: tool)

    def has(self, name: str) -> bool:
        return name in self._factories

    def build(self, names: list[str], build_ctx: ToolBuildContext) -> dict[str, Tool]:
        """按给定名字列表构造本次 run 可用的工具实例。

        这里会保留传入顺序，便于上层把 tool schema 暴露给模型时保持稳定顺序。
        """
        built: dict[str, Tool] = {}
        for name in names:
            factory = self._factories.get(name)
            if factory is None:
                raise ValueError(
                    f"Tool '{name}' not registered. Available: {sorted(self._factories)}"
                )
            built[name] = factory(build_ctx)
        return built

    def to_openai_schemas(self, tools: dict[str, Tool]) -> list[dict]:
        return [tool.to_openai_schema() for tool in tools.values()]

    def available_names(self) -> list[str]:
        return sorted(self._factories)
