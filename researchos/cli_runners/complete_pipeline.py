from __future__ import annotations

"""完整 pipeline 运行器。"""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from ..agents.registry import get_agent_by_id
from ..orchestration.state_machine import StateMachine, validate_t4_gate1_selection_file
from ..orchestration.task_io_contract import get_task_io
from ..runtime.agent import AgentResult
from ..runtime.config import RuntimeSettings
from ..runtime.llm_client import LLMClient
from ..runtime.logger import get_logger
from ..runtime.orchestrator import AgentRunner
from ..runtime.progress import CliProgressEmitter
from ..runtime.run_logger import RunLogger
from ..runtime.workspace import initialize_workspace
from ..schemas.state import StateYaml
from ..schemas.validator import register_builtin_task_checkers, validate_prerequisites, validate_task_artifacts
from ..skills.agent import SkillAgent
from ..skills.loader import resolve_skill
from ..skills.project_specialization.task_adapter import (
    SKILL_NAME as PROJECT_SKILL_SPECIALIZATION_SKILL_NAME,
    build_project_skill_specialization_agent,
)
from ..tools.human_gate import CLIHumanInterface, HumanInputUnavailable, HumanInterface
from ..tools.latex_compile import latex_backend_preflight
from ..tools.registry import ToolRegistry


_LOG = get_logger("complete_pipeline")
_LATEX_PREFLIGHT_TASKS = {"T3.6-COMPILE", "T9"}


def _consumes_shared_literature_contract(task_id: str) -> bool:
    """Return whether a task consumes the workspace's Literature Contract.

    The contract is deliberately inferred from the task I/O declaration rather
    than maintained as a runner-local task list.  A list previously covered
    only T3.5 and two T3.6 nodes, so a legacy workspace could resume directly
    into T4 with an underfilled shallow-reading corpus even though T4, T4.5,
    T5, and T8 all declare ``literature_manifest`` as an input.
    """

    try:
        inputs = get_task_io(task_id).get("inputs") or {}
    except KeyError:
        return False
    return "literature_manifest" in inputs


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self.run_logger = RunLogger(
            self.workspace,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
            quiet=self.runtime_settings.ui.quiet,
            verbose=self.runtime_settings.ui.verbose,
        )
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

        if resume and state.status == "RUNNING":
            state = self.state_machine.mark_interrupted(
                state,
                reason="resume_detected_stale_running_state",
            )
            state.last_error = "检测到上次运行停留在 RUNNING，已按陈旧运行自动转为 PAUSED。"
            state.dump_yaml(state_path)
            self.progress.emit("检测到上次运行未正常收尾，已转为可 resume 状态。", important=True)
            self.run_logger.event(
                "RESUME",
                project_id=state.project_id,
                status="stale_running_marked_paused",
                task=state.current_task,
            )

        if resume and state.status == "FAILED":
            state, should_continue = await self._prepare_failed_resume(state, state_path)
            if not should_continue:
                return 130

        if resume and state.status not in {"PAUSED", "WAITING_HUMAN"}:
            self.progress.error_context(
                stage="resume",
                message=f"当前状态是 {state.status}，不是 PAUSED/WAITING_HUMAN/FAILED",
                log_path=str(self.workspace / self.runtime_settings.workspace.runtime_dir / "logs" / "researchos.log"),
            )
            self.run_logger.event(
                "ERROR",
                kind="resume_rejected",
                task=state.current_task,
                status=state.status,
            )
            return 1
        if resume:
            self.run_logger.event("RESUME", project_id=state.project_id, task=state.current_task, status=state.status)
            self.progress.pipeline_start(
                project_id=state.project_id,
                task=state.current_task,
                resume=True,
                status=state.status,
            )
        else:
            self.run_logger.event("RUN_START", project_id=project_id, task=state.current_task, mode="pipeline")
            self.progress.pipeline_start(project_id=project_id, task=state.current_task, resume=False)

        while True:
            state = await self._run_one_step(state, state_path)
            if state.status == "COMPLETED":
                _LOG.info("pipeline_completed", workspace=str(self.workspace))
                self.run_logger.event("RUN_END", project_id=state.project_id, status="COMPLETED")
                self.progress.emit("[Pipeline] 项目完成", important=True)
                return 0
            if state.status == "FAILED":
                _LOG.warning("pipeline_failed", last_error=state.last_error)
                self.run_logger.event(
                    "ERROR",
                    kind="pipeline_failed",
                    task=state.current_task,
                    message=state.last_error,
                )
                self.run_logger.event("RUN_END", project_id=state.project_id, status="FAILED")
                self.progress.error_context(
                    stage="pipeline",
                    message=str(state.last_error or "unknown"),
                    log_path=str(self.workspace / self.runtime_settings.workspace.runtime_dir / "logs" / "researchos.log"),
                )
                return 1
            if state.status == "PAUSED":
                _LOG.info("pipeline_paused")
                self.run_logger.event("PAUSED", project_id=state.project_id, task=state.current_task, reason=state.last_error)
                self.progress.pipeline_paused(reason=state.last_error)
                return 130
            if state.status == "WAITING_HUMAN":
                _LOG.info("pipeline_waiting_human")
                self.run_logger.event(
                    "ASK_HUMAN",
                    project_id=state.project_id,
                    task=state.current_task,
                    gate_id=state.pending_gate.gate_id if state.pending_gate else None,
                )
                self.progress.pipeline_waiting_human(
                    task=state.current_task,
                    gate_id=state.pending_gate.gate_id if state.pending_gate else None,
                )
                return 130

    async def _prepare_failed_resume(self, state: StateYaml, state_path: Path) -> tuple[StateYaml, bool]:
        failed_history = self._last_failed_task_history(state)
        original_error = state.last_error or (failed_history.error if failed_history else None)
        if failed_history is not None:
            state.current_task = failed_history.task
        state.status = "PAUSED"
        state.paused_at = _now_iso()
        resume_context = {
            "is_resume": True,
            "resume_mode": True,
            "resume_reason": "retry_after_failure",
        }
        if failed_history is not None:
            resume_context["resumed_from"] = failed_history.run_id
            resume_context["resumed_from_run_id"] = failed_history.run_id
        state.task_context.update(resume_context)
        state.last_error = (
            "检测到上次运行失败，已转为可 resume 的 retry_after_failure 状态。"
            + (f" 原错误摘要: {self._compact_error(original_error)}" if original_error else "")
        )
        state.dump_yaml(state_path)
        self.progress.emit(
            "检测到上次运行失败，ResearchOS 已恢复到可 resume 状态。",
            important=True,
        )
        self.run_logger.event(
            "RESUME",
            project_id=state.project_id,
            status="failed_marked_retryable",
            task=state.current_task,
            resumed_from=failed_history.run_id if failed_history else None,
        )

        try:
            decision = await self.human.present_gate(
                gate_id="failed_resume_recovery_gate",
                presentation={
                    "_title": "恢复失败任务",
                    "_description": (
                        "ResearchOS 可以从失败点继续。请确认是否保留当前 task 已写出的声明产物，"
                        "或先把这些产物归档后重跑。"
                    ),
                    "failed_task": state.current_task,
                    "failed_run_id": failed_history.run_id if failed_history else "(unknown)",
                    "error_summary": self._compact_error(original_error),
                    "declared_outputs": self._declared_output_status(state.current_task),
                },
                options=[
                    {
                        "id": "continue_keep",
                        "label": "保留文件继续",
                        "description": "默认选项；保留已有 artifacts，resume 时由当前 task 的恢复逻辑和 validator 判断能否复用。",
                        "is_default": True,
                    },
                    {
                        "id": "archive_outputs",
                        "label": "归档产物重跑",
                        "description": "把当前 task 声明的已存在文件输出移到 _runtime/failed_artifact_archive/，再从当前 task 重跑。",
                    },
                    {
                        "id": "pause_review",
                        "label": "暂停人工检查",
                        "description": "保持 PAUSED，不继续运行；你可以查看日志或手动处理 artifacts，之后再次 resume 会继续当前 task。",
                    },
                ],
            )
        except HumanInputUnavailable as exc:
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = f"恢复失败任务需要用户选择，但当前输入不可用：{exc}"
            state.dump_yaml(state_path)
            self.progress.pipeline_paused(reason=state.last_error)
            return state, False

        option_id = str(decision.get("option_id") or decision.get("key") or "")
        if option_id == "pause_review":
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = "用户选择暂停人工检查失败产物；手动处理后再次 resume 会继续当前 task。"
            state.dump_yaml(state_path)
            self.progress.pipeline_paused(reason=state.last_error)
            return state, False
        if option_id == "archive_outputs":
            manifest = self._archive_declared_outputs_for_failed_resume(state.current_task)
            state.task_context["failed_resume_archive_manifest"] = manifest
            state.last_error = (
                "用户选择归档当前 task 声明输出后重跑；"
                f"归档清单: {manifest}"
            )
            state.dump_yaml(state_path)
            self.progress.emit(
                f"已归档当前 task 的声明文件输出：{manifest}",
                important=True,
            )
        else:
            state.last_error = "用户选择保留现有 artifacts 并继续 resume。"
            state.dump_yaml(state_path)
        return state, True

    def _last_failed_task_history(self, state: StateYaml):
        failed_history = next(
            (
                item
                for item in reversed(state.history)
                if item.task == state.current_task and item.status == "FAILED"
            ),
            None,
        )
        current_node = self.state_machine.nodes.get(state.current_task)
        if failed_history is None and (
            state.current_task.lower().startswith("fail")
            or (current_node is not None and current_node.terminal)
        ):
            failed_history = next(
                (
                    item
                    for item in reversed(state.history)
                    if item.status == "FAILED"
                    and item.task in self.state_machine.nodes
                    and not self.state_machine.nodes[item.task].terminal
                ),
                None,
            )
        return failed_history

    @staticmethod
    def _compact_error(value: object, *, limit: int = 700) -> str:
        text = " ".join(str(value or "").split())
        if len(text) > limit:
            return text[: max(0, limit - 3)] + "..."
        return text

    def _declared_output_status(self, task_id: str) -> list[str]:
        node = self.state_machine.nodes.get(task_id)
        if node is None or not node.outputs:
            return ["该 task 未声明输出文件。"]
        rows: list[str] = []
        for label, rel in node.outputs.items():
            path = self.workspace / rel
            if path.exists():
                kind = "dir" if path.is_dir() else "file"
                rows.append(f"{label}: {rel} ({kind}, exists)")
            else:
                rows.append(f"{label}: {rel} (missing)")
        return rows[:20]

    def _archive_declared_outputs_for_failed_resume(self, task_id: str) -> str:
        node = self.state_machine.nodes.get(task_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_root = (
            self.workspace
            / self.runtime_settings.workspace.runtime_dir
            / "failed_artifact_archive"
            / f"{task_id}_{timestamp}"
        )
        archive_root.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, object] = {
            "task_id": task_id,
            "created_at": _now_iso(),
            "archived": [],
            "missing": [],
            "skipped_directories": [],
        }
        if node is not None and node.outputs:
            for label, rel in node.outputs.items():
                src = self.workspace / rel
                if not src.exists():
                    manifest["missing"].append({"label": label, "path": rel})  # type: ignore[union-attr]
                    continue
                if src.is_dir():
                    manifest["skipped_directories"].append({"label": label, "path": rel})  # type: ignore[union-attr]
                    continue
                dst = archive_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                manifest["archived"].append(  # type: ignore[union-attr]
                    {"label": label, "from": rel, "to": str(dst.relative_to(self.workspace))}
                )
        manifest_path = archive_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(manifest_path.relative_to(self.workspace))

    async def _run_one_step(self, state: StateYaml, state_path: Path) -> StateYaml:
        """推进一个状态机 step。"""
        while True:
            if state.pending_gate is not None:
                if (
                    state.current_task == "T4-GATE1"
                    and validate_t4_gate1_selection_file(self.workspace)[0]
                ):
                    # Final Cards are mandatory before *opening* Gate1 for a
                    # fresh human decision. A selection already persisted by
                    # Gate1 has its own Candidate-pool fingerprint contract,
                    # however. Do not strand that valid decision on resume if
                    # a later card cleanup/archive removed a presentation
                    # artifact: resume advances to the selected post-Gate
                    # path and never renders a partial card.
                    next_task = "T4.5"
                    try:
                        payload = json.loads((self.workspace / "ideation" / "_gate1_user_selection.json").read_text(encoding="utf-8"))
                        if isinstance(payload, dict) and str(payload.get("next_task") or "").strip():
                            next_task = str(payload.get("next_task")).strip()
                    except (OSError, json.JSONDecodeError):
                        pass
                    state.pending_gate = None
                    state.current_task = next_task
                    state.status = "RUNNING"
                    state.last_error = None
                    state.dump_yaml(state_path)
                    self.run_logger.event(
                        "HUMAN_GATE",
                        task="T4-GATE1",
                        gate_id="t4_gate1_selection_gate",
                        mode="selection_file_fast_forward",
                    )
                    continue
                state = await self._present_pending_gate(state, state_path)
                if state.status != "RUNNING":
                    return state
                continue

            node = self.state_machine.nodes[state.current_task]
            if node.terminal:
                state.status = "COMPLETED" if state.status != "FAILED" else state.status
                state.dump_yaml(state_path)
                return state

            # ``resume`` advances through state-machine nodes without
            # returning to the CLI's one-time prerequisite check.  Every
            # consumer that declares the shared manifest must therefore
            # validate the same paper corpus before an immediate Gate can ask
            # the researcher to make a downstream decision, or before an
            # Agent can render an optimistic input panel or submit a model
            # request.
            if _consumes_shared_literature_contract(state.current_task):
                prerequisites_ok, prerequisites_error = validate_prerequisites(self.workspace, state.current_task)
            else:
                prerequisites_ok, prerequisites_error = True, None
            if not prerequisites_ok:
                error = f"任务前置材料未就绪，未提交模型请求: {prerequisites_error}"
                is_literature_coverage_gap = "Literature reading coverage is not ready" in str(prerequisites_error)
                recovered = self.state_machine._pause_for_runtime_recovery_gate(
                    state,
                    error=error,
                    workspace_dir=self.workspace,
                    recovery={
                        "kind": "literature_coverage" if is_literature_coverage_gap else "prerequisite_validation",
                        "error_summary": error,
                        "details": {
                            "task_id": state.current_task,
                            "source": "complete_pipeline_preflight",
                            # A shallow-reading shortfall changes the evidence
                            # set.  It must be repaired from T3 and then flow
                            # through a fresh T3.5/T4 chain, never patched
                            # inside a downstream ideation or writing task.
                            "return_to_task": "T3" if is_literature_coverage_gap else None,
                        },
                    },
                )
                if recovered is None:
                    state.status = "PAUSED"
                    state.paused_at = _now_iso()
                    state.last_error = error
                else:
                    state = recovered
                state.dump_yaml(state_path)
                self.run_logger.event("PAUSED", task=state.current_task, reason=state.last_error, validator="prerequisites")
                self.progress.error_context(
                    stage="任务前置材料校验",
                    message=error,
                    log_path=str(self.workspace / self.runtime_settings.workspace.runtime_dir / "logs" / "researchos.log"),
                )
                if state.status == "WAITING_HUMAN" and state.pending_gate is not None:
                    return await self._present_pending_gate(state, state_path)
                self.progress.pipeline_paused(reason=state.last_error)
                return state

            if self.state_machine.should_pause_for_immediate_gate(state, workspace_dir=self.workspace):
                state = self.state_machine.pause_for_immediate_gate(
                    state,
                    workspace_dir=self.workspace,
                )
                state.dump_yaml(state_path)
                self.run_logger.event(
                    "HUMAN_GATE",
                    task=state.current_task,
                    gate_id=state.pending_gate.gate_id if state.pending_gate else "",
                    mode="immediate_gate_present",
                )
                continue
            break

        try:
            ctx = self.state_machine.build_execution_context(self.workspace, state)
        except Exception as exc:
            error = f"构建执行上下文失败: {exc}"
            recovered = self.state_machine._pause_for_runtime_recovery_gate(
                state,
                error=error,
                workspace_dir=self.workspace,
                recovery={
                    "kind": "runtime",
                    "error_summary": error,
                    "details": {"source": "build_execution_context"},
                },
            )
            if recovered is None:
                state.status = "PAUSED"
                state.paused_at = _now_iso()
                state.last_error = error
            else:
                state = recovered
            state.dump_yaml(state_path)
            self.run_logger.event("ERROR", task=state.current_task, kind="build_context", message=state.last_error)
            if state.status == "WAITING_HUMAN" and state.pending_gate is not None:
                return await self._present_pending_gate(state, state_path)
            return state
        if ctx.task_id in _LATEX_PREFLIGHT_TASKS:
            readiness = latex_backend_preflight(self.runtime_settings.latex)
            ctx.extra["latex_backend_preflight"] = readiness
            if readiness.get("ok"):
                backend = str(readiness.get("selected_backend") or "unknown")
                detail = str(readiness.get("reason") or "ready")
                image = str(readiness.get("image") or "")
                suffix = f"; image={image}" if image else ""
                self.progress.emit(
                    f"[Environment] {ctx.task_id} LaTeX preflight 通过：backend={backend}；{detail}{suffix}",
                    important=True,
                )
            else:
                detail = str(readiness.get("message") or readiness.get("reason") or "no usable LaTeX backend")
                error = f"WAITING_ENVIRONMENT: {ctx.task_id} LaTeX preflight failed: {detail}"
                if ctx.task_id == "T3.6-COMPILE":
                    state = self.state_machine._pause_for_t36_compile_recovery_gate(state, error, self.workspace)
                else:
                    recovered = self.state_machine._pause_for_runtime_recovery_gate(
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
                state.dump_yaml(state_path)
                self.run_logger.event(
                    "PAUSED",
                    task=ctx.task_id,
                    reason=state.last_error,
                    latex_preflight=readiness,
                )
                if state.status == "WAITING_HUMAN" and state.pending_gate is not None:
                    return await self._present_pending_gate(state, state_path)
                self.progress.pipeline_paused(reason=state.last_error)
                return state
        state = self.state_machine.start_task(state, ctx.run_id, workspace_dir=self.workspace)
        self.run_logger.event("TASK_START", task=ctx.task_id, run_id=ctx.run_id, status=state.status)
        state.dump_yaml(state_path)

        runner = self._build_runner(node, ctx)
        try:
            result = await runner.run(ctx)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # CLI 层会把 Ctrl-C / SIGTERM 转成 cancel；runner 这里只负责把状态落到
            # `PAUSED`，保证后续 `resume` 有据可依。
            state = self.state_machine.mark_interrupted(state)
            state.dump_yaml(state_path)
            self.run_logger.event("PAUSED", task=ctx.task_id, reason="interrupted")
            return state
        except Exception as exc:
            # Prompt rendering, tool construction, and provider routing happen
            # before an AgentRunner can enter its normal model/tool loop.  A
            # failure there used to escape this coroutine as a traceback (for
            # example, a newly added StrictUndefined prompt variable) and left
            # a RUNNING state behind.  It is a recoverable runtime failure
            # unless the StateMachine identifies an explicit integrity issue.
            # Preserve the task boundary and feed the same durable Human
            # Recovery Gate used for exhausted validation or provider retries.
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
            self.run_logger.event(
                "ERROR",
                task=ctx.task_id,
                kind="agent_runner_startup",
                message=error,
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
            state = self.state_machine.advance(state, result, workspace_dir=self.workspace)
            state.dump_yaml(state_path)
            if state.status == "WAITING_HUMAN" and state.pending_gate is not None:
                return await self._present_pending_gate(state, state_path)
            return state

        skip_runtime_artifact_validation = (
            result.ok
            and ctx.task_id == "T4"
            and (result.metadata or {}).get("completion_mode") == "t4_gate1_ready"
        )
        ok, errors = (True, None) if skip_runtime_artifact_validation else validate_task_artifacts(
            self.workspace,
            ctx.task_id,
            declared_outputs=node.outputs or None,
        )
        if result.ok and not ok:
            result.ok = False
            result.error = (
                "Runtime artifact validation failed; retrying via state-machine failure route: "
                + str(errors)
            )
            result.message = result.error
            if self.state_machine.is_hard_runtime_integrity_error(result.error):
                result.stop_reason = result.STOP_ERROR
            else:
                # Runner-level validation happens after an Agent reports
                # success, so it previously skipped the Agent repair loop and
                # fell directly into a terminal failure route.  Preserve the
                # failed status while giving the StateMachine a durable human
                # recovery decision for repairable artifacts.
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
            self.run_logger.event(
                "VALIDATION_FAILED",
                task=ctx.task_id,
                reason=result.error,
                validator="runtime_artifact",
            )
            self.progress.runtime_validation_failed(
                task_id=ctx.task_id,
                reason=result.error,
                log_path=str(self.workspace / self.runtime_settings.workspace.runtime_dir / "logs" / "researchos.log"),
                run_id=ctx.run_id,
                outputs=ctx.outputs_expected,
            )

        before_task = state.current_task
        state = self.state_machine.advance(state, result, workspace_dir=self.workspace)
        self.run_logger.event(
            "TASK_END",
            task=ctx.task_id,
            ok=result.ok,
            stop_reason=result.stop_reason,
            error=result.error,
        )
        if before_task != state.current_task:
            self.run_logger.event(
                "STATE_TRANSITION",
                from_task=before_task,
                to_task=state.current_task,
                reason=result.stop_reason,
            )
            self.progress.state_transition(
                from_task=before_task,
                to_task=state.current_task,
                reason=result.stop_reason,
            )
        state.dump_yaml(state_path)
        if state.status == "WAITING_HUMAN" and state.pending_gate is not None:
            return await self._present_pending_gate(state, state_path)
        return state

    async def _present_pending_gate(self, state: StateYaml, state_path: Path) -> StateYaml:
        """展示并处理已经挂起的人类 gate；无输入时持久化为可恢复暂停。"""

        if state.pending_gate is None:
            return state
        state = self.state_machine.refresh_pending_gate_presentation(
            state,
            workspace_dir=self.workspace,
        )
        # A legacy T4 recovery gate can survive an upgrade that introduced the
        # shared shallow-reading contract.  Compatibility-record repair is
        # meaningless while the evidence set itself is incomplete: accepting
        # that retry would only advance to a second, avoidable failure. Replace
        # this *specific* stale recovery surface with the upstream reading
        # decision. The original exception stays in state history/trace and no
        # Candidate, Population, or paper file is removed.
        if (
            state.pending_gate is not None
            and state.pending_gate.gate_id == "t4_recovery_gate"
            and _consumes_shared_literature_contract(state.current_task)
        ):
            prerequisites_ok, prerequisites_error = validate_prerequisites(self.workspace, state.current_task)
            coverage_gap = "Literature reading coverage is not ready" in str(prerequisites_error)
            if not prerequisites_ok and coverage_gap:
                error = f"任务前置材料未就绪，未提交模型请求: {prerequisites_error}"
                recovered = self.state_machine._pause_for_runtime_recovery_gate(
                    state,
                    error=error,
                    workspace_dir=self.workspace,
                    recovery={
                        "kind": "literature_coverage",
                        "error_summary": error,
                        "details": {
                            "task_id": state.current_task,
                            "source": "pending_t4_recovery_literature_preflight",
                            "return_to_task": "T3",
                        },
                    },
                )
                if recovered is not None:
                    state = recovered
        # A refresh can resolve a stale recovery gate deterministically (for
        # example after a compatible persisted artifact migration).  Let the
        # outer step loop create the now-current immediate Gate instead of
        # attempting to render the just-cleared pending object below.
        if state.pending_gate is None:
            return state
        state.dump_yaml(state_path)
        gate_id = state.pending_gate.gate_id
        gate_run_id = f"{state.current_task}_gate_{uuid4().hex[:8]}"
        try:
            io_spec = get_task_io(state.current_task)
        except KeyError:
            # Extension projects may define an immediate gate outside the
            # repository's built-in task catalog. It still receives a durable
            # human-decision event, simply without declared artifact rows.
            io_spec = {"inputs": {}, "outputs": {}, "required_inputs": []}
        inputs = {key: self.workspace / str(path) for key, path in dict(io_spec.get("inputs") or {}).items()}
        outputs = {key: self.workspace / str(path) for key, path in dict(io_spec.get("outputs") or {}).items()}
        self.progress.stage_started(
            task_id=state.current_task,
            run_id=gate_run_id,
            inputs=inputs,
            outputs=outputs,
            required_input_keys={str(key) for key in (io_spec.get("required_inputs") or [])},
            agent="human_gate",
            mode="human_decision",
            is_resume=state.status in {"PAUSED", "WAITING_HUMAN"},
        )
        self.progress.stage_human_action_required(
            task_id=state.current_task,
            gate_id=gate_id,
            reason=str(state.pending_gate.presentation.get("_description") or "需要人工确认关键研究决策。"),
        )
        self.run_logger.event(
            "HUMAN_GATE",
            task=state.current_task,
            gate_id=gate_id,
            option_count=len(state.pending_gate.options or []),
        )
        self.progress.gate_needed(gate_id=gate_id, task=state.current_task)
        try:
            gate_result = await self.human.present_gate(
                gate_id=gate_id,
                presentation=state.pending_gate.presentation,
                options=state.pending_gate.options,
            )
        except HumanInputUnavailable as exc:
            # EOF/no-input is an intentional handoff boundary.  Persist a
            # real PAUSED state rather than leaving WAITING_HUMAN while the
            # CLI claims the workflow was paused.  The durable pending gate
            # (including an unconfirmed T4 directive) is deliberately kept,
            # so resume renders the same decision and cannot execute it.
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = f"人工决策尚未提交，当前 Gate 已保存；resume 将回到这里。{exc}"
            state.task_context["last_unavailable_human_input"] = {
                "gate_id": gate_id,
                "recorded_at": state.paused_at,
                "reason": str(exc),
            }
            state.dump_yaml(state_path)
            self.run_logger.event(
                "ASK_HUMAN",
                task=state.current_task,
                gate_id=gate_id,
                input_transport="unavailable",
                reason=str(exc),
            )
            return state

        before_task = state.current_task
        state = self.state_machine.resolve_pending_gate(
            state,
            gate_result,
            workspace_dir=self.workspace,
        )
        awaiting_confirmation = bool(
            state.pending_gate is not None
            and state.status == "WAITING_HUMAN"
            and state.current_task == before_task
        )
        self.run_logger.event(
            "STATE_TRANSITION",
            from_task=before_task,
            to_task=state.current_task,
            reason=("gate:operation_confirmation_required" if awaiting_confirmation else f"gate:{gate_id}"),
        )
        if awaiting_confirmation:
            # A directive such as “重新演化” was understood, but no scientific
            # operation has happened yet. Do not render it as a completed
            # Gate, a 0/1 artifact result, or a misleading T4-GATE1 ->
            # T4-GATE1 transition.
            self.progress.stage_human_action_required(
                task_id=before_task,
                gate_id=gate_id,
                reason="操作计划已保存，尚未调用模型或改变 Population；请确认或取消该计划。",
            )
        else:
            self.progress.gate_resolved(from_task=before_task, to_task=state.current_task, gate_id=gate_id)
            self.progress.stage_gate_resolved(
                task_id=before_task,
                gate_id=gate_id,
                decision=str(gate_result.get("option_id") or "human_selection"),
            )
            self.progress.stage_completed(
                task_id=before_task,
                run_id=gate_run_id,
                outputs=outputs,
                ok=True,
                summary=f"Human Gate 已记录选择：{gate_result.get('option_id') or 'human_selection'}。",
            )
        state.dump_yaml(state_path)
        # A human decision is a conversation, rather than an invocation
        # boundary.  Some paths deliberately chain gates (for example the
        # survey decision -> template decision), and T4 deliberately opens a
        # second confirmation after it has understood a research operation.
        # Keep presenting the next pending gate in this same CLI session.  A
        # real EOF/no-input is handled above as ``HumanInputUnavailable`` and
        # therefore persists PAUSED without entering this branch.
        if state.status == "WAITING_HUMAN" and state.pending_gate is not None:
            return await self._present_pending_gate(state, state_path)
        return state

    def _build_runner(self, node, ctx):
        """根据 node 类型构造 AgentRunner。"""
        if node.agent is not None:
            agent = get_agent_by_id(node.agent, mode=node.mode)
        elif node.skill is not None:
            skill = resolve_skill(node.skill, self.skill_roots)
            ctx.extra["skill_dir"] = str(skill.skill_dir)
            if node.skill == PROJECT_SKILL_SPECIALIZATION_SKILL_NAME:
                agent = build_project_skill_specialization_agent(
                    skill=skill,
                    available_tools=set(self.tools.available_names()),
                    llm_profile=ctx.llm_override.profile,
                )
            else:
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
