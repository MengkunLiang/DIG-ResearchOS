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
import re
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
from ..runtime.task_recovery import prepare_task_resume_artifacts
from ..schemas.state import BudgetCumulative, GateState, StateYaml, TaskHistoryEntry
from .gate_presenter import build_presentation
from .task_io_contract import get_task_io


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskNode:
    """一个 FSM 节点的运行期表示。"""

    task_id: str
    agent: str | None = None
    skill: str | None = None
    description: str | None = None
    inputs: dict[str, str] | None = None
    outputs: dict[str, str] | None = None
    optional_outputs: dict[str, str] | None = None
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
    round: int | None = None
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

    def validate_definition(self) -> list[str]:
        """对状态机配置做启动前静态校验。

        这里专注于 runtime 自己有能力判断的结构问题：
        - 初始节点是否存在；
        - 非 terminal 节点是否声明了且只声明了一种执行体(agent 或 skill)；
        - 所有 next/branch 是否都指向已知节点或特殊占位符；
        - gate 引用是否存在；
        - 与 task I/O 契约是否明显不一致。
        """

        errors: list[str] = []
        if self.initial_state not in self.nodes:
            errors.append(f"initial_state '{self.initial_state}' not found in state machine nodes")

        for task_id, node in self.nodes.items():
            if node.terminal:
                continue

            if bool(node.agent) == bool(node.skill):
                errors.append(
                    f"{task_id}: non-terminal node must declare exactly one of 'agent' or 'skill'"
                )

            for field_name, target in (
                ("next_on_success", node.next_on_success),
                ("next_on_failure", node.next_on_failure),
            ):
                self._validate_target(task_id, field_name, target, errors)

            self._validate_gate(task_id, node, errors)
            self._validate_task_contract(task_id, node, errors)
        return errors

    def build_execution_context(self, workspace_dir: Path, state: StateYaml) -> ExecutionContext:
        """把当前状态翻译成 AgentRunner 可执行的 ExecutionContext。"""
        node = self.nodes[state.current_task]

        # Phase 2.3: 检查迭代死锁（相同参数重复3次以上）
        self._check_iteration_deadlock(state, node)

        run_id = f"{state.current_task.lower()}_{uuid.uuid4().hex[:8]}"
        outputs = {name: workspace_dir / rel for name, rel in (node.outputs or {}).items()}
        inputs = {name: workspace_dir / rel for name, rel in (node.inputs or {}).items()}

        # ctx.extra 的来源分三层：
        # 1. state.task_context：上一个 gate 决策附带的上下文
        # 2. node.extra：节点自己声明的静态额外信息
        # 3. runtime 自动注入的 resume / iteration 标志
        extra = dict(state.task_context)
        extra.update(node.extra or {})
        if node.description:
            extra.setdefault("task_description", node.description)
        if node.agent:
            extra.setdefault("agent_id", node.agent)
        if node.skill:
            extra.setdefault("skill_id", node.skill)
        if node.mode is not None:
            extra.setdefault("phase", node.mode)
        if node.round is not None:
            extra.setdefault("round", node.round)

        # P0-9 修复: 设置 skill_dir（如果是 skill 节点）
        if node.skill:
            extra["skill_name"] = node.skill
            # 设置实际skill_dir路径供bash_run等工具使用
            skill_dir = workspace_dir / "skills" / node.skill
            extra["skill_dir"] = str(skill_dir)

        iteration = state.iteration_count.get(state.current_task, 0)
        if iteration:
            extra["iteration_count"] = iteration

        # P0-2 修复: 检测 resume 场景并设置 extra 字段
        resumed_from = None
        resume_reason = None

        for history in reversed(state.history):
            if history.task != state.current_task:
                continue

            # 场景1: INTERRUPTED（用户Ctrl+C）
            if history.status == "INTERRUPTED":
                resumed_from = history.run_id
                resume_reason = "interrupted"
                break

            # 场景2: FAILED重试（验证失败，将重试）
            if history.status == "FAILED":
                # 检查是否为重试场景（非终止失败）
                # 注意：STOP_HUMAN_REJECT表示用户拒绝，不应重试
                if hasattr(history, 'stop_reason') and history.stop_reason == "human_reject":
                    break
                # 检查是否配置了失败后重试
                if node.next_on_failure and node.next_on_failure == state.current_task:
                    resumed_from = history.run_id
                    resume_reason = "retry_after_failure"
                    break

            # 场景3: 迭代（通过gate返回同一任务）
            if history.status == "DONE" and iteration > 0:
                resumed_from = history.run_id
                resume_reason = "iteration"
                break

            # 只检查该任务的最近一次运行
            break

        if resumed_from:
            # 设计文档 §13.5 要求的字段
            extra["resumed_from_run_id"] = resumed_from
            extra["resume_mode"] = True
            extra["resume_reason"] = resume_reason
            # 保留旧字段以兼容现有代码
            extra["is_resume"] = True
            extra["resumed_from"] = resumed_from

        # 所有 task 都统一生成恢复快照，让 pipeline resume 与单任务续跑共享同一语义。
        recovery_info = prepare_task_resume_artifacts(
            workspace_dir,
            task_id=node.task_id,
            outputs_expected=outputs,
            base_extra=extra,
        )
        extra.update(recovery_info)

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

        # Phase 2.3: 记录迭代历史（用于死锁检测）
        self._record_iteration_attempt(state, self.nodes[state.current_task])

        return state

    def mark_interrupted(self, state: StateYaml, *, reason: str | None = None) -> StateYaml:
        """收到 SIGINT / SIGTERM 后，把项目置为 PAUSED。"""
        if state.history:
            if state.history[-1].status not in {"DONE", "FAILED", "INTERRUPTED"}:
                state.history[-1].status = "INTERRUPTED"
            state.history[-1].finished_at = _now_iso()
            state.history[-1].stop_reason = state.history[-1].stop_reason or AgentResult.STOP_INTERRUPTED
            if reason and not state.history[-1].error:
                state.history[-1].error = reason
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
        history.completion_mode = (result.metadata or {}).get("completion_mode")
        history.error = result.error
        recoverable_pause = result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }
        history.status = "DONE" if result.ok else "INTERRUPTED" if recoverable_pause else "FAILED"

        state.budget_cumulative = BudgetCumulative(
            tokens_total=state.budget_cumulative.tokens_total + history.tokens,
            cost_usd_total=state.budget_cumulative.cost_usd_total + result.cost_usd,
            gpu_hours_used=state.budget_cumulative.gpu_hours_used,
        )

        # Budget drift warning (§7.1)
        if workspace_dir:
            self._check_budget_drift(state, workspace_dir)

        if result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }:
            state.last_error = result.error
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

        return self._transition_to_next(state, node.next_on_success, workspace_dir=workspace_dir)

    def resolve_pending_gate(
        self,
        state: StateYaml,
        gate_result: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """处理一个已挂起 gate 的用户选择。"""
        if state.pending_gate is None:
            raise ValueError("No pending gate to resolve")
        node = self.nodes[state.current_task]
        next_task = self._resolve_branch(node, gate_result, state, workspace_dir=workspace_dir)
        state.pending_gate = None
        return self._transition_to_next(state, next_task, workspace_dir=workspace_dir)

    def _transition_to_next(
        self,
        state: StateYaml,
        next_task: str | None,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """统一处理正常 next / terminal next / 特殊占位 next。"""
        if next_task is None or next_task == "__terminal__":
            state.status = "COMPLETED"
            return state
        if next_task == "__fail__":
            state.status = "FAILED"
            return state

        next_task = self._resolve_special_target(
            current_task=state.current_task,
            next_task=next_task,
            workspace_dir=workspace_dir,
        )

        target = self.nodes[next_task]
        state.current_task = next_task
        if target.terminal:
            state.status = "FAILED" if next_task.lower().startswith("fail") else "COMPLETED"
        else:
            state.status = "RUNNING"
        return state

    def _gate_id_for_node(self, node: TaskNode) -> str:
        if isinstance(node.gate, dict):
            return str(node.gate.get("id") or node.gate.get("ref") or node.gate.get("type"))
        return str(node.gate)

    def _find_gate(self, gate_id: str) -> dict[str, Any]:
        gate = self.gates.get(gate_id)
        if gate is None:
            raise KeyError(f"Gate '{gate_id}' not found in gates config")
        return gate

    def _resolve_branch(
        self,
        node: TaskNode,
        gate_result: dict[str, Any],
        state: StateYaml,
        *,
        workspace_dir: Path | None = None,
    ) -> str:
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

        next_state = self._resolve_special_target(
            current_task=state.current_task,
            next_task=next_state,
            workspace_dir=workspace_dir,
        )

        # 如果 next_state 指向一个之前成功跑过的 task，则记为一次“正常迭代”。
        if next_state in self.nodes and self._is_iteration(next_state, state):
            state.iteration_count[next_state] = state.iteration_count.get(next_state, 0) + 1

        if next_state in self.nodes:
            limit = self.nodes[next_state].max_iterations
            if limit is not None and state.iteration_count.get(next_state, 0) >= limit:
                if "ITER_LIMIT_GATE" in self.nodes:
                    return "ITER_LIMIT_GATE"
        return next_state

    def _resolve_special_target(
        self,
        *,
        current_task: str,
        next_task: str,
        workspace_dir: Path | None,
    ) -> str:
        """解析状态机里的特殊占位目标。"""
        if next_task != "__parse_from_output__":
            return next_task
        if workspace_dir is None:
            raise ValueError("workspace_dir is required for __parse_from_output__ targets")

        if current_task == "T7.5":
            return self._parse_t75_decision(workspace_dir)

        raise ValueError(f"Unsupported __parse_from_output__ task: {current_task}")

    def _parse_t75_decision(self, workspace_dir: Path) -> str:
        """T7.5 完成后，解析 evaluation_decision.md 的推荐下一步。"""

        default_t8_entry = self._default_t8_entry()
        decision_path = workspace_dir / "evaluation" / "evaluation_decision.md"
        if not decision_path.exists():
            return default_t8_entry

        text = decision_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"next_task:\s*([A-Za-z0-9_.-]+)", text, re.DOTALL)
        if match is None:
            return default_t8_entry

        raw_target = match.group(1).strip()
        aliases = {
            "T8": "T8-RESOURCE",
            "T8-WRITE": "T8-RESOURCE",
            "terminate": "done",
            "terminal": "done",
            "stop": "done",
            "end": "done",
        }
        target = aliases.get(raw_target, raw_target)
        if target not in self.nodes and raw_target in self.nodes:
            return raw_target
        if target not in self.nodes:
            return default_t8_entry
        return target

    def _default_t8_entry(self) -> str:
        if "T8-RESOURCE" in self.nodes:
            return "T8-RESOURCE"
        if "T8-WRITE" in self.nodes:
            return "T8-WRITE"
        return "done"

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
            endpoint=llm_block.get("endpoint"),
            max_context=llm_block.get("max_context"),
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

    def _validate_target(
        self,
        task_id: str,
        field_name: str,
        target: str | None,
        errors: list[str],
    ) -> None:
        if target is None:
            return
        if target in {"__terminal__", "__fail__", "__parse_from_output__"}:
            return
        if target not in self.nodes:
            errors.append(f"{task_id}: {field_name} points to unknown node '{target}'")

    def _validate_gate(self, task_id: str, node: TaskNode, errors: list[str]) -> None:
        if not node.gate:
            return

        gate_id = self._gate_id_for_node(node)
        inline_gate = node.gate if isinstance(node.gate, dict) else {}
        gate_spec = self.gates.get(gate_id, {})
        if gate_id not in self.gates and not inline_gate.get("options"):
            errors.append(f"{task_id}: gate '{gate_id}' not found in gates config")

        for option in list(gate_spec.get("options", [])) + list(inline_gate.get("options", [])):
            next_target = option.get("next")
            if next_target is not None:
                self._validate_target(task_id, f"gate option '{option.get('id') or option.get('key')}'", next_target, errors)

        branch_maps = [
            ("branches", node.branches or {}),
            ("gate.branches", inline_gate.get("branches", {})),
            (f"gates.{gate_id}.branches", gate_spec.get("branches", {})),
        ]
        for field_name, mapping in branch_maps:
            if not isinstance(mapping, dict):
                continue
            for option_id, target in mapping.items():
                self._validate_target(task_id, f"{field_name}.{option_id}", target, errors)

    def _validate_task_contract(self, task_id: str, node: TaskNode, errors: list[str]) -> None:
        """检查节点与 task I/O 契约是否一致。

        这里只对 ResearchOS 已定义 contract 的正式 task 生效；像 `done`/`failed` 这类
        控制节点或自定义调试节点，不做额外限制。
        """

        try:
            contract = get_task_io(task_id)
        except KeyError:
            return

        declared_inputs = dict(node.inputs or {})
        declared_outputs = dict(node.outputs or {})
        declared_outputs.update(dict(node.optional_outputs or {}))
        contract_inputs = dict(contract.get("inputs", {}))
        contract_outputs = dict(contract.get("outputs", {}))

        if declared_inputs != contract_inputs:
            errors.append(
                f"{task_id}: node.inputs does not match task_io_contract "
                f"(declared={declared_inputs}, contract={contract_inputs})"
            )
        if declared_outputs != contract_outputs:
            errors.append(
                f"{task_id}: node.outputs does not match task_io_contract "
                f"(declared={declared_outputs}, contract={contract_outputs})"
            )

    def _check_budget_drift(self, state: StateYaml, workspace_dir: Path) -> None:
        """检查预算漂移并发出警告（§7.1）。

        如果累计花费超过预算的70%，记录警告；
        如果超过90%，记录严重警告。
        """
        from ..runtime.logger import get_logger

        logger = get_logger("state_machine.budget")

        # 读取project.yaml获取预算上限
        project_file = workspace_dir / "project.yaml"
        if not project_file.exists():
            return

        try:
            project_data = yaml.safe_load(project_file.read_text(encoding="utf-8"))
            max_budget = project_data.get("constraints", {}).get("max_budget_usd")
            if max_budget is None or max_budget <= 0:
                return

            spent = state.budget_cumulative.cost_usd_total
            ratio = spent / max_budget

            if ratio >= 0.9:
                logger.warning(
                    f"预算严重超支警告: 已花费 ${spent:.2f} / ${max_budget:.2f} ({ratio*100:.1f}%)"
                )
                # 写入预算警告文件
                warning_file = workspace_dir / ".researchos" / "budget_warning.txt"
                warning_file.parent.mkdir(parents=True, exist_ok=True)
                warning_file.write_text(
                    f"预算严重超支警告 (90%+)\n"
                    f"已花费: ${spent:.2f}\n"
                    f"预算上限: ${max_budget:.2f}\n"
                    f"使用比例: {ratio*100:.1f}%\n"
                    f"当前任务: {state.current_task}\n"
                    f"时间: {_now_iso()}\n",
                    encoding="utf-8"
                )
            elif ratio >= 0.7:
                logger.warning(
                    f"预算警告: 已花费 ${spent:.2f} / ${max_budget:.2f} ({ratio*100:.1f}%)"
                )
        except Exception as e:
            logger.debug(f"预算检查失败: {e}")

    def _check_iteration_deadlock(self, state: StateYaml, node: TaskNode) -> None:
        """检查迭代死锁：相同参数组合尝试3次以上时快速失败。

        Phase 2.3: 防止 Agent 在相同参数上无限迭代。
        """
        from ..runtime.logger import get_logger

        logger = get_logger("state_machine.deadlock")

        task_id = state.current_task
        task_history = state.iteration_history.get(task_id, [])

        if not task_history:
            return

        # 计算当前参数哈希
        current_params = self._extract_task_params(node)
        current_hash = self._compute_param_hash(current_params)

        # 统计相同参数哈希出现次数
        same_param_count = sum(1 for entry in task_history if entry.get("param_hash") == current_hash)

        if same_param_count >= 3:
            error_msg = (
                f"检测到迭代死锁：任务 '{task_id}' 使用相同参数已尝试 {same_param_count} 次。"
                f"\n参数哈希: {current_hash}"
                f"\n参数内容: {current_params}"
                f"\n建议：检查任务配置或修改参数以避免无限循环。"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if same_param_count >= 2:
            logger.warning(
                f"迭代警告：任务 '{task_id}' 使用相同参数已尝试 {same_param_count} 次，"
                f"再次尝试将触发死锁保护。"
            )

    def _record_iteration_attempt(self, state: StateYaml, node: TaskNode) -> None:
        """记录本次迭代尝试到 iteration_history。

        Phase 2.3: 用于后续死锁检测。
        """
        task_id = state.current_task
        params = self._extract_task_params(node)
        param_hash = self._compute_param_hash(params)

        if task_id not in state.iteration_history:
            state.iteration_history[task_id] = []

        state.iteration_history[task_id].append(
            {
                "param_hash": param_hash,
                "timestamp": _now_iso(),
                "params": params,
            }
        )

    @staticmethod
    def _extract_task_params(node: TaskNode) -> dict[str, Any]:
        """提取任务的关键参数用于死锁检测。

        包括：inputs, outputs, llm配置, budget配置等影响任务行为的参数。
        """
        params = {}

        if node.inputs:
            params["inputs"] = dict(node.inputs)
        if node.outputs:
            params["outputs"] = dict(node.outputs)
        if node.llm:
            params["llm"] = dict(node.llm)
        if node.budget:
            params["budget"] = dict(node.budget)
        if node.mode:
            params["mode"] = node.mode
        if node.extra:
            params["extra"] = dict(node.extra)

        return params

    @staticmethod
    def _compute_param_hash(params: dict[str, Any]) -> str:
        """计算参数字典的哈希值。

        使用 frozenset 处理嵌套字典，确保参数顺序不影响哈希结果。
        """
        import json

        # 将参数转换为规范化的JSON字符串，然后计算哈希
        # 使用 sort_keys 确保字典顺序一致
        normalized = json.dumps(params, sort_keys=True, default=str)
        return str(hash(normalized))
