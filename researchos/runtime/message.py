from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any
import uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    @staticmethod
    def create(name: str, arguments: dict[str, Any]) -> "ToolCall":
        return ToolCall(id=_new_id("tc_"), name=name, arguments=arguments)

    def to_openai_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    step: int | None = None
    created_at: str = field(default_factory=_now_iso)
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def system(content: str, *, step: int | None = None) -> "Message":
        return Message(role=Role.SYSTEM, content=content, step=step)

    @staticmethod
    def user(content: str, *, step: int | None = None) -> "Message":
        return Message(role=Role.USER, content=content, step=step)

    @staticmethod
    def assistant(
        content: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        *,
        step: int | None = None,
    ) -> "Message":
        return Message(
            role=Role.ASSISTANT,
            content=content,
            tool_calls=tool_calls or [],
            step=step,
        )

    @staticmethod
    def tool(
        tool_call_id: str,
        name: str,
        content: str,
        *,
        is_error: bool = False,
        step: int | None = None,
        duration_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Message":
        merged = dict(metadata or {})
        merged["is_error"] = is_error
        return Message(
            role=Role.TOOL,
            content=content,
            tool_call_id=tool_call_id,
            name=name,
            step=step,
            duration_ms=duration_ms,
            metadata=merged,
        )

    def to_openai_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role.value}
        if self.content is not None:
            out["content"] = self.content
        if self.tool_calls:
            out["tool_calls"] = [tc.to_openai_dict() for tc in self.tool_calls]
        if self.role == Role.TOOL:
            out["tool_call_id"] = self.tool_call_id
            out["name"] = self.name
        return out

    def to_trace_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["role"] = self.role.value
        return data


def is_empty_assistant(message: Message) -> bool:
    if message.role != Role.ASSISTANT:
        return False
    has_content = bool(message.content and message.content.strip())
    return not has_content and not message.tool_calls

