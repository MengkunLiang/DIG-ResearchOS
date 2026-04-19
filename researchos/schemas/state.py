from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class GateState(BaseModel):
    gate_id: str
    presented_at: str
    presentation: dict[str, Any]
    options: list[dict[str, Any]]


class TaskHistoryEntry(BaseModel):
    task: str
    run_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    stop_reason: str | None = None
    tokens: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    llm_profile: str | None = None
    llm_tier: str | None = None
    llm_model: str | None = None
    llm_endpoint: str | None = None
    error: str | None = None


class BudgetCumulative(BaseModel):
    tokens_total: int = 0
    cost_usd_total: float = 0.0
    gpu_hours_used: float = 0.0


class StateYaml(BaseModel):
    project_id: str
    current_task: str
    status: Literal["RUNNING", "WAITING_HUMAN", "PAUSED", "COMPLETED", "FAILED"] = "RUNNING"
    pending_gate: GateState | None = None
    history: list[TaskHistoryEntry] = Field(default_factory=list)
    iteration_count: dict[str, int] = Field(default_factory=dict)
    budget_cumulative: BudgetCumulative = Field(default_factory=BudgetCumulative)
    paused_at: str | None = None
    last_error: str | None = None

    def dump_yaml(self, path: Path) -> None:
        import yaml

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    @classmethod
    def load_yaml(cls, path: Path) -> "StateYaml":
        import yaml

        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# Backward-compatible aliases for existing imports.
BudgetSummary = BudgetCumulative
PendingGate = GateState
RuntimeState = StateYaml
