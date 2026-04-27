from __future__ import annotations

"""完整 pipeline 运行器。"""

import asyncio
from pathlib import Path

from ..agents.registry import get_agent_by_id
from ..orchestration.state_machine import StateMachine
from ..runtime.config import RuntimeSettings
from ..runtime.llm_client import LLMClient
from ..runtime.logger import get_logger
from ..runtime.orchestrator import AgentRunner
from ..runtime.workspace import initialize_workspace
from ..schemas.state import StateYaml
from ..schemas.validator import register_builtin_task_checkers, validate_task_artifacts
from ..skills.agent import SkillAgent
from ..skills.loader import resolve_skill
from ..tools.human_gate import CLIHumanInterface, HumanInterface
from ..tools.registry import ToolRegistry


_LOG = get_logger("complete_pipeline")


class CompletePipelineRunner:
    """模式 A：在同一个 workspace 中推进完整状态机。"""

    def __init__(
        self,
        *,
        workspace: Path,
        state_machine: StateMachine,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        skill_roots: list[Path] | None = None,
        human_interface: HumanInterface | None = None,
        runtime_settings: RuntimeSettings | None = None,
    ) -> None:
        self.workspace = workspace
        self.state_machine = state_machine
        self.llm = llm_client
        self.tools = tool_registry
        self.runtime_settings = runtime_settings or RuntimeSettings()
        self.human = human_interface or CLIHumanInterface()
        self.skill_roots = skill_roots or []
        register_builtin_task_checkers()

    async def run(self, *, project_id: str, resume: bool = False) -> int:
        """主循环：持续推进直到 completed / failed / paused。"""
        # 与 SingleTaskRunner 相同，这里也把 runtime 目录初始化收敛到 runner 自己，
        # 这样 CLI 之外的测试、脚本或未来上层 API 也能直接复用。
        initialize_workspace(
            self.workspace,
            create_project_file=False,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
        )

        state_path = self.workspace / "state.yaml"
        if state_path.exists():
            state = StateYaml.load_yaml(state_path)
        else:
            state = self.state_machine.create_initial_state(project_id=project_id)

        if resume and state.status not in {"PAUSED", "WAITING_HUMAN"}:
            print("当前状态不是 PAUSED/WAITING_HUMAN，无法 resume。")
            return 1

        while True:
            state = await self._run_one_step(state, state_path)
            if state.status == "COMPLETED":
                _LOG.info("pipeline_completed", workspace=str(self.workspace))
                print("Project completed.")
                return 0
            if state.status == "FAILED":
                _LOG.warning("pipeline_failed", last_error=state.last_error)
                print(f"Project failed: {state.last_error}")
                return 1
            if state.status == "PAUSED":
                _LOG.info("pipeline_paused")
                print("Project paused.")
                return 130

    async def _run_one_step(self, state: StateYaml, state_path: Path) -> StateYaml:
        """推进一个状态机 step。"""
        if state.pending_gate is not None:
            gate_result = await self.human.present_gate(
                gate_id=state.pending_gate.gate_id,
                presentation=state.pending_gate.presentation,
                options=state.pending_gate.options,
            )
            state = self.state_machine.resolve_pending_gate(
                state,
                gate_result,
                workspace_dir=self.workspace,
            )
            state.dump_yaml(state_path)

        node = self.state_machine.nodes[state.current_task]
        if node.terminal:
            state.status = "COMPLETED" if state.status != "FAILED" else state.status
            state.dump_yaml(state_path)
            return state

        ctx = self.state_machine.build_execution_context(self.workspace, state)
        state = self.state_machine.start_task(state, ctx.run_id)
        state.dump_yaml(state_path)

        runner = self._build_runner(node, ctx)
        try:
            result = await runner.run(ctx)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # CLI 层会把 Ctrl-C / SIGTERM 转成 cancel；runner 这里只负责把状态落到
            # `PAUSED`，保证后续 `resume` 有据可依。
            state = self.state_machine.mark_interrupted(state)
            state.dump_yaml(state_path)
            return state

        ok, errors = validate_task_artifacts(
            self.workspace,
            ctx.task_id,
            declared_outputs=node.outputs or None,
        )
        if result.ok and not ok:
            result.ok = False
            result.stop_reason = result.STOP_ERROR
            result.error = "Runtime artifact validation failed: " + "; ".join(errors)
            result.message = result.error

        state = self.state_machine.advance(state, result, workspace_dir=self.workspace)
        state.dump_yaml(state_path)
        return state

    def _build_runner(self, node, ctx):
        """根据 node 类型构造 AgentRunner。"""
        if node.agent is not None:
            agent = get_agent_by_id(node.agent, mode=node.mode)
        elif node.skill is not None:
            skill = resolve_skill(node.skill, self.skill_roots)
            ctx.extra.setdefault("skill_dir", str(skill.skill_dir))
            agent = SkillAgent(
                skill=skill,
                available_tools=set(self.tools.available_names()),
                llm_profile=ctx.llm_override.profile,
            )
        else:
            raise ValueError(f"Task {node.task_id} has neither agent nor skill configured")
        return AgentRunner(
            agent,
            self.tools,
            self.llm,
            self.human,
            runtime_settings=self.runtime_settings,
        )
