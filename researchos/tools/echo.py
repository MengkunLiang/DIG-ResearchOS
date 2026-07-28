"""Minimal deterministic echo tool used by runtime smoke paths and examples.

It has no workspace or network side effects and provides a stable tool-call
surface for validating Agent orchestration behavior.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import Tool, ToolResult


class EchoParams(BaseModel):
    text: str = Field(..., description="要回显的文本")


class EchoTool(Tool):
    name = "echo"
    description = "返回输入文本，用于调试 runtime"
    parameters_schema = EchoParams
    timeout_seconds = 1.0

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(ok=True, content=kwargs["text"])

