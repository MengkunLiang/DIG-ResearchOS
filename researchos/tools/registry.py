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
    policy: WorkspaceAccessPolicy
    human: HumanInterface
    skill_dir: Path | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ToolFactory] = {}

    def register(self, name: str, factory: ToolFactory) -> None:
        if name in self._factories:
            raise ValueError(f"Tool '{name}' already registered")
        self._factories[name] = factory

    def build(self, names: list[str], build_ctx: ToolBuildContext) -> dict[str, Tool]:
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

