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
from .presentation import brief_skill_copy, humanize_skill_copy, summarize_tool_capabilities
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
        "detail": "正在检查并整理你提供的材料。",
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
        "detail": "运行环境暂时不可用，当前进度已保存。",
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
        lines.append(f"{marker} [{qualifier}] {requirement.label}：{requirement.description}")
        if status.selected_path:
            lines.append(f"  已使用 `{status.selected_path}`。")
        else:
            lines.append(f"  可放到：{' 或 '.join(f'`{path}`' for path in requirement.paths)}。")
            if status.detail:
                lines.append(f"  当前检查：{status.detail}。")
        if requirement.example:
            lines.append(f"  示例：`{requirement.example}`。")
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
    lines = ["═" * width, f"Skill 准备 · {skill_name}", "═" * width]
    lines.append(f"恢复标识：{session_id}")
    lines.append(f"状态：{'输入已就绪，可以开始' if readiness.ready else '等待补齐输入'}")
    mode_label = "项目 workspace（自动发现候选材料）" if readiness.workspace_mode == "project" else "独立 workspace（由用户提供材料）"
    lines.append(f"运行模式：{mode_label}")
    lines.append(f"会话记录：{session_file.relative_to(readiness.workspace)}")
    intake_packet = readiness.workspace / "user_inputs" / skill_name / "_intake.md"
    if intake_packet.exists():
        lines.append(f"材料清单：{intake_packet.relative_to(readiness.workspace)}")
    if readiness.scanned_roots:
        lines.append("已扫描：" + "、".join(readiness.scanned_roots))
    if readiness.discovered_files:
        lines.append("发现文件：" + "；".join(
            f"{item.relative_path}（{_format_file_size(item.size_bytes)}）" for item in readiness.discovered_files
        ))
    else:
        lines.append("发现文件：没有符合声明输入类型的文件。")
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
        lines.append("输入检查：")
        lines.extend(_requirement_lines(interaction, readiness))
        if interaction.outputs:
            lines.append("─" * width)
            lines.append("完成后会得到：")
            for output in interaction.outputs:
                lines.append(f"• {output.label}：{output.path}")
                lines.append(f"  含义：{output.description}")
    else:
        lines.append("这个 Skill 尚未声明可自动检查的材料要求；将按已有说明运行。")
    workflow = _session_workflow_from_file(session_file)
    if workflow:
        lines.extend(_workflow_plain_lines(workflow, width=width))
    lines.append("─" * width)
    if readiness.ready:
        lines.append("下一步：现在可开始运行；完成后使用 skill-status 查看持久化结果与文件路径。")
    else:
        lines.append("下一步：先说明希望完成什么；随后系统只询问缺少的材料。你可以上传文件或粘贴内容。")
        lines.append("在非交互终端中，系统会保留当前准备状态，等待你补充后恢复。")
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
    mode = "项目中的现有材料" if readiness.workspace_mode == "project" else "本次提供的材料"
    state = "材料已齐全" if readiness.ready else "需要补充材料"
    accent = "green" if readiness.ready else "yellow"
    header = [
        Text(brief_skill_copy(interaction.summary) if interaction and interaction.summary else "Skill 准备", style="bold"),
        Text(f"恢复标识：{session_id}  ·  {state}  ·  材料范围：{mode}", style=f"bold {accent}"),
        Text(f"会话记录：{session_file.relative_to(readiness.workspace)}", style="dim"),
    ]
    if interaction and not readiness.request_ready:
        header.append(Text(f"还需要你说明任务：{humanize_skill_copy(interaction.request_prompt)}", style="yellow"))
        if interaction.example_request:
            header.append(Text(f"示例：{humanize_skill_copy(interaction.example_request)}", style="dim"))
    elif readiness.request:
        header.append(Text(f"任务说明：{readiness.request}", style="dim"))

    body: list[Any] = [Group(*header)]
    if interaction is None:
        body.append(Text("这个 Skill 尚未声明可自动检查的材料要求。", style="yellow"))
    else:
        states = {item.requirement.key: item for item in readiness.input_statuses}
        requirement_labels = {
            requirement.key: requirement.label
            for requirement in interaction.required_inputs + interaction.optional_inputs
        }
        if readiness.scanned_roots:
            body.append(Text("已扫描：" + "、".join(f"`{path}`" for path in readiness.scanned_roots), style="dim"))
        if readiness.discovered_files:
            discoveries = Table(title="发现的候选文件", box=box.SIMPLE_HEAVY, header_style="bold blue", expand=True)
            discoveries.add_column("文件", min_width=24, max_width=72, overflow="fold")
            discoveries.add_column("大小", width=12, justify="right")
            discoveries.add_column("是否会使用", min_width=20, max_width=42, overflow="fold")
            for item in readiness.discovered_files:
                matched_labels = [requirement_labels.get(key, key) for key in item.matching_input_ids]
                relation = "可作为：" + "、".join(matched_labels) if matched_labels else "暂不使用；不属于本 Skill 所需材料"
                discoveries.add_row(item.relative_path, _format_file_size(item.size_bytes), relation)
            body.append(discoveries)
        else:
            body.append(Text("已检查的目录中没有发现可直接使用的材料。", style="yellow"))
        table = Table(title="需要的材料", box=box.SIMPLE_HEAVY, header_style="bold cyan", expand=True)
        table.add_column("状态", width=14)
        table.add_column("材料", min_width=16, max_width=28, overflow="fold")
        table.add_column("用途", min_width=24, max_width=46, overflow="fold")
        table.add_column("已选文件或放置位置", min_width=28, max_width=58, overflow="fold")
        for requirement in interaction.required_inputs + interaction.optional_inputs:
            status = states[requirement.key]
            if status.is_ready:
                state_text = Text(f"已就绪 · {'必需' if requirement.required else '可选'}", style="green")
                destination = _wrap_skill_path(status.selected_path) if status.selected_path else "已就绪"
            elif requirement.required:
                state_text = Text("待补充 · 必需", style="bold yellow")
                destination = "可放到：" + " 或 ".join(_wrap_skill_path(path) for path in requirement.paths)
            else:
                state_text = Text("可选", style="dim")
                destination = "可放到：" + " 或 ".join(_wrap_skill_path(path) for path in requirement.paths)
            if requirement.example and not status.is_ready:
                destination += f"；示例：{requirement.example}"
            table.add_row(
                state_text,
                humanize_skill_copy(requirement.label),
                brief_skill_copy(requirement.description),
                destination,
            )
        body.append(table)
        if interaction.outputs:
            outputs = Table(title="完成后会得到", box=box.SIMPLE_HEAVY, header_style="bold magenta", expand=True)
            outputs.add_column("产物", min_width=22, max_width=38, overflow="fold")
            outputs.add_column("用途", min_width=32, max_width=72, overflow="fold")
            for output in interaction.outputs:
                outputs.add_row(
                    f"{humanize_skill_copy(output.label)} · {_wrap_skill_path(output.path)}",
                    brief_skill_copy(output.description),
                )
            body.append(outputs)
    workflow = _session_workflow_from_file(session_file)
    if workflow:
        body.append(_workflow_rich_table(workflow))
    if readiness.ready:
        body.append(Text("下一步：材料已经齐全。确认执行后，Skill 才会开始处理。", style="green"))
    else:
        body.append(
            Text(
                "下一步：请先说明希望完成什么；随后系统只询问缺少的材料。你可粘贴内容，或把文件放到上表路径。",
                style="yellow",
            )
        )
        body.append(Text("在非交互终端中，系统会保留当前准备状态；补充材料后可用同一恢复标识继续。", style="yellow"))
        body.append(
            Text(
                f"恢复命令：researchos run-skill {skill_name} --workspace {readiness.workspace} --session-id {session_id} --resume",
                style="dim",
            )
        )
    return _render_skill_rich(Panel(Group(*body), title=f"Skill 准备 · {skill_name}", border_style=accent, expand=True), no_color=no_color)


def render_skill_description(
    *,
    skill_name: str,
    skill_path: Path,
    description: str,
    interaction: SkillInteraction | None,
    workflow: SkillWorkflow | None = None,
    capability_profiles: tuple[str, ...] = (),
    tools: list[str] | None = None,
) -> str:
    """Render the deterministic, copyable user contract for ``describe-skill``."""

    width = 76
    lines = ["═" * width, f"Skill · {skill_name}", "═" * width]
    lines.append(f"路径：{skill_path}")
    if capability_profiles:
        lines.append("能力档位：" + ", ".join(capability_profiles))
    if tools:
        lines.append("可用工具：" + ", ".join(tools))
    if interaction is None:
        lines.append(f"用途：{description}")
        lines.append("交互：兼容模式（尚未声明可检查的输入要求；建议补充引导式交互说明）。")
        lines.append("═" * width)
        return "\n".join(lines)
    if interaction.summary:
        lines.append(f"用途：{interaction.summary}")
        if description.strip() and description.strip() != interaction.summary.strip():
            lines.append(f"技术范围：{description}")
    else:
        lines.append(f"用途：{description}")
    lines.append(f"交互方式：{'引导式' if interaction.mode == 'guided' else '兼容模式'}；界面语言：{interaction.language}")
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
    lines.append("完成后会得到")
    for output in interaction.outputs:
        lines.append(f"• {output.path} — {output.description}")
    if workflow is not None:
        lines.extend(_workflow_plain_lines(workflow_as_session_payload(workflow), width=width))
    lines.append("会话与恢复")
    lines.append("• 非交互运行会先检查输入；缺文件时只保存会话状态，不会启动模型。")
    lines.append("• 交互终端可上传文件或粘贴材料；材料收集只整理到 `user_inputs/<skill>/`，不会提前生成研究、实验或论文结果。")
    lines.append("• 初始材料通过检查后，系统会再次询问“执行/暂停”；只有明确执行授权才会启动。中断后可添加 `--resume --session-id <同一会话>`。")
    lines.append("═" * width)
    return "\n".join(lines)


def render_skill_description_rich(
    *,
    skill_name: str,
    skill_path: Path,
    description: str,
    interaction: SkillInteraction | None,
    workflow: SkillWorkflow | None = None,
    capability_profiles: tuple[str, ...] = (),
    tools: list[str] | None = None,
    no_color: bool = False,
    verbose: bool = False,
) -> str:
    """Render a selected Skill's full, human-actionable contract."""

    if interaction is None:
        return _render_skill_rich(
            Panel(
                Group(
                    Text(brief_skill_copy(description)),
                    Text(f"位置：{skill_path}", style="dim"),
                    Text("此 Skill 尚未声明可自动检查的材料要求；请先查看它的使用说明。", style="yellow"),
                ),
                title=f"SKILL · {skill_name}",
                border_style="yellow",
                expand=True,
            ),
            no_color=no_color,
        )
    overview = [
        Text(brief_skill_copy(interaction.summary or description), style="bold"),
        Text(f"位置：{skill_path}", style="dim"),
        Text(f"交互方式：{'引导式' if interaction.mode == 'guided' else '兼容模式'} · {interaction.language}", style="cyan"),
    ]
    if capability_profiles:
        overview.append(Text("能力范围：" + "、".join(capability_profiles), style="green"))
    if tools:
        overview.append(Text("可用能力：" + summarize_tool_capabilities(tools), style="dim"))
        if verbose:
            overview.append(Text("Tool 明细：" + "、".join(tools), style="dim"))
    if verbose and description.strip() and interaction.summary and description.strip() != interaction.summary.strip():
        overview.append(Text(f"技术说明：{humanize_skill_copy(description)}", style="dim"))
    if interaction.example_request:
        overview.append(Text(f"示例请求：{humanize_skill_copy(interaction.example_request)}", style="dim"))
    inputs = Table(title="开始前需要提供", box=box.SIMPLE_HEAVY, header_style="bold cyan", expand=True)
    inputs.add_column("是否必需", width=10)
    inputs.add_column("材料", min_width=18, max_width=28, overflow="fold")
    inputs.add_column("用途", min_width=26, max_width=48, overflow="fold")
    inputs.add_column("可放位置", min_width=28, max_width=56, overflow="fold")
    for requirement in interaction.required_inputs + interaction.optional_inputs:
        inputs.add_row(
            Text("必需" if requirement.required else "可选", style="bold yellow" if requirement.required else "dim"),
            humanize_skill_copy(requirement.label),
            brief_skill_copy(requirement.description),
            " 或 ".join(_wrap_skill_path(path) for path in requirement.paths),
        )
    outputs = Table(title="完成后会得到", box=box.SIMPLE_HEAVY, header_style="bold magenta", expand=True)
    outputs.add_column("产物", min_width=26, max_width=42, overflow="fold")
    outputs.add_column("用途", min_width=32, max_width=78, overflow="fold")
    for output in interaction.outputs:
        outputs.add_row(
            f"{humanize_skill_copy(output.label)} · {_wrap_skill_path(output.path)}",
            brief_skill_copy(output.description),
        )
    recovery = Text(
        "启动时会先查看列出的材料位置，再只询问缺少的材料。已有项目文件也会先被阅读，不会因文件存在就自动当作证据充分。"
        "中断后可恢复：researchos run-skill " + skill_name + " --session-id <同一会话> --resume",
        style="dim",
    )
    workflow_table = _workflow_rich_table(workflow_as_session_payload(workflow)) if workflow is not None else None
    renderables: list[Any] = [Group(*overview), inputs, outputs]
    if workflow_table is not None:
        renderables.append(workflow_table)
    renderables.append(recovery)
    return _render_skill_rich(
        Panel(Group(*renderables), title=f"Skill · {skill_name}", border_style="cyan", expand=True),
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
    lines = ["═" * width, f"Skill 执行总结 · {session.get('skill_name', 'unknown')}", "═" * width]
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
        "COMPLETED": ("已完成", "green"),
        "FAILED": ("执行失败", "bright_red"),
        "WAITING_RUNTIME": ("等待运行环境", "yellow"),
        "WAITING_INPUT": ("等待补齐材料", "yellow"),
        "WAITING_CONFIRMATION": ("等待执行确认", "bright_yellow"),
        "COLLECTING_INPUT": ("正在收集材料", "cyan"),
        "RUNNING": ("正在运行", "cyan"),
    }
    status_label, accent = labels.get(status, (status, "cyan"))
    header = [
        Text(f"会话：{session_id}  ·  {status_label}", style=f"bold {accent}"),
        Text(
            f"运行：步骤 {metrics.get('steps', 0)} | 模型用量 {metrics.get('tokens', 0)} | "
            f"耗时 {metrics.get('duration_seconds', 0)} 秒",
            style="dim",
        ),
    ]
    message = result.get("message") or progress.get("detail")
    if message:
        header.append(Text(f"当前结果：{message}"))
    output_descriptions = interaction.get("outputs") if isinstance(interaction.get("outputs"), list) else []
    output_by_path = {
        str(item.get("path")): item for item in output_descriptions if isinstance(item, dict) and item.get("path")
    }
    body: list[Any] = [Group(*header)]
    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    if outputs:
        table = Table(title="完成文件检查", box=box.SIMPLE_HEAVY, header_style="bold magenta", expand=True)
        table.add_column("状态", width=10)
        table.add_column("产物", min_width=22, max_width=42, overflow="fold")
        table.add_column("用途", min_width=32, max_width=75, overflow="fold")
        for name, state in outputs.items():
            if not isinstance(state, dict):
                continue
            path = str(state.get("path") or "")
            descriptor = output_by_path.get(path, {})
            exists = bool(state.get("exists"))
            table.add_row(
                Text("已生成" if exists else "未生成", style="green" if exists else "red"),
                f"{humanize_skill_copy(descriptor.get('label') or name)} · {_wrap_skill_path(path)}",
                brief_skill_copy(descriptor.get("description") or "该 Skill 生成的输出文件。"),
            )
        body.append(table)
    workflow = session.get("workflow") if isinstance(session.get("workflow"), dict) else {}
    if workflow:
        body.append(_workflow_rich_table(workflow))
    trace_file = result.get("trace_file")
    if trace_file:
        body.append(Text(f"运行轨迹：{trace_file}", style="dim"))
    if status in {"WAITING_RUNTIME", "WAITING_INPUT", "WAITING_CONFIRMATION", "FAILED"}:
        body.append(
            Text(
                f"恢复：researchos run-skill {session.get('skill_name', 'SKILL')} --workspace {workspace} --session-id {session_id} --resume",
                style="yellow",
            )
        )
    else:
        body.append(Text(f"查看会话：researchos skill-status --workspace {workspace}", style="dim"))
    return _render_skill_rich(
        Panel(Group(*body), title=f"Skill 运行结果 · {session.get('skill_name', '未知')}", border_style=accent, expand=True),
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
    lines = ["═" * width, f"Skill 会话状态 · {workspace}", "═" * width]
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
        "READY": ("输入已就绪", "green"),
        "RUNNING": ("正在运行", "cyan"),
        "WAITING_INPUT": ("等待补齐材料", "yellow"),
        "WAITING_CONFIRMATION": ("等待执行确认", "bright_yellow"),
        "WAITING_RUNTIME": ("等待运行环境", "yellow"),
        "COMPLETED": ("已完成", "green"),
        "FAILED": ("执行失败", "bright_red"),
    }
    table = Table(title=f"Skill 会话状态 · {workspace}", box=box.SIMPLE_HEAVY, header_style="bold cyan", expand=True)
    table.add_column("能力 / 会话", min_width=22, max_width=42, overflow="fold")
    table.add_column("状态", min_width=15, max_width=19)
    table.add_column("进度", min_width=18, max_width=34, overflow="fold")
    table.add_column("当前说明", min_width=28, max_width=62, overflow="fold")
    table.add_column("下一步", min_width=28, max_width=56, overflow="fold")
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
        progress_label = phase_labels.get(phase, phase or "暂未记录进度")
        if step is not None and step_limit:
            progress_label = f"{progress_label} · {step}/{step_limit}"
        readiness = session.get("readiness") if isinstance(session.get("readiness"), dict) else {}
        missing = [
            str(item.get("label") or item.get("id") or "input")
            for item in readiness.get("inputs", [])
            if isinstance(item, dict) and item.get("required") and item.get("state") != "ready"
        ]
        detail = humanize_skill_copy(_status_compact(progress.get("detail") or session.get("request"), 180))
        workflow = session.get("workflow") if isinstance(session.get("workflow"), dict) else {}
        workflow_phase = _workflow_current_phase(workflow) if workflow else None
        if workflow_phase:
            phase_line = (
                f"流程：{workflow_phase.get('label', workflow_phase.get('id'))} "
                f"({ _workflow_status_label(workflow_phase.get('status')) })"
            )
            detail = (phase_line + "\n" + detail) if detail else phase_line
        if missing:
            detail = (detail + "\n" if detail else "") + "待补充：" + "、".join(missing)
        if raw_status in {"WAITING_INPUT", "WAITING_CONFIRMATION", "WAITING_RUNTIME", "FAILED"}:
            action = f"恢复：--session-id {session_id} --resume"
        elif raw_status == "COMPLETED":
            action = "查看已声明的产物"
        else:
            action = "稍后再次运行 skill-status 查看进度"
        table.add_row(
            f"{skill_name} · {session_id}",
            Text(label, style=f"bold {style}"),
            progress_label,
            detail or "-",
            action,
        )
    if not count:
        table.add_row("-", "-", "-", "没有找到 Skill 会话。", "-")
    footer = Text(
        "恢复示例：researchos run-skill <skill> --workspace <workspace> --session-id <session> --resume",
        style="dim",
    )
    return _render_skill_rich(Panel(Group(table, footer), title="ResearchOS · Skill 会话恢复", border_style="cyan", expand=True), no_color=no_color)


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
    """Return a path verbatim; Rich handles viewport-dependent wrapping."""

    return str(path or "")


def _format_file_size(size_bytes: int) -> str:
    """Format a scanned file size for people rather than implementation logs."""

    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _status_compact(value: Any, limit: int) -> str:
    """Normalize session text without discarding user-visible information."""

    del limit
    return " ".join(str(value or "").split())


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
    table = Table(title="执行路径", box=box.SIMPLE_HEAVY, header_style="bold green", expand=True)
    table.add_column("状态", width=14)
    table.add_column("步骤", min_width=20, max_width=34, overflow="fold")
    table.add_column("目标与当前结果", min_width=34, max_width=76, overflow="fold")
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
        state = Text(("当前 · " if current else "") + _workflow_status_label(raw_status), style=style)
        phase_name = str(phase.get("label") or phase.get("id") or "-")
        if phase.get("human_gate"):
            phase_name += "\n需要人工确认"
        detail = str(phase.get("summary") or phase.get("objective") or "-")
        if phase.get("evidence_boundary"):
            detail += "\n证据边界：" + str(phase["evidence_boundary"])
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
