from __future__ import annotations

"""Persistent, human-readable session state for guided standalone skills."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable

from ..runtime.errors import ConfigurationError
from .contracts import SkillInteraction, SkillReadiness, readiness_as_dict


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
    path = write_session(workspace, session_id, session)
    return path, session


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
    else:
        lines.append("这是兼容模式 Skill：未声明可校验的输入契约；将按原有提示词运行。")
    lines.append("─" * width)
    if readiness.ready:
        lines.append("下一步：现在可开始运行；完成后使用 skill-status 查看持久化结果与文件路径。")
    else:
        lines.append("下一步：可上传文件到上面的路径后恢复；交互式运行也可粘贴材料，由受限收集流程整理到对应 user_inputs 文件。")
        lines.append("非交互运行不会调用 LLM，始终保留为可恢复 WAITING_INPUT。")
        lines.append(
            f"恢复示例：researchos run-skill {skill_name} --workspace {readiness.workspace} --session-id {session_id} --resume"
        )
    lines.append("═" * width)
    return "\n".join(lines)


def render_skill_description(*, skill_name: str, skill_path: Path, description: str, interaction: SkillInteraction | None) -> str:
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
    lines.append("会话与恢复")
    lines.append("• 非交互运行会先校验输入；缺文件时只写 `_runtime/skill_sessions/<session>.json`，不调用 LLM。")
    lines.append("• 使用 `--interactive` 时，可上传文件或粘贴材料；受限 intake Agent 只整理人工提供的内容到 `user_inputs/<skill>/`，不产生论文/实验产物。")
    lines.append("• 补齐后会在同一命令中重新校验并继续；中断后可添加 `--resume --session-id <同一会话>`。")
    lines.append("═" * width)
    return "\n".join(lines)


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
    trace_file = result.get("trace_file")
    if trace_file:
        lines.append(f"运行轨迹：{trace_file}")
    lines.append("─" * width)
    if status in {"WAITING_RUNTIME", "WAITING_INPUT", "FAILED"}:
        lines.append(
            "恢复：researchos run-skill "
            f"{session.get('skill_name', 'SKILL')} --workspace {workspace} "
            f"--session-id {session_id} --resume"
        )
    else:
        lines.append("查看会话：researchos skill-status --workspace " + str(workspace))
    lines.append("═" * width)
    return "\n".join(lines)


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
        result = session.get("last_result") if isinstance(session.get("last_result"), dict) else {}
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        if outputs:
            existing = sum(1 for item in outputs.values() if isinstance(item, dict) and item.get("exists"))
            lines.append(f"│ 产物：{existing}/{len(outputs)} 个预期文件存在")
        lines.append(f"│ 会话文件：{path.relative_to(workspace)}")
        intake_packet = session.get("intake_packet")
        if intake_packet:
            lines.append(f"│ 材料清单：{intake_packet}")
        if raw_status in {"WAITING_INPUT", "WAITING_RUNTIME", "FAILED"}:
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


def _status_compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."


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
