from __future__ import annotations

"""runtime 层可复用的测试替身。"""

from dataclasses import dataclass, field
from typing import Any

from ..tools.base import ToolResult
from ..tools.registry import ToolRegistry


@dataclass
class MockDockerExecTool:
    """测试用 docker_exec 替身。按脚本顺序返回预设结果。"""

    scripted_responses: list[dict[str, Any]]
    name: str = "docker_exec"
    call_count: int = 0

    async def execute(self, **kwargs: Any) -> ToolResult:
        if self.call_count >= len(self.scripted_responses):
            return ToolResult(
                ok=False,
                content="no more scripted responses",
                error="out_of_script",
            )
        response = self.scripted_responses[self.call_count]
        self.call_count += 1
        return ToolResult(**response)


@dataclass
class MockToolRegistry:
    """面向测试的简易 registry 包装。"""

    registry: ToolRegistry
    _instances: dict[str, Any] = field(default_factory=dict)

    def register_mock_instance(self, name: str, instance: Any) -> None:
        self._instances[name] = instance
        if not self.registry.has(name):
            self.registry.register(name, lambda _ctx, inst=instance: inst)

    def register_mock(self, name: str, factory) -> None:
        if self.registry.has(name):
            raise ValueError(f"Mock tool '{name}' already exists")
        self.registry.register(name, lambda _ctx, factory=factory: factory)
