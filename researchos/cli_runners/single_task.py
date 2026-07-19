from __future__ import annotations

"""单 task 调试运行器。"""

import asyncio
from datetime import datetime, timezone
import uuid
from pathlib import Path

import yaml

from ..agents.registry import TASK_TO_AGENT_MAP, get_agent_by_id
from ..orchestration.task_aliases import resolve_public_stage_alias
from ..orchestration.state_machine import StateMachine
from ..orchestration.task_io_contract import get_task_io, resolve_inputs, resolve_outputs, task_import_paths
from ..runtime.agent import AgentResult, ExecutionContext, resolve_effective_config
from ..runtime.config import RuntimeSettings
from ..runtime.llm_client import LLMClient
from ..runtime.logger import get_logger
from ..runtime.orchestrator import AgentRunner
from ..runtime.progress import CliProgressEmitter
from ..runtime.system_config import system_config_path
from ..runtime.task_recovery import prepare_task_resume_artifacts
from ..runtime.bridge_catalog import migrate_legacy_bridge_catalogs
from ..runtime.literature_contract import build_literature_manifest, migrate_legacy_literature_paths
from ..runtime.workspace import (
    initialize_workspace,
    merge_workspace_artifact,
    migrate_workspace_note_directories,
)
from ..schemas.state import StateYaml, TaskHistoryEntry
from ..schemas.validator import register_builtin_task_checkers, validate_prerequisites, validate_task_artifacts
from ..skills.agent import SkillAgent
from ..skills.project_specialization.task_adapter import (
    SKILL_NAME as PROJECT_SKILL_SPECIALIZATION_SKILL_NAME,
    TASK_ID as PROJECT_SKILL_SPECIALIZATION_TASK_ID,
    build_project_skill_specialization_agent,
    repository_root as project_specialization_repo_root,
)
from ..skills.loader import resolve_skill
from ..tools.human_gate import CLIHumanInterface, HumanInputUnavailable, HumanInterface
from ..tools.latex_compile import latex_backend_preflight
from ..tools.registry import ToolRegistry


_LOG = get_logger("single_task")
_DEFAULT_STATE_MACHINE_PATH = system_config_path("state_machine.yaml")
_DEFAULT_GATES_PATH = system_config_path("gates.yaml")
_LATEX_PREFLIGHT_TASKS = {"T3.6-COMPILE", "T9"}


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
        skill_roots: list[Path] | None = None,
        from_workspace: Path | None = None,
        override_profile: str | None = None,
        human_interface: HumanInterface | None = None,
        runtime_settings: RuntimeSettings | None = None,
        allow_legacy: bool = False,
    ) -> None:
        self.workspace = workspace
        self.task_id = self._normalize_task_id(task_id, allow_legacy=allow_legacy)
        self.llm = llm_client
        self.tools = tool_registry
        self.skill_roots = skill_roots or []
        self.from_workspace = from_workspace
        self.override_profile = override_profile
        self.runtime_settings = runtime_settings or RuntimeSettings()
        self.human = human_interface or CLIHumanInterface()
        self.progress = CliProgressEmitter(
            quiet=self.runtime_settings.ui.quiet,
            verbose=self.runtime_settings.ui.verbose,
            verbosity=self.runtime_settings.ui.verbosity,
            no_color=self.runtime_settings.ui.no_color,
            json_events=self.runtime_settings.ui.json_events,
            workspace=self.workspace,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
        )
        register_builtin_task_checkers()

    @staticmethod
    def _normalize_task_id(task_id: str, *, allow_legacy: bool = False) -> str:
        normalized = str(task_id or "").strip()
        upper_task_id = normalized.upper()
        legacy_retired = {
            "T5": "T5-HANDOFF",
            "T6": "T5-HANDOFF",
        }
        legacy_explicit = {
            "LEGACY-T5-PILOT": "T5",
            "LEGACY-T6-NOVELTY": "T6",
        }
        if upper_task_id == "LEGACY-T7-FULL":
            raise ValueError(
                "LEGACY-T7-FULL has been removed. The main experiment path is now "
                "T5 external execution -> T8, with external_executor/executor_research_report.md "
                "as the writing handoff interface."
            )
        if upper_task_id == "T7":
            raise ValueError(
                "T7 has been removed from the main workflow. Use T5-HANDOFF/T5-REBOOST-GATE "
                "for external execution or T8-STYLE-GATE after external_executor/executor_research_report.md exists."
            )
        if upper_task_id in legacy_retired:
            replacement = legacy_retired[upper_task_id]
            legacy_name = {
                "T5": "LEGACY-T5-PILOT",
                "T6": "LEGACY-T6-NOVELTY",
            }[upper_task_id]
            raise ValueError(
                f"{normalized} legacy internal experiment node has been retired for ordinary run-task. "
                f"Use {replacement} for the external-executor chain, or use {legacy_name} --allow-legacy "
                "only for explicit old internal-experiment debugging."
            )
        if upper_task_id in legacy_explicit:
            if not allow_legacy:
                raise ValueError(f"{normalized} requires --allow-legacy.")
            return legacy_explicit[upper_task_id]
        canonical = resolve_public_stage_alias(normalized)
        if canonical == "T5-SPECIALIZE-EXECUTOR-SKILLS":
            return PROJECT_SKILL_SPECIALIZATION_TASK_ID
        return canonical

    async def run(self) -> int:
        """执行单次 task 调试。"""
        # runner 作为独立 Python API 使用时，也要自己保证 runtime 目录存在，
        # 不能把这个前提完全交给 CLI 调用方。
        self.progress.emit(f"\n[SingleTask] 初始化 workspace: {self.workspace}", important=True)
        initialize_workspace(
            self.workspace,
            create_project_file=False,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
        )

        if self.from_workspace:
            self.progress.emit(f"[SingleTask] 从 {self.from_workspace} 复制前置产物...", important=True)
            self._copy_prerequisites()

        self.progress.emit("[SingleTask] 校验前置条件...", important=True)
        ok, err = validate_prerequisites(self.workspace, self.task_id)
        if not ok:
            self.progress.error_context(
                stage="前置条件校验",
                message=f"{self.task_id}: {err}",
                log_path=str(self.workspace / self.runtime_settings.workspace.runtime_dir / "logs" / "researchos.log"),
            )
            self.progress.emit("Hint: use --from <other-workspace> to copy upstream artifacts.", important=True)
            return 3

        self.progress.emit(f"[SingleTask] 加载 Agent: {self.task_id}", important=True)
        state_machine = self._load_state_machine()
        task_node = state_machine.nodes.get(self.task_id) if state_machine is not None else None
        agent = self._build_agent(task_node)
        if agent is None:
            self.progress.error_context(stage="加载 Agent", message=f"Unknown or unimplemented task: {self.task_id}")
            return 4

        project_id = self._load_or_fake_project_id()
        state = self._load_or_init_state(project_id)

        # 单任务模式没有独立的 resume 子命令；当用户在同一 workspace
        # 中重跑失败/中断过的任务时，自动把“恢复运行”语义透传给 Agent。
        extra = self._build_task_extra(task_node)
        if task_node is not None and task_node.skill is not None:
            extra["skill_name"] = task_node.skill
            if task_node.skill == PROJECT_SKILL_SPECIALIZATION_SKILL_NAME:
                extra["skill_dir"] = str(project_specialization_repo_root() / "skills" / task_node.skill)
                extra["project_skill_specialization_repo_root"] = str(project_specialization_repo_root())
            else:
                extra["skill_dir"] = str(self.workspace / "skills" / task_node.skill)
        self._inject_resume_extra(extra, state)
        runtime_recovery = state.task_context.get("runtime_recovery")
        if isinstance(runtime_recovery, dict) and runtime_recovery.get("target_task") == self.task_id:
            # CompletePipelineRunner receives task_context through
            # StateMachine.build_execution_context.  Single-task debugging
            # builds its own context, so carry the same durable recovery
            # directive explicitly instead of losing an approved repair window.
            extra["runtime_recovery"] = dict(runtime_recovery)
            extra["resume_mode"] = True
            extra["is_resume"] = True
            extra["resume_reason"] = "runtime_recovery"
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
        # 所有 task 都统一生成恢复快照；T3/T5/T8 会在这个入口里叠加专项恢复信息。
        if self.task_id == "T3" and extra.get("resume_mode"):
            self.progress.emit(
                "[Reader Agent] T3 正在核对已保存的阅读笔记、PDF 可得性与剩余队列；"
                "这一步只做本地恢复检查，尚未提交模型请求。",
                important=True,
            )
        recovery_info = prepare_task_resume_artifacts(
            self.workspace,
            task_id=self.task_id,
            outputs_expected=outputs_expected,
            base_extra=extra,
        )
        extra.update(recovery_info)
        if recovery_info.get("resume_mode"):
            self.progress.emit(
                "[SingleTask] 恢复状态已准备："
                f"已有输出 {recovery_info.get('resume_existing_outputs', [])}，"
                f"待补 {recovery_info.get('resume_missing_outputs', [])}",
                important=True,
            )
        if self.task_id == "T3" and recovery_info.get("resume_queue_count") is not None:
            self.progress.emit(
                "[Reader Agent] T3 恢复队列已准备："
                f"{recovery_info.get('resume_queue_count', 0)} 篇待处理，"
                f"已完成 {recovery_info.get('existing_note_count', 0)} 篇，"
                f"来源={recovery_info.get('resume_queue_source', 'unknown')}",
                important=True,
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
        if task_node is not None:
            llm_ov, budget_ov, tool_ov = StateMachine._build_overrides(task_node)
            ctx.llm_override = llm_ov
            ctx.budget_override = budget_ov
            ctx.tool_policy_override = tool_ov
        if self.override_profile:
            ctx.llm_override.profile = self.override_profile

        self.progress.emit(f"[SingleTask] 准备执行上下文 (run_id: {ctx.run_id})", important=True)
        state_path = self.workspace / "state.yaml"

        # A recoverable T4 failure may already have persisted a Human Gate.
        # Re-present it before rebuilding execution context or clearing its
        # diagnostics, so `run-task T4` and the complete pipeline share the
        # same resume semantics.
        if (
            task_node is not None
            and state_machine is not None
            and state.pending_gate is not None
            and state.status in {"PAUSED", "WAITING_HUMAN"}
        ):
            return await self._present_pending_gate_task(state, state_path, state_machine)

        if (
            task_node is not None
            and state_machine is not None
            and state_machine.should_pause_for_immediate_gate(state, workspace_dir=self.workspace)
        ):
            return await self._run_immediate_gate_task(state, state_path, state_machine)

        if ctx.task_id in _LATEX_PREFLIGHT_TASKS:
            readiness = latex_backend_preflight(self.runtime_settings.latex)
            ctx.extra["latex_backend_preflight"] = readiness
            if not readiness.get("ok"):
                detail = str(readiness.get("message") or readiness.get("reason") or "no usable LaTeX backend")
                error = f"WAITING_ENVIRONMENT: {ctx.task_id} LaTeX preflight failed: {detail}"
                if state_machine is not None and ctx.task_id == "T3.6-COMPILE":
                    state = state_machine._pause_for_t36_compile_recovery_gate(state, error, self.workspace)
                elif state_machine is not None:
                    recovered = state_machine._pause_for_runtime_recovery_gate(
                        state,
                        error=error,
                        workspace_dir=self.workspace,
                        recovery={
                            "kind": "environment",
                            "error_summary": error,
                            "details": {"source": "latex_preflight", "task_id": ctx.task_id},
                        },
                    )
                    if recovered is None:
                        state.status = "PAUSED"
                        state.paused_at = _now_iso()
                        state.last_error = error
                    else:
                        state = recovered
                else:
                    state.status = "PAUSED"
                    state.paused_at = _now_iso()
                    state.last_error = error
                state.dump_yaml(state_path)
                if state.status == "WAITING_HUMAN" and state.pending_gate is not None and state_machine is not None:
                    return await self._present_pending_gate_task(state, state_path, state_machine)
                self.progress.emit(f"[Environment] {state.last_error}", important=True)
                return 130
            backend = str(readiness.get("selected_backend") or "unknown")
            self.progress.emit(
                f"[Environment] {ctx.task_id} LaTeX preflight 通过：backend={backend}；{readiness.get('reason') or 'ready'}",
                important=True,
            )

        state = self._record_started(state, ctx.run_id)
        state.dump_yaml(state_path)

        self.progress.emit("[SingleTask] 启动 Agent 执行...", important=True)
        effective = resolve_effective_config(agent.spec, ctx)
        step_limit = "unlimited" if effective.unlimited_budget else str(effective.max_steps)
        self.progress.emit(f"[SingleTask] Agent 将执行最多 {step_limit} 步", verbose_only=True)
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
            self.progress.emit("\n[SingleTask] 任务被中断", important=True)
            state = self._record_interrupted(state)
            state.dump_yaml(state_path)
            self.progress.emit(
                "任务已安全暂停；进度已保存，可检查 state.yaml 或 trace 后继续。",
                important=True,
            )
            return 130
        except Exception as exc:
            # Keep direct ``run-task`` consistent with the complete pipeline.
            # Startup work such as Jinja prompt rendering happens before the
            # AgentRunner's normal loop-level exception boundary, so an
            # undefined context variable formerly escaped as a Python
            # traceback.  Turn it into a structured recoverable interruption
            # and let the StateMachine open its Human Recovery Gate.
            error = (
                "Agent startup failed before the task loop: "
                f"{type(exc).__name__}: {str(exc) or repr(exc)}"
            )
            result = AgentResult(
                ok=False,
                message=error,
                outputs_produced={},
                steps_used=0,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                duration_seconds=0.0,
                stop_reason=AgentResult.STOP_INTERRUPTED,
                error=error,
                metadata={
                    "runtime_recovery": {
                        "schema_version": "1.0.0",
                        "kind": "runtime",
                        "task_id": ctx.task_id,
                        "run_id": ctx.run_id,
                        "error_summary": " ".join(error.split())[:1200],
                        "details": {
                            "source": "agent_runner_startup",
                            "exception_type": type(exc).__name__,
                        },
                    }
                },
            )
            self.progress.error_context(
                stage="Agent 启动期",
                message=error,
                log_path=str(
                    self.workspace
                    / self.runtime_settings.workspace.runtime_dir
                    / "logs"
                    / "researchos.log"
                ),
            )

        if result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }:
            self.progress.emit(f"\n[SingleTask] 任务暂停: {result.error or result.message}", important=True)
            # Keep isolated task execution aligned with the complete pipeline:
            # a recoverable task-specific interruption may create a durable
            # Human Gate instead of silently returning to the shell.  This is
            # particularly important for T3.6 audit exhaustion and T4 Gate1
            # projection recovery.
            if state_machine is not None:
                state = state_machine.advance(state, result, workspace_dir=self.workspace)
            else:
                state = self._record_finished(state, result)
            state.dump_yaml(state_path)
            if state.status == "WAITING_HUMAN" and state.pending_gate is not None and state_machine is not None:
                return await self._present_pending_gate_task(state, state_path, state_machine)
            self._print_result(result)
            if state.status == "FAILED":
                return 5
            self.progress.emit("任务已暂停；补充所需输入后使用 resume 继续此 workspace。", important=True)
            return 130

        self.progress.emit("\n[SingleTask] Agent 执行完成，开始校验输出产物...", important=True)
        try:
            io_spec = get_task_io(self.task_id)
        except KeyError:
            io_spec = {"inputs": {}, "outputs": {}, "required_inputs": []}
        skip_runtime_artifact_validation = (
            result.ok
            and self.task_id == "T4"
            and (result.metadata or {}).get("completion_mode") in {
                "t4_gate1_ready",
                "t4_pre_novelty_ready",
            }
        )
        ok, errors = (True, None) if skip_runtime_artifact_validation else validate_task_artifacts(
            self.workspace,
            self.task_id,
            declared_outputs=io_spec["outputs"],
        )
        if not ok:
            error_text = str(errors)
            if result.ok:
                self.progress.runtime_validation_failed(
                    task_id=self.task_id,
                    reason=(
                        "Runtime artifact validation failed; task did not produce valid artifacts: "
                        + error_text
                    ),
                    log_path=str(self.workspace / self.runtime_settings.workspace.runtime_dir / "logs" / "researchos.log"),
                    run_id=ctx.run_id,
                    outputs=ctx.outputs_expected,
                )
            self.progress.error_context(
                stage="输出产物校验",
                message=error_text,
                log_path=str(self.workspace / self.runtime_settings.workspace.runtime_dir / "logs" / "researchos.log"),
            )
            if result.ok:
                result.ok = False
                result.error = (
                    "Runtime artifact validation failed; task did not produce valid artifacts: "
                    + error_text
                )
                result.message = result.error
                if state_machine is not None and state_machine.is_hard_runtime_integrity_error(result.error):
                    result.stop_reason = result.STOP_ERROR
                else:
                    result.stop_reason = result.STOP_INTERRUPTED
                    result.metadata = dict(result.metadata or {})
                    result.metadata["runtime_recovery"] = {
                        "schema_version": "1.0.0",
                        "kind": "artifact_validation",
                        "task_id": ctx.task_id,
                        "run_id": ctx.run_id,
                        "error_summary": " ".join(result.error.split())[:1200],
                        "details": {"validator": "runtime_artifact"},
                    }
        else:
            self.progress.emit("[SingleTask] 输出校验通过", important=True)

        # A task-scoped debug run must still leave a real workspace on a
        # resumable pipeline boundary.  Previously only T4 used the state
        # machine here; successful T3.6-COMPILE (and other normal tasks) were
        # recorded as whole-project ``COMPLETED`` even though a next task
        # existed.  That made `resume` reject the workspace after a perfectly
        # valid isolated repair.  Advance exactly one state transition for all
        # configured tasks, then pause before executing the next task.  T4 is
        # the intentional exception because its next state is its own Gate1
        # decision and should be presented in the same command.
        if state_machine is not None:
            state = state_machine.advance(state, result, workspace_dir=self.workspace)
            if (
                result.ok
                and self.task_id != "T4"
                and state.status == "RUNNING"
            ):
                state.status = "PAUSED"
                state.paused_at = _now_iso()
                state.last_error = (
                    f"单任务 {self.task_id} 已完成；下一步 {state.current_task} 尚未执行。"
                    "使用 resume 继续主管道，当前 task 不会被重复运行。"
                )
        else:
            state = self._record_finished(state, result)
        state.dump_yaml(state_path)
        if (
            state.status == "WAITING_HUMAN"
            and state.pending_gate is not None
            and state_machine is not None
            and (self.task_id == "T4" or not result.ok)
        ):
            return await self._present_pending_gate_task(state, state_path, state_machine)
        if (
            self.task_id == "T4"
            and state_machine is not None
            and state.status == "RUNNING"
            and state.current_task == "T4-GATE1"
            and state_machine.should_pause_for_immediate_gate(state, workspace_dir=self.workspace)
        ):
            return await self._run_immediate_gate_task(state, state_path, state_machine)
        self._print_result(result)
        return 0 if result.ok else 5

    async def _run_immediate_gate_task(
        self,
        state: StateYaml,
        state_path: Path,
        state_machine: StateMachine,
    ) -> int:
        state = state_machine.pause_for_immediate_gate(state, workspace_dir=self.workspace)
        state = state_machine.refresh_pending_gate_presentation(
            state,
            workspace_dir=self.workspace,
        )
        state.dump_yaml(state_path)
        return await self._present_pending_gate_task(state, state_path, state_machine)

    async def _present_pending_gate_task(
        self,
        state: StateYaml,
        state_path: Path,
        state_machine: StateMachine,
    ) -> int:
        """Render and resolve an already-persisted immediate or recovery gate."""

        if state.pending_gate is None:
            raise ValueError("Cannot present a Human Gate that was not persisted")
        # A persisted T4-GATE1 can outlive the Portfolio Card that originally
        # produced it (for example after a rollback or interrupted Card
        # Compiler). Refresh through the state-machine boundary before any CLI
        # renderer sees it; the refresh may replace it with T4 recovery.
        state = state_machine.refresh_pending_gate_presentation(
            state,
            workspace_dir=self.workspace,
        )
        state.dump_yaml(state_path)
        if state.pending_gate is None:
            raise ValueError("Gate refresh did not preserve a pending Human Gate")
        # ``run-task T4`` legitimately presents the nested T4-GATE1 decision.
        # Render the I/O contract for the *active Gate state*, not the original
        # command alias, so event logs and artifact rows remain truthful.
        gate_task_id = state.current_task
        try:
            io_spec = get_task_io(gate_task_id)
        except KeyError:
            io_spec = {"inputs": {}, "outputs": {}, "required_inputs": []}
        inputs = {key: self.workspace / str(path) for key, path in dict(io_spec.get("inputs") or {}).items()}
        outputs = {key: self.workspace / str(path) for key, path in dict(io_spec.get("outputs") or {}).items()}
        gate_run_id = f"{gate_task_id}_gate_{uuid.uuid4().hex[:8]}"
        self.progress.stage_started(
            task_id=gate_task_id,
            run_id=gate_run_id,
            inputs=inputs,
            outputs=outputs,
            required_input_keys={str(key) for key in (io_spec.get("required_inputs") or [])},
            agent="human_gate",
            mode="immediate_human_decision",
            is_resume=state.status in {"PAUSED", "WAITING_HUMAN"},
        )
        self.progress.stage_human_action_required(
            task_id=gate_task_id,
            gate_id=state.pending_gate.gate_id,
            reason=str(state.pending_gate.presentation.get("_description") or "需要人工确认关键研究决策。"),
        )
        gate_id = state.pending_gate.gate_id
        try:
            gate_result = await self.human.present_gate(
                gate_id=state.pending_gate.gate_id,
                presentation=state.pending_gate.presentation,
                options=state.pending_gate.options,
            )
        except HumanInputUnavailable as exc:
            # An EOF/no-input boundary must agree with the CLI's visible
            # “paused” result.  Keep the pending Gate so a later resume can
            # continue the same conversation; in particular, an unconfirmed
            # T4 directive remains a draft and is never applied implicitly.
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = f"人工决策尚未提交，当前 Gate 已保存；resume 将回到这里。{exc}"
            state.task_context["last_unavailable_human_input"] = {
                "gate_id": gate_id,
                "reason": str(exc),
                "recorded_at": _now_iso(),
            }
            state.dump_yaml(state_path)
            self.progress.stage_completed(
                task_id=gate_task_id,
                run_id=gate_run_id,
                outputs=outputs,
                ok=False,
                summary="Human Gate 等待输入。",
                error=str(exc),
            )
            self.progress.emit(f"Task paused: {state.last_error}", important=True)
            return 130
        state = state_machine.resolve_pending_gate(state, gate_result, workspace_dir=self.workspace)
        state.dump_yaml(state_path)
        self.progress.stage_gate_resolved(
            task_id=gate_task_id,
            gate_id=gate_id,
            decision=str(gate_result.get("option_id") or "human_selection"),
        )
        self.progress.stage_completed(
            task_id=gate_task_id,
            run_id=gate_run_id,
            outputs=outputs,
            ok=True,
            summary=f"Human Gate 已记录选择：{gate_result.get('option_id') or 'human_selection'}。",
        )
        self.progress.emit(f"[Gate] Gate 已处理，下一状态: {state.current_task}", important=True)
        # Some Gate1 operations intentionally open a second confirmation
        # Gate.  Keep presenting the durable pending state in this invocation
        # instead of making the user discover that another ``run-task`` call is
        # required.  The recursion is bounded by human choices: a pause/exit
        # returns a non-running state, while a confirmed T4 operation moves
        # back to the controller below.
        if state.status == "WAITING_HUMAN" and state.pending_gate is not None:
            return await self._present_pending_gate_task(state, state_path, state_machine)
        # T4's pre-run confirmation is not the task itself.  Treating it as a
        # completed single-task invocation made `run-task T4` return to the
        # shell immediately after `start_standard`, before the controller had
        # made a single model call. Continue in the same command when the
        # confirmed state is native T4; later Gate1 still remains a genuine
        # human decision point.
        if (
            gate_id in {
                "t4_prerun_gate",
                "t4_recovery_gate",
                "t36_assemble_recovery_gate",
                "t36_compile_recovery_gate",
                "runtime_recovery_gate",
            }
            and state.current_task == self.task_id
            and state.status == "RUNNING"
        ):
            if self.task_id == "T4":
                self.progress.emit("[T4] 已确认运行方式，开始形成 Candidate Population。", important=True)
            elif gate_id == "runtime_recovery_gate":
                self.progress.emit("[Runtime] 已确认继续定向恢复；将从已保存产物开始执行。", important=True)
            else:
                self.progress.emit("[T3.6] 已确认继续定向修复综述审计问题。", important=True)
            return await self.run()
        if (
            self.task_id == "T4"
            and state.current_task == "T4-GATE1"
            and state.status == "RUNNING"
            and state_machine.should_pause_for_immediate_gate(state, workspace_dir=self.workspace)
        ):
            return await self._run_immediate_gate_task(state, state_path, state_machine)
        # A gate-only single-task invocation has completed its own declared
        # work once it records a choice.  When that choice advances to another
        # node, persist a clean pipeline boundary instead of leaving a
        # ``RUNNING`` state behind after this process returns to the shell.
        # CompletePipelineRunner intentionally keeps RUNNING and continues its
        # loop, so this belongs only in the isolated runner.  T4's nested
        # Gate1 handling above remains in the same invocation by design.
        if state.status == "RUNNING" and state.current_task != self.task_id:
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = (
                f"单任务 Gate {self.task_id} 已完成；下一步 {state.current_task} 尚未执行。"
                "使用 resume 继续主管道，当前 Gate 不会被重复执行。"
            )
            state.dump_yaml(state_path)
        # A researcher can deliberately inspect a recovery diagnosis and
        # pause, or request an exit, without that resolving the interrupted
        # task.  Returning success here made shell callers believe that the
        # repair had completed even though the durable state remained paused.
        # Confirmed recovery paths return through ``self.run()`` above, so a
        # non-running state at this boundary is a real, resumable pause.
        return 0 if state.status == "RUNNING" else 130

    def _copy_prerequisites(self) -> None:
        """Merge the stage import closure while preserving local work.

        Standard workspace initialization pre-creates note directories.  A
        directory-exists shortcut would therefore skip the source paper cards
        and make a resumed T3.6/T4/T5/T8 run look ready with an empty corpus.
        """

        for rel_path in task_import_paths(self.task_id):
            src = self.from_workspace / rel_path
            dst = self.workspace / rel_path
            if not src.exists():
                continue
            result = merge_workspace_artifact(src, dst, preserve_existing_files=True)
            copied_files = int(result.get("copied_files") or 0)
            if copied_files:
                self.progress.emit(f"copied: {rel_path} ({copied_files} new files)", verbose_only=True)
        migrate_workspace_note_directories(self.workspace, runtime_dir_name=self.runtime_settings.workspace.runtime_dir)
        migrate_legacy_bridge_catalogs(self.workspace)
        migrate_legacy_literature_paths(self.workspace)
        build_literature_manifest(self.workspace, write=True)

    def _load_state_machine(self):
        if not _DEFAULT_STATE_MACHINE_PATH.exists():
            return None
        try:
            return StateMachine(_DEFAULT_STATE_MACHINE_PATH, _DEFAULT_GATES_PATH)
        except Exception:
            return None

    def _build_agent(self, task_node):
        if task_node is not None and task_node.agent is not None:
            return get_agent_by_id(task_node.agent, mode=task_node.mode)
        if task_node is not None and task_node.skill is not None:
            skill = resolve_skill(task_node.skill, self.skill_roots)
            if task_node.skill == PROJECT_SKILL_SPECIALIZATION_SKILL_NAME:
                return build_project_skill_specialization_agent(
                    skill=skill,
                    available_tools=set(self.tools.available_names()),
                    llm_profile=None,
                )
            return SkillAgent(
                skill=skill,
                available_tools=set(self.tools.available_names()),
            )

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
        preserve_pending_gate = (
            state.pending_gate
            if state.current_task == self.task_id and state.status in {"PAUSED", "WAITING_HUMAN"}
            else None
        )
        preserve_error = state.last_error if preserve_pending_gate is not None else None
        state.current_task = self.task_id
        state.pending_gate = preserve_pending_gate
        state.last_error = preserve_error
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
            if state.history[-1].status not in {"DONE", "FAILED", "INTERRUPTED"}:
                state.history[-1].status = "INTERRUPTED"
            state.history[-1].finished_at = _now_iso()
            state.history[-1].stop_reason = state.history[-1].stop_reason or "interrupted"
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
        history.completion_mode = (result.metadata or {}).get("completion_mode")
        history.error = result.error
        if result.ok:
            history.status = "DONE"
            runtime_recovery = state.task_context.get("runtime_recovery")
            if isinstance(runtime_recovery, dict) and runtime_recovery.get("target_task") == self.task_id:
                state.task_context.pop("runtime_recovery", None)
        elif result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }:
            history.status = "INTERRUPTED"
        else:
            history.status = "FAILED"

        state.budget_cumulative.tokens_total += history.tokens
        state.budget_cumulative.cost_usd_total += result.cost_usd
        state.status = (
            "COMPLETED"
            if result.ok
            else "PAUSED"
            if history.status == "INTERRUPTED"
            else "FAILED"
        )
        state.last_error = result.error
        state.paused_at = _now_iso() if state.status == "PAUSED" else None
        return state

    def _print_result(self, result) -> None:
        specialization = (result.metadata or {}).get("project_skill_specialization")
        if isinstance(specialization, dict):
            skills = int(specialization.get("skills") or 0)
            lines = [
                f"Task: {specialization.get('task') or PROJECT_SKILL_SPECIALIZATION_TASK_ID}",
                f"Skill: {specialization.get('skill') or PROJECT_SKILL_SPECIALIZATION_SKILL_NAME}",
                f"Status: {specialization.get('status') or ('ready' if result.ok else 'failed')}",
                f"Skills: {skills}/13",
                f"Context: {specialization.get('context') or 'external_executor/project_skill_context.yaml'}",
                f"Report: {specialization.get('report') or 'external_executor/report/skill_specialization_report.json'}",
                f"Execution: {specialization.get('execution') or 'external_executor/report/skill_specialization_execution.json'}",
                f"Required uncertain fields: {int(specialization.get('required_uncertain_count') or 0)}",
                f"Trace: {specialization.get('trace') or '-'}",
            ]
            if result.error:
                lines.append(f"Error: {result.error}")
            self.progress.emit("\n".join(lines), important=True)
            return
        lines = [
            "=" * 60,
            "[SingleTask] 运行指标（产物说明见上方阶段总结）",
            f"停止原因: {result.stop_reason}",
            f"步骤: {result.steps_used}",
            f"Token: 输入 {result.tokens_in} / 输出 {result.tokens_out}",
            f"估算成本: ${result.cost_usd:.4f}",
            f"耗时: {result.duration_seconds:.1f}s",
        ]
        if (result.metadata or {}).get("completion_mode"):
            lines.append(f"完成模式: {result.metadata['completion_mode']}")
        lines.append(f"声明产物数: {len(result.outputs_produced)}")
        if result.error:
            lines.append(f"错误: {result.error}")
        lines.append("=" * 60)
        self.progress.emit("\n".join(lines), important=True)
