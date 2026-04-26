from __future__ import annotations

"""单 task 调试运行器。"""

import asyncio
from datetime import datetime, timezone
import shutil
import uuid
from pathlib import Path

import yaml

from ..agents.registry import TASK_TO_AGENT_MAP, get_agent_by_id
from ..orchestration.state_machine import StateMachine
from ..orchestration.task_io_contract import get_task_io, resolve_inputs, resolve_outputs
from ..runtime.agent import ExecutionContext, LLMConfigOverride
from ..runtime.config import RuntimeSettings
from ..runtime.llm_client import LLMClient
from ..runtime.logger import get_logger
from ..runtime.orchestrator import AgentRunner
from ..runtime.task_recovery import prepare_task_resume_artifacts
from ..runtime.workspace import initialize_workspace
from ..schemas.state import StateYaml, TaskHistoryEntry
from ..schemas.validator import register_builtin_task_checkers, validate_prerequisites, validate_task_artifacts
from ..tools.human_gate import CLIHumanInterface, HumanInterface
from ..tools.registry import ToolRegistry


_LOG = get_logger("single_task")
_DEFAULT_STATE_MACHINE_PATH = Path(__file__).resolve().parents[2] / "config" / "state_machine.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SingleTaskRunner:
    """模式 B：只运行一个 task，不推进到下一个 task。"""

    def __init__(
        self,
        *,
        workspace: Path,
        task_id: str,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        from_workspace: Path | None = None,
        override_profile: str | None = None,
        human_interface: HumanInterface | None = None,
        runtime_settings: RuntimeSettings | None = None,
    ) -> None:
        self.workspace = workspace
        self.task_id = task_id
        self.llm = llm_client
        self.tools = tool_registry
        self.from_workspace = from_workspace
        self.override_profile = override_profile
        self.runtime_settings = runtime_settings or RuntimeSettings()
        self.human = human_interface or CLIHumanInterface()
        register_builtin_task_checkers()

    async def run(self) -> int:
        """执行单次 task 调试。"""
        # runner 作为独立 Python API 使用时，也要自己保证 runtime 目录存在，
        # 不能把这个前提完全交给 CLI 调用方。
        print(f"\n[进度] 初始化 workspace: {self.workspace}")
        initialize_workspace(
            self.workspace,
            create_project_file=False,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
        )

        if self.from_workspace:
            print(f"[进度] 从 {self.from_workspace} 复制前置产物...", flush=True)
            self._copy_prerequisites()

        print(f"[进度] 校验前置条件...", flush=True)
        ok, err = validate_prerequisites(self.workspace, self.task_id)
        if not ok:
            print(f"Prerequisites not met for {self.task_id}: {err}")
            print("Hint: use --from <other-workspace> to copy upstream artifacts.")
            return 3

        print(f"[进度] 加载 Agent: {self.task_id}", flush=True)
        task_node = self._load_task_node()
        agent = self._build_agent(task_node)
        if agent is None:
            print(f"Unknown or unimplemented task: {self.task_id}")
            return 4

        project_id = self._load_or_fake_project_id()
        state = self._load_or_init_state(project_id)

        # 单任务模式没有独立的 resume 子命令；当用户在同一 workspace
        # 中重跑失败/中断过的任务时，自动把“恢复运行”语义透传给 Agent。
        extra = self._build_task_extra(task_node)
        self._inject_resume_extra(extra, state)
        if self.task_id == "T1":
            project_path = self.workspace / "project.yaml"
            if project_path.exists():
                try:
                    project_data = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
                    # 支持 topic 或 research_direction 字段
                    topic = project_data.get("topic") or project_data.get("research_direction", "")
                    if topic:
                        extra["user_topic"] = topic
                except Exception:
                    pass

        outputs_expected = resolve_outputs(self.workspace, self.task_id)
        # 所有 task 都统一生成恢复快照；T3/T5/T7 会在这个入口里叠加专项恢复信息。
        recovery_info = prepare_task_resume_artifacts(
            self.workspace,
            task_id=self.task_id,
            outputs_expected=outputs_expected,
            base_extra=extra,
        )
        extra.update(recovery_info)
        if recovery_info.get("resume_mode"):
            print(
                "[进度] 恢复状态已准备："
                f"已有输出 {recovery_info.get('resume_existing_outputs', [])}，"
                f"待补 {recovery_info.get('resume_missing_outputs', [])}",
                flush=True,
            )
        if self.task_id == "T3" and recovery_info.get("resume_queue_count") is not None:
            print(
                "[进度] T3 恢复队列已准备："
                f"{recovery_info.get('resume_queue_count', 0)} 篇待处理，"
                f"已完成 {recovery_info.get('existing_note_count', 0)} 篇，"
                f"来源={recovery_info.get('resume_queue_source', 'unknown')}",
                flush=True,
            )

        ctx = ExecutionContext(
            workspace_dir=self.workspace.resolve(),
            project_id=project_id,
            task_id=self.task_id,
            run_id=f"{self.task_id}_single_{uuid.uuid4().hex[:8]}",
            inputs=resolve_inputs(self.workspace, self.task_id),
            outputs_expected=outputs_expected,
            mode=task_node.mode if task_node is not None else None,
            extra=extra,
        )
        if self.override_profile:
            ctx.llm_override = LLMConfigOverride(profile=self.override_profile)

        print(f"[进度] 准备执行上下文 (run_id: {ctx.run_id})", flush=True)
        state_path = self.workspace / "state.yaml"
        state = self._record_started(state, ctx.run_id)
        state.dump_yaml(state_path)

        print(f"[进度] 启动 Agent 执行...", flush=True)
        print(f"[进度] Agent 将执行最多 {agent.spec.max_steps} 步", flush=True)
        runner = AgentRunner(
            agent,
            self.tools,
            self.llm,
            self.human,
            runtime_settings=self.runtime_settings,
        )
        try:
            result = await runner.run(ctx)
        except (asyncio.CancelledError, KeyboardInterrupt):
            print("\n[进度] 任务被中断")
            state = self._record_interrupted(state)
            state.dump_yaml(state_path)
            print("Task interrupted. You can inspect state.yaml and trace files in this workspace.")
            return 130

        print(f"\n[进度] Agent 执行完成，开始校验输出产物...")
        io_spec = get_task_io(self.task_id)
        ok, errors = validate_task_artifacts(
            self.workspace,
            self.task_id,
            declared_outputs=io_spec["outputs"],
        )
        if result.ok and not ok:
            print(f"[进度] 输出校验失败: {'; '.join(errors)}", flush=True)
            result.ok = False
            result.stop_reason = result.STOP_ERROR
            result.error = "Runtime artifact validation failed: " + "; ".join(errors)
            result.message = result.error
        else:
            print(f"[进度] 输出校验通过", flush=True)

        state = self._record_finished(state, result)
        state.dump_yaml(state_path)
        self._print_result(result)
        return 0 if result.ok else 5

    def _copy_prerequisites(self) -> None:
        """从另一个 workspace 复制输入 artifact。"""
        io_spec = get_task_io(self.task_id)
        for rel_path in io_spec["inputs"].values():
            src = self.from_workspace / rel_path
            dst = self.workspace / rel_path
            if not src.exists() or dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            print(f"copied: {rel_path}")

    def _load_task_node(self):
        if not _DEFAULT_STATE_MACHINE_PATH.exists():
            return None
        try:
            state_machine = StateMachine(_DEFAULT_STATE_MACHINE_PATH)
        except Exception:
            return None
        return state_machine.nodes.get(self.task_id)

    def _build_agent(self, task_node):
        if task_node is not None and task_node.agent is not None:
            return get_agent_by_id(task_node.agent, mode=task_node.mode)

        agent_cls = TASK_TO_AGENT_MAP.get(self.task_id)
        if agent_cls is None:
            return None
        if task_node is not None and task_node.mode is not None:
            try:
                return agent_cls(mode=task_node.mode)
            except TypeError:
                pass
        return agent_cls()

    @staticmethod
    def _build_task_extra(task_node) -> dict[str, object]:
        extra = dict(task_node.extra or {}) if task_node is not None else {}
        if task_node is not None and task_node.mode is not None:
            extra.setdefault("phase", task_node.mode)
        if task_node is not None and task_node.round is not None:
            extra.setdefault("round", task_node.round)
        return extra

    def _inject_resume_extra(self, extra: dict[str, object], state: StateYaml) -> None:
        """在单任务模式下补充 resume 语义。"""

        if extra.get("is_resume"):
            return

        resumed_from = None
        resume_reason = None

        for history in reversed(state.history):
            if history.task != self.task_id:
                continue
            if history.status == "INTERRUPTED":
                resumed_from = history.run_id
                resume_reason = "interrupted"
                break
            if history.status == "FAILED":
                resumed_from = history.run_id
                resume_reason = "retry_after_failure"
                break
            break

        if resumed_from is None:
            return

        extra["is_resume"] = True
        extra["resume_mode"] = True
        extra["resumed_from"] = resumed_from
        extra["resumed_from_run_id"] = resumed_from
        extra["resume_reason"] = resume_reason

    def _load_or_fake_project_id(self) -> str:
        """尽量从 project.yaml 读 project_id，没有则生成一个临时值。"""
        project_path = self.workspace / "project.yaml"
        if project_path.exists():
            try:
                data = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
                return data.get("project_id", f"single-task-{self.task_id}")
            except Exception:
                pass
        return f"single-task-{self.task_id}"

    def _load_or_init_state(self, project_id: str) -> StateYaml:
        """读取已有 state.yaml，或为单 task 调试初始化一个最小 state。"""

        state_path = self.workspace / "state.yaml"
        if state_path.exists():
            state = StateYaml.load_yaml(state_path)
        else:
            state = StateYaml(project_id=project_id, current_task=self.task_id)
        state.current_task = self.task_id
        state.pending_gate = None
        state.last_error = None
        return state

    def _record_started(self, state: StateYaml, run_id: str) -> StateYaml:
        """记录一次 single-task run 的开始。"""

        state.status = "RUNNING"
        state.history.append(
            TaskHistoryEntry(
                task=self.task_id,
                run_id=run_id,
                status="RUNNING",
                started_at=_now_iso(),
            )
        )
        return state

    def _record_interrupted(self, state: StateYaml) -> StateYaml:
        """把当前 single-task run 标记为中断。"""

        if state.history:
            state.history[-1].status = "INTERRUPTED"
            state.history[-1].finished_at = _now_iso()
            state.history[-1].stop_reason = "interrupted"
        state.status = "PAUSED"
        state.paused_at = _now_iso()
        return state

    def _record_finished(self, state: StateYaml, result) -> StateYaml:
        """写回 single-task run 的审计结果，但不推进到其他 task。"""

        if not state.history:
            return state

        history = state.history[-1]
        history.finished_at = _now_iso()
        history.stop_reason = result.stop_reason
        history.tokens_in = result.tokens_in
        history.tokens_out = result.tokens_out
        history.tokens = result.tokens_in + result.tokens_out
        history.cost_usd = result.cost_usd
        history.llm_profile = result.llm_profile
        history.llm_tier = result.llm_tier
        history.llm_model = result.llm_model_used
        history.llm_endpoint = result.llm_endpoint_used
        history.error = result.error
        history.status = "DONE" if result.ok else "FAILED"

        state.budget_cumulative.tokens_total += history.tokens
        state.budget_cumulative.cost_usd_total += result.cost_usd
        state.status = "COMPLETED" if result.ok else "FAILED"
        state.last_error = result.error
        state.paused_at = None
        return state

    @staticmethod
    def _print_result(result) -> None:
        print("=" * 60)
        print(f"stop_reason: {result.stop_reason}")
        print(f"steps: {result.steps_used}")
        print(f"tokens: {result.tokens_in} in / {result.tokens_out} out")
        print(f"cost: ${result.cost_usd:.4f}")
        print(f"duration: {result.duration_seconds:.1f}s")
        print(f"outputs: {list(result.outputs_produced.keys())}")
        if result.error:
            print(f"error: {result.error}")
        print("=" * 60)
