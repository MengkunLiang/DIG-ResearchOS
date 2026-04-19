from __future__ import annotations

"""runtime `state.yaml` 的结构化模型。"""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..pydantic_compat import model_dump, model_validate


class GateState(BaseModel):
    """当前挂起的 gate 信息。"""

    gate_id: str
    presented_at: str
    presentation: dict[str, Any]
    options: list[dict[str, Any]]


class TaskHistoryEntry(BaseModel):
    """单次 task run 的审计记录。"""

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
    """项目级累计预算。"""

    tokens_total: int = 0
    cost_usd_total: float = 0.0
    gpu_hours_used: float = 0.0


class StateYaml(BaseModel):
    """state.yaml 顶层模型。"""

    project_id: str
    current_task: str
    status: Literal["RUNNING", "WAITING_HUMAN", "PAUSED", "COMPLETED", "FAILED"] = "RUNNING"
    pending_gate: GateState | None = None
    history: list[TaskHistoryEntry] = Field(default_factory=list)
    iteration_count: dict[str, int] = Field(default_factory=dict)
    budget_cumulative: BudgetCumulative = Field(default_factory=BudgetCumulative)
    # task_context 用于承载 gate 选项附带的 extra，供下一个 task 的 ctx.extra 读取。
    task_context: dict[str, Any] = Field(default_factory=dict)
    paused_at: str | None = None
    last_error: str | None = None

    def dump_yaml(self, path: Path) -> None:
        """原子写入 state.yaml，避免中断时留下半个文件。"""
        import yaml

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(model_dump(self, mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    @classmethod
    def load_yaml(cls, path: Path) -> "StateYaml":
        import yaml

        return model_validate(cls, yaml.safe_load(path.read_text(encoding="utf-8")))


# 兼容旧导入名，避免已有调用点全部重写。
BudgetSummary = BudgetCumulative
PendingGate = GateState
RuntimeState = StateYaml
