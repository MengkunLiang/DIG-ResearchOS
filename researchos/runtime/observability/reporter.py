from __future__ import annotations

"""Unified Stage Start / Progress / Summary rendering for research CLI runs."""

from dataclasses import dataclass
import io
from pathlib import Path
import re
import shutil
from typing import Any, Callable

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .artifacts import ArtifactInfo, compare_artifact, inspect_artifact, relative_path, snapshot_artifacts
from .events import EventStore, ObservabilityEvent
from .extractors import extract_stage_insights
from .stage_catalog import artifact_consumers, artifact_meaning, stage_profile


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
    ) -> None:
        self.workspace = Path(workspace)
        self.runtime_dir_name = runtime_dir_name
        self.verbosity = verbosity if verbosity in {"concise", "normal", "detailed"} else "normal"
        self.quiet = bool(quiet)
        self.no_color = bool(no_color)
        self.json_events = bool(json_events)
        self._emit_fn = emit_fn or (lambda message: print(message, flush=True))
        self.store = EventStore(self.workspace, runtime_dir_name=runtime_dir_name)
        self._runs: dict[str, _StageRun] = {}

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
            self._plain(f"[Stage] {task_id} · {profile.goal}")
            return
        renderables: list[Any] = [
            Text(profile.goal, style="bold"),
            Text(f"研究问题：{profile.research_question}"),
            Text("计划操作：" + " -> ".join(profile.operations)),
        ]
        if profile.branch_note:
            renderables.append(Text(profile.branch_note, style="yellow"))
        renderables.append(self._artifact_table("Inputs", input_snapshot, include_consumers=False))
        renderables.append(self._expected_output_table(outputs))
        self._render(Panel(Group(*renderables), title=profile.title, border_style=self._stage_accent(task_id), expand=False))
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
                self._plain(f"[Compile] {task_id} · {tex_path or 'LaTeX source'} · {engine} · backend={backend}")
            elif self.verbosity != "concise":
                rows = [
                    ("Source", tex_path or "未声明"),
                    ("Engine", engine),
                    ("Backend", backend),
                    ("Execution", "真实编译已开始；语法错误会快速停止，完整日志写入 workspace。"),
                ]
                self._render(Panel(self._rows_table(rows), title=f"{task_id} · Real LaTeX Compilation", border_style="yellow", expand=False))

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
        run = self._runs.get(run_id)
        if run is not None and task_id == "T2" and _is_retrieval_tool(tool_name):
            source = str(payload.get("source") or tool_name.removesuffix("_search")).replace("_", " ")
            disposition = _tool_disposition(ok=ok, data=payload, error=error)
            previous = run.source_health.get(source, {})
            run.source_health[source] = {
                "source": source,
                "disposition": disposition,
                "attempts": payload.get("attempts") or previous.get("attempts") or 1,
                "records": len(payload.get("papers") or []) if isinstance(payload.get("papers"), list) else previous.get("records"),
                "failure_class": payload.get("failure_class"),
            }
        if not ok:
            disposition = _tool_disposition(ok=ok, data=payload, error=error)
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
            self._plain(f"[Agent] {agent} · {summary[:240]}")
            return
        title = f"{task_id} · {'Decision Context' if human_action_context else 'Agent Update'}"
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

        if self.quiet:
            return
        if self.verbosity != "detailed" and tool_name in {"read_file", "list_files", "glob_files", "grep_search"}:
            return
        style = _tool_style(tool_name, ok=None)
        segments = [
            ("  TOOL  ", f"bold {style}"),
            (tool_name, f"bold {style}"),
            (f"  {purpose}", "dim"),
        ]
        if output_path:
            segments.append((f"\n        target: {output_path}", "dim"))
        if self.detailed and input_summary:
            segments.append((f"\n        input: {input_summary}", "dim"))
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
    ) -> None:
        """Render a colored bounded tool outcome when no richer metric panel exists."""

        if self.quiet:
            return
        status = _tool_disposition(ok=ok, data=data, error=None)
        style = _tool_style(tool_name, ok=ok, disposition=status)
        text = _compact_cli_text(summary, 220)
        segments = [
            ("  RESULT ", f"bold {style}"),
            (status, f"bold {style}"),
            (f"  {tool_name}", "bold"),
        ]
        if text:
            segments.append((f"  {text}", "dim" if status == "DONE" else ("yellow" if status in {"SKIPPED", "DEGRADED"} else "red")))
        if output_path:
            segments.append((f"\n        artifact: {output_path}", "dim"))
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
            self._plain(f"[Stage] {task_id} {'完成' if ok else '暂停'} · 事件：{relative_path(self.workspace, event_path)}")
            return
        renderables: list[Any] = [Text(summary if summary else "阶段执行已结束。")]
        if error:
            renderables.append(Text(f"当前问题：{error}", style="bold red"))
        if insights:
            for insight in insights:
                renderables.append(self._insight_panel(insight))
        if task_id == "T2" and run and run.source_health and self.verbosity != "concise":
            rows = []
            for item in sorted(run.source_health.values(), key=lambda row: str(row.get("source") or "")):
                detail = str(item.get("disposition") or "unknown")
                if item.get("records") is not None:
                    detail += f" · {item['records']} records"
                if item.get("failure_class"):
                    detail += f" · {item['failure_class']}"
                rows.append((str(item.get("source") or "source"), detail))
            renderables.append(
                Panel(
                    self._rows_table(rows),
                    title="T2 · Retrieval Source Health",
                    border_style="yellow" if any(item.get("disposition") == "DEGRADED" for item in run.source_health.values()) else "cyan",
                    expand=False,
                )
            )
        if run and run.actual_reads and self.verbosity != "concise":
            visible = sorted(run.actual_reads)[:10]
            more = f"；另有 {len(run.actual_reads) - len(visible)} 项" if len(run.actual_reads) > len(visible) else ""
            renderables.append(Text("实际读取：" + "、".join(visible) + more, style="dim"))
        renderables.append(self._artifact_manifest(output_infos, statuses))
        renderables.append(Text(f"过程事件：{relative_path(self.workspace, event_path)}", style="dim"))
        title = f"{task_id} · {'Stage Summary' if ok else 'Stage Pause'}"
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
            self._plain(f"[Stage] {task_id} 的外层 Artifact 校验未通过；事件：{relative_path(self.workspace, event_path)}")
            return
        details: list[Any] = [
            Text(
                "Agent 已结束，但状态机的独立 Artifact 契约校验未通过；"
                "该阶段不能作为成功结果进入下游。",
                style="bold yellow",
            ),
            Text(f"原因：{reason}", style="red"),
            self._artifact_manifest(
                output_infos,
                {key: "invalidated" for key in output_infos},
            ),
        ]
        if log_path:
            details.append(Text(f"运行日志：{log_path}", style="dim"))
        details.append(Text(f"过程事件：{relative_path(self.workspace, event_path)}", style="dim"))
        self._render(Panel(Group(*details), title=f"{task_id} · Runtime Validation", border_style="red", expand=False))

    def human_action_required(self, *, task_id: str, run_id: str, gate_id: str, reason: str) -> None:
        self._event("human_action_required", task_id=task_id, run_id=run_id, severity="warning", payload={"gate_id": gate_id, "reason": reason})

    def gate_resolved(self, *, task_id: str, run_id: str, gate_id: str, decision: str) -> None:
        self._event("decision_made", task_id=task_id, run_id=run_id, payload={"gate_id": gate_id, "decision": decision})

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
        table.add_column("Artifact", max_width=34, overflow="fold")
        table.add_column("Meaning", max_width=38, overflow="fold")
        table.add_column("Status", max_width=16)
        table.add_column("Records / Size", max_width=22, overflow="fold")
        if include_consumers:
            table.add_column("Used By", max_width=20, overflow="fold")
        if not values:
            row = ["-", "本阶段没有声明上游 Artifact。", "not_applicable", "-"]
            if include_consumers:
                row.append("-")
            table.add_row(*row)
            return table
        for key, info in values.items():
            measure = info.detail or _size_label(info.size_bytes)
            row = [f"{key}\n{info.path}", artifact_meaning(info.path), info.status, measure]
            if include_consumers:
                row.append(artifact_consumers("", info.path))
            table.add_row(*row)
        return table

    def _expected_output_table(self, outputs: dict[str, Path]) -> Table:
        values = {
            key: ArtifactInfo(path=relative_path(self.workspace, path), status="planned", kind="planned")
            for key, path in outputs.items()
        }
        return self._artifact_table("Expected Outputs", values, include_consumers=True)

    def _artifact_manifest(self, values: dict[str, ArtifactInfo], statuses: dict[str, str]) -> Table:
        table = Table(title="Artifact Manifest", box=box.SIMPLE_HEAVY, show_header=True, header_style="bold green", expand=False)
        table.add_column("Output Artifact", max_width=34, overflow="fold")
        table.add_column("Meaning", max_width=38, overflow="fold")
        table.add_column("Status", max_width=16)
        table.add_column("Records / Size", max_width=22, overflow="fold")
        table.add_column("Used By", max_width=20, overflow="fold")
        for key, info in values.items():
            table.add_row(
                f"{key}\n{info.path}",
                artifact_meaning(info.path),
                statuses.get(key, info.status),
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

    def _rows_table(self, rows: list[tuple[str, str]]) -> Table:
        table = Table(box=box.SIMPLE, show_header=False, expand=False, pad_edge=False)
        table.add_column(max_width=42, overflow="fold")
        table.add_column(max_width=76, overflow="fold")
        for left, right in rows:
            table.add_row(left, right)
        return table

    def _render(self, renderable: Any) -> None:
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
            self._emit_fn("\n" + text)

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
        if task_id.startswith("T7"):
            return "bright_red"
        if task_id.startswith("T8"):
            return "bright_cyan"
        if task_id.startswith("T9"):
            return "bright_green"
        return "cyan"

    def _plain(self, message: str) -> None:
        self._emit_fn(message)


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
    text = " ".join(normalize_cli_markdown(str(value or "")).split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def _tool_disposition(*, ok: bool, data: dict[str, Any] | None, error: str | None) -> str:
    payload = data if isinstance(data, dict) else {}
    failure = str(payload.get("failure_class") or error or "").casefold()
    if ok:
        return "DONE"
    if payload.get("optional_input") is True or str(payload.get("display_disposition") or "").casefold() == "skipped":
        return "SKIPPED"
    if failure in {"rate_limited", "network_unavailable", "timeout", "http_5xx", "transient_http"} and payload.get("fallback_available", True):
        return "DEGRADED"
    return "FAILED"


def _tool_style(tool_name: str, ok: bool | None, disposition: str | None = None) -> str:
    """Use stable colors by tool category, with outcome taking precedence."""

    if ok is True:
        return "green"
    if disposition in {"SKIPPED", "DEGRADED"}:
        return "yellow"
    if ok is False:
        return "bright_red"
    normalized = str(tool_name or "").casefold()
    if normalized in {"ask_human", "human_gate", "finish_task"} or "gate" in normalized:
        return "bright_yellow"
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
        return {"title": f"{task_id} · {_tool_label(tool_name)}", "rows": rows[:10]} if rows else None
    if tool_name == "save_paper_note":
        rows = []
        for key in ("paper_id", "note_path", "evidence_level", "pages_read", "page_count", "coverage", "truncated"):
            if data.get(key) is not None:
                rows.append((key, data[key]))
        return {"title": "T3 · Paper Evidence Card", "rows": rows} if rows else None
    if tool_name == "log_t4_ideation_progress":
        event = data.get("event") if isinstance(data.get("event"), dict) else data
        rows = []
        for key, label in (
            ("phase", "阶段"),
            ("status", "状态"),
            ("candidate_id", "候选"),
            ("channel", "补充通道"),
            ("completed", "已完成"),
            ("total", "总数"),
            ("recommendation", "建议"),
        ):
            if event.get(key) not in (None, ""):
                rows.append((label, event[key]))
        return {"title": "T4 · Candidate Governance Progress", "rows": rows} if rows else None
    if tool_name in {"build_synthesis_workbench", "build_survey_state", "build_survey_figures", "assemble_survey", "audit_survey_coverage"}:
        rows = [(key, value) for key, value in data.items() if isinstance(value, (int, float, str, bool))][:10]
        return {"title": f"{task_id} · {_tool_label(tool_name)}", "rows": rows} if rows else None
    if tool_name in {"ingest_external_results", "audit_experiment_integrity", "build_post_experiment_novelty_check", "map_results_to_claims", "build_experiment_evidence_pack", "audit_paper_claims"}:
        rows = [(key, value) for key, value in data.items() if isinstance(value, (int, float, str, bool))][:10]
        return {"title": f"{task_id} · {_tool_label(tool_name)}", "rows": rows} if rows else None
    if tool_name in {"build_manuscript_resource_index", "build_alignment_matrix", "initialize_manuscript_state", "update_manuscript_section_state", "audit_manuscript_claims", "audit_writing_craft", "prepare_submission_bundle", "latex_compile"}:
        rows = [(key, value) for key, value in data.items() if isinstance(value, (int, float, str, bool))][:10]
        return {"title": f"{task_id} · {_tool_label(tool_name)}", "rows": rows} if rows else None
    return None


def _tool_label(tool_name: str) -> str:
    return {
        "deduplicate_papers": "候选去重",
        "analyze_dedup_rate": "去重质量检查",
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
