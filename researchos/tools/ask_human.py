from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .base import Tool, ToolResult
from .human_gate import HumanInputUnavailable, HumanInterface


class AskHumanParams(BaseModel):
    question: str = Field(..., min_length=1, description="要问用户的问题")
    suggestions: list[str] | None = Field(None, description="可选参考建议")

    @field_validator("suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, value: object) -> object:
        """兼容模型把 suggestions JSON array 当字符串传入的情况。"""

        if value is None or isinstance(value, list):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            return [part.strip() for part in re.split(r"[,;/\n]+", raw) if part.strip()]
        return value


class AskHumanTool(Tool):
    name = "ask_human"
    description = "向用户提问并返回用户回答"
    parameters_schema = AskHumanParams
    timeout_seconds = 3600.0

    def __init__(
        self,
        human: HumanInterface,
        *,
        workspace_dir: Path | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ):
        self.human = human
        self.workspace_dir = workspace_dir
        self.task_id = task_id
        self.run_id = run_id

    async def execute(self, **kwargs) -> ToolResult:
        interaction_id = f"human_{uuid4().hex[:12]}"
        try:
            answer = await self.human.ask_clarification(
                question=kwargs["question"],
                suggestions=kwargs.get("suggestions"),
            )
        except HumanInputUnavailable as exc:
            return ToolResult(
                ok=False,
                content=f"Human input unavailable: {exc}",
                data={
                    "interaction_id": interaction_id,
                    "question": kwargs["question"],
                    "answer": "",
                    "input_unavailable": True,
                },
                error="human_input_unavailable",
            )
        if not answer.strip():
            return ToolResult(
                ok=False,
                content="Human input unavailable: empty answer",
                data={
                    "interaction_id": interaction_id,
                    "question": kwargs["question"],
                    "answer": "",
                    "input_unavailable": True,
                },
                error="human_input_unavailable",
            )
        self._record_interaction(
            interaction_id=interaction_id,
            question=kwargs["question"],
            suggestions=kwargs.get("suggestions") or [],
            answer=answer,
        )
        return ToolResult(
            ok=True,
            content=(
                f"User answered: {answer}\n"
                f"[ResearchOS human_interaction_id: {interaction_id}]"
            ),
            data={"interaction_id": interaction_id, "question": kwargs["question"], "answer": answer},
        )

    def _record_interaction(
        self,
        *,
        interaction_id: str,
        question: str,
        suggestions: list[str],
        answer: str,
    ) -> None:
        if self.workspace_dir is None:
            return
        path = self.workspace_dir / "_runtime" / "human_interactions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "version": "1.0",
            "semantics": "researchos_human_interaction_record",
            "interaction_id": interaction_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "suggestions": suggestions,
            "answer": answer,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
