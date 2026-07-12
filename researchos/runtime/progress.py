from __future__ import annotations

"""User-facing CLI progress narration.

This module intentionally stays presentation-only.  It summarizes existing
runtime events, tool calls, tool results, and progress markdown files without
changing agent prompts, task contracts, artifact schemas, or research logic.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable

from .run_logger import SEARCH_TOOL_NAMES
from .observability import StageReporter
from .observability.reporter import normalize_cli_markdown


EmitFn = Callable[[str], None]

_BLOCK_PREFIXES = (
    "[",
    "==",
    "Project ",
    "Task ",
    "Pause ",
    "Hint:",
    "copied:",
)


_T4_GATE1_ARTIFACTS: dict[str, tuple[int, str]] = {
    "ideation/_pass1_forward_candidates.json": (1, "Pass 1 原始候选池"),
    "ideation/_pass2_grounding_review.json": (2, "Pass 2 接地复核"),
    "ideation/_candidate_directions.json": (3, "Gate1 结构化候选池"),
    "ideation/_family_distribution.md": (4, "候选谱系分布检查"),
    "ideation/_gate1_candidate_cards.md": (5, "Gate1 完整候选卡片"),
    "ideation/_gate1_selection_brief.md": (6, "Gate1 选择简报"),
}


@dataclass(frozen=True)
class ToolNarrative:
    purpose: str
    input_summary: str
    expected_output: str
    output_path: str | None


@dataclass(frozen=True)
class ToolOutcome:
    """User-facing disposition; separate from the model-facing ToolResult bool."""

    status: str
    style: str
    important: bool


def classify_tool_outcome(*, ok: bool, data: dict[str, Any] | None, error: str | None = None) -> ToolOutcome:
    """Classify degraded scholarly-source outcomes without concealing failures.

    ``ok`` remains authoritative for the agent/runtime.  This function only
    determines how a researcher should read the terminal result.
    """

    payload = data if isinstance(data, dict) else {}
    disposition = str(payload.get("display_disposition") or "").casefold()
    failure_class = str(payload.get("failure_class") or error or "").casefold()
    if ok:
        return ToolOutcome("DONE", "green", False)
    if disposition == "skipped" or payload.get("optional_input") is True:
        return ToolOutcome("SKIPPED", "yellow", False)
    if failure_class in {"rate_limited", "network_unavailable", "timeout", "http_5xx", "transient_http"} and payload.get("fallback_available", True):
        return ToolOutcome("DEGRADED", "yellow", False)
    return ToolOutcome("FAILED", "bright_red", True)


class CliProgressEmitter:
    """Small output gate for human-readable CLI progress."""

    SEPARATOR = "-" * 76

    def __init__(
        self,
        *,
        quiet: bool = False,
        verbose: bool = False,
        verbosity: str = "normal",
        no_color: bool = False,
        json_events: bool = False,
        workspace: Path | None = None,
        runtime_dir_name: str = "_runtime",
        emit_fn: EmitFn | None = None,
    ) -> None:
        self.quiet = bool(quiet)
        self.verbose = bool(verbose)
        self.verbosity = "detailed" if verbose and verbosity == "normal" else verbosity
        self.no_color = bool(no_color)
        self.json_events = bool(json_events)
        self._emit_fn = emit_fn or (lambda message: print(message, flush=True))
        self._last_message_kind: str | None = None
        self._active_task_id: str | None = None
        self._t4_input_trace_emitted = False
        self._suppressed_t4_tool_results: dict[str, int] = {}
        self._workspace = Path(workspace) if workspace is not None else None
        self._runtime_dir_name = runtime_dir_name
        self._reporter: StageReporter | None = None
        self._structured_runs: dict[str, str] = {}
        # ``StageReporter`` may already have rendered a semantic metric panel
        # for a tool result.  Keep the ordinary result trace from repeating the
        # same fact immediately below it.
        self._structured_tool_result_rendered: dict[tuple[str, str], int] = {}
        if self._workspace is not None:
            self.configure_observability(workspace=self._workspace)

    def configure_observability(self, *, workspace: Path) -> None:
        """Attach the shared StageReporter without changing research behavior."""

        self._workspace = Path(workspace)
        self._reporter = StageReporter(
            workspace=self._workspace,
            runtime_dir_name=self._runtime_dir_name,
            verbosity=self.verbosity,
            quiet=self.quiet,
            no_color=self.no_color,
            json_events=self.json_events,
            emit_fn=self._emit_fn,
        )

    def stage_started(
        self,
        *,
        task_id: str,
        run_id: str,
        inputs: dict[str, Path],
        outputs: dict[str, Path],
        required_input_keys: set[str],
        agent: str,
        mode: str,
        is_resume: bool = False,
    ) -> None:
        if self._reporter is None:
            return
        self._structured_runs[task_id] = run_id
        self._reporter.stage_started(
            task_id=task_id,
            run_id=run_id,
            inputs=inputs,
            outputs=outputs,
            required_input_keys=required_input_keys,
            agent=agent,
            mode=mode,
            is_resume=is_resume,
        )

    def stage_tool_call(
        self,
        *,
        task_id: str,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        if self._reporter is not None:
            self._reporter.observe_tool_call(
                task_id=task_id,
                run_id=run_id,
                tool_name=tool_name,
                arguments=arguments,
            )

    def stage_tool_result(
        self,
        *,
        task_id: str,
        run_id: str,
        tool_name: str,
        ok: bool,
        data: dict[str, Any] | None,
        error: str | None,
    ) -> bool:
        if self._reporter is None:
            return False
        rendered = self._reporter.observe_tool_result(
            task_id=task_id,
            run_id=run_id,
            tool_name=tool_name,
            ok=ok,
            data=data,
            error=error,
        )
        if rendered:
            key = (task_id, tool_name)
            self._structured_tool_result_rendered[key] = self._structured_tool_result_rendered.get(key, 0) + 1
        return rendered

    def stage_human_action_required(self, *, task_id: str, gate_id: str, reason: str) -> None:
        run_id = self._structured_runs.get(task_id)
        if self._reporter is not None and run_id:
            self._reporter.human_action_required(task_id=task_id, run_id=run_id, gate_id=gate_id, reason=reason)

    def stage_gate_resolved(self, *, task_id: str, gate_id: str, decision: str) -> None:
        run_id = self._structured_runs.get(task_id)
        if self._reporter is not None and run_id:
            self._reporter.gate_resolved(task_id=task_id, run_id=run_id, gate_id=gate_id, decision=decision)

    def stage_completed(
        self,
        *,
        task_id: str,
        run_id: str,
        outputs: dict[str, Path],
        ok: bool,
        summary: str,
        error: str | None = None,
    ) -> None:
        if self._reporter is not None:
            self._reporter.stage_completed(
                task_id=task_id,
                run_id=run_id,
                outputs=outputs,
                ok=ok,
                summary=summary,
                error=error,
            )
        self._structured_runs.pop(task_id, None)
        for key in [key for key in self._structured_tool_result_rendered if key[0] == task_id]:
            self._structured_tool_result_rendered.pop(key, None)

    def agent_markdown(
        self,
        *,
        task_id: str,
        agent: str,
        content: str,
        human_action_context: bool,
        verbose_only: bool,
    ) -> None:
        """Render public agent prose as Markdown, never as a raw trace block."""

        if verbose_only and not self.verbose:
            return
        if self.quiet and not human_action_context:
            return
        cleaned = normalize_cli_markdown(content)
        if not cleaned:
            return
        run_id = self._structured_runs.get(task_id)
        if self._reporter is not None and run_id:
            self._reporter.render_agent_markdown(
                task_id=task_id,
                run_id=run_id,
                agent=agent,
                content=cleaned,
                human_action_context=human_action_context,
            )
            return
        self.emit(f"[Agent Output]\n{cleaned}", important=human_action_context)

    def emit(self, message: str, *, important: bool = False, verbose_only: bool = False) -> None:
        if verbose_only and not self.verbose:
            return
        if self.quiet and not important:
            return
        formatted = format_cli_message(message, previous_kind=self._last_message_kind)
        if not formatted:
            return
        self._emit_fn(formatted)
        self._last_message_kind = classify_cli_message(message)

    def agent_start(
        self,
        *,
        task_id: str,
        agent: str,
        phase: str,
        objective: str,
        inputs: list[str],
        expected_outputs: list[str],
        expected_artifacts: str,
        llm_tier: str,
        step_limit: str,
    ) -> None:
        self._active_task_id = task_id
        self._t4_input_trace_emitted = False
        self._suppressed_t4_tool_results = {}
        if task_id in self._structured_runs:
            return
        if self.quiet:
            self.emit(f"[{agent}] 启动 {task_id}: {_compact_text(objective, 120)}", important=True)
            return
        input_text = ", ".join(inputs[:6]) if inputs else "未声明"
        if len(inputs) > 6:
            input_text += f", ...(+{len(inputs) - 6})"
        output_text = ", ".join(expected_outputs[:6]) if expected_outputs else "未声明"
        if len(expected_outputs) > 6:
            output_text += f", ...(+{len(expected_outputs) - 6})"
        if self.verbose:
            self.emit(
                "\n"
                f"{self.SEPARATOR}\n"
                f"[{agent}] 阶段启动\n"
                f"任务：{task_id} | 阶段：{phase}\n"
                f"目标：{objective}\n"
                f"输入来源：{input_text}\n"
                f"预计产物：{expected_artifacts}\n"
                f"输出文件：{output_text}\n"
                f"运行设置：模型层级 {llm_tier}，最大步数 {step_limit}"
            )
            return
        lines = [
            self.SEPARATOR,
            f"[{agent}] {task_id} 启动",
            f"目标：{_compact_text(objective, 150)}",
            f"产物：{_compact_text(expected_artifacts, 150)}",
        ]
        if task_id == "T4":
            lines.extend(
                [
                    "执行轨迹：准备证据包 -> 生成候选 -> 接地复核 -> 写入 Gate1 卡片 -> 等待人工选择",
                    "说明：轨迹来自工具调用和已落盘产物，不展示模型内部推理。",
                ]
            )
        self.emit("\n".join(lines), important=True)

    def agent_step(
        self,
        *,
        agent: str,
        step: int,
        step_limit: str,
        tokens: int,
        cost_usd: float,
    ) -> None:
        self.emit(
            f"[{agent} Agent] 正在推进第 {step}/{step_limit} 步；"
            f"累计 token {tokens}，估算成本 ${cost_usd:.4f}",
            verbose_only=True,
        )

    def llm_request_started(self, *, task_id: str, step: int) -> None:
        """Show that the provider request was actually submitted immediately.

        This is deliberately separate from the delayed heartbeat.  It tells a
        user that the runtime reached the provider boundary without implying
        anything about hidden model reasoning.
        """

        if task_id in self._structured_runs and not self.verbose and self.verbosity != "detailed":
            return
        label = task_id.removeprefix("SKILL_") if task_id.startswith("SKILL_") else task_id
        self.emit(
            f"[运行中] {label} · step {step} | 模型请求已提交，正在等待下一组可执行动作。"
        )

    def llm_waiting(
        self,
        *,
        task_id: str,
        agent: str,
        step: int,
        elapsed_seconds: int,
    ) -> None:
        """Show a heartbeat for an in-flight provider call.

        This is intentionally a runtime fact, not a claim about private model
        reasoning.  It is visible by default for Skills and T4 where the next
        durable artifact can otherwise take a while to appear.
        """

        if task_id in self._structured_runs and not self.verbose and self.verbosity != "detailed":
            return
        label = task_id.removeprefix("SKILL_") if task_id.startswith("SKILL_") else task_id
        self.emit(
            f"[运行中] {label} · step {step} | 正在等待模型返回下一组可执行动作；"
            f"本次调用已持续 {elapsed_seconds}s。",
        )

    def tool_call(self, *, agent: str, tool_name: str, narrative: ToolNarrative) -> None:
        if self.quiet:
            return
        active_task = self._active_task_id
        structured_run = self._structured_runs.get(active_task or "")
        if self._reporter is not None and active_task and structured_run:
            # T4 often reads a large evidence pack.  In normal mode retain one
            # truthful public milestone instead of printing a read-file storm;
            # detailed mode still renders each call through StageReporter.
            if active_task == "T4" and tool_name in {"read_file", "list_files", "glob_files", "grep_search"}:
                self._suppressed_t4_tool_results[tool_name] = self._suppressed_t4_tool_results.get(tool_name, 0) + 1
                if not self._t4_input_trace_emitted:
                    self._t4_input_trace_emitted = True
                    self.emit("[T4 Trace] 正在核验上游证据、桥接材料与论文笔记 section。")
                if self.verbosity != "detailed":
                    return
            purpose = narrative.purpose
            gate1_artifact = _t4_gate1_artifact(narrative.output_path) if active_task == "T4" else None
            if gate1_artifact:
                index, label = gate1_artifact
                purpose = f"Gate1 artifact {index}/6 · {label}"
            self._reporter.render_tool_call(
                task_id=active_task,
                run_id=structured_run,
                agent=agent,
                tool_name=tool_name,
                purpose=purpose,
                input_summary=narrative.input_summary,
                output_path=narrative.output_path,
            )
            return
        if self._active_task_id in self._structured_runs and self._active_task_id != "T4" and not self.verbose:
            return
        if self._active_task_id == "T4" and tool_name in {"read_file", "list_files", "glob_files", "grep_search"}:
            self._suppressed_t4_tool_results[tool_name] = self._suppressed_t4_tool_results.get(tool_name, 0) + 1
            if not self._t4_input_trace_emitted:
                self._t4_input_trace_emitted = True
                self.emit("[轨迹] T4 正在核验上游证据和文献笔记 section。")
            return
        if self._active_task_id == "T4" and tool_name in {"write_file", "write_structured_file", "append_file"}:
            gate1_artifact = _t4_gate1_artifact(narrative.output_path)
            if gate1_artifact:
                index, label = gate1_artifact
                self.emit(
                    f"[T4 Gate1] {index}/6 写入中 · {label}\n"
                    f"文件：{narrative.output_path}"
                )
                return
            stage = _t4_artifact_stage(narrative.output_path)
            if stage:
                self.emit(f"[轨迹] T4 {stage}")
        if self._active_task_id == "T4" and tool_name == "finish_task":
            self.emit("[轨迹] T4 正在校验 Gate1 候选产物，并准备转入人工选择。")
        if not self.verbose:
            line = (
                f"[Tool] {tool_name}: {_compact_text(narrative.purpose, 90)}；"
                f"输入：{_compact_text(narrative.input_summary, 120)}"
            )
            if narrative.output_path:
                line += f"；写入：{narrative.output_path}"
            self.emit(line)
            return
        lines = [
            f"[Tool Decision] {agent} Agent 准备调用 {tool_name}",
            f"目的：{narrative.purpose}",
            f"输入摘要：{narrative.input_summary}",
            f"预期结果：{narrative.expected_output}",
        ]
        if narrative.output_path:
            lines.append(f"预计写入/影响：{narrative.output_path}")
        self.emit("\n".join(lines))

    def tool_result(
        self,
        *,
        agent: str,
        tool_name: str,
        ok: bool,
        result_summary: str,
        output_path: str | None = None,
        next_step: str | None = None,
        duration_ms: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        suppressed = self._suppressed_t4_tool_results.get(tool_name, 0)
        if suppressed:
            if suppressed == 1:
                self._suppressed_t4_tool_results.pop(tool_name, None)
            else:
                self._suppressed_t4_tool_results[tool_name] = suppressed - 1
            return
        outcome = classify_tool_outcome(ok=ok, data=data)
        important = outcome.important
        active_task = self._active_task_id
        structured_run = self._structured_runs.get(active_task or "")
        if self._reporter is not None and active_task and structured_run:
            semantic_key = (active_task, tool_name)
            semantic_count = self._structured_tool_result_rendered.get(semantic_key, 0)
            if semantic_count:
                if semantic_count == 1:
                    self._structured_tool_result_rendered.pop(semantic_key, None)
                else:
                    self._structured_tool_result_rendered[semantic_key] = semantic_count - 1
                return
            summary = result_summary
            gate1_artifact = _t4_gate1_artifact(output_path) if active_task == "T4" else None
            if gate1_artifact:
                index, label = gate1_artifact
                prefix = f"Gate1 artifact {index}/6 · {label}"
                summary = f"{prefix}: {'已保存' if ok else '写入失败'}。{result_summary}"
            self._reporter.render_tool_result(
                task_id=active_task,
                run_id=structured_run,
                tool_name=tool_name,
                ok=ok,
                summary=summary,
                output_path=output_path,
                data=data,
            )
            return
        if self._active_task_id in self._structured_runs and self._active_task_id != "T4" and ok and not self.verbose:
            return
        if self.quiet and not important:
            return
        status = "完成" if ok else {"SKIPPED": "跳过", "DEGRADED": "降级继续", "FAILED": "失败"}[outcome.status]
        if self._active_task_id == "T4":
            if tool_name == "log_t4_ideation_progress" and ok:
                event = (data or {}).get("event") if isinstance(data, dict) else None
                if isinstance(event, dict):
                    self.emit(_format_t4_execution_event(event))
                    return
            gate1_artifact = _t4_gate1_artifact(output_path)
            if gate1_artifact:
                index, label = gate1_artifact
                if ok:
                    self.emit(
                        f"[T4 Gate1] {index}/6 已保存 · {label}\n"
                        f"文件：{output_path}"
                    )
                else:
                    self.emit(
                        f"[T4 Gate1] {index}/6 写入失败 · {label}\n"
                        f"文件：{output_path or '未确定'}\n"
                        f"原因：{_compact_text(result_summary, 220)}",
                        important=True,
                    )
                return
        if ok and not self.verbose:
            line = f"[Tool] {tool_name} {status}: {_compact_text(result_summary, 150)}"
            if output_path:
                line += f" -> {output_path}"
            self.emit(line)
            return
        lines = [f"[Tool Result] {tool_name} {status}", f"结果摘要：{result_summary}"]
        if output_path:
            lines.append(f"写入文件：{output_path}")
        if duration_ms is not None and self.verbose:
            lines.append(f"耗时：{duration_ms} ms")
        next_step = _useful_next_step(next_step)
        if next_step and outcome.important:
            lines.append(f"建议：{next_step}")
        self.emit("\n".join(lines), important=important)

    def progress_file_update(
        self,
        *,
        label: str,
        path: str,
        bullets: list[str],
        next_step: str | None = None,
    ) -> None:
        if self.quiet or not bullets:
            return
        lines = [f"[Progress] {label} 已更新：{path}"]
        lines.extend(f"- {item}" for item in bullets[:4])
        self.emit("\n".join(lines))

    def agent_done(
        self,
        *,
        task_id: str,
        agent: str,
        ok: bool,
        stop_reason: str,
        summary: str,
        artifacts: list[str],
        next_step: str | None,
        trace_file: str | None = None,
        error: str | None = None,
        outputs_expected: dict[str, Path] | None = None,
        run_id: str | None = None,
    ) -> None:
        structured_run_id = self._structured_runs.get(task_id)
        if self._reporter is not None and structured_run_id and outputs_expected is not None:
            self.stage_completed(
                task_id=task_id,
                run_id=run_id or structured_run_id,
                outputs=outputs_expected,
                ok=ok,
                summary=summary,
                error=error,
            )
            return
        important = True
        status = "阶段完成" if ok else "阶段停止"
        lines = [
            self.SEPARATOR,
            f"[{agent}] {task_id} {status}",
            "阶段总结",
            f"- 完成了什么：{summary if self.verbose else _compact_text(summary, 320)}",
            f"- 本阶段职责：{describe_task_artifacts(task_id)}",
        ]
        if not ok:
            lines.insert(2, f"停止原因：{stop_reason}")
        if artifacts:
            lines.append("- 已产出文件：")
            for artifact in artifacts:
                lines.append(f"  - {artifact}：{describe_output_artifact(artifact, task_id=task_id)}")
        elif not ok and trace_file:
            lines.append(f"详细日志：{trace_file}")
        elif ok:
            lines.append("- 已产出文件：当前任务未声明独立文件；请以上方阶段结果和状态机校验为准。")
        if artifacts and trace_file and self.verbose:
            lines.append(f"详细日志：{trace_file}")
        if error:
            lines.append(f"当前问题：{_compact_text(error, 260)}")
        if trace_file and not artifacts and not self.verbose:
            lines.append(f"日志：{trace_file}")
        elif trace_file and self.verbose and not artifacts:
            lines.append(f"详细日志：{trace_file}")
        next_step = _useful_next_step(next_step)
        if next_step and not ok:
            lines.append(f"建议：{next_step}")
        if ok:
            completion_next = next_step_after_completed_task(task_id)
            if completion_next:
                lines.append(f"- 下一步：{completion_next}")
            else:
                lines.append("- 下一步：状态机会校验上述产物，然后推进到配置的后续节点。")
        self.emit("\n".join(lines), important=important)

    def validation_start(self, *, task_id: str) -> None:
        self.emit(f"[Validation] {task_id}: 检查声明产物和 schema", important=True)

    def validation_result(
        self,
        *,
        task_id: str,
        ok: bool,
        error: str | None = None,
        failure_count: int | None = None,
        retry_limit: int | None = None,
    ) -> None:
        if ok:
            self.emit(f"[Validation] {task_id}: 通过", important=True)
            return
        counter = ""
        if failure_count is not None and retry_limit is not None:
            counter = f" ({failure_count}/{retry_limit})"
        self.emit(
            f"[Validation] {task_id}: 未通过{counter} - {_compact_text(error, 260)}",
            important=True,
        )

    def runtime_pause(self, *, message: str) -> None:
        self.emit(f"[Runtime] 暂停：{_compact_text(message, 260)}", important=True)

    def pipeline_start(self, *, project_id: str, task: str, resume: bool, status: str | None = None) -> None:
        if resume:
            suffix = f"，状态 {status}" if status else ""
            self.emit(f"[Pipeline] Resume {project_id}: 当前任务 {task}{suffix}", important=True)
        else:
            self.emit(f"[Pipeline] 启动 {project_id}: 首个任务 {task}", important=True)

    def pipeline_paused(self, *, reason: str | None = None) -> None:
        if reason:
            self.emit(f"[Pipeline] 已暂停：{_compact_text(reason, 260)}", important=True)
        else:
            self.emit("[Pipeline] 已暂停", important=True)

    def gate_needed(self, *, gate_id: str, task: str) -> None:
        self.emit(f"{self.SEPARATOR}\n[Gate] {task}: 等待用户选择 ({gate_id})", important=True)

    def gate_resolved(self, *, from_task: str, to_task: str, gate_id: str) -> None:
        self.emit(f"{self.SEPARATOR}\n[Gate] 已确认 {gate_id}: {from_task} -> {to_task}", important=True)

    def runtime_validation_failed(
        self,
        *,
        task_id: str,
        reason: str,
        log_path: str | None = None,
        run_id: str | None = None,
        outputs: dict[str, Path] | None = None,
    ) -> None:
        if self._reporter is not None and run_id and outputs is not None:
            self._reporter.stage_invalidated(
                task_id=task_id,
                run_id=run_id,
                outputs=outputs,
                reason=reason,
                log_path=log_path,
            )
        lines = [
            f"[Validation] {task_id}: runtime artifact 校验未通过",
            f"原因：{_compact_text(reason, 260)}",
        ]
        if log_path:
            lines.append(f"日志：{log_path}")
        self.emit("\n".join(lines), important=True)

    def compact_state_transition(
        self,
        *,
        from_task: str,
        to_task: str,
    ) -> None:
        self.emit(f"{self.SEPARATOR}\n[State] {from_task} -> {to_task}", important=True)

    def legacy_agent_done(
        self,
        *,
        task_id: str,
        agent: str,
        ok: bool,
        stop_reason: str,
        summary: str,
        artifacts: list[str],
        next_step: str | None,
        trace_file: str | None = None,
        error: str | None = None,
    ) -> None:
        """Kept for compatibility with old tests that imported emitted text shape."""
        important = True
        status = "阶段完成" if ok else "阶段停止"
        lines = [
            f"[{agent} Agent] {status}",
            f"任务：{task_id} | stop_reason={stop_reason}",
            f"完成内容：{summary}",
        ]
        if artifacts:
            lines.append("输出文件：" + ", ".join(artifacts[:8]))
            if len(artifacts) > 8:
                lines.append(f"更多输出：还有 {len(artifacts) - 8} 个文件已生成")
        if error:
            lines.append(f"当前问题：{_compact_text(error, 260)}")
        if trace_file:
            lines.append(f"详细日志：{trace_file}")
        next_step = _useful_next_step(next_step)
        if next_step and not ok:
            lines.append(f"建议：{next_step}")
        self.emit("\n".join(lines), important=important)

    def state_transition(
        self,
        *,
        from_task: str,
        to_task: str,
        reason: str,
    ) -> None:
        if self.verbose:
            self.emit(
                f"{self.SEPARATOR}\n[State] {from_task} 已结束，系统进入 {to_task}。原因：{_compact_text(reason, 160)}",
                important=True,
            )
            return
        self.emit(f"{self.SEPARATOR}\n[State] {from_task} -> {to_task}", important=True)

    def error_context(
        self,
        *,
        stage: str,
        agent: str | None = None,
        tool_name: str | None = None,
        message: str,
        log_path: str | None = None,
    ) -> None:
        lines = [f"[Error] {stage} 失败"]
        if agent:
            lines.append(f"Agent：{agent}")
        if tool_name:
            lines.append(f"工具：{tool_name}")
        lines.append(f"原因：{_compact_text(message, 300)}")
        if log_path:
            lines.append(f"建议查看：{log_path}")
        self.emit("\n".join(lines), important=True)


def format_cli_message(message: str, *, previous_kind: str | None = None) -> str:
    """Apply light block spacing to user-facing CLI messages.

    This is presentation-only: it keeps raw logs untouched while making the
    interactive console easier to scan. Short in-line text stays unchanged, but
    status blocks, agent/tool headings, and section separators get one leading
    blank line.
    """

    text = _drop_generic_next_step_lines(str(message or "").strip())
    if not text:
        return ""
    kind = classify_cli_message(text)
    if kind == "block":
        return "\n" + text
    if kind == "stream" and previous_kind not in {None, "stream"}:
        return "\n" + text
    return text


def classify_cli_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return "empty"
    first_line = text.splitlines()[0].strip()
    if not first_line:
        return "empty"
    if first_line.startswith("[Agent] Abstract sweep progress:"):
        return "stream"
    if first_line.startswith("[Reader Agent] T3 深读进度："):
        return "stream"
    if first_line.startswith(_BLOCK_PREFIXES) or "\n" in text:
        return "block"
    return "plain"


def _useful_next_step(next_step: str | None) -> str | None:
    text = str(next_step or "").strip()
    if not text:
        return None
    lowered = text.casefold()
    generic_fragments = (
        "状态机将根据当前节点配置进入下一阶段",
        "状态机会根据当前节点配置进入下一阶段",
        "根据当前节点配置进入下一阶段",
        "推进下一步",
        "供 agent 回填上下文并推进下一步",
        "agent 将据此更新当前判断",
        "进入下一阶段",
    )
    if any(fragment.casefold() in lowered for fragment in generic_fragments):
        return None
    return text


def _drop_generic_next_step_lines(message: str) -> str:
    if not message:
        return ""
    kept: list[str] = []
    for line in message.splitlines():
        clean = line.strip()
        if clean.startswith(("下一步：", "下一步:", "Next:")):
            candidate = re.sub(r"^(?:下一步：|下一步:|Next:)\s*", "", clean, flags=re.IGNORECASE)
            if _useful_next_step(candidate) is None:
                continue
        kept.append(line)
    return "\n".join(kept).strip()


def _t4_artifact_stage(output_path: str | None) -> str | None:
    """Translate durable T4 artifact milestones into a user-facing trace."""

    path = str(output_path or "")
    stages = {
        "ideation/t4_progress.md": "正在记录候选构建阶段和输入范围。",
        "ideation/bridge_coverage_review.json": "正在记录桥接候选的证据边界和暂缓原因。",
    }
    return stages.get(path)


def _t4_gate1_artifact(output_path: str | None) -> tuple[int, str] | None:
    normalized = str(output_path or "").replace("\\", "/").lstrip("./")
    return _T4_GATE1_ARTIFACTS.get(normalized)


def _t4_count_text(arguments: dict[str, Any]) -> str | None:
    completed = arguments.get("completed")
    total = arguments.get("total")
    if completed is None or total is None:
        return None
    return f"{completed}/{total}"


def _format_t4_execution_event(event: dict[str, Any]) -> str:
    """Format a persisted public T4 progress event, never model reasoning."""

    phase_labels = {
        "context_pack": "上下文包",
        "pass1_mainline": "Pass1 主线",
        "pass1_supplement": "Pass1 补充通道",
        "pass2_grounding": "Pass2 接地复核",
        "scoring": "评分整理",
        "gate_cards": "Gate1 卡片",
    }
    status_labels = {
        "started": "已开始",
        "candidate_started": "候选开始",
        "candidate_completed": "候选完成",
        "channel_started": "通道开始",
        "channel_completed": "通道完成",
        "completed": "已完成",
    }
    phase = phase_labels.get(str(event.get("phase") or ""), str(event.get("phase") or "T4"))
    status = status_labels.get(str(event.get("status") or ""), str(event.get("status") or "更新"))
    completed = event.get("completed")
    total = event.get("total")
    count = f" {completed}/{total}" if completed is not None and total is not None else ""
    subject = str(event.get("candidate_id") or event.get("channel") or "").strip()
    title = _compact_text(str(event.get("candidate_title") or ""), 70)
    tail = f" · {subject}" if subject else ""
    if title:
        tail += f" · {title}"
    recommendation = str(event.get("recommendation") or "").strip()
    if recommendation:
        tail += f" · 建议={recommendation}"
    scores = event.get("score_snapshot") if isinstance(event.get("score_snapshot"), dict) else {}
    if scores:
        score_text = ", ".join(f"{key}={value}/5" for key, value in scores.items())
        tail += f" · 评分={score_text}"
    return f"[T4 轨迹] {phase}{count} · {status}{tail}"


def describe_output_artifact(path: str, *, task_id: str = "") -> str:
    """Explain a durable output path without pretending it was inspected here."""

    normalized = str(path or "").replace("\\", "/").lstrip("./")
    exact = {
        "literature/search_log.md": "检索、去重、回填、候选切分和覆盖缺口的审计记录。",
        "literature/papers_raw.jsonl": "多源原始检索结果；用于追溯每条候选的来源。",
        "literature/papers_dedup.jsonl": "合并标识符和标题重复后的候选池。",
        "literature/papers_verified.jsonl": "经过字段与可读性核验、可进入后续阅读处置的候选池。",
        "literature/papers_backlog.jsonl": "未进入当前 active pool 的可追溯候选，后续可回捞。",
        "literature/deep_read_queue.jsonl": "T3 的结构化精读优先队列与排序依据。",
        "literature/literature_params.json": "本轮 T2/T3 覆盖、语言和中文文献策略的最终参数记录。",
        "literature/comparison_table.csv": "跨论文的方法、数据、证据和限制的对照表。",
        "literature/synthesis.md": "T3.5 的文献综合，供 T4 idea 和论文写作引用。",
        "literature/missing_areas.md": "已覆盖内容、证据缺口与后续补检索建议。",
        "literature/domain_map.json": "领域、方法家族和桥接主题的结构化地图。",
        "ideation/_pass1_forward_candidates.json": "T4 Pass 1 的原始发散候选池，供覆盖审计。",
        "ideation/_pass2_grounding_review.json": "T4 Pass 2 对候选的接地复核、风险和上桌建议。",
        "ideation/_candidate_directions.json": "完整的 Gate1 候选结构、评分、实验和支撑论文数据。",
        "ideation/_family_distribution.md": "候选来源和机制谱系的集中度检查。",
        "ideation/_gate1_candidate_cards.md": "供人工比较的完整 Gate1 候选卡片。",
        "ideation/_gate1_selection_brief.md": "候选选择、合并与风险提示的决策简报。",
        "ideation/selected_idea_brief.md": "用户 Gate1 选择和 T4 后半段收敛方向的可读记录。",
        "ideation/hypotheses.md": "可证伪研究假设、边界条件与预期方向。",
        "ideation/experiment_plan.md": "实验设计、对照、指标、风险和停止条件。",
        "ideation/novelty_audit.md": "与相邻工作的差异、撞车风险和 claim 降级建议。",
        "drafts/paper.tex": "整篇论文 TeX 草稿，后续审稿和提交的主输入。",
        "drafts/related_work.bib": "论文引用所需的 BibTeX 条目；会接受 provenance 审计。",
        "drafts/citation_provenance_audit.md": "每条文内引用与文献笔记/证据边界的可读审计报告。",
        "drafts/citation_provenance_audit.json": "引用 provenance 审计的机器可读结果。",
        "drafts/claim_audit.md": "论文 claim、证据来源和强度限制的审计结果。",
        "submission/submission_checklist.md": "投稿前格式、匿名化、编译和材料核对清单。",
    }
    if normalized in exact:
        return exact[normalized]
    prefix_rules = (
        ("literature/paper_notes/", "一篇结构化精读笔记，包含可引用 section、证据等级和边界。"),
        ("literature/paper_notes_bridge/", "桥接领域论文的结构化笔记，用于核验跨域机制迁移。"),
        ("literature/abstract_notes/", "摘要级轻读笔记，只能支撑背景或待核验线索。"),
        ("drafts/section_outlines/", "章节级证据补充或局部写作大纲，供对应论文段落回查。"),
        ("drafts/sections/", "单个论文章节的草稿与局部证据绑定结果。"),
        ("experiments/", "实验运行、结果或审计材料，供结果摄取和写作 evidence pack 使用。"),
        ("external_executor/", "外部执行器交接、运行或结果回传材料。"),
        ("submission/", "投稿包、模板迁移或最终编译核验材料。"),
    )
    for prefix, description in prefix_rules:
        if normalized.startswith(prefix):
            return description
    if task_id.startswith("T8"):
        return "本论文写作阶段声明的产物；用于后续审稿、修订或提交校验。"
    return "本阶段声明并写入的可追溯产物；详细结构请在该文件中查看。"


def next_step_after_completed_task(task_id: str) -> str | None:
    """Give a stable user-facing next action for common completed stages."""

    hints = {
        "T1": "进入 T2 文献参数 Gate，确认覆盖规模、稿件语言和中文文献策略。",
        "T2": "进入 T2 文献覆盖 Gate，确认候选池是否足够或是否需要补检索。",
        "T3": "进入 T3.5 文献综合，形成可审计的 synthesis 和研究缺口。",
        "T3.5": "进入综述分支/写作决策 Gate；无论是否写综述，synthesis 都会继续供 T4 使用。",
        "T4": "进入 Gate1，由你选择、合并、重构或重新分析候选方向。",
        "T4.5": "进入 T5 前的 context re-boost 与外部实验交接准备。",
        "T5-HANDOFF": "进入项目专属 skill 和实验材料确认流程。",
        "T7-INGEST": "进入实验完整性审计与 result-to-claim 映射。",
        "T7-CLAIMS": "进入写作放行评估，确认实验与证据是否足够支撑论文。",
        "T8-RESOURCE": "进入论文结构与章节级写作计划。",
        "T8-WRITE": "进入章节级写作与证据绑定。",
        "T8-DRAFT": "进入作者自查、双轮审稿和修订流程。",
        "T9": "核对投稿包、PDF 编译结果与 submission checklist。",
    }
    if task_id.startswith("T8-SEC-"):
        return "继续完成剩余章节，并在整稿拼装时运行 claim 与 citation provenance 审计。"
    return hints.get(task_id)


def describe_task_artifacts(task_id: str) -> str:
    """Return a concise user-facing explanation of a task's expected products."""

    if task_id.startswith("T8-SEC-"):
        return "生成单个论文章节草稿、局部 claim 记录和章节级审计材料"
    task_map = {
        "T1": "项目配置、研究边界、种子材料索引和跨领域检索计划",
        "T2": "检索 query、候选论文池、去重结果、阅读队列、覆盖缺口和检索日志",
        "T2-PARAM-GATE": "文献覆盖参数候选方案，等待用户选择",
        "T2-PARAM-CONFIRM-GATE": "用户确认后的文献覆盖参数记录",
        "T2-COVERAGE-GATE": "候选文献覆盖情况与是否继续补检索的决策",
        "T3": "论文精读笔记、摘要级笔记、comparison table、BibTeX 和 notes manifest",
        "T3.5": "综述合成工作台、领域地图、缺口分析和 synthesis 文档",
        "T3.6-GATE-SURVEY": "综述阶段产物确认、写作/结束选择与后续路径决策",
        "T4": "候选研究假设、idea 排序依据、实验计划、风险清单和 Gate1 展示材料",
        "T4-GATE1": "面向用户选择的 idea 卡片、候选池摘要和最终选择记录",
        "T4.5": "新颖性审计、撞车风险、机制 tuple 审计和 claim 降级建议",
        "T5-REBOOST-GATE": "LLM 对 Pre-T5 材料做 context re-boost 并生成 handoff 上下文",
        "T5-HANDOFF": "外部实验 handoff pack、执行协议、项目专属 skills 和交接提示",
        "T5-SKILL-CUSTOMIZATION-GATE": "检查项目专属 skill suite 和 specialization report",
        "T5-EXPR-MATERIAL-GATE": "外部实验材料放置确认与 expr 目录快照",
        "T5-EXECUTOR-GATE": "用户选择实验执行方式的 gate 记录",
        "T5-DRY-RUN": "mock 外部执行器协议验证产物",
        "T5-EXTERNAL-WAIT": "外部执行器 result pack 的等待/校验状态",
        "T7-INGEST": "规范化实验结果、provenance 和结果摘要",
        "T7-AUDIT": "实验完整性、method drift、framework figure 和 provenance 审计",
        "T7-POST-NOVELTY": "结合实验结果后的新颖性复核和 claim 边界",
        "T7-CLAIMS": "result-to-claim 映射和写作 evidence pack",
        "T7.5": "判断实验与证据是否足够支撑进入论文写作",
        "T8-RESOURCE": "写作资源索引、证据计划、图表计划和引用资源映射",
        "T8-STYLE-GATE": "目标模板/语言/venue 风格选择记录",
        "T8-WRITE": "论文总大纲、章节结构和资源驱动写作计划",
        "T8-SECTION-PLAN": "paper_state、章节局部大纲和章节写作任务表",
        "T8-DRAFT": "整篇 paper.tex、章节拼装结果、claim 审计和 BibTeX 绑定",
        "T8-SELF-CHECK": "作者自查报告、明显问题清单和修订建议",
        "T8-REVIEW-1": "第一轮审稿意见、patch list 和风险排序",
        "T8-REVIEW-2": "第二轮审稿意见、剩余缺陷和修改优先级",
        "T8-REVISE-1": "按第一轮审稿意见修订后的论文与变更记录",
        "T8-REVISE-2": "按第二轮审稿意见修订后的论文与最终变更记录",
        "T8-PAPER-CLAIM-AUDIT": "写作完成后的 claim/evidence 一致性最终审计",
        "T9": "投稿包、PDF 编译结果、TeX 修复记录和 submission checklist",
        "HELLO": "最小 smoke-test 输出，用于验证 runtime 与工具链可运行",
    }
    return task_map.get(task_id, "当前状态机节点声明的输出文件和校验产物")


def next_step_for_task(task_id: str, *, ok: bool = True) -> str | None:
    if not ok:
        return "检查错误摘要、_runtime/logs/researchos.log 和 trace 后 resume 或修复产物"
    return None


def build_tool_narrative(
    *,
    task_id: str,
    agent: str,
    tool_name: str,
    arguments: dict[str, Any],
    workspace_dir: Path | None = None,
    verbose: bool = False,
) -> ToolNarrative:
    purpose = _tool_purpose(task_id, agent, tool_name)
    expected_output = _tool_expected_output(tool_name)
    input_summary = summarize_tool_arguments(tool_name, arguments, verbose=verbose)
    output_path = _tool_output_path(tool_name, arguments, workspace_dir)
    return ToolNarrative(
        purpose=purpose,
        input_summary=input_summary,
        expected_output=expected_output,
        output_path=output_path,
    )


def summarize_tool_arguments(tool_name: str, arguments: dict[str, Any], *, verbose: bool = False) -> str:
    max_len = 360 if verbose else 220
    if tool_name in SEARCH_TOOL_NAMES:
        query = arguments.get("query") or arguments.get("search_query") or arguments.get("title") or ""
        fields = [
            ("query", query),
            ("max", arguments.get("max_results") or arguments.get("per_page") or arguments.get("rows")),
            ("bucket", arguments.get("query_bucket") or arguments.get("search_bucket")),
            ("bridge", arguments.get("bridge_id")),
        ]
        return _join_fields(fields, max_len=max_len)
    if tool_name in {"read_file", "write_file", "write_structured_file", "append_file"}:
        fields = [("path", arguments.get("path"))]
        if verbose and "content" in arguments:
            fields.append(("content", _compact_text(arguments.get("content"), 120)))
        return _join_fields(fields, max_len=max_len)
    if tool_name in {"list_files", "glob_files"}:
        return _join_fields(
            [
                ("path", arguments.get("path") or arguments.get("directory") or arguments.get("root")),
                ("pattern", arguments.get("pattern") or arguments.get("glob")),
            ],
            max_len=max_len,
        )
    if tool_name == "grep_search":
        return _join_fields(
            [
                ("pattern", arguments.get("pattern") or arguments.get("query")),
                ("path", arguments.get("path") or arguments.get("directory")),
            ],
            max_len=max_len,
        )
    if tool_name == "ask_human":
        return _join_fields(
            [
                ("question", _compact_text(arguments.get("question"), 180)),
                ("suggestions", _summarize_sequence(arguments.get("suggestions"), 3)),
            ],
            max_len=max_len,
        )
    if tool_name == "finish_task":
        return _join_fields([("summary", arguments.get("summary") or "请求 runtime 校验输出")], max_len=max_len)
    if tool_name == "log_scout_progress":
        return _join_fields(
            [
                ("action", arguments.get("action")),
                ("query", arguments.get("query")),
                ("count", arguments.get("count")),
                ("source", arguments.get("source")),
                ("detail", arguments.get("detail")),
            ],
            max_len=max_len,
        )
    if tool_name == "log_t4_ideation_progress":
        return _join_fields(
            [
                ("phase", arguments.get("phase")),
                ("status", arguments.get("status")),
                ("candidate", arguments.get("candidate_id")),
                ("channel", arguments.get("channel")),
                ("count", _t4_count_text(arguments)),
            ],
            max_len=max_len,
        )
    if tool_name == "save_paper_note":
        return _join_fields(
            [
                ("paper_id", arguments.get("paper_id")),
                ("title", arguments.get("title")),
                ("note_type", arguments.get("note_type")),
            ],
            max_len=max_len,
        )
    if tool_name in {"process_seed_paper", "upload_seed_pdf"}:
        return _join_fields(
            [
                ("path", arguments.get("path") or arguments.get("file_path")),
                ("title_hint", arguments.get("title") or arguments.get("title_hint")),
            ],
            max_len=max_len,
        )
    important_keys = (
        "path",
        "paper_id",
        "work_id",
        "doi",
        "title",
        "command",
        "action",
        "source",
        "query",
        "mode",
        "section_id",
    )
    fields = [(key, arguments.get(key)) for key in important_keys if key in arguments]
    if fields:
        return _join_fields(fields, max_len=max_len)
    return f"参数 {len(arguments)} 项（详细参数已写入 trace，不在 CLI 展开）"


def summarize_tool_result(
    *,
    tool_name: str,
    ok: bool,
    content: str | None,
    data: dict[str, Any] | None,
    error: str | None,
    metadata: dict[str, Any] | None = None,
    verbose: bool = False,
) -> tuple[str, str | None]:
    data = data if isinstance(data, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    if not ok:
        outcome = classify_tool_outcome(ok=ok, data=data, error=error)
        if outcome.status == "SKIPPED":
            path = _extract_output_path(tool_name, data)
            label = str(data.get("optional_input_label") or path or "可选输入")
            return f"可选输入未提供：{label}；系统将继续使用其余已验证材料。", path
        if outcome.status == "DEGRADED":
            source = str(data.get("source") or tool_name)
            attempts = data.get("attempts")
            attempt_text = f"（已尝试 {attempts} 次）" if attempts else ""
            if str(data.get("failure_class") or error or "") == "rate_limited":
                return f"{source} 暂时触发速率限制{attempt_text}；其他可用来源继续。", _extract_output_path(tool_name, data)
            return f"{source} 暂时不可用{attempt_text}；其他可用来源继续，后续可恢复重试。", _extract_output_path(tool_name, data)
        if tool_name == "save_paper_note" and data:
            progress = str(data.get("progress") or "").strip()
            summary = summarize_reader_note_progress(data, progress=progress)
            detail = _compact_text(error or content or "工具返回失败", 180)
            return f"{summary}；问题：{detail}", _extract_output_path(tool_name, data)
        return _compact_text(error or content or "工具返回失败", 280), _extract_output_path(tool_name, data)

    if tool_name in SEARCH_TOOL_NAMES:
        papers = data.get("papers")
        reported = len(papers) if isinstance(papers, list) else data.get("count") or data.get("total") or 0
        auto = metadata.get("auto_persist_raw")
        if isinstance(auto, dict):
            persisted = auto.get("count", 0)
            merged = auto.get("merged_count", 0)
            raw_after = auto.get("raw_count_after")
            summary = f"返回 {reported} 条候选，新增落盘 {persisted} 条，合并重复 {merged} 条"
            if raw_after is not None:
                summary += f"，papers_raw 当前 {raw_after} 条"
            return summary, "literature/papers_raw.jsonl"
        return f"返回 {reported} 条候选", None

    if tool_name == "read_file":
        path = _extract_output_path(tool_name, data)
        size = data.get("size")
        offset = data.get("offset")
        max_chars = data.get("max_chars")
        truncated = bool(data.get("truncated"))
        if isinstance(size, int) and isinstance(offset, int) and isinstance(max_chars, int):
            end = min(offset + max_chars, size)
            if truncated:
                return f"已读取字符区间 {offset}:{end} / {size}；如需核验更多内容可继续按 offset 定向读取", path
            return f"已读取完整文件，约 {size} 字符", path
        return _compact_text(content or "读取完成", 240 if verbose else 160), path

    if tool_name in {"list_files", "glob_files", "grep_search"}:
        count = _first_present(data, "count", "matched_count", "file_count", "item_count")
        path = _extract_output_path(tool_name, data)
        if count is not None:
            return f"读取/匹配到 {count} 项", path
        return _compact_text(content or "读取完成", 240 if verbose else 160), path

    if tool_name in {"write_file", "write_structured_file", "append_file"}:
        path = _extract_output_path(tool_name, data)
        byte_count = _first_present(data, "bytes", "size", "written_bytes")
        summary = "文件写入完成"
        if byte_count is not None:
            summary += f"，约 {byte_count} bytes"
        return summary, path

    if tool_name == "log_scout_progress":
        if data.get("skipped"):
            reason = _compact_text(str(data.get("reason") or "缺少必要字段"), 160)
            return f"Scout 进度记录已跳过：{reason}", "literature/temp/scout_progress.md"
        bullets = summarize_progress_text(content or "", max_items=3)
        summary = "Scout 进度已记录"
        if bullets:
            summary += "：" + "；".join(bullets)
        return summary, "literature/temp/scout_progress.md"

    if tool_name == "log_t4_ideation_progress":
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        return _format_t4_execution_event(event), "ideation/t4_execution_events.jsonl"

    if tool_name == "save_paper_note":
        progress = str(data.get("progress") or "").strip()
        path = _extract_output_path(tool_name, data) or data.get("note_path")
        return summarize_reader_note_progress(data, progress=progress), _string_or_none(path)

    if tool_name == "finish_task":
        return "agent 请求进入输出校验；runtime 正在检查声明产物和 schema", None

    if tool_name == "ask_human":
        return "已获得用户输入", _extract_output_path(tool_name, data)

    path = _extract_output_path(tool_name, data)
    counts = _summarize_counts(data)
    if counts:
        return counts, path
    return _compact_text(content or "工具执行完成", 240 if verbose else 180), path


def summarize_progress_markdown(path: Path, *, max_items: int = 4) -> list[str]:
    if not path.exists():
        return []
    try:
        return summarize_progress_text(path.read_text(encoding="utf-8", errors="replace"), max_items=max_items)
    except OSError:
        return []


def summarize_progress_text(text: str, *, max_items: int = 4) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    bullets: list[str] = []
    pattern = re.compile(r"^\[[^\]]+\]\s+\*\*(?P<step>[^*]+)\*\*:?\s*(?P<detail>.*)$")
    for line in reversed(lines):
        match = pattern.match(line)
        if match:
            step = match.group("step").strip()
            detail = match.group("detail").strip()
            bullets.append(_compact_text(f"{step}: {detail}" if detail else step, 180))
        elif line.startswith("- ") or line.startswith("* "):
            bullets.append(_compact_text(line[2:], 180))
        elif ":" in line and len(line) < 220:
            bullets.append(_compact_text(line, 180))
        if len(bullets) >= max_items:
            break
    return list(reversed(bullets))


def summarize_reader_note_progress(data: dict[str, Any], *, progress: str | None = None) -> str:
    """Build the compact T3 note summary shown in the CLI."""

    entry = data.get("manifest_entry") if isinstance(data.get("manifest_entry"), dict) else {}
    title = _first_present(data, "paper_title", "title") or entry.get("title")
    title = _compact_text(title, 86)
    rank = _first_present(data, "original_queue_rank", "queue_rank")
    paper_label = title or str(_first_present(data, "resolved_paper_id", "record_display_key") or "").strip()
    if paper_label and rank not in (None, ""):
        paper_label = f"#{rank} {paper_label}"

    status = _first_present(data, "note_status", "read_status")
    if not status:
        raw_status = str(data.get("status") or "").strip()
        if raw_status == "already_complete":
            status = "已完成"
        elif raw_status:
            status = raw_status
    status = _format_note_status(status)

    venue_bits = []
    year = _string_or_none(_first_present(data, "paper_year", "year") or entry.get("year"))
    venue = _string_or_none(_first_present(data, "paper_venue", "venue") or entry.get("venue"))
    if year:
        venue_bits.append(year)
    if venue:
        venue_bits.append(_compact_text(venue, 36))
    venue_text = "，".join(venue_bits)

    saved_status = str(data.get("status") or "").strip()
    if saved_status == "already_complete":
        head = "论文阅读笔记已存在且合格"
    elif saved_status == "incomplete":
        head = "论文阅读笔记已保存但需修补"
    else:
        head = "论文阅读笔记已保存"
    if paper_label:
        head += f"：{paper_label}"

    pieces = [head]
    if venue_text:
        pieces.append(venue_text)
    if status:
        pieces.append(f"状态 {status}")
    progress_label = _format_t3_progress(progress or str(data.get("progress") or ""))
    if progress_label:
        pieces.append(progress_label)
    return "；".join(pieces)


def safe_relative(path: Path | str | None, workspace_dir: Path | None) -> str | None:
    if path is None:
        return None
    try:
        p = Path(path)
    except TypeError:
        return None
    if workspace_dir is not None:
        try:
            return p.resolve().relative_to(workspace_dir.resolve()).as_posix()
        except Exception:
            pass
    return str(path)


def _tool_purpose(task_id: str, agent: str, tool_name: str) -> str:
    if tool_name in SEARCH_TOOL_NAMES:
        return "扩展当前主题的候选文献，并验证相关论文是否真实存在"
    if tool_name in {"read_file", "list_files", "glob_files", "grep_search"}:
        return "读取当前阶段需要的上游材料、已有产物或恢复状态"
    if tool_name in {"write_file", "write_structured_file", "append_file"}:
        return "把当前阶段的分析结果持久化，供校验和下游阶段使用"
    if tool_name == "log_scout_progress":
        return "把 Scout 的阶段性进展写入进度文件，并同步给正在观察运行的用户"
    if tool_name == "log_t4_ideation_progress":
        return "记录 T4 候选/补充通道/接地评分的公开执行里程碑，并同步给正在观察运行的用户"
    if tool_name == "finish_task":
        return "触发 runtime 输出校验，确认声明产物是否已经完整可用"
    if tool_name == "ask_human":
        return "让用户确认关键选择，避免系统在目标、参数或执行方式上自行假设"
    if tool_name == "save_paper_note":
        return "保存论文阅读证据，供 synthesis、引用和后续 claim 审计使用"
    if tool_name in {"expand_queries", "deduplicate_papers", "score_papers", "filter_by_domain"}:
        return "整理候选文献池，使检索结果能进入可比较、可筛选的状态"
    if tool_name in {"enrich_papers", "backfill_paper_abstracts", "build_deep_read_queue"}:
        return "补全文献元数据并构建后续阅读队列"
    if tool_name in {"build_synthesis_workbench", "build_survey_state", "assemble_survey"}:
        return "把已读证据组织成可写作、可审计的综述材料"
    if tool_name in {"build_experiment_handoff_pack", "select_external_executor", "wait_for_external_executor_result"}:
        return "准备或推进外部实验执行链路"
    if tool_name in {"ingest_external_results", "audit_experiment_integrity", "map_results_to_claims"}:
        return "摄取、审计并映射实验结果到论文 claim"
    if tool_name in {"latex_compile", "docker_exec"}:
        return "验证论文或实验环境是否能真实运行"
    return f"推进 {task_id} 中 {agent} Agent 的当前子任务"


def _tool_expected_output(tool_name: str) -> str:
    if tool_name in SEARCH_TOOL_NAMES:
        return "论文标题、摘要/元数据、年份、来源链接和可落盘的候选记录"
    if tool_name == "read_file":
        return "目标文件的必要内容摘要，供 agent 判断下一步"
    if tool_name in {"list_files", "glob_files"}:
        return "目录中的候选文件列表，用于定位输入和已有产物"
    if tool_name == "grep_search":
        return "匹配位置和片段，用于快速定位代码或文档中的关键信息"
    if tool_name in {"write_file", "write_structured_file", "append_file"}:
        return "写入后的 artifact 路径和基本大小信息"
    if tool_name == "log_scout_progress":
        return "新增 progress markdown 条目和简洁进度摘要"
    if tool_name == "log_t4_ideation_progress":
        return "一条已持久化的 T4 执行事件及对应候选/通道摘要"
    if tool_name == "finish_task":
        return "校验结果、缺失产物或任务完成状态"
    if tool_name == "ask_human":
        return "用户选择/回答，并写入 runtime 上下文"
    if tool_name == "save_paper_note":
        return "paper note 文件、notes manifest 更新和阅读进度"
    if tool_name == "latex_compile":
        return "LaTeX 编译状态、错误摘要和 PDF/日志路径"
    return "结构化工具结果"


def _tool_output_path(tool_name: str, arguments: dict[str, Any], workspace_dir: Path | None) -> str | None:
    if tool_name in {"write_file", "write_structured_file", "append_file", "read_file"}:
        return safe_relative(arguments.get("path"), workspace_dir)
    if tool_name in SEARCH_TOOL_NAMES:
        return "literature/papers_raw.jsonl"
    if tool_name == "log_scout_progress":
        return "literature/temp/scout_progress.md"
    if tool_name == "log_t4_ideation_progress":
        return "ideation/t4_execution_events.jsonl"
    if tool_name == "save_paper_note":
        return "literature/paper_notes/"
    if tool_name == "generate_search_log":
        return "literature/search_log.md"
    return None


def _extract_output_path(tool_name: str, data: dict[str, Any]) -> str | None:
    for key in ("path", "output_path", "file", "log_path", "note_path", "manifest_path"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    paths = data.get("paths")
    if isinstance(paths, dict):
        short = [str(value) for value in paths.values() if isinstance(value, str) and value.strip()]
        if short:
            return ", ".join(short[:3]) + (f", ...(+{len(short) - 3})" if len(short) > 3 else "")
    if tool_name in SEARCH_TOOL_NAMES:
        return "literature/papers_raw.jsonl"
    return None


def _summarize_counts(data: dict[str, Any]) -> str:
    interesting = []
    for key in (
        "count",
        "total",
        "raw_count",
        "dedup_count",
        "backlog_count",
        "active_count",
        "notes_generated",
        "candidate_count",
        "kept_count",
        "filtered_count",
        "failed",
    ):
        if key in data and isinstance(data.get(key), (int, float, str)):
            interesting.append(f"{key}={data.get(key)}")
    return "；".join(interesting[:8])


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)
    return None


def _join_fields(fields: list[tuple[str, Any]], *, max_len: int) -> str:
    parts = []
    for key, value in fields:
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple, set)):
            value_text = _summarize_sequence(value, 4)
        elif isinstance(value, dict):
            value_text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            value_text = str(value)
        parts.append(f"{key}={_compact_text(value_text, max_len)}")
    if not parts:
        return "无显式摘要字段；完整参数已写入 trace"
    return _compact_text("；".join(parts), max_len)


def _summarize_sequence(value: Any, max_items: int) -> str:
    if not isinstance(value, (list, tuple, set)):
        return _compact_text(value, 160)
    items = [str(item) for item in list(value)[:max_items]]
    suffix = f", ...(+{len(value) - max_items})" if len(value) > max_items else ""
    return "[" + ", ".join(items) + suffix + "]"


def _compact_text(value: Any, max_len: int = 200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        return text[: max(0, max_len - 3)] + "..."
    return text


def _format_note_status(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.strip("[] ")
    mapping = {
        "FULL-TEXT": "FULL-TEXT",
        "PARTIAL-TEXT": "PARTIAL-TEXT",
        "ABSTRACT-ONLY": "ABSTRACT-ONLY",
        "complete": "complete",
        "incomplete": "incomplete",
        "已完成": "已完成",
    }
    return mapping.get(text, _compact_text(text, 32))


def _format_t3_progress(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    target = re.match(r"^(?P<done>\d+)\s*/\s*(?P<total>\d+)\s+target notes complete$", text)
    if target:
        return f"精读 {target.group('done')}/{target.group('total')} 篇"
    queue = re.match(r"^(?P<done>\d+)\s*/\s*(?P<total>\d+)\s+queue notes complete$", text)
    if queue:
        return f"队列 {queue.group('done')}/{queue.group('total')} 篇"
    return _compact_text(text, 60)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
