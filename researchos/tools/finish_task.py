from __future__ import annotations

from pydantic import BaseModel, Field

from .base import Tool, ToolResult


class FinishTaskParams(BaseModel):
    summary: str = Field(..., min_length=1, description="任务完成摘要")


class FinishTaskTool(Tool):
    name = "finish_task"
    description = "声明任务已完成，触发 runtime 输出校验"
    parameters_schema = FinishTaskParams
    timeout_seconds = 1.0

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(ok=True, content=f"Finish acknowledged: {kwargs['summary']}")

