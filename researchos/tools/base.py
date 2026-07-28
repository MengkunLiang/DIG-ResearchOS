"""Base protocol shared by all policy-aware ResearchOS tool implementations.

It standardizes parameter schemas, result envelopes, and asynchronous execution
so AgentRunner can validate tools without knowing their research-domain logic.
"""

from __future__ import annotations


from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..pydantic_compat import model_json_schema


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
        if not isinstance(self.name, str) or not self.name:
            raise TypeError(f"Tool name must be a non-empty string, got: {self.name!r}")
        if not isinstance(self.description, str):
            raise TypeError(
                f"Tool '{self.name}' description must be a string, got: {type(self.description).__name__}"
            )
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": model_json_schema(self.parameters_schema),
            },
        }
