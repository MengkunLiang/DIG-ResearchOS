from __future__ import annotations

"""Persistent, human-readable session state for guided standalone skills."""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import shutil
from typing import Any, Iterable

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..runtime.errors import ConfigurationError
from .contracts import SkillInteraction, SkillReadiness, readiness_as_dict
from .workflow import SkillWorkflow, workflow_as_session_payload


SESSION_DIR = Path("_runtime/skill_sessions")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_session_id(value: str) -> str:
    session_id = str(value or "").strip()
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise ConfigurationError(
            "skill session id must contain only letters, digits, '.', '_' or '-' and be at most 80 characters"
        )
    return session_id


def session_path(workspace: Path, session_id: str) -> Path:
    return workspace / SESSION_DIR / f"{normalize_session_id(session_id)}.json"


def load_session(workspace: Path, session_id: str) -> dict[str, Any] | None:
    path = session_path(workspace, session_id)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"invalid skill session {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"invalid skill session {path}: root must be an object")
    return value


def write_session(workspace: Path, session_id: str, data: dict[str, Any]) -> Path:
    path = session_path(workspace, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = _now()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _append_turn(session: dict[str, Any], event: dict[str, Any]) -> None:
    """Append a bounded, user-visible session event.

    Skill sessions are long lived and may be resumed many times.  Keeping a
    small event tail makes the status command useful without turning a session
    record into a second trace file.  The complete trace remains under
    ``_runtime/traces``.
    """

    turns = session.setdefault("turns", [])
    if not isinstance(turns, list):
        turns = []
        session["turns"] = turns
    turns.append({"at": _now(), **event})
    if len(turns) > 80:
        del turns[:-80]


def record_readiness(
    *,
    workspace: Path,
    session_id: str,
    skill_name: str,
    skill_path: Path,
    readiness: SkillReadiness,
    resume: bool,
    intake_packet_path: Path | None = None,
    workflow: SkillWorkflow | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create or update a session after a deterministic readiness check."""

    previous = load_session(workspace, session_id)
    if previous and previous.get("skill_name") not in {None, skill_name}:
        raise ConfigurationError(
            f"skill session '{session_id}' belongs to {previous.get('skill_name')!r}, not {skill_name!r}; use --session-id"
        )
    if resume and previous is None:
        raise ConfigurationError(
            f"skill session '{session_id}' does not exist; omit --resume to create it"
        )
    interaction = readiness.interaction
    session = previous or {
        "schema_version": 1,
        "session_id": normalize_session_id(session_id),
        "skill_name": skill_name,
        "skill_path": str(skill_path),
        "workspace": str(workspace),
        "created_at": _now(),
        "turns": [],
    }
    session["request"] = readiness.request or str(session.get("request", ""))
    session["readiness"] = readiness_as_dict(readiness)
    if intake_packet_path is not None:
        session["intake_packet"] = str(intake_packet_path.relative_to(workspace))
    session["status"] = "READY" if readiness.ready else "WAITING_INPUT"
    _append_turn(
        session,
        {
            "event": "readiness_checked",
            "ready": readiness.ready,
            "resume": resume,
        },
    )
    if interaction:
        session["interaction"] = {
            "mode": interaction.mode,
            "language": interaction.language,
            "summary": interaction.summary,
            "outputs": [
                {
                    "id": output.key,
                    "label": output.label,
                    "path": output.path,
                    "description": output.description,
                }
                for output in interaction.outputs
            ],
        }
    if workflow is not None:
        _merge_workflow_session_payload(session, workflow)
    path = write_session(workspace, session_id, session)
    return path, session


def _merge_workflow_session_payload(session: dict[str, Any], workflow: SkillWorkflow) -> None:
    """Refresh workflow labels without erasing durable phase progress on resume."""

    previous = session.get("workflow") if isinstance(session.get("workflow"), dict) else {}
    previous_phases = {
        str(item.get("id")): item
        for item in previous.get("phases", [])
        if isinstance(item, dict) and item.get("id")
    }
    payload = workflow_as_session_payload(workflow)
    phases = payload["phases"]
    for phase in phases:
        old = previous_phases.get(str(phase["id"]))
        if not old:
            continue
        for key in ("status", "summary", "artifacts", "evidence_boundary", "next_action", "updated_at"):
            if key in old:
                phase[key] = old[key]
    current = str(previous.get("current_phase") or "")
    phase_ids = {str(phase["id"]) for phase in phases}
    if current in phase_ids:
        payload["current_phase"] = current
    session["workflow"] = payload


def record_workflow_progress(
    *,
    workspace: Path,
    session_id: str,
    phase_id: str,
    status: str,
    summary: str,
    artifacts: list[str],
    evidence_boundary: str,
    next_action: str,
) -> tuple[Path, dict[str, Any]]:
    """Persist one user-visible integrated-Skill phase transition."""

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    workflow = session.get("workflow") if isinstance(session.get("workflow"), dict) else None
    if workflow is None:
        raise ConfigurationError("this Skill session does not declare an integrated workflow")
    phases = workflow.get("phases") if isinstance(workflow.get("phases"), list) else []
    phase = next(
        (item for item in phases if isinstance(item, dict) and str(item.get("id")) == phase_id),
        None,
    )
    if phase is None:
        known = ", ".join(str(item.get("id")) for item in phases if isinstance(item, dict))
        raise ConfigurationError(f"unknown workflow phase '{phase_id}'; expected one of: {known}")
    if status not in {"running", "completed", "waiting_input", "waiting_evidence", "skipped"}:
        raise ConfigurationError(f"unsupported workflow phase status: {status}")
    clean_artifacts = [str(item).strip() for item in artifacts if str(item).strip()]
    phase.update(
        {
            "status": status,
            "summary": str(summary).strip(),
            "artifacts": clean_artifacts,
            "evidence_boundary": str(evidence_boundary).strip(),
            "next_action": str(next_action).strip(),
            "updated_at": _now(),
        }
    )
    workflow["current_phase"] = phase_id
    session["workflow"] = workflow
    session["progress"] = {
        "step": None,
        "step_limit": None,
        "phase": f"workflow:{phase_id}",
        "tool_name": "update_skill_workflow",
        "detail": str(summary).strip(),
        "updated_at": _now(),
    }
    _append_turn(
        session,
        {
            "event": "workflow_phase_updated",
            "phase_id": phase_id,
            "status": status,
            "detail": str(summary).strip()[:500],
        },
    )
    return write_session(workspace, session_id, session), dict(phase)


def record_run_started(workspace: Path, session_id: str) -> Path:
    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    session["status"] = "RUNNING"
    session["progress"] = {
        "step": 0,
        "step_limit": None,
        "phase": "starting",
        "tool_name": None,
        "detail": "运行已启动，正在构建可执行上下文。",
        "updated_at": _now(),
    }
    _append_turn(session, {"event": "run_started"})
    return write_session(workspace, session_id, session)


def record_input_collection_started(workspace: Path, session_id: str) -> Path:
    """Mark an interactive, constrained material collection turn as active."""

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    session["status"] = "COLLECTING_INPUT"
    session["progress"] = {
        "phase": "collecting_input",
        "tool_name": None,
        "detail": "正在通过受限材料收集流程确认上传或整理人工提供的内容。",
        "updated_at": _now(),
    }
    _append_turn(session, {"event": "input_collection_started"})
    return write_session(workspace, session_id, session)


def record_input_collection_finished(
    *,
    workspace: Path,
    session_id: str,
    ready: bool,
    message: str,
) -> Path:
    """Persist the observable result of intake without claiming Skill completion."""

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    session["status"] = "READY" if ready else "WAITING_INPUT"
    session["progress"] = {
        "phase": "input_ready" if ready else "waiting_input",
        "tool_name": None,
        "detail": message,
        "updated_at": _now(),
    }
    _append_turn(session, {"event": "input_collection_finished", "ready": ready, "detail": message[:500]})
    return write_session(workspace, session_id, session)


def record_skill_execution_confirmation_pending(
    *,
    workspace: Path,
    session_id: str,
    message: str,
    input_ready: bool,
) -> Path:
    """Persist an explicit human decision point before a Skill can execute."""

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    session["status"] = "WAITING_CONFIRMATION" if input_ready else "WAITING_INPUT"
    session["progress"] = {
        "phase": "awaiting_execution_confirmation" if input_ready else "waiting_input",
        "tool_name": None,
        "detail": message,
        "updated_at": _now(),
    }
    _append_turn(
        session,
        {
            "event": "execution_confirmation_pending",
            "input_ready": input_ready,
            "detail": message[:500],
        },
    )
    return write_session(workspace, session_id, session)


def record_runtime_pause(*, workspace: Path, session_id: str, error: Exception | str) -> Path:
    """Preserve a recoverable pre-run failure such as an unavailable provider."""

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    message = str(error)
    session["status"] = "WAITING_RUNTIME"
    session["last_runtime_error"] = message
    session["progress"] = {
        "phase": "waiting_runtime",
        "tool_name": None,
        "detail": message,
        "updated_at": _now(),
    }
    _append_turn(session, {"event": "runtime_preparation_failed", "error": message})
    return write_session(workspace, session_id, session)


def record_run_progress(
    *,
    workspace: Path,
    session_id: str,
    step: int | None = None,
    step_limit: int | str | None = None,
    phase: str,
    detail: str,
    tool_name: str | None = None,
) -> Path:
    """Persist one observable Skill runtime milestone.

    ``phase`` intentionally describes externally observable work only, for
    example ``awaiting_llm`` or ``tool_running``.  It must never contain a
    model reasoning transcript.
    """

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    progress = {
        "step": step,
        "step_limit": step_limit,
        "phase": str(phase),
        "tool_name": tool_name,
        "detail": str(detail),
        "updated_at": _now(),
    }
    session["progress"] = progress
    _append_turn(
        session,
        {
            "event": "runtime_progress",
            "step": step,
            "phase": str(phase),
            "tool_name": tool_name,
            "detail": str(detail)[:500],
        },
    )
    return write_session(workspace, session_id, session)


def record_run_result(
    *,
    workspace: Path,
    session_id: str,
    result: Any,
    outputs_expected: dict[str, Path],
) -> Path:
    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    output_status = {
        name: {
            "path": str(path.relative_to(workspace)),
            "exists": path.exists(),
        }
        for name, path in outputs_expected.items()
    }
    session["status"] = "COMPLETED" if bool(result.ok) else "FAILED"
    session["last_result"] = {
        "ok": bool(result.ok),
        "stop_reason": str(result.stop_reason),
        "message": str(result.message),
        "error": result.error,
        "trace_file": str(result.trace_file) if result.trace_file else None,
        "outputs": output_status,
    }
    session["progress"] = {
        "step": int(getattr(result, "steps_used", 0) or 0),
        "step_limit": None,
        "phase": "completed" if bool(result.ok) else "stopped",
        "tool_name": None,
        "detail": str(result.message),
        "updated_at": _now(),
    }
    session["metrics"] = {
        "steps": int(getattr(result, "steps_used", 0) or 0),
        "tokens": int(getattr(result, "tokens_in", 0) or 0) + int(getattr(result, "tokens_out", 0) or 0),
        "duration_seconds": round(float(getattr(result, "duration_seconds", 0.0) or 0.0), 2),
    }
    _append_turn(
        session,
        {
            "event": "run_finished",
            "ok": bool(result.ok),
            "stop_reason": str(result.stop_reason),
        },
    )
    return write_session(workspace, session_id, session)


def _requirement_lines(interaction: SkillInteraction, readiness: SkillReadiness) -> list[str]:
    by_key = {status.requirement.key: status for status in readiness.input_statuses}
    lines: list[str] = []
    for requirement in interaction.required_inputs + interaction.optional_inputs:
        status = by_key[requirement.key]
        marker = "✓" if status.is_ready else ("○" if not requirement.required else "✗")
        qualifier = "必需" if requirement.required else "可选"
        lines.append(f"{marker} [{qualifier}] {requirement.label}")
        lines.append(f"  作用：{requirement.description}")
        if status.selected_path:
            lines.append(f"  已使用：{status.selected_path}")
        else:
            lines.append(f"  请上传其一：{'  或  '.join(requirement.paths)}")
            if status.detail:
                lines.append(f"  检查结果：{status.detail}")
        if requirement.example:
            lines.append(f"  示例：{requirement.example}")
    return lines


def render_readiness_panel(
    *,
    skill_name: str,
    session_id: str,
    session_file: Path,
    readiness: SkillReadiness,
) -> str:
    """Render a compact Chinese-first terminal panel for guided interaction."""

    interaction = readiness.interaction
    width = 76
    lines = ["", "═" * width, f"SKILL 会话 · {skill_name}", "═" * width]
    lines.append(f"会话：{session_id}")
    lines.append(f"状态：{'输入已就绪，可以开始' if readiness.ready else '等待补齐输入'}")
    mode_label = "项目 workspace（自动发现候选材料）" if readiness.workspace_mode == "project" else "独立 workspace（由用户提供材料）"
    lines.append(f"运行模式：{mode_label}")
    lines.append(f"会话文件：{session_file.relative_to(readiness.workspace)}")
    intake_packet = readiness.workspace / "user_inputs" / skill_name / "_intake.md"
    if intake_packet.exists():
        lines.append(f"材料清单：{intake_packet.relative_to(readiness.workspace)}")
    if interaction:
        if interaction.summary:
            lines.append(f"用途：{interaction.summary}")
        if not readiness.request_ready:
            lines.append(f"✗ 任务说明：{interaction.request_prompt}")
            if interaction.example_request:
                lines.append(f"  示例请求：{interaction.example_request}")
        elif readiness.request:
            lines.append(f"任务说明：{readiness.request}")
        lines.append("─" * width)
        lines.append("输入检查")
        lines.extend(_requirement_lines(interaction, readiness))
        if interaction.outputs:
            lines.append("─" * width)
            lines.append("本次完成后应得到")
            for output in interaction.outputs:
                lines.append(f"• {output.label}：{output.path}")
                lines.append(f"  含义：{output.description}")
    workflow = _session_workflow_from_file(session_file)
    if workflow:
        lines.extend(_workflow_plain_lines(workflow, width=width))
    else:
        lines.append("这是兼容模式 Skill：未声明可校验的输入契约；将按原有提示词运行。")
    lines.append("─" * width)
    if readiness.ready:
        lines.append("下一步：现在可开始运行；完成后使用 skill-status 查看持久化结果与文件路径。")
    else:
        lines.append("下一步：TTY 终端默认进入定向材料收集。你可上传文件、粘贴内容，或让系统逐项询问并整理到对应 user_inputs 文件。")
        lines.append("使用 --non-interactive 或管道运行时，系统不调用 LLM，只保留可恢复 WAITING_INPUT。")
        lines.append(
            f"恢复示例：researchos run-skill {skill_name} --workspace {readiness.workspace} --session-id {session_id} --resume"
        )
    lines.append("═" * width)
    return "\n".join(lines)


def render_readiness_panel_rich(
    *,
    skill_name: str,
    session_id: str,
    session_file: Path,
    readiness: SkillReadiness,
    no_color: bool = False,
) -> str:
    """Render the interactive input check with explicit upload destinations."""

    interaction = readiness.interaction
    mode = "项目 workspace（已检查现有项目材料）" if readiness.workspace_mode == "project" else "独立 workspace（由你提供材料）"
    state = "INPUT READY · 输入已就绪" if readiness.ready else "WAITING INPUT · 等待补齐输入"
    accent = "green" if readiness.ready else "yellow"
    header = [
        Text(interaction.summary if interaction and interaction.summary else "Skill 输入检查", style="bold"),
        Text(f"会话：{session_id}  ·  {state}  ·  {mode}", style=f"bold {accent}"),
        Text(f"会话文件：{session_file.relative_to(readiness.workspace)}", style="dim"),
    ]
    if interaction and not readiness.request_ready:
        header.append(Text(f"仍需任务说明：{interaction.request_prompt}", style="yellow"))
        if interaction.example_request:
            header.append(Text(f"示例：{interaction.example_request}", style="dim"))
    elif readiness.request:
        header.append(Text(f"任务说明：{readiness.request}", style="dim"))

    body: list[Any] = [Group(*header)]
    if interaction is None:
        body.append(Text("这是兼容模式 Skill：未声明可校验输入契约。", style="yellow"))
    else:
        states = {item.requirement.key: item for item in readiness.input_statuses}
        table = Table(title="Input Readiness", box=box.SIMPLE_HEAVY, header_style="bold cyan", expand=True)
        table.add_column("State", width=13)
        table.add_column("Input", min_width=16, max_width=25, overflow="fold")
        table.add_column("Purpose", min_width=24, max_width=46, overflow="fold")
        table.add_column("Selected / Upload Path", min_width=28, max_width=58, overflow="fold")
        for requirement in interaction.required_inputs + interaction.optional_inputs:
            status = states[requirement.key]
            qualifier = "required" if requirement.required else "optional"
            if status.is_ready:
                state_text = Text(f"READY · {qualifier}", style="green")
                destination = _wrap_skill_path(status.selected_path) if status.selected_path else "已就绪"
            elif requirement.required:
                state_text = Text("MISSING · required", style="bold yellow")
                destination = "Upload:\n" + "\n or\n".join(_wrap_skill_path(path) for path in requirement.paths)
            else:
                state_text = Text("OPTIONAL", style="dim")
                destination = "Optional:\n" + "\n or\n".join(_wrap_skill_path(path) for path in requirement.paths)
            if requirement.example and not status.is_ready:
                destination += f"\nExample: {requirement.example}"
            table.add_row(state_text, requirement.label, requirement.description, destination)
        body.append(table)
        if interaction.outputs:
            outputs = Table(title="Expected Outputs", box=box.SIMPLE_HEAVY, header_style="bold magenta", expand=True)
            outputs.add_column("Artifact", min_width=22, max_width=38, overflow="fold")
            outputs.add_column("Meaning", min_width=32, max_width=72, overflow="fold")
            for output in interaction.outputs:
                outputs.add_row(f"{output.label}\n{_wrap_skill_path(output.path)}", output.description)
            body.append(outputs)
    workflow = _session_workflow_from_file(session_file)
    if workflow:
        body.append(_workflow_rich_table(workflow))
    if readiness.ready:
        body.append(Text("下一步：将开始执行 Skill；完成后用 skill-status 查看会话、产物和恢复信息。", style="green"))
    else:
        body.append(
            Text(
                "下一步：TTY 终端会默认进入定向材料收集。你可粘贴材料，或上传到上表路径后以同一 session resume。",
                style="yellow",
            )
        )
        body.append(Text("使用 --non-interactive 或管道运行时：尚未调用 LLM；不会开始研究/写作产出。", style="yellow", no_wrap=True))
        body.append(
            Text(
                f"Resume: researchos run-skill {skill_name} --workspace {readiness.workspace} --session-id {session_id} --resume",
                style="dim",
            )
        )
    return _render_skill_rich(Panel(Group(*body), title=f"SKILL · {skill_name}", border_style=accent, expand=True), no_color=no_color)


def render_skill_description(
    *,
    skill_name: str,
    skill_path: Path,
    description: str,
    interaction: SkillInteraction | None,
    workflow: SkillWorkflow | None = None,
) -> str:
    """Render the deterministic, copyable user contract for ``describe-skill``."""

    width = 76
    lines = ["", "═" * width, f"SKILL · {skill_name}", "═" * width]
    lines.append(f"路径：{skill_path}")
    if interaction is None:
        lines.append(f"用途：{description}")
        lines.append("交互：兼容模式（没有声明输入契约；建议先迁移为 guided Skill）。")
        lines.append("═" * width)
        return "\n".join(lines)
    if interaction.summary:
        lines.append(f"用途：{interaction.summary}")
        if description.strip() and description.strip() != interaction.summary.strip():
            lines.append(f"技术范围：{description}")
    else:
        lines.append(f"用途：{description}")
    lines.append(f"交互：{interaction.mode} / {interaction.language}")
    lines.append("─" * width)
    lines.append("如何开始")
    if interaction.example_request:
        lines.append(f"示例：researchos run-skill {skill_name} \"{interaction.example_request}\" --workspace ./workspace/my-project")
    else:
        lines.append(f"命令：researchos run-skill {skill_name} \"你的任务说明\" --workspace ./workspace/my-project")
    lines.append("输入文件（满足每个必需项的任一路径即可）")
    for requirement in interaction.required_inputs + interaction.optional_inputs:
        qualifier = "必需" if requirement.required else "可选"
        lines.append(f"• [{qualifier}] {requirement.label}：{requirement.description}")
        for path in requirement.paths:
            lines.append(f"  - {path}")
    lines.append("输出文件")
    for output in interaction.outputs:
        lines.append(f"• {output.path} — {output.description}")
    if workflow is not None:
        lines.extend(_workflow_plain_lines(workflow_as_session_payload(workflow), width=width))
    lines.append("会话与恢复")
    lines.append("• 非交互运行会先校验输入；缺文件时只写 `_runtime/skill_sessions/<session>.json`，不调用 LLM。")
    lines.append("• TTY 终端默认进入多轮材料收集；可上传文件或粘贴材料。受限 intake Agent 只整理人工提供的内容到 `user_inputs/<skill>/`，不产生论文/实验产物。")
    lines.append("• 初始材料通过检查后，系统会再次询问“执行/暂停”；只有明确执行授权才会启动 Skill。中断后可添加 `--resume --session-id <同一会话>`。")
    lines.append("═" * width)
    return "\n".join(lines)


def render_skill_description_rich(
    *,
    skill_name: str,
    skill_path: Path,
    description: str,
    interaction: SkillInteraction | None,
    workflow: SkillWorkflow | None = None,
    no_color: bool = False,
) -> str:
    """Render a selected Skill's full, human-actionable contract."""

    if interaction is None:
        return _render_skill_rich(
            Panel(
                Group(
                    Text(description),
                    Text(f"Path: {skill_path}", style="dim"),
                    Text("Compatibility mode: no guided input contract is declared.", style="yellow"),
                ),
                title=f"SKILL · {skill_name}",
                border_style="yellow",
                expand=True,
            ),
            no_color=no_color,
        )
    overview = [
        Text(interaction.summary or description, style="bold"),
        Text(f"Path: {skill_path}", style="dim"),
        Text(f"Interaction: {interaction.mode} · {interaction.language}", style="cyan"),
    ]
    if description.strip() and interaction.summary and description.strip() != interaction.summary.strip():
        overview.append(Text(f"Technical scope: {description}", style="dim"))
    if interaction.example_request:
        overview.append(Text(f"Example request: {interaction.example_request}", style="dim"))
    inputs = Table(title="What You Need to Provide", box=box.SIMPLE_HEAVY, header_style="bold cyan", expand=True)
    inputs.add_column("Required", width=10)
    inputs.add_column("Input", min_width=18, max_width=28, overflow="fold")
    inputs.add_column("Why", min_width=26, max_width=48, overflow="fold")
    inputs.add_column("Accepted Location", min_width=28, max_width=56, overflow="fold")
    for requirement in interaction.required_inputs + interaction.optional_inputs:
        inputs.add_row(
            Text("required" if requirement.required else "optional", style="bold yellow" if requirement.required else "dim"),
            requirement.label,
            requirement.description,
            "\n or\n".join(_wrap_skill_path(path) for path in requirement.paths),
        )
    outputs = Table(title="What the Skill Produces", box=box.SIMPLE_HEAVY, header_style="bold magenta", expand=True)
    outputs.add_column("Output", min_width=26, max_width=42, overflow="fold")
    outputs.add_column("Meaning", min_width=32, max_width=78, overflow="fold")
    for output in interaction.outputs:
        outputs.add_row(f"{output.label}\n{_wrap_skill_path(output.path)}", output.description)
    recovery = Text(
        "Interactive mode asks follow-up questions and stores supplied material under user_inputs/<skill>/ before proceeding. "
        "A project workspace is inspected first; a standalone workspace uses the upload locations above. "
        "Resume: researchos run-skill " + skill_name + " --session-id <same-session> --resume",
        style="dim",
    )
    workflow_table = _workflow_rich_table(workflow_as_session_payload(workflow)) if workflow is not None else None
    renderables: list[Any] = [Group(*overview), inputs, outputs]
    if workflow_table is not None:
        renderables.append(workflow_table)
    renderables.append(recovery)
    return _render_skill_rich(
        Panel(Group(*renderables), title=f"SKILL · {skill_name}", border_style="cyan", expand=True),
        no_color=no_color,
    )


def render_skill_completion_panel(*, workspace: Path, session_id: str) -> str:
    """Render a concrete, human-facing completion or pause summary."""

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    width = 76
    status = str(session.get("status") or "UNKNOWN")
    result = session.get("last_result") if isinstance(session.get("last_result"), dict) else {}
    metrics = session.get("metrics") if isinstance(session.get("metrics"), dict) else {}
    progress = session.get("progress") if isinstance(session.get("progress"), dict) else {}
    interaction = session.get("interaction") if isinstance(session.get("interaction"), dict) else {}
    output_descriptions = interaction.get("outputs") if isinstance(interaction.get("outputs"), list) else []
    output_by_path = {
        str(item.get("path")): item
        for item in output_descriptions
        if isinstance(item, dict) and item.get("path")
    }

    state_text = {
        "COMPLETED": "已完成",
        "FAILED": "执行失败",
        "WAITING_RUNTIME": "等待运行环境恢复",
        "WAITING_INPUT": "等待补齐输入",
        "WAITING_CONFIRMATION": "等待人工确认执行",
        "COLLECTING_INPUT": "正在收集输入",
        "RUNNING": "仍在运行",
    }.get(status, status)
    lines = ["", "═" * width, f"SKILL 执行总结 · {session.get('skill_name', 'unknown')}", "═" * width]
    lines.append(f"会话：{session_id}  |  状态：{state_text}")
    if metrics:
        lines.append(
            "运行统计："
            f"步骤 {metrics.get('steps', 0)} | token {metrics.get('tokens', 0)} | "
            f"耗时 {metrics.get('duration_seconds', 0)}s"
        )
    if result.get("message"):
        lines.append(f"结果：{result['message']}")
    elif progress.get("detail"):
        lines.append(f"当前：{progress['detail']}")
    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    if outputs:
        lines.extend(["─" * width, "产物检查"])
        for name, state in outputs.items():
            if not isinstance(state, dict):
                continue
            path = str(state.get("path") or "")
            descriptor = output_by_path.get(path, {})
            marker = "✓" if state.get("exists") else "✗"
            label = descriptor.get("label") or name
            lines.append(f"{marker} {label}：{path}")
            if descriptor.get("description"):
                lines.append(f"  含义：{descriptor['description']}")
    workflow = session.get("workflow") if isinstance(session.get("workflow"), dict) else {}
    if workflow:
        lines.extend(_workflow_plain_lines(workflow, width=width))
    trace_file = result.get("trace_file")
    if trace_file:
        lines.append(f"运行轨迹：{trace_file}")
    lines.append("─" * width)
    if status in {"WAITING_RUNTIME", "WAITING_INPUT", "WAITING_CONFIRMATION", "FAILED"}:
        lines.append(
            "恢复：researchos run-skill "
            f"{session.get('skill_name', 'SKILL')} --workspace {workspace} "
            f"--session-id {session_id} --resume"
        )
    else:
        lines.append("查看会话：researchos skill-status --workspace " + str(workspace))
    lines.append("═" * width)
    return "\n".join(lines)


def render_skill_completion_panel_rich(*, workspace: Path, session_id: str, no_color: bool = False) -> str:
    """Render one durable session outcome with visible artifacts and recovery."""

    session = load_session(workspace, session_id)
    if session is None:
        raise ConfigurationError(f"skill session '{session_id}' does not exist")
    status = str(session.get("status") or "UNKNOWN")
    result = session.get("last_result") if isinstance(session.get("last_result"), dict) else {}
    metrics = session.get("metrics") if isinstance(session.get("metrics"), dict) else {}
    progress = session.get("progress") if isinstance(session.get("progress"), dict) else {}
    interaction = session.get("interaction") if isinstance(session.get("interaction"), dict) else {}
    labels = {
        "COMPLETED": ("COMPLETED", "green"),
        "FAILED": ("FAILED", "bright_red"),
        "WAITING_RUNTIME": ("WAITING RUNTIME", "yellow"),
        "WAITING_INPUT": ("WAITING INPUT", "yellow"),
        "WAITING_CONFIRMATION": ("WAITING CONFIRMATION", "bright_yellow"),
        "COLLECTING_INPUT": ("COLLECTING INPUT", "cyan"),
        "RUNNING": ("RUNNING", "cyan"),
    }
    status_label, accent = labels.get(status, (status, "cyan"))
    header = [
        Text(f"Session: {session_id}  ·  {status_label}", style=f"bold {accent}"),
        Text(
            f"Runtime: steps {metrics.get('steps', 0)} | tokens {metrics.get('tokens', 0)} | "
            f"elapsed {metrics.get('duration_seconds', 0)}s",
            style="dim",
        ),
    ]
    message = result.get("message") or progress.get("detail")
    if message:
        header.append(Text(f"Result: {message}"))
    output_descriptions = interaction.get("outputs") if isinstance(interaction.get("outputs"), list) else []
    output_by_path = {
        str(item.get("path")): item for item in output_descriptions if isinstance(item, dict) and item.get("path")
    }
    body: list[Any] = [Group(*header)]
    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    if outputs:
        table = Table(title="Artifact Check", box=box.SIMPLE_HEAVY, header_style="bold magenta", expand=True)
        table.add_column("State", width=10)
        table.add_column("Output", min_width=22, max_width=42, overflow="fold")
        table.add_column("Meaning", min_width=32, max_width=75, overflow="fold")
        for name, state in outputs.items():
            if not isinstance(state, dict):
                continue
            path = str(state.get("path") or "")
            descriptor = output_by_path.get(path, {})
            exists = bool(state.get("exists"))
            table.add_row(
                Text("READY" if exists else "MISSING", style="green" if exists else "red"),
                f"{descriptor.get('label') or name}\n{_wrap_skill_path(path)}",
                str(descriptor.get("description") or "Declared Skill output."),
            )
        body.append(table)
    workflow = session.get("workflow") if isinstance(session.get("workflow"), dict) else {}
    if workflow:
        body.append(_workflow_rich_table(workflow))
    trace_file = result.get("trace_file")
    if trace_file:
        body.append(Text(f"Trace: {trace_file}", style="dim"))
    if status in {"WAITING_RUNTIME", "WAITING_INPUT", "WAITING_CONFIRMATION", "FAILED"}:
        body.append(
            Text(
                f"Resume: researchos run-skill {session.get('skill_name', 'SKILL')} --workspace {workspace} --session-id {session_id} --resume",
                style="yellow",
            )
        )
    else:
        body.append(Text(f"Inspect sessions: researchos skill-status --workspace {workspace}", style="dim"))
    return _render_skill_rich(
        Panel(Group(*body), title=f"SKILL 执行总结 · {session.get('skill_name', 'unknown')}", border_style=accent, expand=True),
        no_color=no_color,
    )


def render_skill_status_panel(
    *,
    workspace: Path,
    entries: Iterable[tuple[Path, dict[str, Any]]],
) -> str:
    """Render all persisted Skill sessions as scan-friendly terminal cards."""

    width = 84
    cards = list(entries)
    lines = ["", "═" * width, f"SKILL 会话状态 · {workspace}", "═" * width]
    state_labels = {
        "READY": "输入已就绪",
        "RUNNING": "运行中",
        "WAITING_INPUT": "等待补齐输入",
        "WAITING_CONFIRMATION": "等待人工确认执行",
        "WAITING_RUNTIME": "等待运行环境恢复",
        "COMPLETED": "已完成",
        "FAILED": "执行失败",
    }
    phase_labels = {
        "starting": "正在建立运行上下文",
        "preparing_step": "正在准备下一步",
        "awaiting_llm": "等待模型返回工具调用",
        "llm_response_received": "正在校验模型返回",
        "tool_running": "正在执行工具",
        "tool_completed": "工具已完成",
        "tool_failed": "工具执行失败",
        "waiting_runtime": "等待运行环境",
        "completed": "已完成",
        "stopped": "已停止",
    }
    for path, session in cards:
        session_id = str(session.get("session_id") or path.stem)
        skill_name = str(session.get("skill_name") or "unknown")
        raw_status = str(session.get("status") or "UNKNOWN")
        status = state_labels.get(raw_status, raw_status)
        lines.append("┌" + "─" * (width - 2) + "┐")
        lines.append(f"│ {skill_name} · 会话 {session_id}")
        lines.append(f"│ 状态：{status}")
        request = _status_compact(session.get("request"), 220)
        if request:
            lines.append(f"│ 请求：{request}")
        readiness = session.get("readiness") if isinstance(session.get("readiness"), dict) else {}
        mode = str(readiness.get("workspace_mode") or "standalone")
        lines.append(f"│ 运行模式：{'项目 workspace' if mode == 'project' else '独立 workspace'}")
        missing = [
            str(item.get("label") or item.get("id") or "input")
            for item in readiness.get("inputs", [])
            if isinstance(item, dict) and item.get("required") and item.get("state") != "ready"
        ]
        if missing:
            lines.append("│ 待补充：" + "、".join(missing))
        progress = session.get("progress") if isinstance(session.get("progress"), dict) else {}
        if progress:
            phase = str(progress.get("phase") or "unknown")
            step = progress.get("step")
            step_limit = progress.get("step_limit")
            position = f"步骤 {step}/{step_limit}" if step is not None and step_limit else "当前阶段"
            lines.append(f"│ 进度：{position} · {phase_labels.get(phase, phase)}")
            if progress.get("tool_name"):
                lines.append(f"│ 当前工具：{progress['tool_name']}")
            detail = _status_compact(progress.get("detail"), 220)
            if detail:
                lines.append(f"│ 说明：{detail}")
        workflow = session.get("workflow") if isinstance(session.get("workflow"), dict) else {}
        if workflow:
            phase = _workflow_current_phase(workflow)
            if phase:
                lines.append(f"│ 工作流：{phase.get('label', phase.get('id'))} · {_workflow_status_label(phase.get('status'))}")
        result = session.get("last_result") if isinstance(session.get("last_result"), dict) else {}
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        if outputs:
            existing = sum(1 for item in outputs.values() if isinstance(item, dict) and item.get("exists"))
            lines.append(f"│ 产物：{existing}/{len(outputs)} 个预期文件存在")
        lines.append(f"│ 会话文件：{path.relative_to(workspace)}")
        intake_packet = session.get("intake_packet")
        if intake_packet:
            lines.append(f"│ 材料清单：{intake_packet}")
        if raw_status in {"WAITING_INPUT", "WAITING_CONFIRMATION", "WAITING_RUNTIME", "FAILED"}:
            lines.append(
                "│ 恢复：researchos run-skill "
                f"{skill_name} --workspace {workspace} --session-id {session_id} --resume"
            )
        elif raw_status == "RUNNING":
            lines.append("│ 下一步：稍后重复执行 skill-status 可查看持久化进度；完整事件见 trace。")
        elif raw_status == "COMPLETED":
            lines.append("│ 下一步：查看上面的产物或启动下一项 Skill。")
        lines.append("└" + "─" * (width - 2) + "┘")
    lines.append("═" * width)
    return "\n".join(lines)


def render_skill_status_panel_rich(
    *,
    workspace: Path,
    entries: Iterable[tuple[Path, dict[str, Any]]],
    no_color: bool = False,
) -> str:
    """Render resumable sessions as a single sortable-looking status table."""

    phase_labels = {
        "starting": "启动上下文",
        "preparing_step": "准备下一步",
        "awaiting_llm": "等待模型动作",
        "llm_response_received": "校验模型返回",
        "tool_running": "执行工具",
        "tool_completed": "工具完成",
        "tool_failed": "工具失败",
        "waiting_runtime": "等待运行环境",
        "completed": "已完成",
        "stopped": "已停止",
    }
    status_styles = {
        "READY": ("READY", "green"),
        "RUNNING": ("RUNNING", "cyan"),
        "WAITING_INPUT": ("WAITING INPUT", "yellow"),
        "WAITING_CONFIRMATION": ("WAITING CONFIRMATION", "bright_yellow"),
        "WAITING_RUNTIME": ("WAITING RUNTIME", "yellow"),
        "COMPLETED": ("COMPLETED", "green"),
        "FAILED": ("FAILED", "bright_red"),
    }
    table = Table(title=f"Skill Sessions · {workspace}", box=box.SIMPLE_HEAVY, header_style="bold cyan", expand=True)
    table.add_column("Skill / Session", min_width=22, max_width=42, overflow="fold")
    table.add_column("Status", min_width=15, max_width=19)
    table.add_column("Progress", min_width=18, max_width=34, overflow="fold")
    table.add_column("Current Detail", min_width=28, max_width=62, overflow="fold")
    table.add_column("Action", min_width=28, max_width=56, overflow="fold")
    count = 0
    for path, session in entries:
        count += 1
        session_id = str(session.get("session_id") or path.stem)
        skill_name = str(session.get("skill_name") or "unknown")
        raw_status = str(session.get("status") or "UNKNOWN")
        label, style = status_styles.get(raw_status, (raw_status, "white"))
        progress = session.get("progress") if isinstance(session.get("progress"), dict) else {}
        phase = str(progress.get("phase") or "")
        step = progress.get("step")
        step_limit = progress.get("step_limit")
        progress_label = phase_labels.get(phase, phase or "no progress record")
        if step is not None and step_limit:
            progress_label = f"{progress_label} · {step}/{step_limit}"
        readiness = session.get("readiness") if isinstance(session.get("readiness"), dict) else {}
        missing = [
            str(item.get("label") or item.get("id") or "input")
            for item in readiness.get("inputs", [])
            if isinstance(item, dict) and item.get("required") and item.get("state") != "ready"
        ]
        detail = _status_compact(progress.get("detail") or session.get("request"), 180)
        workflow = session.get("workflow") if isinstance(session.get("workflow"), dict) else {}
        workflow_phase = _workflow_current_phase(workflow) if workflow else None
        if workflow_phase:
            phase_line = (
                f"Workflow: {workflow_phase.get('label', workflow_phase.get('id'))} "
                f"({ _workflow_status_label(workflow_phase.get('status')) })"
            )
            detail = (phase_line + "\n" + detail) if detail else phase_line
        if missing:
            detail = (detail + "\n" if detail else "") + "Missing: " + "、".join(missing)
        if raw_status in {"WAITING_INPUT", "WAITING_CONFIRMATION", "WAITING_RUNTIME", "FAILED"}:
            action = f"resume --session-id {session_id}"
        elif raw_status == "COMPLETED":
            action = "Inspect declared outputs"
        else:
            action = "Run skill-status again for durable progress"
        table.add_row(
            f"{skill_name}\n{session_id}",
            Text(label, style=f"bold {style}"),
            progress_label,
            detail or "-",
            action,
        )
    if not count:
        table.add_row("-", "-", "-", "No Skill session found.", "-")
    footer = Text(
        "恢复示例：researchos run-skill <skill> --workspace <workspace> --session-id <session> --resume",
        style="dim",
    )
    return _render_skill_rich(Panel(Group(table, footer), title="ResearchOS · Skill Recovery", border_style="cyan", expand=True), no_color=no_color)


def _render_skill_rich(renderable: Any, *, no_color: bool) -> str:
    width = max(100, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=not no_color,
        color_system=None if no_color else "truecolor",
        no_color=no_color,
        width=width,
        highlight=False,
        _environ={"COLUMNS": str(width), "LINES": "40"},
    )
    console.print(renderable)
    return buffer.getvalue().rstrip()


def _wrap_skill_path(path: object) -> str:
    """Wrap path-like strings at directory boundaries, never inside a token."""

    return str(path or "").replace("/", "/\n")


def _status_compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."


def _session_workflow_from_file(session_file: Path) -> dict[str, Any]:
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("workflow") if isinstance(data, dict) and isinstance(data.get("workflow"), dict) else {}


def _workflow_current_phase(workflow: dict[str, Any]) -> dict[str, Any] | None:
    current = str(workflow.get("current_phase") or "")
    phases = workflow.get("phases") if isinstance(workflow.get("phases"), list) else []
    for phase in phases:
        if isinstance(phase, dict) and str(phase.get("id") or "") == current:
            return phase
    return next((phase for phase in phases if isinstance(phase, dict)), None)


def _workflow_status_label(value: object) -> str:
    return {
        "pending": "待执行",
        "running": "进行中",
        "completed": "完成",
        "waiting_input": "等待补料",
        "waiting_evidence": "等待证据",
        "skipped": "已跳过",
    }.get(str(value or "pending"), str(value or "待执行"))


def _workflow_plain_lines(workflow: dict[str, Any], *, width: int) -> list[str]:
    phases = workflow.get("phases") if isinstance(workflow.get("phases"), list) else []
    if not phases:
        return []
    lines = ["─" * width, "集成工作流"]
    summary = str(workflow.get("summary") or "").strip()
    if summary:
        lines.append(f"目标：{summary}")
    for index, phase in enumerate(phases, start=1):
        if not isinstance(phase, dict):
            continue
        marker = "●" if phase.get("id") == workflow.get("current_phase") else "○"
        gate = " · 人工决策" if phase.get("human_gate") else ""
        lines.append(
            f"{marker} {index}. {phase.get('label', phase.get('id'))} · "
            f"{_workflow_status_label(phase.get('status'))}{gate}"
        )
        if phase.get("summary"):
            lines.append(f"  结果：{_status_compact(phase.get('summary'), 180)}")
        if phase.get("evidence_boundary"):
            lines.append(f"  边界：{_status_compact(phase.get('evidence_boundary'), 180)}")
    return lines


def _workflow_rich_table(workflow: dict[str, Any]) -> Table:
    table = Table(title="Integrated Workflow", box=box.SIMPLE_HEAVY, header_style="bold green", expand=True)
    table.add_column("State", width=14)
    table.add_column("Phase", min_width=20, max_width=34, overflow="fold")
    table.add_column("Objective / Current Result", min_width=34, max_width=76, overflow="fold")
    for phase in workflow.get("phases", []):
        if not isinstance(phase, dict):
            continue
        current = phase.get("id") == workflow.get("current_phase")
        raw_status = str(phase.get("status") or "pending")
        style = {
            "completed": "green",
            "running": "cyan",
            "waiting_input": "yellow",
            "waiting_evidence": "yellow",
            "skipped": "dim",
        }.get(raw_status, "dim")
        state = Text(("CURRENT · " if current else "") + _workflow_status_label(raw_status), style=style)
        phase_name = str(phase.get("label") or phase.get("id") or "-")
        if phase.get("human_gate"):
            phase_name += "\nHuman Gate"
        detail = str(phase.get("summary") or phase.get("objective") or "-")
        if phase.get("evidence_boundary"):
            detail += "\nBoundary: " + str(phase["evidence_boundary"])
        table.add_row(state, phase_name, detail)
    return table


def iter_sessions(workspace: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    directory = workspace / SESSION_DIR
    if not directory.exists():
        return []
    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            entries.append((path, value))
    return entries
