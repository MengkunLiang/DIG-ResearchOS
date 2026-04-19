from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..runtime.agent import (
    AgentResult,
    BudgetOverride,
    ExecutionContext,
    LLMConfigOverride,
    ToolPolicyOverride,
)
from ..schemas.state import BudgetCumulative, GateState, StateYaml, TaskHistoryEntry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskNode:
    task_id: str
    agent: str | None = None
    skill: str | None = None
    inputs: dict[str, str] | None = None
    outputs: dict[str, str] | None = None
    next_on_success: str | None = None
    next_on_failure: str | None = None
    terminal: bool = False
    llm: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    tools: dict[str, Any] | None = None
    mode: str | None = None
    gate: str | dict[str, Any] | None = None
    branches: dict[str, str] | None = None
    max_iterations: int | None = None
    extra: dict[str, Any] | None = None


class StateMachine:
    def __init__(self, config_path: Path, gates_config_path: Path | None = None):
        self.config_path = config_path
        self.gates_config_path = gates_config_path
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.initial_state = raw["initial_state"]
        self.nodes = self._parse_nodes(raw)
        self.gates = self._load_gates(gates_config_path)

    def _parse_nodes(self, raw: dict[str, Any]) -> dict[str, TaskNode]:
        source = raw.get("states") or raw.get("nodes") or {}
        if isinstance(source, list):
            return {item["id"]: TaskNode(task_id=item["id"], **{k: v for k, v in item.items() if k != "id"}) for item in source}
        return {task_id: TaskNode(task_id=task_id, **cfg) for task_id, cfg in source.items()}

    def _load_gates(self, gates_config_path: Path | None) -> dict[str, dict[str, Any]]:
        if gates_config_path is None or not gates_config_path.exists():
            return {}
        raw = yaml.safe_load(gates_config_path.read_text(encoding="utf-8")) or {}
        gates = raw.get("gates", raw)
        if not isinstance(gates, dict):
            raise ValueError("gates config must be a mapping")
        return gates

    def create_initial_state(self, project_id: str) -> StateYaml:
        return StateYaml(project_id=project_id, current_task=self.initial_state)

    def build_execution_context(self, workspace_dir: Path, state: StateYaml) -> ExecutionContext:
        node = self.nodes[state.current_task]
        run_id = f"{state.current_task.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        outputs = {
            name: workspace_dir / rel for name, rel in (node.outputs or {}).items()
        }
        inputs = {
            name: workspace_dir / rel for name, rel in (node.inputs or {}).items()
        }
        extra = dict(node.extra or {})
        iteration = state.iteration_count.get(state.current_task, 0)
        if iteration:
            extra["iteration_count"] = iteration

        resumed_from = None
        for history in reversed(state.history):
            if history.task != state.current_task:
                continue
            if history.status == "INTERRUPTED":
                resumed_from = history.run_id
            break
        if resumed_from:
            extra["is_resume"] = True
            extra["resumed_from"] = resumed_from

        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id=state.project_id,
            task_id=node.task_id,
            run_id=run_id,
            inputs=inputs,
            outputs_expected=outputs,
            mode=node.mode,
            extra=extra,
        )
        llm_ov, budget_ov, tool_ov = self._build_overrides(node)
        ctx.llm_override = llm_ov
        ctx.budget_override = budget_ov
        ctx.tool_policy_override = tool_ov
        return ctx

    def start_task(self, state: StateYaml, run_id: str) -> StateYaml:
        state.status = "RUNNING"
        state.pending_gate = None
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

    def mark_interrupted(self, state: StateYaml) -> StateYaml:
        if state.history:
            state.history[-1].status = "INTERRUPTED"
            state.history[-1].finished_at = _now_iso()
            state.history[-1].stop_reason = AgentResult.STOP_INTERRUPTED
        state.status = "PAUSED"
        state.paused_at = _now_iso()
        return state

    def advance(self, state: StateYaml, result: AgentResult, *, workspace_dir: Path | None = None) -> StateYaml:
        history = state.history[-1]
        history.finished_at = _now_iso()
        history.stop_reason = result.stop_reason
        history.tokens = result.tokens_in + result.tokens_out
        history.tokens_in = result.tokens_in
        history.tokens_out = result.tokens_out
        history.cost_usd = result.cost_usd
        history.llm_profile = result.llm_profile
        history.llm_tier = result.llm_tier
        history.llm_model = result.llm_model_used
        history.llm_endpoint = result.llm_endpoint_used
        history.error = result.error
        history.status = "DONE" if result.ok else "FAILED"

        state.budget_cumulative = BudgetCumulative(
            tokens_total=state.budget_cumulative.tokens_total + history.tokens,
            cost_usd_total=state.budget_cumulative.cost_usd_total + result.cost_usd,
            gpu_hours_used=state.budget_cumulative.gpu_hours_used,
        )

        if result.stop_reason == AgentResult.STOP_INTERRUPTED:
            return self.mark_interrupted(state)

        node = self.nodes[state.current_task]
        if not result.ok:
            state.last_error = result.error
            next_task = node.next_on_failure
            if next_task and next_task in self.nodes and not self.nodes[next_task].terminal:
                state.current_task = next_task
                state.status = "RUNNING"
            else:
                if next_task and next_task in self.nodes:
                    state.current_task = next_task
                state.status = "FAILED"
            return state

        if node.gate:
            gate_id = self._gate_id_for_node(node)
            gate_spec = self._find_gate(gate_id)
            state.pending_gate = GateState(
                gate_id=gate_id,
                presented_at=_now_iso(),
                presentation=self._build_presentation(gate_spec, state, workspace_dir),
                options=list(gate_spec.get("options", [])),
            )
            state.status = "WAITING_HUMAN"
            return state

        return self._transition_to_next(state, node.next_on_success)

    def resolve_pending_gate(self, state: StateYaml, gate_result: dict[str, Any]) -> StateYaml:
        if state.pending_gate is None:
            raise ValueError("No pending gate to resolve")
        node = self.nodes[state.current_task]
        next_task = self._resolve_branch(node, gate_result, state)
        state.pending_gate = None
        return self._transition_to_next(state, next_task)

    def _transition_to_next(self, state: StateYaml, next_task: str | None) -> StateYaml:
        if next_task is None:
            state.status = "COMPLETED"
            return state
        target = self.nodes[next_task]
        state.current_task = next_task
        if target.terminal:
            state.status = "FAILED" if next_task.lower().startswith("fail") else "COMPLETED"
        else:
            state.status = "RUNNING"
        return state

    def _gate_id_for_node(self, node: TaskNode) -> str:
        if isinstance(node.gate, dict):
            return str(node.gate.get("id") or node.gate.get("ref"))
        return str(node.gate)

    def _find_gate(self, gate_id: str) -> dict[str, Any]:
        gate = self.gates.get(gate_id)
        if gate is None:
            raise KeyError(f"Gate '{gate_id}' not found in gates config")
        return gate

    def _build_presentation(
        self,
        gate_spec: dict[str, Any],
        state: StateYaml,
        workspace_dir: Path | None,
    ) -> dict[str, Any]:
        presentation: dict[str, Any] = {}
        for key, source in (gate_spec.get("presentation") or {}).items():
            if isinstance(source, str):
                presentation[key] = source
                continue
            if not isinstance(source, dict):
                presentation[key] = source
                continue
            if "literal" in source:
                presentation[key] = source["literal"]
                continue
            if "from_state" in source:
                presentation[key] = self._read_state_path(state, str(source["from_state"]))
                continue
            if "from_file" in source:
                if workspace_dir is None:
                    presentation[key] = f"<missing workspace for {source['from_file']}>"
                else:
                    path = (workspace_dir / str(source["from_file"])).resolve()
                    presentation[key] = path.read_text(encoding="utf-8") if path.exists() else ""
                continue
            presentation[key] = source
        return presentation

    def _read_state_path(self, state: StateYaml, dotted: str) -> Any:
        current: Any = state.model_dump(mode="json")
        for part in dotted.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _resolve_branch(self, node: TaskNode, gate_result: dict[str, Any], state: StateYaml) -> str:
        option_id = gate_result.get("option_id")
        branches = dict(node.branches or {})
        if isinstance(node.gate, dict):
            branches.update(node.gate.get("branches", {}))
        gate_spec = self.gates.get(self._gate_id_for_node(node), {})
        branches.update(gate_spec.get("branches", {}))
        if option_id not in branches:
            raise KeyError(f"Gate option '{option_id}' has no branch mapping")
        next_state = branches[option_id]

        if next_state in self.nodes and self._is_iteration(next_state, state):
            state.iteration_count[next_state] = state.iteration_count.get(next_state, 0) + 1

        if next_state in self.nodes:
            limit = self.nodes[next_state].max_iterations
            if limit is not None and state.iteration_count.get(next_state, 0) >= limit:
                if "ITER_LIMIT_GATE" in self.nodes:
                    return "ITER_LIMIT_GATE"
        return next_state

    def _is_iteration(self, next_state: str, state: StateYaml) -> bool:
        return any(
            history.task == next_state and history.status == "DONE"
            for history in state.history
        )

    def _build_overrides(
        self,
        node: TaskNode,
    ) -> tuple[LLMConfigOverride, BudgetOverride, ToolPolicyOverride]:
        llm_block = node.llm or {}
        llm_ov = LLMConfigOverride(
            profile=llm_block.get("profile"),
            tier=llm_block.get("tier"),
            model=llm_block.get("model"),
            temperature=llm_block.get("temperature"),
        )

        budget_block = node.budget or {}
        budget_ov = BudgetOverride(
            max_steps=budget_block.get("max_steps"),
            max_tokens=budget_block.get("max_tokens"),
            max_wall_seconds=budget_block.get("max_wall_seconds"),
        )

        tools_block = node.tools or {}
        tool_ov = ToolPolicyOverride(
            allowed_read_prefixes=tools_block.get("allowed_read_prefixes"),
            allowed_write_prefixes=tools_block.get("allowed_write_prefixes"),
            extra_tool_names=tools_block.get("extra_tool_names", tools_block.get("extra", [])),
        )
        return llm_ov, budget_ov, tool_ov
