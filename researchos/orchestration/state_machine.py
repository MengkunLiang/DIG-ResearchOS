from __future__ import annotations

"""ResearchOS 状态机解释器。

本模块负责三件事：
1. 把 `config/state_machine.yaml` 解析成 task 节点；
2. 基于 `AgentResult` 推进 `state.yaml`；
3. 在 gate / resume / iteration 这些跨 task 语义上做统一处理。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Any

import yaml

from ..pydantic_compat import model_dump
from ..runtime.agent import (
    AgentResult,
    BudgetOverride,
    ExecutionContext,
    LLMConfigOverride,
    ToolPolicyOverride,
)
from ..schemas.state import BudgetCumulative, GateState, StateYaml, TaskHistoryEntry
from .gate_presenter import build_presentation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskNode:
    """一个 FSM 节点的运行期表示。"""

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
    """ResearchOS runtime 层的状态机。

    设计原则：
    - 状态推进要尽量声明式：节点配置决定下一跳，代码只解释语义；
    - `state.yaml` 是唯一真相来源，CLI 每次操作都可重建状态；
    - runtime 只管理“如何推进”，不管理 agent 内部如何恢复历史工作。
    """

    def __init__(self, config_path: Path, gates_config_path: Path | None = None):
        self.config_path = config_path
        self.gates_config_path = gates_config_path
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.initial_state = raw["initial_state"]
        self.nodes = self._parse_nodes(raw)
        self.gates = self._load_gates(gates_config_path)

    def _parse_nodes(self, raw: dict[str, Any]) -> dict[str, TaskNode]:
        """同时兼容两种配置风格：

        - `states: {T1: {...}}`
        - `nodes: [{id: T1, ...}]`
        """
        source = raw.get("states") or raw.get("nodes") or {}
        if isinstance(source, list):
            return {
                item["id"]: TaskNode(
                    task_id=item["id"],
                    **{key: value for key, value in item.items() if key != "id"},
                )
                for item in source
            }
        return {task_id: TaskNode(task_id=task_id, **cfg) for task_id, cfg in source.items()}

    def _load_gates(self, gates_config_path: Path | None) -> dict[str, dict[str, Any]]:
        if gates_config_path is None or not gates_config_path.exists():
            return {}
        raw = yaml.safe_load(gates_config_path.read_text(encoding="utf-8")) or {}
        gates = raw.get("gates", raw)
        if isinstance(gates, list):
            return {gate["id"]: gate for gate in gates}
        if not isinstance(gates, dict):
            raise ValueError("gates config must be a mapping or list")
        return gates

    def create_initial_state(self, project_id: str) -> StateYaml:
        """创建项目首次运行时的状态。"""
        return StateYaml(project_id=project_id, current_task=self.initial_state)

    def build_execution_context(self, workspace_dir: Path, state: StateYaml) -> ExecutionContext:
        """把当前状态翻译成 AgentRunner 可执行的 ExecutionContext。"""
        node = self.nodes[state.current_task]
        run_id = f"{state.current_task.lower()}_{uuid.uuid4().hex[:8]}"
        outputs = {name: workspace_dir / rel for name, rel in (node.outputs or {}).items()}
        inputs = {name: workspace_dir / rel for name, rel in (node.inputs or {}).items()}

        # ctx.extra 的来源分三层：
        # 1. state.task_context：上一个 gate 决策附带的上下文
        # 2. node.extra：节点自己声明的静态额外信息
        # 3. runtime 自动注入的 resume / iteration 标志
        extra = dict(state.task_context)
        extra.update(node.extra or {})

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
        """task 开始执行前，先写入一条 RUNNING history。"""
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
        """收到 SIGINT / SIGTERM 后，把项目置为 PAUSED。"""
        if state.history:
            state.history[-1].status = "INTERRUPTED"
            state.history[-1].finished_at = _now_iso()
            state.history[-1].stop_reason = AgentResult.STOP_INTERRUPTED
        state.status = "PAUSED"
        state.paused_at = _now_iso()
        return state

    def advance(
        self,
        state: StateYaml,
        result: AgentResult,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """根据一次 agent run 的结果推进状态机。"""
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
            presentation = build_presentation(
                gate_spec,
                model_dump(state, mode="json"),
                workspace_dir or Path("."),
            )
            state.pending_gate = GateState(
                gate_id=gate_id,
                presented_at=_now_iso(),
                presentation=presentation,
                options=list(gate_spec.get("options", [])),
            )
            state.status = "WAITING_HUMAN"
            return state

        return self._transition_to_next(state, node.next_on_success)

    def resolve_pending_gate(self, state: StateYaml, gate_result: dict[str, Any]) -> StateYaml:
        """处理一个已挂起 gate 的用户选择。"""
        if state.pending_gate is None:
            raise ValueError("No pending gate to resolve")
        node = self.nodes[state.current_task]
        next_task = self._resolve_branch(node, gate_result, state)
        state.pending_gate = None
        return self._transition_to_next(state, next_task)

    def _transition_to_next(self, state: StateYaml, next_task: str | None) -> StateYaml:
        """统一处理正常 next / terminal next / 特殊占位 next。"""
        if next_task is None or next_task == "__terminal__":
            state.status = "COMPLETED"
            return state
        if next_task == "__fail__":
            state.status = "FAILED"
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

    def _resolve_branch(self, node: TaskNode, gate_result: dict[str, Any], state: StateYaml) -> str:
        """根据 gate 选择计算下一跳。

        支持两类配置：
        - `gate.options[*].next`
        - `branches: {option_id: next_task}`
        """
        option_id = gate_result.get("option_id") or gate_result.get("key")
        gate_spec = self.gates.get(self._gate_id_for_node(node), {})
        option = self._find_option(gate_spec, option_id) or self._find_option_from_node(node, option_id)
        next_state = None
        if option is not None:
            next_state = option.get("next")
            if option.get("extra"):
                state.task_context.update(option["extra"])

        branches = dict(node.branches or {})
        if isinstance(node.gate, dict):
            branches.update(node.gate.get("branches", {}))
        branches.update(gate_spec.get("branches", {}))
        if next_state is None:
            next_state = branches.get(option_id)
        if next_state is None:
            raise KeyError(f"Gate option '{option_id}' has no branch mapping")

        # 如果 next_state 指向一个之前成功跑过的 task，则记为一次“正常迭代”。
        if next_state in self.nodes and self._is_iteration(next_state, state):
            state.iteration_count[next_state] = state.iteration_count.get(next_state, 0) + 1

        if next_state in self.nodes:
            limit = self.nodes[next_state].max_iterations
            if limit is not None and state.iteration_count.get(next_state, 0) >= limit:
                if "ITER_LIMIT_GATE" in self.nodes:
                    return "ITER_LIMIT_GATE"
        return next_state

    @staticmethod
    def _find_option(gate_spec: dict[str, Any], option_id: str | None) -> dict[str, Any] | None:
        for option in gate_spec.get("options", []):
            key = option.get("id") or option.get("key")
            if key == option_id:
                return option
        return None

    @staticmethod
    def _find_option_from_node(node: TaskNode, option_id: str | None) -> dict[str, Any] | None:
        if not isinstance(node.gate, dict):
            return None
        for option in node.gate.get("options", []):
            key = option.get("id") or option.get("key")
            if key == option_id:
                return option
        return None

    def _is_iteration(self, next_state: str, state: StateYaml) -> bool:
        return any(history.task == next_state and history.status == "DONE" for history in state.history)

    def _build_overrides(
        self,
        node: TaskNode,
    ) -> tuple[LLMConfigOverride, BudgetOverride, ToolPolicyOverride]:
        """把节点里的 llm/budget/tools 块转换成 ExecutionContext override。"""
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
