from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..runtime.agent import AgentResult, BudgetOverride, ExecutionContext, LLMConfigOverride, ToolPolicyOverride
from ..schemas.state import BudgetSummary, RuntimeState, TaskHistoryEntry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskNode:
    task_id: str
    agent: str
    outputs: dict[str, str]
    next_on_success: str | None = None
    next_on_failure: str | None = None
    terminal: bool = False
    llm: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    tools: dict[str, Any] | None = None


class StateMachine:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.initial_state = raw["initial_state"]
        self.nodes = {
            task_id: TaskNode(task_id=task_id, **cfg) for task_id, cfg in raw["states"].items()
        }

    def create_initial_state(self, project_id: str) -> RuntimeState:
        return RuntimeState(project_id=project_id, current_task=self.initial_state)

    def build_execution_context(self, workspace_dir: Path, state: RuntimeState) -> ExecutionContext:
        node = self.nodes[state.current_task]
        run_id = f"{state.current_task.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        outputs = {name: workspace_dir / rel for name, rel in node.outputs.items()}
        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id=state.project_id,
            task_id=node.task_id,
            run_id=run_id,
            outputs_expected=outputs,
        )
        if node.llm:
            ctx.llm_override = LLMConfigOverride(
                profile=node.llm.get("profile"),
                tier=node.llm.get("tier"),
                model=node.llm.get("model"),
                temperature=node.llm.get("temperature"),
            )
        if node.budget:
            ctx.budget_override = BudgetOverride(
                max_steps=node.budget.get("max_steps"),
                max_tokens=node.budget.get("max_tokens"),
                max_wall_seconds=node.budget.get("max_wall_seconds"),
            )
        if node.tools:
            ctx.tool_policy_override = ToolPolicyOverride(
                allowed_read_prefixes=node.tools.get("allowed_read_prefixes"),
                allowed_write_prefixes=node.tools.get("allowed_write_prefixes"),
                extra_tool_names=node.tools.get("extra_tool_names", []),
            )
        if state.status == "PAUSED":
            ctx.extra["is_resume"] = True
        return ctx

    def start_task(self, state: RuntimeState, run_id: str) -> RuntimeState:
        state.status = "RUNNING"
        state.paused_at = None
        state.history.append(
            TaskHistoryEntry(
                task=state.current_task,
                run_id=run_id,
                status="RUNNING",
                started_at=_now_iso(),
            )
        )
        return state

    def advance(self, state: RuntimeState, result: AgentResult) -> RuntimeState:
        history = state.history[-1]
        history.finished_at = _now_iso()
        history.stop_reason = result.stop_reason
        history.tokens_in = result.tokens_in
        history.tokens_out = result.tokens_out
        history.cost_usd = result.cost_usd
        history.llm_profile = result.llm_profile
        history.llm_tier = result.llm_tier
        history.llm_model = result.llm_model_used
        history.llm_endpoint = result.llm_endpoint_used
        history.error = result.error
        history.status = "DONE" if result.ok else result.stop_reason.upper()
        state.budget_cumulative = BudgetSummary(
            tokens_in=state.budget_cumulative.tokens_in + result.tokens_in,
            tokens_out=state.budget_cumulative.tokens_out + result.tokens_out,
            cost_usd=state.budget_cumulative.cost_usd + result.cost_usd,
        )

        if result.stop_reason == AgentResult.STOP_INTERRUPTED:
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            return state

        node = self.nodes[state.current_task]
        if result.ok:
            next_task = node.next_on_success
            if next_task is None or self.nodes[next_task].terminal:
                state.current_task = next_task or state.current_task
                state.status = "COMPLETED"
            else:
                state.current_task = next_task
                state.status = "RUNNING"
            return state

        next_task = node.next_on_failure
        state.last_error = result.error
        if next_task and next_task in self.nodes and not self.nodes[next_task].terminal:
            state.current_task = next_task
            state.status = "RUNNING"
        else:
            state.status = "FAILED"
        return state

