from __future__ import annotations

"""完整 pipeline 运行器。"""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from ..agents.registry import get_agent_by_id
from ..orchestration.state_machine import StateMachine, validate_t4_gate1_selection_file
from ..runtime.agent import AgentResult
from ..runtime.config import RuntimeSettings
from ..runtime.llm_client import LLMClient
from ..runtime.logger import get_logger
from ..runtime.orchestrator import AgentRunner
from ..runtime.progress import CliProgressEmitter
from ..runtime.run_logger import RunLogger
from ..runtime.workspace import initialize_workspace
from ..schemas.state import StateYaml
from ..schemas.validator import register_builtin_task_checkers, validate_task_artifacts
from ..skills.agent import SkillAgent
from ..skills.loader import resolve_skill
from ..tools.human_gate import CLIHumanInterface, HumanInputUnavailable, HumanInterface
from ..tools.registry import ToolRegistry


_LOG = get_logger("complete_pipeline")


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
                self.run_logger.event("ASK_HUMAN", project_id=state.project_id, task=state.current_task)
                self.progress.emit("Project waiting for human input.", important=True)
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
                    state.pending_gate = None
                    state.current_task = "T4"
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
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = f"构建执行上下文失败: {exc}"
            state.dump_yaml(state_path)
            self.run_logger.event("ERROR", task=state.current_task, kind="build_context", message=state.last_error)
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

        if result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }:
            state = self.state_machine.advance(state, result, workspace_dir=self.workspace)
            state.dump_yaml(state_path)
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
            result.stop_reason = result.STOP_ERROR
            result.error = (
                "Runtime artifact validation failed; retrying via state-machine failure route: "
                + str(errors)
            )
            result.message = result.error
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
        return state

    async def _present_pending_gate(self, state: StateYaml, state_path: Path) -> StateYaml:
        """展示并处理已经挂起的人类 gate；只有输入不可用时才暂停。"""

        if state.pending_gate is None:
            return state
        gate_id = state.pending_gate.gate_id
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
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = str(exc)
            state.dump_yaml(state_path)
            self.run_logger.event("PAUSED", task=state.current_task, gate_id=gate_id, reason=state.last_error)
            return state

        before_task = state.current_task
        state = self.state_machine.resolve_pending_gate(
            state,
            gate_result,
            workspace_dir=self.workspace,
        )
        self.run_logger.event(
            "STATE_TRANSITION",
            from_task=before_task,
            to_task=state.current_task,
            reason=f"gate:{gate_id}",
        )
        self.progress.gate_resolved(from_task=before_task, to_task=state.current_task, gate_id=gate_id)
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
