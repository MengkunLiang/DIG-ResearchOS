from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class TaskHistoryEntry(BaseModel):
    task: str
    run_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    stop_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    llm_profile: str | None = None
    llm_tier: str | None = None
    llm_model: str | None = None
    llm_endpoint: str | None = None
    error: str | None = None


class BudgetSummary(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class PendingGate(BaseModel):
    gate_id: str
    options: list[dict]
    presentation: dict


class RuntimeState(BaseModel):
    project_id: str
    current_task: str
    status: Literal["RUNNING", "WAITING_HUMAN", "PAUSED", "COMPLETED", "FAILED"] = "RUNNING"
    pending_gate: PendingGate | None = None
    history: list[TaskHistoryEntry] = Field(default_factory=list)
    iteration_count: int = 0
    budget_cumulative: BudgetSummary = Field(default_factory=BudgetSummary)
    paused_at: str | None = None
    last_error: str | None = None

    def dump_yaml(self, path: Path) -> None:
        import yaml

        path.write_text(yaml.safe_dump(self.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")

    @classmethod
    def load_yaml(cls, path: Path) -> "RuntimeState":
        import yaml

        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

