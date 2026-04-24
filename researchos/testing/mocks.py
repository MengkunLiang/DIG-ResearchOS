from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..runtime.errors import LLMProviderError
from ..runtime.llm_client import LLMResponse, ModelBinding
from ..tools.human_gate import HumanInterface


@dataclass
class FakeToolFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "tool_call_1"

    @property
    def function(self) -> FakeToolFunction:
        return FakeToolFunction(name=self.name, arguments=json.dumps(self.arguments, ensure_ascii=False))


@dataclass
class FakeLLMMessage:
    content: str | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)


@dataclass
class FakeChoice:
    message: FakeLLMMessage


@dataclass
class FakeUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class FakeRawCompletion:
    message: FakeLLMMessage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def choices(self) -> list[FakeChoice]:
        return [FakeChoice(self.message)]

    @property
    def usage(self) -> FakeUsage:
        return FakeUsage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )

    @property
    def _hidden_params(self) -> dict[str, float]:
        return {"response_cost": self.cost_usd}


class MockLLMClient:
    def __init__(
        self,
        responses: list[FakeRawCompletion],
        *,
        context_window: int = 4000,
        fail_with: Exception | None = None,
    ):
        self.responses = list(responses)
        self.context_window = context_window
        self.fail_with = fail_with
        self.call_count = 0
        self.last_messages: list[list[dict[str, Any]]] = []

    def resolve(
        self,
        *,
        profile: str | None,
        tier: str,
        model_override: str | None = None,
        endpoint_override: str | None = None,
        max_context_override: int | None = None,
    ) -> list[tuple[ModelBinding, SimpleNamespace]]:
        max_ctx = max_context_override or self.context_window
        return [(ModelBinding(model=model_override or "mock-model", endpoint=endpoint_override or "mock", max_context=max_ctx), SimpleNamespace(name="mock"))]

    def get_context_window(self, binding: ModelBinding) -> int:
        return self.context_window

    def get_truncation_config(self) -> dict[str, float]:
        return {"trigger_ratio": 0.8, "target_ratio": 0.6}

    def count_tokens(self, messages: list[dict[str, Any]], binding: ModelBinding) -> int:
        return sum(len(json.dumps(message, ensure_ascii=False)) for message in messages) // 4

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        tier: str,
        profile: str | None = None,
        model_override: str | None = None,
        endpoint_override: str | None = None,
        max_context_override: int | None = None,
        timeout: int = 120,
        max_retries_per_model: int = 2,
        retry_base_delay: float = 2.0,
    ) -> LLMResponse:
        self.call_count += 1
        self.last_messages.append(messages)
        if self.fail_with is not None:
            raise LLMProviderError(str(self.fail_with))
        if not self.responses:
            raise LLMProviderError("No mock responses left")
        raw = self.responses.pop(0)
        return LLMResponse(
            raw=raw,
            model_used=model_override or "mock-model",
            endpoint_used=endpoint_override or "mock-endpoint",
            tokens_in=raw.prompt_tokens,
            tokens_out=raw.completion_tokens,
            cost_usd=raw.cost_usd,
            duration_ms=1,
        )


class MockHumanInterface(HumanInterface):
    def __init__(
        self,
        *,
        approval: bool = True,
        clarification_answer: str = "mock-answer",
        gate_result: dict[str, Any] | None = None,
        approvals: list[bool] | None = None,
        clarifications: list[str] | None = None,
        gate_choices: list[dict[str, Any]] | None = None,
    ):
        self.approval = approval
        self.clarification_answer = clarification_answer
        self.gate_result = gate_result or {"option_id": "default", "captured": {}}
        self._approvals = list(approvals or [])
        self._clarifications = list(clarifications or [])
        self._gate_choices = list(gate_choices or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        self.calls.append(("approval", {"tool_name": tool_name, "arguments": arguments}))
        if self._approvals:
            return self._approvals.pop(0)
        return self.approval

    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        self.calls.append(
            ("clarification", {"question": question, "suggestions": suggestions or []})
        )
        if self._clarifications:
            return self._clarifications.pop(0)
        return self.clarification_answer

    async def present_gate(
        self, *, gate_id: str, presentation: dict, options: list[dict]
    ) -> dict:
        self.calls.append(
            (
                "gate",
                {"gate_id": gate_id, "presentation": presentation, "options": options},
            )
        )
        if self._gate_choices:
            return self._gate_choices.pop(0)
        return self.gate_result
