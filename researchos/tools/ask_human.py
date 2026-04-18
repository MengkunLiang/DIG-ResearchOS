from __future__ import annotations

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .human_gate import HumanInterface


class AskHumanParams(BaseModel):
    question: str = Field(..., min_length=1, description="要问用户的问题")
    suggestions: list[str] | None = Field(None, description="可选参考建议")


class AskHumanTool(Tool):
    name = "ask_human"
    description = "向用户提问并返回用户回答"
    parameters_schema = AskHumanParams
    timeout_seconds = 3600.0

    def __init__(self, human: HumanInterface):
        self.human = human

    async def execute(self, **kwargs) -> ToolResult:
        answer = await self.human.ask_clarification(
            question=kwargs["question"],
            suggestions=kwargs.get("suggestions"),
        )
        return ToolResult(
            ok=True,
            content=f"User answered: {answer}",
            data={"question": kwargs["question"], "answer": answer},
        )

