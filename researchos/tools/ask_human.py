"""Tool adapter for explicit human decisions inside an Agent workflow.

It records the question, options, and selected response through the configured
interface rather than allowing a model to assume a missing authorization.
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .base import Tool, ToolResult
from .human_gate import HumanInputUnavailable, HumanInterface
from ..schemas.state import GateState, StateYaml


PENDING_HUMAN_INTERACTION = "_runtime/pending_human_interaction.json"
RESUMED_HUMAN_ANSWER = "_runtime/resume/resumed_human_answer.json"


def human_question_fingerprint(task_id: str | None, question: str) -> str:
    """Return a stable identity for a resumable Agent question."""

    payload = f"{str(task_id or '').strip()}\n{' '.join(str(question).split())}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AskHumanParams(BaseModel):
    question: str = Field(..., min_length=1, description="要问用户的问题")
    suggestions: list[str] | None = Field(None, description="可选参考建议")

    @field_validator("question", mode="before")
    @classmethod
    def _normalize_question(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("ask_human.question 不能为空；必须把需要用户看的草案/候选/决策上下文写进 question")
        return text

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
        question = kwargs["question"]
        suggestions = kwargs.get("suggestions") or []
        fingerprint = human_question_fingerprint(self.task_id, question)
        resumed = self._consume_resumed_answer(fingerprint)
        if resumed is not None:
            answer = str(resumed.get("answer") or "").strip()
            if answer:
                self._record_interaction(
                    interaction_id=str(resumed.get("interaction_id") or interaction_id),
                    question=question,
                    suggestions=suggestions,
                    answer=answer,
                )
                return ToolResult(
                    ok=True,
                    content=(
                        f"User answered after resume: {answer}\n"
                        f"[ResearchOS human_interaction_id: {resumed.get('interaction_id') or interaction_id}]"
                    ),
                    data={
                        "interaction_id": resumed.get("interaction_id") or interaction_id,
                        "question": question,
                        "answer": answer,
                        "resumed": True,
                    },
                )

        self._persist_pending_interaction(
            interaction_id=interaction_id,
            fingerprint=fingerprint,
            question=question,
            suggestions=suggestions,
        )
        try:
            answer = await self.human.ask_clarification(
                question=question,
                suggestions=suggestions,
            )
        except HumanInputUnavailable as exc:
            return ToolResult(
                ok=False,
                content=f"Human input unavailable: {exc}",
                data={
                    "interaction_id": interaction_id,
                    "question": question,
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
                    "question": question,
                    "answer": "",
                    "input_unavailable": True,
                },
                error="human_input_unavailable",
            )
        self._record_interaction(
            interaction_id=interaction_id,
            question=question,
            suggestions=suggestions,
            answer=answer,
        )
        self._clear_pending_interaction(fingerprint)
        return ToolResult(
            ok=True,
            content=(
                f"User answered: {answer}\n"
                f"[ResearchOS human_interaction_id: {interaction_id}]"
            ),
            data={"interaction_id": interaction_id, "question": question, "answer": answer},
        )

    def _pending_path(self) -> Path | None:
        return self.workspace_dir / PENDING_HUMAN_INTERACTION if self.workspace_dir is not None else None

    def _resumed_answer_path(self) -> Path | None:
        return self.workspace_dir / RESUMED_HUMAN_ANSWER if self.workspace_dir is not None else None

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _persist_pending_interaction(
        self,
        *,
        interaction_id: str,
        fingerprint: str,
        question: str,
        suggestions: list[str],
    ) -> None:
        """Persist the exact question and WAITING_HUMAN state before blocking."""

        path = self._pending_path()
        if path is None:
            return
        created_at = datetime.now(timezone.utc).isoformat()
        payload: dict[str, object] = {
            "version": "1.0",
            "semantics": "pending_agent_human_interaction",
            "interaction_id": interaction_id,
            "question_fingerprint": fingerprint,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "created_at": created_at,
            "question": question,
            "suggestions": suggestions,
            "resume_behavior": "present_exact_question_then_restart_task_with_answer_context",
        }
        self._atomic_write_json(path, payload)

        state_path = self.workspace_dir / "state.yaml" if self.workspace_dir is not None else None
        if state_path is None or not state_path.exists() or not self.task_id:
            return
        try:
            state = StateYaml.load_yaml(state_path)
        except Exception:
            return
        if state.current_task != self.task_id:
            return
        state.status = "WAITING_HUMAN"
        state.paused_at = created_at
        state.last_error = None
        state.pending_gate = GateState(
            gate_id="agent_ask_human_gate",
            presented_at=created_at,
            presentation={
                "_title": "继续回答上次的确认问题",
                "_description": "问题和当前任务位置已保存。重启后会回到这里，不会把等待计为任务失败。",
                "interaction_id": interaction_id,
                "question_fingerprint": fingerprint,
                "question": question,
                "suggestions": suggestions,
            },
            options=[{"id": "answer", "label": "提交回答"}],
        )
        state.task_context["pending_agent_human_interaction"] = {
            "interaction_id": interaction_id,
            "question_fingerprint": fingerprint,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "request_path": PENDING_HUMAN_INTERACTION,
        }
        if state.history and state.history[-1].task == self.task_id and state.history[-1].run_id == self.run_id:
            state.history[-1].status = "WAITING_HUMAN"
        state.dump_yaml(state_path)

    def _clear_pending_interaction(self, fingerprint: str) -> None:
        path = self._pending_path()
        if path is not None and path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict) or payload.get("question_fingerprint") == fingerprint:
                path.unlink(missing_ok=True)

        if self.workspace_dir is None or not self.task_id:
            return
        state_path = self.workspace_dir / "state.yaml"
        if not state_path.exists():
            return
        try:
            state = StateYaml.load_yaml(state_path)
        except Exception:
            return
        if (
            state.current_task == self.task_id
            and state.pending_gate is not None
            and state.pending_gate.gate_id == "agent_ask_human_gate"
            and state.pending_gate.presentation.get("question_fingerprint") == fingerprint
        ):
            state.pending_gate = None
            state.status = "RUNNING"
            state.paused_at = None
            state.task_context.pop("pending_agent_human_interaction", None)
            if state.history and state.history[-1].task == self.task_id and state.history[-1].run_id == self.run_id:
                state.history[-1].status = "RUNNING"
            state.dump_yaml(state_path)

    def _consume_resumed_answer(self, fingerprint: str) -> dict[str, object] | None:
        path = self._resumed_answer_path()
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("question_fingerprint") != fingerprint:
            return None
        path.unlink(missing_ok=True)
        return payload

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
