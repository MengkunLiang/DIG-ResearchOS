from __future__ import annotations

"""Unified Stage Start / Progress / Summary rendering for research CLI runs."""

from dataclasses import dataclass
import io
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Callable

from rich import box
from rich.cells import cell_len
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .artifacts import ArtifactInfo, compare_artifact, inspect_artifact, relative_path, snapshot_artifacts
from .events import EventStore, ObservabilityEvent
from .extractors import extract_stage_insights
from .stage_catalog import artifact_consumers, artifact_meaning, stage_display_name, stage_profile


EmitFn = Callable[[str], None]


@dataclass
class _StageRun:
    workspace: Path
    task_id: str
    run_id: str
    input_snapshot: dict[str, ArtifactInfo]
    output_snapshot: dict[str, ArtifactInfo]
    actual_reads: set[str]
    source_health: dict[str, dict[str, Any]]


class StageReporter:
    """Render durable research facts while preserving a separate event stream."""

    def __init__(
        self,
        *,
        workspace: Path,
        runtime_dir_name: str = "_runtime",
        verbosity: str = "normal",
        quiet: bool = False,
        no_color: bool = False,
        json_events: bool = False,
        emit_fn: EmitFn | None = None,
        live_output: bool | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.runtime_dir_name = runtime_dir_name
        self.verbosity = verbosity if verbosity in {"concise", "normal", "detailed"} else "normal"
        self.quiet = bool(quiet)
        self.no_color = bool(no_color)
        self.json_events = bool(json_events)
        # The progress emitter normally supplies its print function so panels
        # and ordinary lines share one formatting path.  That must not disable
        # a transient status line in a real CLI, while tests/notebooks with a
        # custom emitter still need ordinary append-only messages.
        self._owns_terminal = (emit_fn is None) if live_output is None else bool(live_output)
        self._emit_fn = emit_fn or (lambda message: print(message, flush=True))
        self.store = EventStore(self.workspace, runtime_dir_name=runtime_dir_name)
        self._runs: dict[str, _StageRun] = {}
        self._live_line_active = False
        # In a real terminal, heartbeats redraw one line. Plain/no-colour
        # output cannot redraw, so retain a small public-display cache and do
        # not turn concurrent LLM calls into repeated identical log lines.
        # Events remain complete in the JSONL audit stream.
        self._append_only_heartbeat_state: dict[tuple[str, str, str, str, str], dict[str, int | bool]] = {}

    @property
    def detailed(self) -> bool:
        return self.verbosity == "detailed"

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
        input_snapshot = {
            key: inspect_artifact(self.workspace, path, required=key in required_input_keys)
            for key, path in inputs.items()
        }
        output_snapshot = snapshot_artifacts(self.workspace, outputs.values())
        run = _StageRun(
            workspace=self.workspace,
            task_id=task_id,
            run_id=run_id,
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            actual_reads=set(),
            source_health={},
        )
        self._runs[run_id] = run
        profile = stage_profile(task_id)
        self._event(
            "stage_started",
            task_id=task_id,
            run_id=run_id,
            payload={
                "agent": agent,
                "mode": mode,
                "is_resume": is_resume,
                "declared_inputs": {key: value.to_dict() for key, value in input_snapshot.items()},
                "expected_outputs": {key: relative_path(self.workspace, value) for key, value in outputs.items()},
            },
        )
        if self.quiet:
            self._plain(f"[Stage] {stage_display_name(task_id)} · {profile.goal}")
            return
        renderables: list[Any] = [
            Text(profile.goal, style="bold"),
            Text(f"研究问题：{profile.research_question}"),
            Text("本步会做：" + " → ".join(_humanize_runtime_label(item) for item in profile.operations)),
        ]
        if profile.branch_note:
            renderables.append(Text(profile.branch_note, style="yellow"))
        if input_snapshot:
            renderables.append(Text(_stage_input_overview(input_snapshot, required_input_keys)))
        if outputs:
            renderables.append(Text(_stage_output_overview(task_id, outputs)))
        if self.detailed:
            if input_snapshot:
                renderables.append(self._artifact_table("输入文件", input_snapshot, include_consumers=False))
            if outputs:
                renderables.append(self._expected_output_table(outputs))
        self._render(Panel(Group(*renderables), title=stage_display_name(task_id), border_style=self._stage_accent(task_id), expand=False))
        if is_resume:
            prior = self.store.recent_for_task(task_id=task_id, limit=8)
            prior_completed = any(event.get("event_type") == "stage_completed" for event in prior)
            detail = "已存在已完成事件；本次只展示当前待完成工作。" if prior_completed else "未发现可复用的已完成事件。"
            self._plain(f"恢复摘要：{detail}")

    def observe_tool_call(
        self,
        *,
        task_id: str,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        path = (
            arguments.get("path")
            or arguments.get("pdf_path")
            or arguments.get("directory")
            or arguments.get("root")
        )
        if tool_name in {"read_file", "list_files", "glob_files", "grep_search", "extract_paper_sections", "extract_pdf_text"} and isinstance(path, str):
            rel = _safe_workspace_relative(self.workspace, path)
            if rel:
                run.actual_reads.add(rel)
                self._event(
                    "inputs_loaded",
                    task_id=task_id,
                    run_id=run_id,
                    payload={"tool": tool_name, "path": rel, "access_kind": "actual_read"},
                )
        if tool_name == "latex_compile":
            tex_path = str(arguments.get("tex_path") or "").strip()
            engine = str(arguments.get("engine") or "pdflatex").strip()
            backend = str(arguments.get("backend") or "auto").strip()
            self._event(
                "calculation_started",
                task_id=task_id,
                run_id=run_id,
                payload={"tool": tool_name, "tex_path": tex_path, "engine": engine, "backend": backend},
            )
            if self.quiet:
                self._plain(f"[编译] {task_id} · {tex_path or 'LaTeX 源文件'} · {engine} · 后端={backend}")
            elif self.verbosity != "concise":
                rows = [
                    ("源文件", tex_path or "未声明"),
                    ("编译引擎", engine),
                    ("编译后端", backend),
                    ("说明", "真实编译已开始；语法错误会快速停止，完整日志会写入工作区。"),
                ]
                self._render(Panel(self._rows_table(rows), title=f"{stage_display_name(task_id)} · 正在真实编译 LaTeX", border_style="yellow", expand=False))

    def observe_tool_result(
        self,
        *,
        task_id: str,
        run_id: str,
        tool_name: str,
        ok: bool,
        data: dict[str, Any] | None,
        error: str | None,
    ) -> bool:
        """Record a semantic progress event; return whether it was rendered."""

        payload = data if isinstance(data, dict) else {}
        # Shell output is useful to the Agent and trace, but a successful
        # command panel is implementation noise in normal researcher-facing
        # output. ``render_tool_result`` still provides a compact detailed
        # trace when requested, and failures retain their actionable summary.
        if ok and tool_name in {"bash_run", "docker_exec"} and self.verbosity != "detailed":
            return False
        run = self._runs.get(run_id)
        if run is not None and task_id == "T2" and _is_retrieval_tool(tool_name):
            source = str(payload.get("source") or tool_name.removesuffix("_search")).replace("_", " ")
            disposition = _tool_disposition(ok=ok, data=payload, error=error, tool_name=tool_name)
            previous = run.source_health.get(source, {})
            run.source_health[source] = {
                "source": source,
                "disposition": disposition,
                "attempts": payload.get("attempts") or previous.get("attempts") or 1,
                "records": len(payload.get("papers") or []) if isinstance(payload.get("papers"), list) else previous.get("records"),
                "failure_class": payload.get("failure_class"),
            }
        if not ok:
            disposition = _tool_disposition(ok=ok, data=payload, error=error, tool_name=tool_name)
            if _is_exploratory_probe_miss(tool_name, error=error, data=payload):
                self._event(
                    "exploratory_probe_miss",
                    task_id=task_id,
                    run_id=run_id,
                    severity="info",
                    payload={"tool": tool_name, "error": error or payload.get("failure_class")},
                )
                return False
            if disposition in {"AUTO_REPAIR", "AUTO_FALLBACK"}:
                self._event(
                    "repair_in_progress" if disposition == "AUTO_REPAIR" else "fallback_applied",
                    task_id=task_id,
                    run_id=run_id,
                    severity="info",
                    payload={
                        "tool": tool_name,
                        "repair_scope": payload.get("repair_scope") if disposition == "AUTO_REPAIR" else payload.get("fallback_action"),
                        "path": payload.get("path"),
                    },
                )
                return False
            self._event(
                "warning_emitted",
                task_id=task_id,
                run_id=run_id,
                severity="error" if disposition == "FAILED" else "warning",
                payload={
                    "tool": tool_name,
                    "error": error or "tool_failed",
                    "disposition": disposition,
                    "failure_class": payload.get("failure_class"),
                    "fallback_available": payload.get("fallback_available"),
                },
            )
            return False
        summary = _tool_calculation_summary(task_id, tool_name, payload)
        if summary is None:
            return False
        event_type = "ranking_generated" if tool_name in {"score_papers", "build_deep_read_queue", "analyze_idea_concentration"} else "calculation_summary"
        self._event(event_type, task_id=task_id, run_id=run_id, payload={"tool": tool_name, **summary})
        if self.quiet or self.verbosity == "concise":
            return False
        title = str(summary.get("title") or tool_name)
        rows = [(str(key), str(value)) for key, value in summary.get("rows", [])]
        if not rows:
            return False
        self._render(Panel(self._rows_table(rows), title=title, border_style="blue", expand=False))
        return True

    def render_agent_markdown(
        self,
        *,
        task_id: str,
        run_id: str,
        agent: str,
        content: str,
        human_action_context: bool,
    ) -> None:
        """Render user-visible Agent Markdown without exposing runtime payloads."""

        cleaned = normalize_cli_markdown(content)
        if not cleaned:
            return
        self._event(
            "agent_update_rendered",
            task_id=task_id,
            run_id=run_id,
            payload={"agent": agent, "human_action_context": human_action_context, "chars": len(cleaned)},
        )
        if self.quiet:
            summary = " ".join(cleaned.split())
            self._plain(f"[运行说明] {agent} · {summary}")
            return
        title = f"{stage_display_name(task_id)} · {'需要你决定' if human_action_context else '运行说明'}"
        self._render(
            Panel(
                Markdown(cleaned, code_theme="ansi_dark"),
                title=title,
                border_style="yellow" if human_action_context else self._stage_accent(task_id),
                expand=False,
            )
        )

    def render_tool_call(
        self,
        *,
        task_id: str,
        run_id: str,
        agent: str,
        tool_name: str,
        purpose: str,
        input_summary: str,
        output_path: str | None,
    ) -> None:
        """Render a compact colored tool-start trace at a useful density."""

        if self.quiet or not self.detailed:
            return
        style = _tool_style(tool_name, ok=None)
        segments = [
            ("正在执行：", f"bold {style}"),
            (_tool_label(tool_name), f"bold {style}"),
            (f" · {purpose}", "dim"),
        ]
        if output_path:
            segments.append((f" · 文件：{output_path}", "dim"))
        if self.detailed and input_summary:
            segments.append((f" · 输入：{input_summary}", "dim"))
        self._render(Text.assemble(*segments))

    def render_tool_result(
        self,
        *,
        task_id: str,
        run_id: str,
        tool_name: str,
        ok: bool,
        summary: str,
        output_path: str | None,
        data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Render a colored bounded tool outcome when no richer metric panel exists."""

        if self.quiet:
            return
        if self.verbosity != "detailed" and ok and tool_name in {"read_file", "list_files", "glob_files", "grep_search"}:
            return
        if _is_exploratory_probe_miss(tool_name, error=error, data=data) and self.verbosity != "detailed":
            return
        status = _tool_disposition(ok=ok, data=data, error=error, tool_name=tool_name)
        if status in {"SKIPPED", "AUTO_REPAIR", "AUTO_FALLBACK", "DEGRADED"} and self.verbosity != "detailed":
            return
        style = _tool_style(tool_name, ok=ok, disposition=status)
        text = _compact_cli_text(summary, 220)
        status_label = {
            "DONE": "已完成",
            "SKIPPED": "已跳过",
            "AUTO_REPAIR": "正在自动修补",
            "AUTO_FALLBACK": "已自动降级",
            "DEGRADED": "已降级继续",
            "EXPLORATORY_MISS": "探索性检查未命中，已继续",
            "FAILED": "未完成",
        }.get(status, status)
        segments = [
            (
                "✓ "
                if status == "DONE"
                else ("· " if status in {"SKIPPED", "EXPLORATORY_MISS"} else ("◐ " if status in {"AUTO_REPAIR", "AUTO_FALLBACK", "DEGRADED"} else "! ")),
                f"bold {style}",
            ),
            (_tool_label(tool_name), "bold"),
            (f" · {status_label}", f"bold {style}"),
        ]
        if text:
            segments.append(
                (
                    f" · {text}",
                    "dim"
                    if status == "DONE"
                    else (
                        "cyan"
                        if status in {"AUTO_REPAIR", "AUTO_FALLBACK"}
                        else ("yellow" if status in {"SKIPPED", "DEGRADED"} else ("dim" if status == "EXPLORATORY_MISS" else "red"))
                    ),
                )
            )
        if output_path:
            segments.append((f" · 文件：{_compact_cli_text(output_path, 120)}", "dim"))
        self._render(Text.assemble(*segments))

    def stage_completed(
        self,
        *,
        task_id: str,
        run_id: str,
        outputs: dict[str, Path],
        ok: bool,
        summary: str,
        error: str | None,
    ) -> None:
        run = self._runs.get(run_id)
        before = run.output_snapshot if run is not None else {}
        output_infos: dict[str, ArtifactInfo] = {}
        statuses: dict[str, str] = {}
        for key, path in outputs.items():
            rel = relative_path(self.workspace, path)
            info = inspect_artifact(self.workspace, path, required=ok)
            output_infos[key] = info
            statuses[key] = compare_artifact(before.get(rel), info)
        insights = extract_stage_insights(task_id, self.workspace, detailed=self.detailed)
        unsupported = _count_unsupported(insights)
        for key, info in output_infos.items():
            disposition = statuses[key]
            if disposition in {"created", "updated", "reused"}:
                self._event(
                    f"artifact_{disposition}",
                    task_id=task_id,
                    run_id=run_id,
                    payload={"artifact_key": key, "artifact": info.to_dict(), "disposition": disposition},
                )
            elif disposition in {"missing", "invalid"}:
                self._event(
                    "warning_emitted",
                    task_id=task_id,
                    run_id=run_id,
                    severity="warning",
                    payload={
                        "artifact_key": key,
                        "artifact": info.to_dict(),
                        "reason": f"output_{disposition}",
                    },
                )
        for insight in insights:
            for left, right in insight.get("rows", []):
                if _is_unsupported_row(left, right):
                    self._event(
                        "unsupported_recorded",
                        task_id=task_id,
                        run_id=run_id,
                        severity="warning",
                        payload={
                            "source": str(insight.get("title") or "stage insight"),
                            "label": str(left),
                            "detail": str(right),
                        },
                    )
        event_type = "stage_completed" if ok else "stage_paused"
        event_path = self._event(
            event_type,
            task_id=task_id,
            run_id=run_id,
            severity="info" if ok else "warning",
            payload={
                "ok": ok,
                "summary": summary,
                "error": error,
                "actual_read_inputs": sorted(run.actual_reads) if run else [],
                "source_health": list(run.source_health.values()) if run else [],
                "outputs": {key: {**info.to_dict(), "disposition": statuses[key]} for key, info in output_infos.items()},
                "insight_count": len(insights),
                "unsupported_hint_count": unsupported,
            },
        )
        if self.quiet:
            self._plain(f"[步骤] {task_id} {'完成' if ok else '暂停'} · 事件：{relative_path(self.workspace, event_path)}")
            return
        if task_id.startswith("T3.6") and ok and not self.detailed:
            # Survey section, assembly, and feed receipts are intentionally
            # small. Rendering them through the generic completion Panel used
            # to nest a Survey summary Panel inside a second success Panel,
            # making a one-file update look like a new research decision.
            ready_paths = [
                relative_path(self.workspace, path)
                for key, path in outputs.items()
                if statuses.get(key) in {"created", "updated", "reused"}
            ]
            label = stage_display_name(task_id)
            receipt_label = label if label.startswith("T3.6") else f"T3.6 · {label}"
            if ready_paths:
                self._render(Text(f"✓ {receipt_label} 完成 · 已生成：{'、'.join(ready_paths[:2])}", style="green", overflow="fold"))
                remaining = len(ready_paths) - 2
                if remaining > 0:
                    self._render(Text(f"  另有 {remaining} 项结果已保存。", style="dim"))
            else:
                self._render(Text(f"✓ {receipt_label} 完成 · {summary or '结果已保存。'}", style="green", overflow="fold"))
            return
        renderables: list[Any] = [Text(summary if summary else "阶段执行已结束。")]
        if error:
            renderables.append(Text(f"当前问题：{_public_error_summary(error)}", style="bold red"))
        if insights and (ok or self.detailed):
            # Normal CLI output should answer "what happened" without turning a
            # completion event into a full audit report.  The durable files and
            # --verbose retain every distribution and artifact-level detail.
            # A paused run has not passed its task contract, so showing a rich
            # success-looking insight panel in normal UI would make staged files
            # look like a completed research decision.
            visible_insights = insights if self.detailed else insights[:1]
            for insight in visible_insights:
                renderables.append(self._insight_panel(insight))
        if task_id == "T4.5" and ok:
            file_guide = self._t45_file_guide()
            if file_guide is not None:
                renderables.append(file_guide)
        if task_id == "T2" and run and run.source_health and self.detailed:
            rows = []
            for item in sorted(run.source_health.values(), key=lambda row: str(row.get("source") or "")):
                detail = str(item.get("disposition") or "unknown")
                if item.get("records") is not None:
                    detail += f" · {item['records']} 条记录"
                if item.get("failure_class"):
                    detail += f" · {item['failure_class']}"
                rows.append((str(item.get("source") or "source"), detail))
            renderables.append(
                Panel(
                    self._rows_table(rows),
                    title="文献检索 · 来源状态",
                    border_style="yellow" if any(item.get("disposition") == "DEGRADED" for item in run.source_health.values()) else "cyan",
                    expand=False,
                )
            )
        if run and run.actual_reads and self.detailed:
            visible = sorted(run.actual_reads)[:10]
            more = f"；另有 {len(run.actual_reads) - len(visible)} 项" if len(run.actual_reads) > len(visible) else ""
            renderables.append(Text("实际读取：" + "、".join(visible) + more, style="dim"))
        if self.detailed:
            display_statuses = {
                key: (
                    "staged"
                    if not ok and status in {"created", "updated", "reused"}
                    else status
                )
                for key, status in statuses.items()
            }
            renderables.append(self._artifact_manifest(output_infos, display_statuses))
            renderables.append(Text(f"过程事件：{relative_path(self.workspace, event_path)}", style="dim"))
        else:
            written = sum(status in {"created", "updated", "reused"} for status in statuses.values())
            unavailable = sum(status in {"missing", "invalid", "optional_missing"} for status in statuses.values())
            if ok:
                result_text = f"结果：{written}/{len(output_infos)} 项文件已就绪。"
            else:
                result_text = (
                    f"暂停时已写入：{written}/{len(output_infos)} 项；"
                    "尚未通过本步骤完成校验，不能进入下游。"
                )
            if unavailable:
                result_text += f" {unavailable} 项可选文件尚未提供。"
            renderables.append(Text(result_text, style="dim"))
        title = f"{stage_display_name(task_id)} · {'本步骤完成' if ok else '本步骤暂停'}"
        self._render(Panel(Group(*renderables), title=title, border_style="green" if ok else "yellow", expand=False))

    def stage_invalidated(
        self,
        *,
        task_id: str,
        run_id: str,
        outputs: dict[str, Path],
        reason: str,
        log_path: str | None = None,
    ) -> None:
        """Record that the outer task contract invalidated a completed run.

        Agent-level output validation and state-machine validation intentionally
        happen at different layers.  The latter may reject a run which the
        agent already reported as complete.  This event makes that transition
        visible instead of leaving a contradictory success summary on screen.
        """

        output_infos = {
            key: inspect_artifact(self.workspace, path, required=False)
            for key, path in outputs.items()
        }
        event_path = self._event(
            "stage_invalidated",
            task_id=task_id,
            run_id=run_id,
            severity="warning",
            payload={
                "reason": reason,
                "outputs": {key: info.to_dict() for key, info in output_infos.items()},
                "log_path": log_path,
            },
        )
        if self.quiet:
            self._plain(f"[步骤] {task_id} 的独立文件校验未通过；事件：{relative_path(self.workspace, event_path)}")
            return
        details: list[Any] = [
            Text(
                "校验未通过：当前步骤已结束，但状态机的独立文件检查未通过；"
                "该阶段不能作为成功结果进入下游。",
                style="bold yellow",
            ),
            Text(f"原因：{_public_error_summary(reason)}", style="red"),
        ]
        if self.detailed:
            details.append(
                self._artifact_manifest(
                    output_infos,
                    {key: "invalidated" for key in output_infos},
                )
            )
            if log_path:
                details.append(Text(f"运行日志：{log_path}", style="dim"))
            details.append(Text(f"过程事件：{relative_path(self.workspace, event_path)}", style="dim"))
        else:
            details.append(Text(f"受影响结果：{len(output_infos)} 项。", style="dim"))
        self._render(Panel(Group(*details), title=f"{stage_display_name(task_id)} · 运行结果校验", border_style="red", expand=False))

    def human_action_required(self, *, task_id: str, run_id: str, gate_id: str, reason: str) -> None:
        self._event("human_action_required", task_id=task_id, run_id=run_id, severity="warning", payload={"gate_id": gate_id, "reason": reason})

    def gate_resolved(self, *, task_id: str, run_id: str, gate_id: str, decision: str) -> None:
        self._event("decision_made", task_id=task_id, run_id=run_id, payload={"gate_id": gate_id, "decision": decision})

    def render_runtime_heartbeat(
        self,
        *,
        task_id: str,
        run_id: str,
        step: int,
        elapsed_seconds: int | None,
        activity: str | None,
        next_artifact: str | None,
        artifact_completed: int | None,
        artifact_total: int | None,
        current_deliverable: str | None = None,
        following_phase: str | None = None,
        phase_elapsed_seconds: int | None = None,
    ) -> None:
        """Render one compact, replaceable public model-wait heartbeat.

        The event stream receives every pulse.  A real terminal receives one
        in-place line, so a slow provider does not bury a human decision in a
        wall of repeated messages.  The line describes only public runtime
        state, never hidden model reasoning.
        """

        pulses = ("◐", "◓", "◑", "◒")
        tick = 0 if elapsed_seconds is None else max(1, elapsed_seconds // 12)
        pulse = pulses[tick % len(pulses)]
        status = "模型请求已提交" if elapsed_seconds is None else "模型调用仍在执行"
        public_activity = activity or _default_public_activity(task_id)
        artifact_progress: str | None = None
        if artifact_completed is not None and artifact_total is not None:
            if task_id == "T4":
                artifact_progress = f"{artifact_completed}/{artifact_total} 个候选选择所需文件已写入"
            else:
                artifact_progress = f"已完成 {artifact_completed}/{artifact_total} 项"
        self._event(
            "llm_heartbeat",
            task_id=task_id,
            run_id=run_id,
            payload={
                "step": step,
                "elapsed_seconds": elapsed_seconds,
                "phase_elapsed_seconds": phase_elapsed_seconds,
                "status": status,
                "activity": public_activity,
                "next_artifact": next_artifact or "未提供",
                "current_deliverable": current_deliverable or next_artifact or "未提供",
                "following_phase": following_phase or "",
                "artifact_progress": artifact_progress,
            },
        )
        if self.verbosity == "concise":
            return
        if not self._should_render_append_only_heartbeat(
            task_id=task_id,
            run_id=run_id,
            activity=public_activity,
            next_artifact=next_artifact,
            artifact_progress=artifact_progress,
            elapsed_seconds=elapsed_seconds,
        ):
            return
        # Keep full public activity in the durable event stream.  The terminal
        # heartbeat is a progress pulse, not a second phase description; use a
        # semantic T4 label so a narrow terminal never wraps a long bilingual
        # phase name into a visually broken pseudo-progress line.
        display_activity = _terminal_heartbeat_activity(task_id, public_activity)
        required_parts = [f"[{stage_display_name(task_id)}] {pulse}", status, display_activity]
        optional_parts: list[str] = []
        if elapsed_seconds is not None:
            required_parts.insert(2, f"本次调用已等待 {elapsed_seconds}s")
        if artifact_progress:
            optional_parts.append(artifact_progress)
        deliverable = current_deliverable or next_artifact
        if deliverable:
            label = "本次产出" if task_id == "T4" else "下一步"
            optional_parts.append(f"{label}：{deliverable}")
        if following_phase and task_id == "T4":
            optional_parts.append(f"完成后：{following_phase}")
        self._render_live_line(_fit_live_status(required_parts, optional_parts))

    def _should_render_append_only_heartbeat(
        self,
        *,
        task_id: str,
        run_id: str,
        activity: str,
        next_artifact: str | None,
        artifact_progress: str | None,
        elapsed_seconds: int | None,
    ) -> bool:
        """Throttle append-only display while retaining every audit event.

        T4 can run two routes concurrently. Their provider heartbeats have the
        same public meaning, but previously printed twice in no-colour mode and
        in captured CLI logs. Show the initial submission, the first visible
        wait, each changed public status, and then at most once per minute for
        an unchanged status.
        """

        if self._owns_terminal and not self.no_color and sys.stdout.isatty():
            return True
        signature = (
            task_id,
            run_id,
            " ".join(activity.split()),
            " ".join(str(next_artifact or "").split()),
            " ".join(str(artifact_progress or "").split()),
        )
        state = self._append_only_heartbeat_state.setdefault(signature, {})
        if elapsed_seconds is None:
            if bool(state.get("request_emitted")):
                return False
            state["request_emitted"] = True
            return True
        elapsed = max(0, int(elapsed_seconds))
        if not bool(state.get("wait_emitted")):
            state["wait_emitted"] = True
            state["last_wait_elapsed"] = elapsed
            return True
        last_wait = int(state.get("last_wait_elapsed") or 0)
        if elapsed >= last_wait + 60:
            state["last_wait_elapsed"] = elapsed
            return True
        return False

    def render_t4_runtime_heartbeat(
        self,
        *,
        task_id: str,
        run_id: str,
        step: int,
        elapsed_seconds: int | None,
        activity: str | None,
        next_artifact: str | None,
        artifact_completed: int | None,
        artifact_total: int | None,
        current_deliverable: str | None = None,
        following_phase: str | None = None,
        phase_elapsed_seconds: int | None = None,
    ) -> None:
        """Compatibility entry point for the richer T4 public milestone."""

        self.render_runtime_heartbeat(
            task_id=task_id,
            run_id=run_id,
            step=step,
            elapsed_seconds=elapsed_seconds,
            phase_elapsed_seconds=phase_elapsed_seconds,
            activity=activity,
            next_artifact=next_artifact,
            artifact_completed=artifact_completed,
            artifact_total=artifact_total,
            current_deliverable=current_deliverable,
            following_phase=following_phase,
        )

    def _event(
        self,
        event_type: str,
        *,
        task_id: str,
        run_id: str,
        payload: dict[str, Any],
        severity: str = "info",
    ) -> Path:
        event = ObservabilityEvent(event_type=event_type, task_id=task_id, run_id=run_id, payload=payload, severity=severity)
        path = self.store.append(event)
        if self.json_events:
            # Explicitly requested machine mode: emit one JSON object per event.
            # Interactive users should keep this disabled so human gates remain
            # visually separated from the durable event mirror.
            import json

            self._emit_fn(json.dumps(event.to_dict(), ensure_ascii=False, default=str))
        return path

    def _artifact_table(self, title: str, values: dict[str, ArtifactInfo], *, include_consumers: bool) -> Table:
        table = Table(title=title, box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan", expand=False)
        table.add_column("文件", max_width=34, overflow="fold")
        table.add_column("用途", max_width=38, overflow="fold")
        table.add_column("状态", max_width=16)
        table.add_column("记录数 / 大小", max_width=22, overflow="fold")
        if include_consumers:
            table.add_column("后续使用", max_width=20, overflow="fold")
        if not values:
            row = ["-", "本步骤没有声明上游输入文件。", "不适用", "-"]
            if include_consumers:
                row.append("-")
            table.add_row(*row)
            return table
        for key, info in values.items():
            measure = info.detail or _size_label(info.size_bytes)
            row = [f"{key}\n{info.path}", artifact_meaning(info.path), _artifact_status_label(info.status), measure]
            if include_consumers:
                row.append(artifact_consumers("", info.path))
            table.add_row(*row)
        return table

    def _expected_output_table(self, outputs: dict[str, Path]) -> Table:
        values = {
            key: ArtifactInfo(path=relative_path(self.workspace, path), status="planned", kind="planned")
            for key, path in outputs.items()
        }
        return self._artifact_table("完成后会生成", values, include_consumers=True)

    def _artifact_manifest(self, values: dict[str, ArtifactInfo], statuses: dict[str, str]) -> Table:
        table = Table(title="本次生成文件", box=box.SIMPLE_HEAVY, show_header=True, header_style="bold green", expand=False)
        table.add_column("文件", max_width=34, overflow="fold")
        table.add_column("用途", max_width=38, overflow="fold")
        table.add_column("状态", max_width=16)
        table.add_column("记录数 / 大小", max_width=22, overflow="fold")
        table.add_column("后续使用", max_width=20, overflow="fold")
        for key, info in values.items():
            table.add_row(
                f"{key}\n{info.path}",
                artifact_meaning(info.path),
                _artifact_status_label(statuses.get(key, info.status)),
                info.detail or _size_label(info.size_bytes),
                artifact_consumers("", info.path),
            )
        return table

    def _insight_panel(self, insight: dict[str, Any]) -> Panel:
        rows = [(str(left), str(right)) for left, right in insight.get("rows", [])]
        group: list[Any] = [Text(str(insight.get("explanation") or ""), style="dim")]
        if rows:
            group.append(self._rows_table(rows))
        return Panel(Group(*group), title=str(insight.get("title") or "阶段统计"), border_style="magenta", expand=False)

    def _t45_file_guide(self) -> Panel | None:
        """Point researchers to formal T4.5 outputs before T5 consumes them."""

        candidates = (
            (
                "完整研究方案",
                "ideation/proposal/research_proposal.md",
                "问题、机制、贡献、现实意义、风险和完整实验逻辑。",
                "优先阅读；T5 会把它作为执行边界。",
            ),
            (
                "正式假设",
                "ideation/hypotheses.md",
                "核心假设、可观察预测和可证伪边界。",
                "核对主张；T5 会保留其验证约束。",
            ),
            (
                "实验计划",
                "ideation/exp_plan.yaml",
                "数据/benchmark、指标、baseline、实验设置与停止条件。",
                "补充协议设置；T5 据此授权真实实验。",
            ),
            (
                "贡献-假设映射",
                "ideation/contribution_hypothesis_map.yaml",
                "每项拟议贡献由哪条假设支撑。",
                "T5/T8 用于主张对齐。",
            ),
            (
                "验证映射",
                "ideation/validation_map.yaml",
                "每条假设应由哪些观察和实验验证。",
                "T5 用于实验设计。",
            ),
            (
                "停止条件",
                "ideation/kill_criteria.yaml",
                "哪些结果要求收缩、修正或放弃主张。",
                "T5 用于实验诊断和风险控制。",
            ),
            (
                "新颖性审计",
                "ideation/novelty_audit.md",
                "相似工作、机制差异、必需 baseline 与审计结论。",
                "T5/T8 用于比较边界和 Related Work。",
            ),
            (
                "方案追溯记录",
                "ideation/proposal_manifest.json",
                "proposal 的来源、审计状态与交接边界。",
                "恢复或追溯时查看。",
            ),
        )
        rows = [row for row in candidates if (self.workspace / row[1]).is_file()]
        if not rows:
            return None
        table = Table(
            expand=True,
            show_header=True,
            show_lines=True,
            box=box.SQUARE,
            header_style="bold bright_cyan",
            border_style="bright_cyan",
        )
        table.add_column("重点文件", width=17, overflow="fold")
        table.add_column("保存位置", width=42, overflow="fold")
        table.add_column("包含什么", ratio=2, overflow="fold")
        table.add_column("何时使用", ratio=2, overflow="fold")
        for label, path, contents, when in rows:
            table.add_row(label, Text(path, style="cyan", overflow="fold"), contents, when)
        note = Text(
            "这些文件均已在当前 workspace 保存；T5 会读取其中的正式研究约束，不会把 T4/T4.5 的关键信息丢失。",
            style="dim",
            overflow="fold",
        )
        return Panel(Group(table, note), title="T4.5 完成后 · 重点研究文件", border_style="bright_cyan", expand=True)

    def _rows_table(self, rows: list[tuple[str, str]]) -> Table:
        table = Table(box=box.SIMPLE, show_header=False, expand=False, pad_edge=False)
        table.add_column(max_width=42, overflow="fold")
        table.add_column(max_width=76, overflow="fold")
        for left, right in rows:
            table.add_row(left, right)
        return table

    def _render(self, renderable: Any) -> None:
        self._end_live_line()
        terminal_width = shutil.get_terminal_size(fallback=(120, 40)).columns
        width = max(88, min(160, terminal_width))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self.no_color,
            color_system=None if self.no_color else "truecolor",
            width=width,
            no_color=self.no_color,
            highlight=False,
            # Rich otherwise re-queries the controlling pseudo-terminal when
            # color is forced and can silently fall back to 80 columns.
            _environ={"COLUMNS": str(width), "LINES": "40"},
        )
        console.print(renderable)
        text = buffer.getvalue().rstrip()
        if text:
            self._emit_fn(text)

    @staticmethod
    def _stage_accent(task_id: str) -> str:
        """Use stable stage accents so panels remain scannable in long runs."""

        if task_id.startswith("T1"):
            return "cyan"
        if task_id.startswith("T2"):
            return "blue"
        if task_id.startswith("T3.5") or task_id.startswith("T4"):
            return "magenta"
        if task_id.startswith("T3"):
            return "green"
        if task_id.startswith("T5"):
            return "yellow"
        if task_id.startswith("T8"):
            return "bright_cyan"
        if task_id.startswith("T9"):
            return "bright_green"
        return "cyan"

    def _plain(self, message: str) -> None:
        self._end_live_line()
        self._emit_fn(message)

    def _render_live_line(self, line: str) -> None:
        """Update one terminal line while preserving event-level audit history.

        Custom emitters used by tests, notebooks, and programmatic callers do
        not expose a writable TTY, so they receive a compact ordinary line. A
        real CLI owns stdout and can refresh the same line without growing the
        terminal transcript.
        """

        # ``--no-color`` is also the portable/log-friendly mode.  Never leak
        # cursor controls into a redirected stream, a test capture, notebook,
        # or an ANSI-free terminal just to keep a heartbeat on one line.
        if not self._owns_terminal or self.no_color or not sys.stdout.isatty():
            self._emit_fn(line)
            return
        sys.stdout.write("\r\033[2K" + line)
        sys.stdout.flush()
        self._live_line_active = True

    def finish_live_status(self) -> None:
        """Finish a transient status line before a durable terminal message."""

        self._end_live_line()

    def _end_live_line(self) -> None:
        if self._live_line_active and self._owns_terminal:
            sys.stdout.write("\n")
            sys.stdout.flush()
        self._live_line_active = False


_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_KEYCAP_RE = re.compile(r"([0-9])(?:\ufe0f)?\s*\u20e3")


def normalize_cli_markdown(content: str) -> str:
    """Make externally generated Markdown safe and readable in a terminal.

    Agent prose is intentionally allowed to use Markdown, but terminal fonts
    often render keycap emoji (``1️⃣``) as separated symbols such as
    ``1️ ⃣``.  Convert only that presentation form to ordinary ordered-list
    syntax.  ANSI controls belong to the raw trace, never to rendered agent
    prose, so strip them here as a second defensive boundary.
    """

    text = str(content or "")
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _KEYCAP_RE.sub(r"\1.", text)
    # A malformed provider stream can leave a variation selector or enclosing
    # keycap behind after whitespace normalization.  These have no semantic
    # role in research prose, so remove only the isolated presentation marks.
    text = text.replace("\ufe0f", "").replace("\u20e3", "")
    text = re.sub(r"(?m)^(\s*#{1,6}\s+[0-9])\.\s*\.", r"\1.", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compact_cli_text(value: object, limit: int = 220) -> str:
    """Normalize a public message and end only on a natural textual boundary."""

    # ``0`` (a successful exit status) and ``False`` (for example an
    # untruncated command result) are informative values.  Treating either as
    # falsy produced empty rows in otherwise useful status cards.
    raw = "" if value is None else str(value)
    text = " ".join(normalize_cli_markdown(raw).split())
    if len(text) <= limit:
        return text
    boundaries = [match.end() for match in re.finditer(r"[。！？；;](?:\s|$)|\.\s", text) if match.end() <= limit]
    if boundaries:
        return text[: boundaries[-1]].strip()
    return "信息较长；完整记录可通过 --verbose、trace 或运行日志查看。"


def public_error_summary(value: object) -> str:
    """Keep recoverable runtime details actionable without dumping provider traces."""

    text = " ".join(str(value or "").split())
    lowered = text.casefold()
    if not text:
        return "操作未完成；请根据当前步骤补充材料或稍后恢复。"
    if any(token in lowered for token in ("timeout", "timed out", "rate limit", "rate_limited", "temporarily unavailable", "network", "http 5", "connection")):
        return "服务暂时不可用，当前进度已保存；可稍后恢复，或用 --verbose 查看连接诊断。"
    if any(token in lowered for token in ("access_denied", "permission denied", "not allowed", "unauthorized")):
        return "当前 Skill 没有读取或写入所需位置的权限；请检查材料位置和本次任务范围。"
    if "t4 本轮结构化输出不完整" in text or "t4 evolution 结果没有通过结构与证据边界校验" in lowered:
        return "T4 的本轮输出尚未完成，已保存可恢复的证据材料；恢复后会从未完成的候选生成步骤继续。"
    if "schema_validation_failed" in lowered or "schema" in lowered:
        return "结构化结果尚未通过检查；请根据字段提示补齐内容后重试。"
    if any(token in lowered for token in ("missing", "not found", "does not exist", "file not found")):
        return "所需文件尚未找到；请检查材料是否已放到本次任务提示的位置。"
    return "操作未完成；可用 --verbose、trace 或运行日志查看完整诊断。"


# Kept as an internal compatibility alias for callers that were introduced
# before the CLI began reusing the same public summary.
_public_error_summary = public_error_summary


def _fit_live_status(required_parts: list[str], optional_parts: list[str]) -> str:
    """Keep a heartbeat on one terminal line by dropping optional facts, never slicing prose."""

    width = max(40, shutil.get_terminal_size(fallback=(120, 40)).columns - 1)
    parts = [part for part in required_parts + optional_parts if part]
    while len(parts) > len(required_parts) and cell_len(" · ".join(parts)) > width:
        parts.pop()
    if cell_len(" · ".join(parts)) <= width:
        return " · ".join(parts)
    # Do not slice Chinese or English prose by character count.  At the
    # narrowest supported width, fall back to a compact, stable runtime pulse;
    # the event JSONL and phase-start panel retain the complete explanation.
    compact = [required_parts[0], "进行中", required_parts[-1]]
    if cell_len(" · ".join(compact)) <= width:
        return " · ".join(compact)
    return " · ".join([required_parts[0], "进行中"])


def _terminal_heartbeat_activity(task_id: str, activity: str) -> str:
    """Return a semantic, bounded terminal label while events keep the full text."""

    normalized_task = str(task_id or "").upper()
    text = " ".join(str(activity or "").split())
    if normalized_task != "T4":
        return text
    labels = (
        (("证据路由", "证据整理"), "证据整理"),
        (("研究机会", "Opportunity"), "研究机会探索"),
        (("多视角", "P0", "候选发散"), "多视角 Idea 发散"),
        (("候选谱系", "Genome", "Family"), "候选谱系整理"),
        (("独立评估", "双轴评估", "评分"), "独立评估"),
        (("演化意图", "Evolution 计划", "演化计划"), "演化计划"),
        (("Child", "子代"), "Child 生成与复评"),
        (("保留多样性", "Candidate Population", "候选筛选"), "候选筛选"),
        (("候选卡", "Portfolio", "决策页", "Gate1"), "候选决策页整理"),
    )
    for keywords, label in labels:
        if any(keyword.casefold() in text.casefold() for keyword in keywords):
            return label
    return "T4 阶段处理中"


def _artifact_status_label(value: object) -> str:
    """Translate machine status values at the terminal boundary."""

    return {
        "planned": "待生成",
        "created": "已生成",
        "updated": "已更新",
        "unchanged": "已存在且未改动",
        "staged": "已写入，待校验",
        "missing": "未生成",
        "invalid": "不可读取",
        "invalidated": "校验未通过",
        "not_applicable": "不适用",
    }.get(str(value or ""), str(value or "未知"))


def _stage_input_overview(values: dict[str, ArtifactInfo], required_keys: set[str]) -> str:
    """Give the researcher a useful start-of-stage material summary, not a path dump."""

    available = sum(info.status == "available" for info in values.values())
    required_missing = [
        info.path
        for key, info in values.items()
        if key in required_keys and info.status != "available"
    ]
    optional_missing = sum(info.status == "optional_missing" for info in values.values())
    text = f"已检查输入：{available}/{len(values)} 项已就绪。"
    if required_missing:
        text += " 仍需提供：" + "、".join(required_missing) + "。"
    elif optional_missing:
        text += f" {optional_missing} 项可选材料尚未提供。"
    return text


def _stage_output_overview(task_id: str, outputs: dict[str, Path]) -> str:
    labels = {
        "T2": "文献池、阅读队列和覆盖记录",
        "T3": "论文阅读笔记和综合材料",
        "T4": "研究构想比较、依据和选择建议",
        "T5": "实验交接材料和执行状态",
        "T8": "论文草稿和写作核验记录",
        "T9": "投稿包和真实编译结果",
    }
    label = labels.get(task_id, "本阶段结果")
    return f"完成后：将生成或更新 {label}（共 {len(outputs)} 项）。"


def _humanize_runtime_label(value: object) -> str:
    """Translate catalog labels that are only meaningful as runtime metadata."""

    text = str(value or "")
    replacements = (
        ("Research Scope Initialization", "研究范围初始化"),
        ("Literature Discovery & Domain Mapping", "文献检索与领域映射"),
        ("Evidence-Grounded Literature Reading", "证据约束的文献阅读"),
        ("Literature Synthesis", "文献综合"),
        ("Survey Decision", "综述分支决策"),
        ("Survey Taxonomy Plan", "综述分类框架规划"),
        ("Survey Section State", "综述章节证据绑定"),
        ("Survey Taxonomy Visual", "综述分类框架图"),
        ("Survey Assembly", "综述拼装与审计"),
        ("Survey Real Compilation", "综述真实编译"),
        ("Idea Generation & Candidate Governance", "研究方向生成与候选治理"),
        ("Novelty & Collision Audit", "新颖性与相似工作审计"),
        ("Research-to-Execution Reboost", "研究到执行的重新整理"),
        ("External Experiment Handoff", "外部实验交接"),
        ("Project Skill Review", "项目专属 Skill 审查"),
        ("External Material Intake", "外部材料准备"),
        ("Executor Selection", "外部执行方式选择"),
        ("Executor Protocol Dry Run", "外部执行协议演练"),
        ("External Executor Wait", "等待外部执行器回传"),
        ("External Result Ingestion", "外部结果接收"),
        ("Experiment Integrity Audit", "实验完整性审计"),
        ("Post-Experiment Novelty Review", "实验后的新颖性复核"),
        ("Result-to-Claim Compilation", "结果到论文主张的整理"),
        ("PI Evidence Decision", "研究负责人证据决策"),
        ("Manuscript Resource Index", "论文写作资料索引"),
        ("Venue and Writing Style", "投稿目标与写作风格"),
        ("Paper Storyline & Outline", "论文叙事与大纲"),
        ("Section Writing Plan", "章节写作计划"),
        ("Manuscript Assembly", "论文拼装"),
        ("Author Self-check", "作者自检"),
        ("Review Round 1", "第一轮论文审阅"),
        ("Review Round 2", "第二轮论文审阅"),
        ("Revision Round 1", "第一轮修订"),
        ("Revision Round 2", "第二轮修订"),
        ("Final Claim Audit", "最终主张审计"),
        ("Submission Bundle & Real Compilation", "投稿包与真实编译"),
        ("Literature Decision Gate", "文献方案确认"),
        ("Survey Branch", "综述分支"),
        ("External Execution Preparation", "外部执行准备"),
        ("Manuscript Section Draft", "论文章节起草"),
        ("Manuscript Review & Revision", "论文审阅与修订"),
        ("ResearchOS Stage", "ResearchOS 当前步骤"),
        ("Skill Intake", "Skill 材料收集"),
        ("Query portfolio", "检索组合"),
        ("metadata 验证", "元数据核验"),
        ("citation expansion", "引用扩展"),
        ("method family", "方法家族"),
        ("contribution space", "贡献空间"),
        ("mechanism clusters", "机制聚类"),
        ("tension/transfer", "张力与迁移关系"),
        ("section state", "章节状态"),
        ("evidence binding", "证据绑定"),
        ("citation plan", "引用计划"),
        ("corpus inventory", "语料清单"),
        ("taxonomy plan", "分类框架规划"),
        ("coverage gaps", "覆盖缺口"),
        ("coverage audit", "覆盖审计"),
        ("citation audit", "引用审计"),
        ("LaTeX compile", "LaTeX 编译"),
        ("compile", "编译"),
        ("artifact inventory", "文件清单"),
        ("human decision", "人工决策"),
        ("section evidence", "章节证据"),
        ("state update", "状态更新"),
        ("review findings", "审阅发现"),
        ("patches", "修改项"),
        ("claim audit", "主张审计"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def _tool_disposition(
    *,
    ok: bool,
    data: dict[str, Any] | None,
    error: str | None,
    tool_name: str | None = None,
) -> str:
    payload = data if isinstance(data, dict) else {}
    failure = str(payload.get("failure_class") or error or "").casefold()
    if ok:
        return "DONE"
    if _is_exploratory_probe_miss(str(tool_name or ""), error=error, data=payload):
        return "EXPLORATORY_MISS"
    if payload.get("optional_input") is True or str(payload.get("display_disposition") or "").casefold() == "skipped":
        return "SKIPPED"
    if str(payload.get("display_disposition") or "").casefold() in {"auto_repair", "repairing"} and payload.get("repairable", True):
        return "AUTO_REPAIR"
    if str(payload.get("display_disposition") or "").casefold() in {"auto_fallback", "fallback"} and payload.get("fallback_available", True):
        return "AUTO_FALLBACK"
    if failure in {"note_incomplete", "schema_validation_failed"}:
        return "AUTO_REPAIR"
    if failure in {"rate_limited", "network_unavailable", "timeout", "http_5xx", "transient_http"} and payload.get("fallback_available", True):
        return "DEGRADED"
    return "FAILED"


def _is_exploratory_probe_miss(
    tool_name: str,
    *,
    error: str | None,
    data: dict[str, Any] | None,
) -> bool:
    """Identify harmless workspace probes while retaining their trace events."""

    payload = data if isinstance(data, dict) else {}
    failure = str(payload.get("failure_class") or error or "").casefold()
    return str(tool_name or "").casefold() in {"read_file", "list_files", "glob_files", "grep_search"} and failure in {
        "access_denied",
        "not_found",
        "file_not_found",
    }


def _tool_style(tool_name: str, ok: bool | None, disposition: str | None = None) -> str:
    """Use stable colors by tool category, with outcome taking precedence."""

    if ok is True:
        return "green"
    if disposition in {"SKIPPED", "DEGRADED"}:
        return "yellow"
    if disposition == "EXPLORATORY_MISS":
        return "dim"
    if disposition == "AUTO_REPAIR":
        return "cyan"
    if disposition == "AUTO_FALLBACK":
        return "cyan"
    if ok is False:
        return "bright_red"
    normalized = str(tool_name or "").casefold()
    if normalized in {"ask_human", "human_gate", "finish_task"} or "gate" in normalized:
        return "bright_yellow"
    if normalized == "update_skill_workflow":
        return "bright_green"
    if "latex" in normalized or normalized in {"build_survey_figures", "build_domain_map"}:
        return "yellow"
    if any(token in normalized for token in ("search", "retrieve", "query", "citation", "fetch")):
        return "cyan"
    if any(token in normalized for token in ("write", "save", "append", "build_", "assemble", "prepare", "map_", "audit")):
        return "magenta"
    if any(token in normalized for token in ("read", "list", "glob", "grep", "extract")):
        return "blue"
    return "bright_cyan"


def _is_retrieval_tool(tool_name: str) -> bool:
    normalized = str(tool_name or "").casefold()
    return normalized.endswith("_search") or normalized in {"search_papers", "multi_source_search", "fetch_outgoing_citations"}


def _tool_calculation_summary(task_id: str, tool_name: str, data: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name == "web_fetch":
        rows = _selected_scalar_rows(data, ("status_code", "content_type", "truncated"))
        return {"title": f"{stage_display_name(task_id)} · 网页内容", "rows": rows} if rows else None
    if tool_name in {"bash_run", "docker_exec"}:
        rows = _selected_scalar_rows(data, ("exit_code", "cwd", "truncated"))
        title = "命令执行 · 隔离环境" if tool_name == "docker_exec" else "命令执行"
        return {"title": title, "rows": rows} if rows else None
    if tool_name in {"deduplicate_papers", "analyze_dedup_rate", "build_verified_papers", "build_deep_read_queue", "build_domain_map", "build_access_audit"}:
        rows = []
        for key in ("raw_count", "dedup_count", "count", "verified_count", "failure_count", "queue_count", "backlog_count", "rate", "duplicate_rate"):
            if data.get(key) is not None:
                rows.append((key, data[key]))
        if not rows:
            for key in ("metadata", "domain_map"):
                value = data.get(key)
                if isinstance(value, dict):
                    for nested, nested_value in value.items():
                        if isinstance(nested_value, (int, float, str)):
                            rows.append((nested, nested_value))
        return {"title": f"{stage_display_name(task_id)} · {_tool_label(tool_name)}", "rows": rows[:10]} if rows else None
    if tool_name == "save_paper_note":
        rows = []
        labels = {
            "paper_id": "论文编号",
            "note_path": "论文阅读笔记",
            "evidence_level": "阅读范围",
            "pages_read": "已读页数",
            "page_count": "总页数",
            "coverage": "阅读覆盖情况",
            "truncated": "是否仅读取部分内容",
        }
        for key in labels:
            if data.get(key) is not None:
                rows.append((labels[key], data[key]))
        return {"title": "论文阅读 · 论文阅读笔记", "rows": rows} if rows else None
    if tool_name == "log_t4_ideation_progress":
        event = data.get("event") if isinstance(data.get("event"), dict) else data
        rows = []
        phase_labels = {
            "context_pack": "上下文证据包",
            "pass1_mainline": "第一轮主线方向",
            "pass1_supplement": "第一轮补充方向",
            "pass2_grounding": "第二轮依据核验",
            "scoring": "评分与比较",
            "gate_cards": "候选比较卡",
        }
        status_labels = {
            "started": "开始",
            "candidate_started": "开始整理",
            "candidate_completed": "整理完成",
            "channel_started": "开始处理",
            "channel_completed": "处理完成",
            "completed": "已完成",
        }
        for key, label in (
            ("candidate_id", "候选"),
            ("candidate_title", "方向"),
            ("channel", "补充通道"),
        ):
            if event.get(key) not in (None, ""):
                rows.append((label, event[key]))
        phase = str(event.get("phase") or "")
        status = str(event.get("status") or "")
        for label, value in (
            ("阶段", phase_labels.get(phase, phase)),
            ("状态", status_labels.get(status, status)),
        ):
            if value:
                rows.append((label, value))
        completed = event.get("completed")
        total = event.get("total")
        if completed is not None or total is not None:
            rows.append(("候选进度", f"{completed if completed is not None else '?'} / {total if total is not None else '?'}"))
        for key, label in (
            ("recommendation", "建议"),
        ):
            if event.get(key) not in (None, ""):
                rows.append((label, event[key]))
        return {"title": "研究方向比较 · 候选进度", "rows": rows} if rows else None
    if tool_name == "update_skill_workflow":
        phase = data.get("phase") if isinstance(data.get("phase"), dict) else {}
        rows = []
        for key, label in (("id", "子阶段"), ("status", "状态"), ("label", "名称"), ("summary", "当前结论")):
            if phase.get(key) not in (None, ""):
                rows.append((label, phase[key]))
        return {"title": f"{stage_display_name(task_id)} · Integrated Skill Workflow", "rows": rows} if rows else None
    if tool_name in {"build_synthesis_workbench", "build_survey_state", "build_survey_figures", "assemble_survey", "audit_survey_coverage"}:
        rows = _selected_scalar_rows(
            data,
            ("status", "section_count", "figure_count", "table_count", "coverage", "issues", "output_path", "path"),
        )
        return {"title": f"{stage_display_name(task_id)} · {_tool_label(tool_name)}", "rows": rows} if rows else None
    if tool_name in {"ingest_external_results", "audit_experiment_integrity", "build_post_experiment_novelty_check", "map_results_to_claims", "build_experiment_evidence_pack", "audit_paper_claims"}:
        rows = _selected_scalar_rows(
            data,
            ("status", "result_count", "claim_count", "issue_count", "warning_count", "output_path", "path"),
        )
        return {"title": f"{stage_display_name(task_id)} · {_tool_label(tool_name)}", "rows": rows} if rows else None
    if tool_name in {"build_manuscript_resource_index", "build_alignment_matrix", "initialize_manuscript_state", "update_manuscript_section_state", "audit_manuscript_claims", "audit_writing_craft", "prepare_submission_bundle", "latex_compile"}:
        report = data.get("compile_report") if tool_name == "latex_compile" and isinstance(data.get("compile_report"), dict) else {}
        rows = _selected_scalar_rows(
            {**report, **data},
            ("pdf_path", "exit_code", "engine", "selected_backend", "status", "section_count", "claim_count", "issue_count", "output_path", "path"),
        )
        return {"title": f"{stage_display_name(task_id)} · {_tool_label(tool_name)}", "rows": rows} if rows else None
    return None


def _selected_scalar_rows(data: dict[str, Any], keys: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return bounded display rows, never arbitrary tool payload fields.

    Tool data can contain HTML, full model output, stdout, entire JSONL records,
    or stack traces.  A terminal panel is a status display, not a second trace.
    """

    labels = {
        "path": "文件",
        "output_path": "文件",
        "pdf_path": "PDF",
        "status": "状态",
        "exit_code": "退出码",
        "engine": "编译器",
        "selected_backend": "编译方式",
        "status_code": "HTTP 状态",
        "content_type": "内容类型",
        "truncated": "内容是否较长",
        "size": "文件大小",
        "section_count": "章节数",
        "figure_count": "图数",
        "table_count": "表数",
        "coverage": "覆盖情况",
        "issues": "问题数",
        "issue_count": "问题数",
        "warning_count": "提示数",
        "result_count": "结果数",
        "claim_count": "主张数",
        "cwd": "工作目录",
    }
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen or data.get(key) in (None, ""):
            continue
        seen.add(key)
        value = data[key]
        if not isinstance(value, (str, int, float, bool)):
            continue
        rows.append((labels.get(key, key), _compact_cli_text(value, 96)))
    return rows[:8]


def _default_public_activity(task_id: str) -> str:
    """Return a short, user-facing label when a task has no finer milestone."""

    normalized = task_id.upper()
    if normalized.startswith("T2"):
        return "正在整理检索与筛选结果"
    if normalized.startswith("T3.6"):
        return "正在整理综述材料"
    if normalized.startswith("T3.5"):
        return "正在归纳文献综合结果"
    if normalized.startswith("T3"):
        return "正在整理论文阅读笔记"
    if normalized.startswith("T4"):
        return "正在整理研究构思与依据"
    if normalized.startswith("T5"):
        return "正在整理实验执行材料"
    if normalized.startswith("T8"):
        return "正在准备论文草稿"
    if normalized.startswith("T9"):
        return "正在准备投稿文件"
    if normalized.startswith("SKILL_"):
        return "正在处理已提供材料"
    return "正在处理当前工作"


def _tool_label(tool_name: str) -> str:
    return {
        "deduplicate_papers": "候选去重",
        "analyze_dedup_rate": "去重质量检查",
        "fetch_paper_pdf": "PDF 获取",
        "extract_pdf_text": "PDF 文本提取",
        "extract_paper_sections": "论文部分提取",
        "build_verified_papers": "Metadata 验证",
        "build_deep_read_queue": "阅读队列",
        "build_domain_map": "引用图与领域映射",
        "build_access_audit": "全文访问审计",
        "build_synthesis_workbench": "综合工作台",
        "build_survey_state": "Survey 状态",
        "build_survey_figures": "Survey 图表",
        "assemble_survey": "Survey 拼装",
        "audit_survey_coverage": "Survey 覆盖审计",
        "ingest_external_results": "结果摄取",
        "audit_experiment_integrity": "实验完整性审计",
        "build_post_experiment_novelty_check": "实验后新颖性复核",
        "map_results_to_claims": "Result-to-Claim 映射",
        "build_experiment_evidence_pack": "实验写作证据包",
        "audit_paper_claims": "论文 Claim 审计",
        "build_manuscript_resource_index": "写作资源索引",
        "build_alignment_matrix": "章节证据对齐",
        "initialize_manuscript_state": "章节写作状态",
        "update_manuscript_section_state": "章节状态更新",
        "audit_manuscript_claims": "稿件 Claim 审计",
        "audit_writing_craft": "写作质量审计",
        "prepare_submission_bundle": "投稿 Bundle",
        "latex_compile": "真实 LaTeX 编译",
        "update_skill_workflow": "Skill 工作流阶段",
    }.get(tool_name, tool_name)


def _safe_workspace_relative(workspace: Path, value: str) -> str | None:
    try:
        path = Path(value)
        candidate = path if path.is_absolute() else workspace / path
        resolved = candidate.resolve()
        resolved.relative_to(workspace.resolve())
        return relative_path(workspace, resolved)
    except (OSError, TypeError, ValueError):
        return None


def _count_unsupported(insights: list[dict[str, Any]]) -> int:
    return sum(
        1
        for insight in insights
        for _left, right in insight.get("rows", [])
        if _is_unsupported_row(_left, right)
    )


def _is_unsupported_row(left: Any, right: Any) -> bool:
    text = f"{left} {right}".casefold()
    return any(marker in text for marker in ("unsupported", "证据不足", "需复核", "abstract-only", "metadata-only"))


def _size_label(size: int) -> str:
    if size <= 0:
        return "-"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
