from __future__ import annotations

"""Tool 抽象。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class ToolResult:
    """工具执行完成后回给 runtime 的标准结果。"""

    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """所有 runtime tool 的共同基类。"""

    name: str
    description: str
    parameters_schema: type[BaseModel]
    timeout_seconds: float = 60.0
    requires_human_approval: bool = False
    idempotent: bool = True

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        ...

    def to_openai_schema(self) -> dict[str, Any]:
        """把 pydantic 参数模型转换成模型可见的 OpenAI tool schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema.model_json_schema(),
            },
        }
