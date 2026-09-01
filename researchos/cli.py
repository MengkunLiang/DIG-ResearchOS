"""ResearchOS 命令行入口。

这里统一封装 runtime 的几个主要使用场景：
- `run` / `resume`：完整 pipeline 模式，走 StateMachine；
- `run-task`：单 task 调试模式，只跑一个 T-stage；
- `run-skill`：独立运行一个 skill；
- `validate` / `status` / `trace` / `selftest`：辅助诊断命令。
"""

from __future__ import annotations


import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import importlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import sys

import yaml
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .ui.tables import lightweight_ruled_table
from .agents.registry import AGENT_REGISTRY
from .cli_runners import CompletePipelineRunner, SingleTaskRunner
from .orchestration.task_aliases import resolve_public_stage_alias
from .orchestration.t5_t8_bridge import accept_and_ingest_t5_handoff, prepare_t8_state
from .orchestration.task_io_contract import get_task_io, task_import_paths, task_io_contract_source
from .orchestration.state_machine import StateMachine, _validate_t45_post_novelty_formalization
from .pydantic_compat import model_dump
from .runtime.agent import AgentResult
from .runtime.config_audit import build_config_audit_summary
from .runtime.cli_ui import render_startup_summary, show_startup_banner
from .runtime.config import LatexSettings, RuntimeSettings, UISettings, load_runtime_settings, resolve_runtime_config_path
from .runtime.environment import (
    collect_runtime_environment,
    command_version,
    write_runtime_environment,
)
from .runtime.llm_client import LLMClient
from .runtime.logger import configure_file_logging, configure_logging
from .runtime.model_settings import (
    DEFAULT_MODEL_SETTINGS_PATH,
    load_dotenv_for_model_settings,
    inspect_model_settings_source,
    load_model_settings,
    normalize_provider,
    provider_default_api_base,
    provider_requires_api_key,
    provider_requires_api_base,
    resolve_model_settings_path,
    write_api_key_to_dotenv,
    write_model_settings,
)
from .runtime.observability.reporter import public_error_summary
from .runtime.system_config import system_config_path
from .runtime.workflow_mode import (
    AUTO_PRESETS,
    configure_workflow_mode,
    load_workflow_mode,
    parse_auto_execution_setup_answer,
    parse_execution_setup_proposal,
    parse_workflow_mode_answer,
    parse_workflow_mode_proposal,
)
from .latex_templates import (
    available_ccf_template_ids,
    ccf_template_entries,
    normalize_ccf_template_id,
    parse_available_ccf_template_answer,
)
from .runtime.trace import render_trace_for_humans
from .runtime.bridge_catalog import migrate_legacy_bridge_catalogs
from .runtime.literature_contract import build_literature_manifest, migrate_legacy_literature_paths
from .runtime.workspace import (
    WorkspaceInitResult,
    initialize_workspace,
    resolve_workspace_project_id,
    merge_workspace_artifact,
    migrate_workspace_note_directories,
)
from .ideation.formalization import legacy_t45_upgrade_reason
from .ideation.novelty_verdict import extract_final_gate_verdict, is_passing_final_gate_verdict
from .schemas.state import StateYaml
from .schemas.validator import (
    build_declared_outputs_from_state_machine,
    register_builtin_task_checkers,
    validate_declared_outputs,
    validate_prerequisites,
    validate_task_artifacts,
)
from .skills.contracts import (
    check_skill_readiness,
    expected_outputs_from_metadata,
    parse_skill_interaction,
    prepare_skill_intake_packet,
)
from .skills.audit import audit_skill_suite, render_skill_suite_audit
from .skills.workflow import parse_skill_workflow
from .tools.survey_tools import AuditSurveyCoverageTool
from .tools.workspace_policy import WorkspaceAccessPolicy
from .skills.catalog import (
    catalog_entries,
    ordered_skills,
    render_skill_catalog,
    render_skill_catalog_rich,
    search_skill_matches,
    search_skills,
    skills_in_category,
)
from .skills.loader import (
    discover_skills_from_roots,
    is_standalone_skill,
    register_skill_tools,
    resolve_skill,
)
from .skills.routing import managed_skill_route
from .skills.runner import run_skill, run_skill_intake
from .skills.session import (
    iter_sessions,
    load_session,
    record_skill_execution_confirmation_pending,
    record_input_collection_finished,
    record_input_collection_started,
    record_human_input_pause,
    record_readiness,
    record_runtime_pause,
    record_run_result,
    record_run_started,
    render_skill_completion_panel,
    render_skill_completion_panel_rich,
    render_readiness_panel,
    render_readiness_panel_rich,
    render_skill_description,
    render_skill_description_rich,
    render_skill_status_panel,
    render_skill_status_panel_rich,
)
from .tools.builtin import register_builtin_tools
from .tools.human_gate import (
    CLIHumanInterface,
    HumanInterface,
    HumanInputUnavailable,
    build_t2_parameter_llm_interpreter,
    build_t4_directive_llm_interpreter,
    build_workflow_mode_llm_interpreter,
    build_workflow_setup_llm_interpreter,
)
from .ui.workflow_settings import workflow_settings_panel
from .tools.latex_compile import latex_backend_preflight
from .tools.mcp_adapter import connect_stdio_mcp_server, load_mcp_server_configs, register_mcp_servers


# Keep .env support explicit and shared with the model-settings loader. The
# function never overrides values that a shell or container already supplied.
load_dotenv_for_model_settings()
from .tools.registry import ToolRegistry


def ensure_workspace_layout(workspace_dir: Path, runtime_settings: RuntimeSettings) -> None:
    """创建 runtime 运行所需的固定目录。"""

    initialize_workspace(
        workspace_dir,
        create_project_file=False,
        runtime_dir_name=runtime_settings.workspace.runtime_dir,
    )


def _path_is_within(child: Path, parent: Path) -> bool:
    """判断一个路径是否位于另一个路径之下。"""

    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _detect_container_environment() -> dict[str, any]:
    """检测是否在 Docker 容器内运行。

    使用共享的容器检测工具。

    Returns:
        dict: 包含容器环境信息的字典
            - in_container: bool，是否在容器内
            - container_id: str | None，容器 ID
            - hostname: str | None，主机名
    """
    from researchos.runtime.container_detection import is_running_in_container

    return {
        "in_container": is_running_in_container(),
        "container_id": os.getenv("CONTAINER_ID"),
        "hostname": os.getenv("HOSTNAME"),
    }


def _detect_environment_warnings() -> list[str]:
    """检测当前 shell 的环境是否与实际解释器一致。

    容器内模式：
    - 跳过 conda 环境检查（容器内不需要 conda）
    - 只输出容器环境信息

    宿主机模式：
    - 执行完整的环境一致性检查
    - 检查 conda 环境、PATH、解释器等

    这个检查主要用于抓一种很隐蔽但很常见的问题：
    - 提示符看起来已经 `(researchos)`；
    - 但 PATH 里优先命中的 `python` / `researchos` 实际仍来自 base 环境。

    这种错配会直接导致：
    - `python -m researchos.cli` 跑的是错误解释器；
    - `litellm` 看起来"装了又像没装"；
    - console script 与当前代码/依赖不一致。
    """
    # 检测容器环境
    container_env = _detect_container_environment()

    # 容器内模式：跳过 conda 检查
    if container_env["in_container"]:
        return []  # 容器内不需要警告

    # 宿主机模式：执行完整检查
    warnings: list[str] = []
    conda_prefix_raw = os.getenv("CONDA_PREFIX")
    sys_prefix = Path(sys.prefix).resolve()
    sys_executable = Path(sys.executable).resolve()

    if conda_prefix_raw:
        conda_prefix = Path(conda_prefix_raw).resolve()
        if not _path_is_within(sys_executable, conda_prefix):
            warnings.append(
                f"当前 Python 解释器是 {sys_executable}，但激活的 conda 环境目录是 {conda_prefix}。"
            )

    shell_python = shutil.which("python")
    if shell_python:
        shell_python_path = Path(shell_python).resolve()
        if shell_python_path != sys_executable:
            warnings.append(
                f"PATH 中优先命中的 python 是 {shell_python_path}，当前实际运行的解释器是 {sys_executable}。"
            )

    researchos_bin = shutil.which("researchos")
    if researchos_bin:
        researchos_path = Path(researchos_bin).resolve()
        if not _path_is_within(researchos_path, sys_prefix):
            warnings.append(
                f"`researchos` 命令来自 {researchos_path}，但当前 Python 前缀是 {sys_prefix}。"
            )

    if warnings:
        # ``CONDA_DEFAULT_ENV`` describes the shell which *launched* this
        # process.  It can legitimately be ``base`` when a caller invokes an
        # environment's Python by absolute path.  Recommending that shell
        # value used to send an otherwise healthy ResearchOS process back to
        # base, where its editable package or dependencies might be absent.
        # The running interpreter is the sole reliable recovery target.
        warnings.append(
            "请使用当前解释器重新运行："
            f'`"{sys_executable}" -m researchos.cli ...`；'
            "或激活包含该解释器的环境后，再使用 `python -m researchos.cli ...`。"
        )
    return warnings


def _emit_environment_warnings() -> None:
    """把环境信息和警告打印到 stderr。

    容器内模式：
    - 输出容器环境信息
    - 不输出 conda 相关警告

    宿主机模式：
    - 输出环境错配警告
    """
    # 检测容器环境
    container_env = _detect_container_environment()

    stream = sys.stderr

    # 容器内模式：输出容器信息
    if container_env["in_container"]:
        stream.write("[env-info] 运行在 Docker 容器内\n")
        if container_env["container_id"]:
            stream.write(f"[env-info] 容器 ID: {container_env['container_id']}\n")
        if container_env["hostname"]:
            stream.write(f"[env-info] 主机名: {container_env['hostname']}\n")
        stream.flush()
        return

    # 宿主机模式：输出警告
    warnings = _detect_environment_warnings()
    if not warnings:
        return

    stream.write("[env-warning] 检测到当前 shell 环境与实际解释器可能不一致。\n")
    for item in warnings:
        stream.write(f"[env-warning] {item}\n")
    stream.flush()


def _configure_workspace_logging(
    args: argparse.Namespace,
    workspace_dir: Path,
    runtime_settings: RuntimeSettings,
) -> None:
    """把 Python/structlog 调试日志写入 debug log。

    `_runtime/logs/researchos.log` 由 RunLogger 专门做人类时间线，不再承载
    stdlib/structlog/LiteLLM 的普通 INFO。
    """

    configure_file_logging(
        runtime_settings.logs_dir(workspace_dir) / "researchos-debug.log",
        level=args.log_level,
    )
    if not runtime_settings.ui.verbose:
        # Progress, Gate, and error renderers own the normal interactive CLI.
        # Keep structured library/runtime logging in the workspace debug log so
        # a JSON line cannot split a Rich screen or leak provider internals.
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(logging.CRITICAL + 1)


def _runtime_settings_for_args(settings: RuntimeSettings, args: argparse.Namespace) -> RuntimeSettings:
    """Apply CLI UI overrides without mutating the frozen settings object."""

    quiet = (getattr(args, "quiet", False) is True) or settings.ui.quiet
    verbose = (getattr(args, "verbose", False) is True) or settings.ui.verbose
    verbosity = getattr(args, "verbosity", None) or settings.ui.verbosity
    no_color = (getattr(args, "no_color", False) is True) or settings.ui.no_color
    json_events = (getattr(args, "json_events", False) is True) or settings.ui.json_events
    if quiet and verbose:
        verbose = False
    if verbosity not in {"concise", "normal", "detailed"}:
        verbosity = "normal"
    if quiet == settings.ui.quiet and verbose == settings.ui.verbose and verbosity == settings.ui.verbosity and no_color == settings.ui.no_color and json_events == settings.ui.json_events:
        return settings
    return RuntimeSettings(
        workspace=settings.workspace,
        logging=settings.logging,
        human_interface=settings.human_interface,
        agent_behavior=settings.agent_behavior,
        debug=settings.debug,
        ui=UISettings(
            no_banner=settings.ui.no_banner,
            quiet=quiet,
            verbose=verbose,
            verbosity=verbosity,
            no_color=no_color,
            json_events=json_events,
        ),
        web_fetch=settings.web_fetch,
        latex=settings.latex,
    )


def _skill_ui_uses_color(args: argparse.Namespace) -> bool:
    """Keep Skill screens readable in logs while enabling Rich interactively."""

    return not bool(getattr(args, "_effective_no_color", getattr(args, "no_color", False)))


def _render_skill_readiness_for_cli(
    args: argparse.Namespace,
    *,
    skill_name: str,
    session_id: str,
    session_file: Path,
    readiness: Any,
) -> str:
    return render_readiness_panel_rich(
        skill_name=skill_name,
        session_id=session_id,
        session_file=session_file,
        readiness=readiness,
        no_color=not _skill_ui_uses_color(args),
    )


def _render_skill_completion_for_cli(args: argparse.Namespace, *, workspace: Path, session_id: str) -> str:
    return render_skill_completion_panel_rich(
        workspace=workspace,
        session_id=session_id,
        no_color=not _skill_ui_uses_color(args),
    )


def _render_skill_description_for_cli(
    args: argparse.Namespace,
    *,
    skill_name: str,
    skill_path: Path,
    description: str,
    interaction: Any,
    workflow: Any = None,
    capability_profiles: tuple[str, ...] = (),
    tools: list[str] | None = None,
    execution_scope: str = "standalone",
    execution_owner: str = "",
    managed_route: str = "",
) -> str:
    return render_skill_description_rich(
        skill_name=skill_name,
        skill_path=skill_path,
        description=description,
        interaction=interaction,
        workflow=workflow,
        capability_profiles=capability_profiles,
        tools=tools,
        execution_scope=execution_scope,
        execution_owner=execution_owner,
        managed_route=managed_route,
        no_color=not _skill_ui_uses_color(args),
        verbose=bool(getattr(args, "verbose", False)),
    )


def _managed_skill_route_text(skill: Any, workspace: Path) -> str:
    """Return an actionable, state-aware route for a non-standalone Skill."""

    return managed_skill_route(
        skill_name=skill.name,
        execution_scope=skill.execution_scope,
        execution_owner=skill.execution_owner,
        workspace=workspace,
    ).render()


def _render_skill_catalog_for_cli(
    args: argparse.Namespace,
    *,
    skills: Any,
    workspace: Path,
    index_by_name: dict[str, int] | None = None,
    heading: str = "ResearchOS · 独立 Skill 目录",
    notice: str | None = None,
) -> str:
    return render_skill_catalog_rich(
        skills=skills,
        workspace=workspace,
        index_by_name=index_by_name,
        heading=heading,
        notice=notice,
        no_color=not _skill_ui_uses_color(args),
    )


def _print_managed_skill_catalog_for_cli(
    args: argparse.Namespace,
    *,
    workspace: Path,
    pipeline_skills: list[Any],
    executor_templates: list[Any],
) -> None:
    """Show managed modules separately without making them look runnable.

    ``list-skills`` is intentionally a directory of direct sessions.  The
    explicit ``--include-managed`` view exists for auditing and orientation,
    but does not add managed modules to the interactive browser or its numeric
    launch choices.
    """

    table = lightweight_ruled_table(title="受流程管理的 Skill 模块（不能用 run-skill 启动）", header_style="bold yellow", expand=True)
    table.add_column("类型", min_width=16, max_width=24, overflow="fold")
    table.add_column("模块", min_width=26, max_width=42, overflow="fold")
    table.add_column("正确入口", min_width=44, max_width=86, overflow="fold")
    for skill in sorted(pipeline_skills, key=lambda item: item.name):
        route = _managed_skill_route_text(skill, workspace).replace("\n", " ")
        table.add_row("Pipeline-owned", skill.name, route)
    for skill in sorted(executor_templates, key=lambda item: item.name):
        route = _managed_skill_route_text(skill, workspace).replace("\n", " ")
        table.add_row("External-executor template", skill.name, route)
    _cli_console(args).print(
        Panel(
            Group(
                Text(
                    "这些模块是工作流或外部执行器的组成部分。它们不会出现在 `browse-skills` 的可启动序号中，"
                    "也不能通过 `run-skill` 绕过上游材料和状态检查。",
                    style="yellow",
                ),
                table,
            ),
            title="ResearchOS · Skill 分层说明",
            border_style="yellow",
            expand=True,
        )
    )


def _render_skill_status_for_cli(args: argparse.Namespace, *, workspace: Path, entries: Any) -> str:
    return render_skill_status_panel_rich(
        workspace=workspace,
        entries=entries,
        no_color=not _skill_ui_uses_color(args),
    )


def _build_human_interface(
    runtime_settings: RuntimeSettings,
    *,
    llm_client: LLMClient | None = None,
) -> HumanInterface:
    """按 runtime 配置构造人机接口。

    当前 runtime 只实现了 CLI backend，因此对未知 backend 直接 fail fast，
    避免用户以为自己已经切到了一个并不存在的 Web/API 模式。
    """

    backend = runtime_settings.human_interface.backend.lower().strip()
    if backend in {"", "cli"}:
        interpreter = build_t2_parameter_llm_interpreter(llm_client) if llm_client is not None else None
        t4_interpreter = build_t4_directive_llm_interpreter(llm_client) if llm_client is not None else None
        workflow_mode_interpreter = build_workflow_mode_llm_interpreter(llm_client) if llm_client is not None else None
        workflow_setup_interpreter = build_workflow_setup_llm_interpreter(llm_client) if llm_client is not None else None
        return CLIHumanInterface(
            t2_parameter_interpreter=interpreter,
            t4_directive_interpreter=t4_interpreter,
            workflow_mode_interpreter=workflow_mode_interpreter,
            workflow_setup_interpreter=workflow_setup_interpreter,
            no_color=runtime_settings.ui.no_color,
        )
    raise SystemExit(f"Unsupported human_interface.backend: {runtime_settings.human_interface.backend}")


@dataclass
class PreparedRuntime:
    """CLI 启动后交给各命令使用的公共依赖。"""

    skill_roots: list[Path]
    registry: ToolRegistry
    llm_client: LLMClient
    skill_count: int = 0
    mcp_server_count: int = 0
    mcp_tool_count: int = 0
    mcp_clients: list[object] = field(default_factory=list)

    async def aclose(self) -> None:
        close = getattr(self.llm_client, "aclose", None)
        try:
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result
        finally:
            for client in reversed(self.mcp_clients):
                client_close = getattr(client, "aclose", None)
                if not callable(client_close):
                    continue
                result = client_close()
                if hasattr(result, "__await__"):
                    await result


class LLMConfigurationWizardError(RuntimeError):
    """A setup-wizard failure that must not be rendered as a runtime outage.

    The startup commands call the configuration wizard before the pipeline has
    started.  A failed connection test or an unexpected wizard exception is a
    configuration result, not evidence that the workspace, PDF tooling, or
    pipeline runtime is unavailable.  Keeping that distinction explicit stops
    higher-level command handlers from collapsing an actionable setup result
    into the generic runtime-unavailable panel.
    """

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class _CliSignalController:
    """Route an interrupt to one CLI command instead of every loop task.

    Cancelling ``asyncio.all_tasks`` also cancels progress reporting, runtime
    cleanup, and the command frame that persists ``state.yaml``.  A CLI
    invocation has one operation task, so that is the only task a first
    interrupt is allowed to cancel.  A second interrupt is an explicit request
    to terminate immediately.
    """

    loop: asyncio.AbstractEventLoop
    operation_task: asyncio.Task[object]
    interrupt_count: int = 0
    _loop_signals: set[int] = field(default_factory=set)
    _fallback_handlers: dict[int, object] = field(default_factory=dict)
    _closed: bool = False

    def install(self) -> "_CliSignalController":
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, self.handle_interrupt, sig)
                self._loop_signals.add(sig)
            except (NotImplementedError, RuntimeError):
                # Windows' Proactor loop does not implement add_signal_handler.
                # Preserve the previous handler so direct signal registration is
                # scoped to this command as well.
                self._fallback_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, lambda *_args, _sig=sig: self.handle_interrupt(_sig))
        return self

    def handle_interrupt(self, sig: int) -> None:
        """Handle a first graceful interrupt or an explicit second hard stop."""

        self.interrupt_count += 1
        if self.interrupt_count == 1:
            label = "Ctrl+C" if sig == signal.SIGINT else "终止信号"
            print(
                f"\n[Interrupt] 已收到 {label}，正在保存进度并安全暂停；再次按 Ctrl+C 将立即退出。",
                file=sys.stderr,
                flush=True,
            )
            if not self.operation_task.done():
                self.operation_task.cancel(f"ResearchOS interrupted by {label}")
            return

        print("\n[Interrupt] 已收到第二次中断，立即退出。", file=sys.stderr, flush=True)
        self.close()
        # A second signal deliberately bypasses asynchronous cleanup.  The
        # first signal already initiated the durable pause path.
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)

    def close(self) -> None:
        """Remove command-scoped handlers when an embedded loop remains alive."""

        if self._closed:
            return
        self._closed = True
        for sig in self._loop_signals:
            self.loop.remove_signal_handler(sig)
        for sig, previous in self._fallback_handlers.items():
            signal.signal(sig, previous)


def install_signal_handlers() -> _CliSignalController:
    """Install command-scoped SIGINT/SIGTERM handling for the current CLI task."""

    loop = asyncio.get_running_loop()
    operation_task = asyncio.current_task(loop=loop)
    if operation_task is None:  # pragma: no cover - asyncio commands always have a task
        raise RuntimeError("ResearchOS signal handling requires an active asyncio task")
    return _CliSignalController(loop=loop, operation_task=operation_task).install()


def _persist_cli_interrupt(args: argparse.Namespace) -> tuple[Path | None, bool]:
    """Persist a first Ctrl+C as a resumable workspace or Skill pause.

    This is deliberately a CLI-boundary fallback.  Agent runners still persist
    their own richer interruption details when cancellation reaches them; this
    path covers startup, provider setup, material intake, and other awaits
    outside those runners.
    """

    raw_workspace = getattr(args, "workspace", None)
    if not raw_workspace:
        return None, False
    workspace_dir = Path(raw_workspace).resolve()
    reason = "已收到 Ctrl+C；当前进度已保存，可使用 resume 继续。"
    persisted = False
    state_path = workspace_dir / "state.yaml"
    if state_path.is_file():
        try:
            state = StateYaml.load_yaml(state_path)
            if state.status != "COMPLETED":
                state_machine = StateMachine(
                    Path(getattr(args, "state_machine", system_config_path("state_machine.yaml"))).resolve(),
                    Path(getattr(args, "gates", "") or system_config_path("gates.yaml")).resolve(),
                )
                state = state_machine.mark_interrupted(state, reason=reason)
                state.last_error = reason
                state.dump_yaml(state_path)
                persisted = True
        except Exception:
            # Ctrl+C must not turn a recoverable interrupt into a traceback just
            # because an old or partially-created workspace has a bad state.
            pass

    if getattr(args, "command", "") == "run-skill":
        skill_name = str(getattr(args, "skill_name", "") or "").strip()
        session_id = str(getattr(args, "session_id", "") or skill_name).strip()
        if session_id and load_session(workspace_dir, session_id) is not None:
            try:
                record_runtime_pause(
                    workspace=workspace_dir,
                    session_id=session_id,
                    error=RuntimeError(reason),
                )
                persisted = True
            except Exception:
                pass
    return workspace_dir, persisted


def _render_cli_interrupt_summary(args: argparse.Namespace, workspace_dir: Path | None, persisted: bool) -> None:
    """Show the only user-facing result needed after a graceful interrupt."""

    if workspace_dir is None:
        message = "命令已停止。当前命令尚未关联 workspace，因此没有需要恢复的项目状态。"
        next_step = "重新运行刚才的命令。"
    elif persisted:
        message = "已安全暂停，已完成的工作和当前状态都已保存。"
        if getattr(args, "command", "") == "run-skill":
            skill_name = str(getattr(args, "skill_name", "") or "<skill>")
            session_id = str(getattr(args, "session_id", "") or skill_name)
            next_step = (
                "继续：python -m researchos.cli run-skill "
                f"{skill_name} --workspace {workspace_dir} --session-id {session_id} --resume"
            )
        else:
            next_step = f"继续：python -m researchos.cli resume --workspace {workspace_dir}"
    else:
        message = "命令已停止；尚未创建可恢复的运行状态。"
        next_step = "检查启动参数后重新运行该命令。"
    _cli_console(args).print(
        Panel(
            Group(Text(message), Text(next_step, style="bold cyan")),
            title="已安全暂停",
            border_style="yellow",
            expand=True,
        )
    )


def _run_async_cli_command(args: argparse.Namespace, command: object) -> int:
    """Run an async CLI command and turn first interrupts into exit code 130."""

    try:
        return asyncio.run(command)
    except (asyncio.CancelledError, KeyboardInterrupt):
        workspace_dir, persisted = _persist_cli_interrupt(args)
        _render_cli_interrupt_summary(args, workspace_dir, persisted)
        return 130


def _resolve_skill_roots(args: argparse.Namespace, workspace_dir: Path) -> list[Path]:
    """计算 skill 搜索根目录。

    默认会尝试：
    - 当前工作目录下的 `skills/`
    - workspace 下的 `skills/`
    用户也可以通过 `--skills-root` 追加自定义路径。
    """

    raw_roots = list(args.skills_root or ["skills"])
    repo_skills_root = Path(__file__).resolve().parents[1] / "skills"
    candidates: list[Path] = []
    seen: set[Path] = set()
    if repo_skills_root.exists():
        candidates.append(repo_skills_root.resolve())
        seen.add(repo_skills_root.resolve())
    for raw_root in raw_roots:
        original = Path(raw_root)
        expanded: list[Path] = []
        if original.is_absolute():
            expanded.append(original.resolve())
        else:
            expanded.append((Path.cwd() / original).resolve())
            expanded.append((workspace_dir / original).resolve())
        for item in expanded:
            if item in seen:
                continue
            seen.add(item)
            candidates.append(item)
    return candidates


def _build_tool_registry(
    skill_roots: list[Path],
    runtime_settings: RuntimeSettings,
    discovered_skills: dict | None = None,
) -> ToolRegistry:
    """构造本次 CLI 运行用到的完整 ToolRegistry。"""

    registry = ToolRegistry()
    register_builtin_tools(registry, runtime_settings)
    register_skill_tools(registry, skill_roots, discovered_skills=discovered_skills)
    return registry


def _load_object(spec: str):
    """按 `pkg.mod:attr` 或 `pkg.mod.attr` 形式加载对象。"""

    if ":" in spec:
        module_name, attr_name = spec.split(":", 1)
    else:
        module_name, _, attr_name = spec.rpartition(".")
    if not module_name or not attr_name:
        raise ValueError(
            "Invalid dotted object spec. Expected 'package.module:attr' or 'package.module.attr'."
        )
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


async def _maybe_register_mcp_tools(
    args: argparse.Namespace,
    registry: ToolRegistry,
) -> tuple[int, int, list[object]]:
    """按需从 MCP 配置中注册远端工具。

    默认使用官方 MCP Python SDK 的 stdio connector；用户只有在接入自定义
    transport 时才需要传入 ``--mcp-connector``。发现到的工具会依照每个
    server 的 ``allowed_agents`` 配置注入 Agent/Skill 的可用工具集合。
    """

    config_path = Path(args.mcp_config).resolve()
    if not config_path.exists():
        return 0, 0, []

    server_configs = load_mcp_server_configs(config_path)
    if not server_configs:
        return 0, 0, []

    connector = _load_object(args.mcp_connector) if args.mcp_connector else connect_stdio_mcp_server
    before = set(registry.available_names())
    clients = await register_mcp_servers(registry, server_configs, connector)
    after = set(registry.available_names())
    registered = sorted(after - before)
    for server_config, client in zip(server_configs, clients, strict=True):
        prefix = "mcp_" + "".join(
            char.lower() if char.isalnum() else "_"
            for char in str(getattr(client, "name", server_config.get("name") or "server"))
        ).strip("_") + "_"
        server_tools = [name for name in registered if name.startswith(prefix)]
        allowed_agents = server_config.get("allowed_agents")
        if allowed_agents is not None and not isinstance(allowed_agents, list):
            raise ValueError(
                f"MCP server {server_config.get('name') or '(unnamed)'} field allowed_agents must be a list"
            )
        registry.grant_dynamic_tools(server_tools, allowed_agents=allowed_agents)
    return len(server_configs), len(registered), list(clients)


def _validate_agent_tools(registry: ToolRegistry) -> None:
    """启动时检查所有正式 agent 的 `tool_names` 都已注册。"""

    available = set(registry.available_names())
    missing: list[str] = []
    for agent_name, agent_cls in AGENT_REGISTRY.items():
        agent = agent_cls()
        for tool_name in agent.spec.tool_names:
            if tool_name not in available:
                missing.append(f"{agent_name}: missing tool '{tool_name}'")
    if missing:
        raise SystemExit("Agent tool validation failed:\n" + "\n".join(missing))


def _is_quiet_args(args: argparse.Namespace, runtime_settings: RuntimeSettings | None = None) -> bool:
    if bool(getattr(args, "quiet", False)):
        return True
    return bool(runtime_settings and runtime_settings.ui.quiet)


def _cli_console(args: argparse.Namespace | None = None) -> Console:
    """Create one Rich console for interactive setup and command guidance."""

    no_color = bool(
        getattr(args, "_effective_no_color", getattr(args, "no_color", False))
        if args is not None
        else False
    )
    return Console(
        force_terminal=not no_color,
        color_system=None if no_color else "truecolor",
        no_color=no_color,
        highlight=False,
        width=max(80, min(140, shutil.get_terminal_size(fallback=(120, 40)).columns)),
    )


def _masked_secret_confirmation(value: object) -> str:
    """Confirm API-key capture without printing the credential itself."""

    secret = str(value or "").strip()
    if not secret:
        return "未设置"
    if re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", secret):
        return f"环境变量引用 {secret}"
    suffix = secret[-4:] if len(secret) >= 4 else "*" * len(secret)
    return f"已设置，长度 {len(secret)}，末尾 …{suffix}"


def _parse_context_window_choice(value: str) -> int:
    """Parse an explicit capacity such as ``128k`` or the ``auto`` sentinel."""

    normalized = value.strip().casefold().replace("_", "").replace(",", "")
    if normalized in {"auto", "自动"}:
        return 0
    match = re.fullmatch(r"(\d+)\s*([km]?)", normalized)
    if not match:
        raise ValueError("请输入 128k、256k、1m、纯数字 token 数，或 auto")
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2)]
    tokens = int(match.group(1)) * multiplier
    if not 4_096 <= tokens <= 100_000_000:
        raise ValueError("上下文容量必须在 4k 到 100m tokens 之间")
    return tokens


def _render_workspace_entry_panel(
    args: argparse.Namespace,
    *,
    title: str,
    message: str,
    workspace: Path,
    state: StateYaml | None = None,
    border_style: str = "yellow",
) -> None:
    """Explain run/resume command semantics before any model or Tool is started."""

    details = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
    details.add_column(style="bold cyan", no_wrap=True)
    details.add_column(overflow="fold")
    details.add_row("Workspace", str(workspace))
    if state is not None:
        details.add_row("项目", state.project_id)
        details.add_row("当前步骤", state.current_task)
        details.add_row("状态", state.status)
    actions = Text()
    if state is None:
        actions.append("新项目：", style="bold")
        actions.append(f"python -m researchos.cli run --workspace {workspace}\n")
        actions.append("从其他项目指定阶段开始：", style="bold")
        actions.append(
            f"python -m researchos.cli run --workspace {workspace} --from <source-workspace> --start-task <task>"
        )
    else:
        actions.append("继续当前项目：", style="bold")
        actions.append(f"python -m researchos.cli resume --workspace {workspace}")
        if state.status in {"PAUSED", "WAITING_HUMAN", "FAILED"}:
            actions.append("\n从指定阶段重新进入：", style="bold")
            actions.append(f"python -m researchos.cli resume --workspace {workspace} --from-task <task>")
    _cli_console(args).print(
        Panel(
            Group(Text(message), details, actions),
            title=title,
            border_style=border_style,
            expand=True,
        )
    )


def _validate_pipeline_workspace_entry(args: argparse.Namespace, workspace_dir: Path) -> int | None:
    """Enforce unambiguous `run` versus `resume` workspace semantics."""

    state_path = workspace_dir / "state.yaml"
    resume = bool(getattr(args, "resume", False))
    if not state_path.exists():
        if resume:
            _render_workspace_entry_panel(
                args,
                title="无法恢复项目",
                message="该目录没有可恢复的 state.yaml，因此 `resume` 不会创建新项目。",
                workspace=workspace_dir,
                border_style="bright_red",
            )
            return 2
        return None
    if not state_path.is_file():
        _render_workspace_entry_panel(
            args,
            title="项目状态无效",
            message="state.yaml 不是普通文件，无法安全判断该 workspace 的执行状态。",
            workspace=workspace_dir,
            border_style="bright_red",
        )
        return 2
    try:
        state = StateYaml.load_yaml(state_path)
    except Exception as exc:
        _render_workspace_entry_panel(
            args,
            title="项目状态无法读取",
            message=f"state.yaml 无法解析：{exc}",
            workspace=workspace_dir,
            border_style="bright_red",
        )
        return 2
    if not resume:
        _render_workspace_entry_panel(
            args,
            title="检测到已有项目",
            message="目标 workspace 已存在 state.yaml；`run` 只用于新项目，不能隐式继续或覆盖已有状态。",
            workspace=workspace_dir,
            state=state,
        )
        return 2
    if state.status == "COMPLETED":
        upgrade_reason = legacy_t45_upgrade_reason(workspace_dir)
        if upgrade_reason:
            # This is a contract migration, not a reset: retain every old
            # audit, Candidate and Proposal artifact, then resume from the
            # first new formalization phase in a fresh model context.
            state.current_task = "T4.5-FORMALIZE"
            state.status = "PAUSED"
            state.pending_gate = None
            state.paused_at = datetime.now(timezone.utc).isoformat()
            state.last_error = upgrade_reason
            state.task_context["t45_formalization_upgrade"] = {
                "reason": upgrade_reason,
                "requested_at": state.paused_at,
                "from_contract": "monolithic_t45_v1",
                "to_contract": "blueprint_claim_registry_orientation_review_v2",
                "preserves_existing_artifacts": True,
            }
            state.dump_yaml(state_path)
            _render_workspace_entry_panel(
                args,
                title="已准备升级 T4.5 研究方案",
                message=(
                    "检测到旧版 T4.5 的审计已通过，但 Proposal/claims 尚未通过当前统一质量 gate。"
                    "系统已保留原有审计和所有工作区文件，并将从 T4.5-FORMALIZE 用新的独立上下文重新正式化；"
                    "不会重新运行 T4 Candidate 演化或 novelty 检索。"
                ),
                workspace=workspace_dir,
                state=state,
                border_style="bright_cyan",
            )
            return None
        _render_workspace_entry_panel(
            args,
            title="项目已经完成",
            message="该项目状态为 COMPLETED，不能通过 `resume` 重启。请新建 workspace，或显式从其他项目复制前置产物开始。",
            workspace=workspace_dir,
            state=state,
        )
        return 2
    return None


def _render_state_machine_definition_error(
    args: argparse.Namespace,
    state_machine: StateMachine,
    errors: list[str],
) -> None:
    """Show a configuration mismatch as an actionable Rich panel."""

    sources = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
    sources.add_column(style="bold cyan", no_wrap=True)
    sources.add_column(overflow="fold")
    sources.add_row("State machine", str(state_machine.config_path.resolve()))
    sources.add_row("I/O contract", str(task_io_contract_source()))
    sources.add_row("下一步", "python -m researchos.cli validate-config")

    issues = Text()
    for error in errors[:8]:
        issues.append("- ", style="bold red")
        issues.append(error)
        issues.append("\n")
    if len(errors) > 8:
        issues.append(f"另有 {len(errors) - 8} 项配置问题；请运行 validate-config 查看完整列表。")

    _cli_console(args).print(
        Panel(
            Group(
                Text("当前加载的 workflow 配置与运行时 I/O contract 不一致，尚未启动任何 Agent。", style="bold"),
                sources,
                issues,
            ),
            title="[配置检查] 无法启动工作流",
            border_style="bright_red",
            expand=True,
        )
    )


def _render_runtime_unavailable(
    args: argparse.Namespace,
    *,
    message: str,
    error: Exception | str | None = None,
) -> None:
    """Explain a recoverable runtime failure without dumping provider internals."""

    body: list[object] = [Text(message)]
    detail = _safe_runtime_failure_detail(error)
    if detail:
        body.append(Text(detail, style="dim"))
    body.append(Text("当前工作区未被推进；修复后可使用原命令或 resume 继续。", style="yellow"))
    if bool(getattr(args, "verbose", False)) and error:
        body.append(Text(f"诊断：{error}", style="dim"))
    _cli_console(args).print(
        Panel(Group(*body), title="运行环境暂时不可用", border_style="bright_yellow", expand=True)
    )


def _render_llm_configuration_wizard_failure(
    args: argparse.Namespace,
    error: LLMConfigurationWizardError,
) -> None:
    """Report a nested setup failure without mislabelling it as runtime I/O.

    ``configure-llm`` already renders provider-specific failures such as an
    invalid key.  This follow-up panel only establishes the command boundary:
    the original run has not started, saved configuration is retained, and the
    next action is to repair the one model connection.
    """

    body: list[object] = [Text(str(error))]
    body.append(Text("当前工作区未被推进；已保存的模型设置不会被回滚。", style="yellow"))
    body.append(
        Text(
            "修正后可直接重新运行原命令；也可先运行 `python -m researchos.cli configure-llm` 单独检查连接。",
            style="cyan",
        )
    )
    if bool(getattr(args, "verbose", False)) and error.__cause__ is not None:
        body.append(Text(f"诊断：{error.__cause__}", style="dim"))
    _cli_console(args).print(
        Panel(Group(*body), title="模型配置向导未完成", border_style="bright_yellow", expand=True)
    )


def _render_runtime_preparation_failure(
    args: argparse.Namespace,
    *,
    message: str,
    error: Exception,
) -> int:
    """Render the correct startup boundary error and return its exit code."""

    if isinstance(error, LLMConfigurationWizardError):
        _render_llm_configuration_wizard_failure(args, error)
        return error.exit_code
    _render_runtime_unavailable(args, message=message, error=error)
    return 1


def _safe_runtime_failure_detail(error: Exception | str | None) -> str:
    """Turn common startup failures into an actionable, secret-free sentence."""

    text = " ".join(str(error or "").split()).lower()
    if not text:
        return "可运行 `python -m researchos.cli selftest` 查看模型连接检查。"
    if any(token in text for token in ("api key", "authentication", "unauthorized", "401", "invalid key")):
        return "模型认证未通过。请检查 model_settings.yaml 中的 provider、API key 与 API URL。"
    if any(token in text for token in ("timeout", "timed out", "connection", "unavailable", "503", "502")):
        return "模型服务暂时无响应。确认 URL 后可直接重试，或运行 selftest 查看连接状态。"
    if any(token in text for token in ("model", "not found", "404", "deployment")):
        return "模型名称或部署名称不可用。请检查 model_settings.yaml 中的 model。"
    if any(token in text for token in ("missing", "configuration", "model_settings")):
        return "模型配置不完整。运行 `python -m researchos.cli configure-llm` 可只补充缺失项。"
    if "litellm" in text or "pdfplumber" in text:
        return "本地依赖未就绪。运行 `python -m researchos.cli selftest` 查看需要安装的组件。"
    return "可运行 `python -m researchos.cli selftest` 查看模型连接和本地依赖检查。"


def _startup_banner_enabled(args: argparse.Namespace, runtime_settings: RuntimeSettings) -> bool:
    return not (
        _is_quiet_args(args, runtime_settings)
        or bool(getattr(args, "no_banner", False))
        or bool(runtime_settings.ui.no_banner)
    )


async def _maybe_run_selftest(args: argparse.Namespace, llm_client: LLMClient) -> None:
    """按需执行 endpoint 自检。"""

    status = llm_client.configuration_status()
    if not status.get("ready", False):
        missing = ", ".join(status.get("missing") or ["LLM configuration"])
        raise RuntimeError(
            "[需要配置模型] 缺少 "
            f"{missing}。运行 `python -m researchos.cli configure-llm` 配置并测试唯一的模型连接。"
        )

    if getattr(args, "skip_startup_selftest", False):
        return

    # Selecting "现在配置" already performs the same endpoint selftest after
    # persisting the missing fields.  A second request immediately afterwards
    # adds latency and can turn a successful setup into an apparently random
    # startup failure when a provider briefly throttles or disconnects.  The
    # marker is scoped to this in-memory CLI invocation, never persisted in
    # user settings or workspace state.
    if bool(getattr(args, "_llm_connection_verified_during_setup", False)):
        return

    # 对 run / resume / run-task / run-skill，默认执行启动自检；
    # `--startup-selftest` 保留作向后兼容参数，不再是唯一触发开关。
    should_selftest = args.command in {"run", "resume", "run-task", "run-skill"}
    should_selftest = should_selftest or getattr(args, "startup_selftest", False)
    if not should_selftest:
        return
    llm_results = await llm_client.selftest()
    dependency_results = _dependency_selftest()
    failed = {name: item for name, item in llm_results.items() if not item.get("ok")}
    if not bool(getattr(args, "quiet", False)):
        _render_selftest_summary(
            args,
            llm_results=llm_results,
            dependency_results=dependency_results,
            title="启动检查",
        )
    if failed:
        details = "; ".join(
            f"{name}: {_selftest_compact_error(item.get('error'))}"
            for name, item in failed.items()
        )
        raise RuntimeError(f"模型启动检查未通过：{details}")


def _render_llm_setup_required(status: dict[str, Any]) -> None:
    _render_llm_setup_required_rich(status)


def _render_llm_setup_required_rich(
    status: dict[str, Any],
    *,
    args: argparse.Namespace | None = None,
) -> None:
    missing = ", ".join(status.get("missing") or ["LLM configuration"])
    details = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
    details.add_column(style="bold cyan", no_wrap=True)
    details.add_column(overflow="fold")
    details.add_row("缺少", missing)
    details.add_row("配置文件", str(status.get("settings_path") or "config/model_settings.yaml"))
    details.add_row("连接模式", "所有 Agent 和 Skill 共用一个 provider 与一个 model")
    details.add_row("下一步", "可选择引导填写，或直接编辑下方实际生效的 model_settings.yaml；模板文件不会自动生效")
    _cli_console(args).print(
        Panel(
            Group(
                Text("尚未找到完整的模型连接配置。ResearchOS 不会在没有 model 的情况下启动任务。"),
                details,
            ),
            title="需要配置模型",
            border_style="bright_yellow",
            expand=True,
        )
    )


def _render_llm_configuration_step(
    args: argparse.Namespace,
    *,
    step: str,
    title: str,
    description: str,
    current: str,
) -> None:
    _cli_console(args).print(
        Panel(
            Group(
                Text(description),
                Text(f"当前设置：{current or '未设置'}", style="dim"),
            ),
            title=f"模型配置 {step} · {title}",
            border_style="cyan",
            expand=True,
            padding=(0, 1),
        )
    )


def _render_llm_provider_choices(args: argparse.Namespace, *, current: str) -> None:
    """Present provider presets as a readable chooser instead of one long prompt."""

    choices = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True)
    choices.add_column("类别", style="bold cyan", no_wrap=True)
    choices.add_column("可直接输入的 Provider", overflow="fold")
    choices.add_row("常用云端", "deepseek、qwen、siliconflow、openai、openrouter")
    choices.add_row("其他云端", "anthropic、google、groq、together、fireworks、mistral、cohere、xai、perplexity、cerebras、nvidia_nim、moonshot、zhipu、minimax")
    choices.add_row("本地部署", "ollama、lm_studio、vllm（通常不需要 API key）")
    choices.add_row("自定义网关", "openai_compatible（随后填写完整 API URL）")
    _cli_console(args).print(
        Panel(
            Group(
                Text("输入一个名称即可；已知云端服务会自动使用官方 URL。回车保留当前选择；未列出的兼容 OpenAI 服务请选择 openai_compatible。"),
                choices,
                Text(f"当前选择：{current}", style="dim"),
            ),
            title="模型配置 1 · Provider",
            border_style="cyan",
            expand=True,
            padding=(0, 1),
        )
    )


def _render_llm_context_capacity_notice(
    args: argparse.Namespace,
    *,
    settings_path: Path,
) -> None:
    """Explain the context fallback that lives beside the model connection."""

    settings = load_model_settings(settings_path)
    fallback = settings["context_window_fallback"]
    truncation = settings["truncation"]
    rate_limit = settings["rate_limit"]
    recovery = settings["fallback"]
    details = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
    details.add_column(style="bold cyan", no_wrap=True)
    details.add_column(overflow="fold")
    details.add_row("当前兜底", f"{fallback:,} tokens")
    override = int(settings.get("context_window_override") or 0)
    details.add_row(
        "人工声明",
        f"{override:,} tokens（优先于自动探测）" if override else "未设置：先自动探测模型容量",
    )
    details.add_row("何时生效", "仅当 provider/model 未报告可核验的真实 context window 时使用")
    details.add_row("优先级", "provider 报告的真实容量优先；该值不会覆盖已发现的真实容量")
    details.add_row("它表示什么", "整次模型调用共享的总上下文容量，不是单独的用户输入上限")
    details.add_row("容量包含", "system prompt、研究材料、对话历史、Tool 输入/结果和为回复预留的空间")
    details.add_row(
        "单次模型请求 deadline",
        f"{recovery['request_timeout_seconds']:,} 秒；与下方同模型 fallback 重试共同控制正式科研调用",
    )
    configured_history_cap = int(truncation.get("max_input_tokens") or 0)
    details.add_row(
        "单次保留输入上限",
        f"{configured_history_cap:,} tokens；只压缩较早的会话/Tool 历史，不会减少 PDF 阅读或已保存笔记"
        if configured_history_cap > 0
        else "跟随有效总上下文容量（默认）；无需维护第二个数字",
    )
    details.add_row(
        "本地限流",
        "关闭（默认；按 provider 实际配额处理）"
        if not rate_limit["enabled"]
        else f"已启用：{rate_limit['tokens_per_minute']:,} TPM，burst {rate_limit['burst']:,}",
    )
    details.add_row("配置位置", str(settings_path.resolve()))
    details.add_row("同一文件中的字段", "context_window_override（可选）、context_window_fallback、fallback.request_timeout_seconds、truncation（可选 max_input_tokens）和可选 rate_limit")
    _cli_console(args).print(
        Panel(
            Group(
                Text("ResearchOS 会先尝试识别当前 model 的真实上下文容量；识别不到时才使用同一模型配置文件中的下面字段。"),
                details,
            ),
            title="上下文容量说明",
            border_style="cyan",
            expand=True,
        )
    )


def _render_llm_manual_edit_instructions(
    args: argparse.Namespace,
    *,
    settings_path: Path,
) -> None:
    """Show exact local paths and validation before waiting for a manual edit."""

    target_path = settings_path.expanduser().resolve()
    template_path = DEFAULT_MODEL_SETTINGS_PATH.with_name("model_settings.example.yaml").resolve()
    details = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
    details.add_column(style="bold cyan", no_wrap=True)
    details.add_column(overflow="fold")
    details.add_row("现在要编辑", str(target_path))
    details.add_row("参考模板（不会生效）", str(template_path))
    details.add_row("最小必填字段", "provider、api_key、model；仅 openai_compatible 还必须填写 api_base")
    details.add_row("上下文/输入字段", "日常只维护 context_window_fallback；truncation.max_input_tokens 仅是可选 gateway 兼容覆盖")
    details.add_row("请求 deadline", "fallback.request_timeout_seconds 与同块的重试参数共同控制正式科研模型请求；默认 300 秒")
    details.add_row("本地限流字段", "rate_limit 默认关闭；它不等于模型容量，只有明确知道 provider 配额时才启用")
    details.add_row("若实际文件不存在", f"cp {template_path} {target_path}")
    details.add_row("保存后校验", f"python -m researchos.cli selftest --model-settings {target_path}")
    _cli_console(args).print(
        Panel(
            Group(
                Text("你可以直接用编辑器修改“现在要编辑”这一文件；不要只改 example 模板。API key 可填写为环境变量引用，例如 ${DEEPSEEK_API_KEY}。"),
                details,
                Text("完成编辑后回到本终端按 Enter，ResearchOS 会重新读取并检查该文件。"),
            ),
            title="手动配置模型",
            border_style="cyan",
            expand=True,
        )
    )


def _render_llm_configuration_saved(
    args: argparse.Namespace,
    *,
    provider: str,
    api_base: str,
    api_key: str,
    model: str,
    settings_path: Path,
    secret_location: str,
) -> None:
    table = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("Provider", provider)
    table.add_row("API URL", api_base or "使用 provider 默认 URL")
    table.add_row("API key", _masked_secret_confirmation(api_key))
    table.add_row("Model", model)
    table.add_row("设置文件", str(settings_path))
    table.add_row("Key 保存位置", secret_location)
    settings = load_model_settings(settings_path)
    context_fallback = settings["context_window_fallback"]
    table.add_row("上下文容量兜底", f"{context_fallback:,} tokens；仅在 provider 未报告真实容量时使用")
    override = int(settings.get("context_window_override") or 0)
    table.add_row(
        "上下文容量模式",
        f"人工声明 {override:,} tokens（不再自动探测）" if override else "自动探测；探测不到时使用上方兜底",
    )
    request_timeout = settings["fallback"]["request_timeout_seconds"]
    table.add_row("单次模型请求 deadline", f"{request_timeout:,} 秒；可在同一文件的 fallback.request_timeout_seconds 调整")
    _cli_console(args).print(Panel(table, title="模型配置已保存", border_style="green", expand=True))


def _configure_llm_args_from_startup(args: argparse.Namespace) -> argparse.Namespace:
    """Create a full configure command namespace from the active command.

    A prior implementation assembled a small hand-written ``Namespace`` for
    the nested wizard.  The parser evolves over time, so that object silently
    lost shared presentation and command fields.  Any helper that later read a
    newly added field could fail only from the ``run -> 现在配置`` route.  Clone
    the original parser output instead, then replace only configure-specific
    values, so direct and nested setup always share the same command contract.
    """

    configure_values = dict(vars(args))
    configure_values.update(
        command="configure-llm",
        provider=None,
        api_base=None,
        api_key=None,
        model=None,
        context_window=None,
        key_storage=None,
        check=True,
        model_settings=getattr(args, "model_settings", "config/model_settings.yaml"),
    )
    return argparse.Namespace(**configure_values)


async def _ensure_llm_is_ready(args: argparse.Namespace, client: LLMClient) -> LLMClient:
    """Guide an interactive user through the one-time LLM setup before a run."""

    status = client.configuration_status()
    if status.get("ready", False):
        return client
    _render_llm_setup_required_rich(status, args=args)
    if not sys.stdin.isatty():
        await client.aclose()
        raise SystemExit(2)

    settings_path = Path(status.get("settings_path") or getattr(args, "model_settings", "config/model_settings.yaml"))
    choices = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True)
    choices.add_column("输入", style="bold cyan", no_wrap=True)
    choices.add_column("方式", style="bold")
    choices.add_column("你会做什么", overflow="fold")
    choices.add_row("1", "引导配置", "逐项填写缺少信息；API key 默认在当前终端可见，保存摘要只显示掩码。")
    choices.add_row("2", "直接编辑文件", f"编辑 {settings_path.expanduser().resolve()}；保存后回到这里按 Enter，系统会重新读取并校验。")
    choices.add_row("3", "退出", "不修改任何设置，也不启动项目。")
    _cli_console(args).print(
        Panel(
            Group(
                Text("两种方式都会写入同一个实际生效文件。`model_settings.example.yaml` 只供复制参考，单独修改它不会生效。"),
                choices,
            ),
            title="选择配置方式",
            border_style="cyan",
            expand=True,
        )
    )
    choice = input("请选择 [1 引导填写 / 2 直接编辑 / 3 退出]: ").strip()
    if choice in {"2", "edit", "e"}:
        _render_llm_manual_edit_instructions(
            args,
            settings_path=settings_path,
        )
        input()
    elif choice not in {"1", "configure", "c"}:
        await client.aclose()
        raise SystemExit(2)
    await client.aclose()
    if choice in {"1", "configure", "c"}:
        try:
            exit_code = await configure_llm_command(_configure_llm_args_from_startup(args))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise LLMConfigurationWizardError(
                "模型配置向导发生内部错误，原工作流尚未启动。"
            ) from exc
        if exit_code != 0:
            if exit_code == 1:
                raise LLMConfigurationWizardError(
                    "模型设置已保存，但连接检查未通过；请根据上一条连接诊断修正 provider、API key、API URL 或 model。",
                    exit_code=exit_code,
                )
            raise LLMConfigurationWizardError(
                "模型配置尚未完成；原工作流不会在未验证的连接上启动。",
                exit_code=exit_code,
            )
        # ``configure-llm`` has just completed the connection selftest.  The
        # runtime preparation path must not immediately make an identical
        # network request and report a transient second failure as startup I/O.
        args._llm_connection_verified_during_setup = True
    else:
        # The manual route may have added an environment-variable reference or
        # a new .env file while this process was waiting.  Reload it before
        # constructing the refreshed client, otherwise a correct edit can be
        # misreported as an incomplete configuration.
        load_dotenv_for_model_settings(settings_path.resolve())
    refreshed = LLMClient(settings_path.resolve())
    refreshed_status = refreshed.configuration_status()
    if not refreshed_status.get("ready", False):
        await refreshed.aclose()
        _render_llm_setup_required_rich(refreshed_status, args=args)
        raise SystemExit(2)
    return refreshed


async def configure_llm_command(args: argparse.Namespace) -> int:
    """Write and optionally test the public single-model LLM configuration."""

    settings_path = resolve_model_settings_path(Path(getattr(args, "model_settings", "config/model_settings.yaml")))
    _render_llm_context_capacity_notice(args, settings_path=settings_path)
    current = load_model_settings(settings_path)
    source = inspect_model_settings_source(settings_path)
    interactive = sys.stdin.isatty()
    requested_provider = getattr(args, "provider", None)
    requested_api_base = getattr(args, "api_base", None)
    requested_api_key = getattr(args, "api_key", None)
    requested_model = getattr(args, "model", None)
    requested_context_window = getattr(args, "context_window", None)
    try:
        provider = normalize_provider(requested_provider or current["provider"])
    except ValueError as exc:
        _render_llm_setup_required_rich({"missing": [str(exc)], "settings_path": str(settings_path)}, args=args)
        return 2
    api_base = str(requested_api_base or current["api_base"]).strip()
    if requested_provider and not requested_api_base:
        api_base = provider_default_api_base(provider)
    api_key = str(requested_api_key or current["api_key"]).strip()
    model = str(requested_model or current["model"]).strip()
    raw_api_key = str(source.get("api_key") or "").strip()
    raw_provider = str(source.get("provider") or "").strip()
    try:
        existing_provider = normalize_provider(raw_provider) if raw_provider else None
    except ValueError:
        existing_provider = None
    provider_changed = bool(existing_provider and existing_provider != provider)
    if provider_changed and not requested_api_key:
        # A credential reference belongs to its original provider. Never carry
        # it into a newly selected provider merely because the old config was
        # otherwise complete.
        api_key = ""
        raw_api_key = ""
    if provider_changed and not requested_model:
        # Model identifiers are provider-specific just as credentials are.
        # Require an explicit replacement rather than constructing a connection
        # with a new provider and an inherited, usually invalid model name.
        model = ""

    needs_provider = not requested_provider and not raw_provider
    needs_api_base = provider_requires_api_base(provider) and not api_base and not requested_api_base
    needs_api_key = provider_requires_api_key(provider) and not api_key and not requested_api_key
    needs_model = not model and not requested_model
    api_key_changed = bool(requested_api_key)
    context_window_override: int | None = None
    if requested_context_window is not None:
        try:
            context_window_override = _parse_context_window_choice(str(requested_context_window))
        except ValueError as exc:
            _render_llm_setup_required_rich({"missing": [str(exc)], "settings_path": str(settings_path)}, args=args)
            return 2

    if interactive and any((needs_provider, needs_api_base, needs_api_key, needs_model)):
        _cli_console(args).print(
            Panel(
                Text("所有 ResearchOS 阶段共用一个 provider 和一个 model。只会询问缺少或无效的项；已有设置保持不变。API key 默认明文显示，方便当场检查；保存后的摘要仍只显示掩码。共享屏幕时可使用 --hide-api-key 隐藏输入。"),
                title="模型配置向导",
                border_style="cyan",
                expand=True,
            )
        )
        prompted_count = 0
        if needs_provider:
            _render_llm_provider_choices(args, current=provider)
            selected_provider = input(f"Provider [当前 {provider}，回车保留]: ").strip() or provider
            previous_provider = provider
            try:
                provider = normalize_provider(selected_provider)
            except ValueError as exc:
                _render_llm_setup_required_rich({"missing": [str(exc)], "settings_path": str(settings_path)}, args=args)
                return 2
            prompted_count = 1
            if provider != previous_provider and not requested_api_base:
                api_base = provider_default_api_base(provider)
            if provider != previous_provider and not requested_api_key:
                api_key = ""
                raw_api_key = ""

        needs_api_base = provider_requires_api_base(provider) and not api_base and not requested_api_base
        needs_api_key = provider_requires_api_key(provider) and not api_key and not requested_api_key
        needs_model = not model and not requested_model
        remaining_steps = sum((needs_api_base, needs_api_key, needs_model))
        total_steps = prompted_count + remaining_steps
        step_number = prompted_count

        if needs_api_base:
            step_number += 1
            _render_llm_configuration_step(
                args,
                step=f"{step_number}/{total_steps}",
                title="API URL",
                description="自建 gateway 或 openai_compatible 需要完整 URL。",
                current=api_base or provider_default_api_base(provider),
            )
            api_base = input(f"API base URL [{api_base or provider_default_api_base(provider) or '需要填写'}]: ").strip() or api_base or provider_default_api_base(provider)

        if needs_api_key:
            step_number += 1
            hide_api_key = bool(getattr(args, "hide_api_key", False))
            _render_llm_configuration_step(
                args,
                step=f"{step_number}/{total_steps}",
                title="API key",
                description=(
                    "本次输入会直接显示在终端，便于检查是否完整粘贴。可填写真实 key 或 ${PROVIDER_API_KEY}；保存后只显示安全摘要。"
                    if not hide_api_key
                    else "本次输入会隐藏。可填写真实 key 或 ${PROVIDER_API_KEY}；保存后只显示安全摘要。"
                ),
                current="未设置",
            )
            key_prompt = "API key 或 ${环境变量名}（本终端可见）: " if not hide_api_key else "API key（隐藏输入）: "
            if hide_api_key:
                from getpass import getpass

                entered_key = getpass(key_prompt).strip()
            else:
                entered_key = input(key_prompt).strip()
            api_key = entered_key or api_key
            api_key_changed = bool(entered_key)
            _cli_console(args).print(
                Panel(
                    Text(f"API key 已接收：{_masked_secret_confirmation(api_key)}"),
                    title="API key 已确认",
                    border_style="green" if api_key else "yellow",
                    expand=True,
                )
            )

        if needs_model:
            step_number += 1
            _render_llm_configuration_step(
                args,
                step=f"{step_number}/{total_steps}",
                title="Model",
                description="填写 provider 可用的精确 model 名称。所有 Agent 和 Skill 将使用此 model。",
                current=model,
            )
            model = input("Model 名称: ").strip() or model

        current_override = int(current.get("context_window_override") or 0)
        _render_llm_configuration_step(
            args,
            step="可选",
            title="上下文容量",
            description=(
                "直接回车使用自动策略：先探测，探测不到才使用 262k 兜底。"
                "如果你确知当前网关/部署的真实上限，可输入 262100、128k、256k 或 1m；该人工值会优先于自动探测。"
            ),
            current=(f"人工声明 {current_override:,} tokens" if current_override else "自动探测 + 262k 兜底"),
        )
        while True:
            capacity_input = input("上下文容量 [回车=自动；262100/128k/256k/1m]: ").strip()
            if not capacity_input:
                # A blank answer is an intentional return to the documented
                # default strategy, rather than silently preserving a capacity
                # that may have belonged to a previous provider or gateway.
                context_window_override = 0
                break
            try:
                context_window_override = _parse_context_window_choice(capacity_input)
                break
            except ValueError as exc:
                _cli_console(args).print(Text(f"输入无效：{exc}", style="bold red"))

    required_fields = [("provider", provider), ("model", model)]
    if provider_requires_api_key(provider):
        required_fields.insert(1, ("api_key", api_key))
    if provider_requires_api_base(provider):
        required_fields.insert(1, ("api_base", api_base))
    missing = [name for name, value in required_fields if not value]
    if missing:
        _render_llm_setup_required_rich(
            {"missing": missing, "settings_path": str(settings_path)},
            args=args,
        )
        _cli_console(args).print(
            Panel(
                Text("运行 configure-llm 逐项配置，或通过 --provider、--api-key、--model（openai_compatible 还需 --api-base）传入参数。"),
                title="配置未完成",
                border_style="bright_yellow",
                expand=True,
            )
        )
        return 2

    key_storage = str(getattr(args, "key_storage", None) or "").strip().lower()
    if interactive and api_key_changed and not key_storage:
        choice = input("API key 保存到 [1] model_settings.yaml 或 [2] .env？[2]: ").strip()
        key_storage = {"1": "config", "2": "env", "config": "config", "env": "env"}.get(choice, "env")
    if key_storage not in {"config", "env"}:
        key_storage = "config"
    stored_key = api_key if api_key_changed else raw_api_key
    secret_location = str(source.get("source_path") or settings_path)
    if api_key_changed and key_storage == "env" and api_key:
        env_path, env_name = write_api_key_to_dotenv(provider=provider, api_key=api_key, settings_path=settings_path)
        stored_key = "${" + env_name + "}"
        secret_location = str(env_path)

    written = write_model_settings(
        provider=provider,
        api_base=api_base,
        api_key=stored_key,
        model=model,
        fallback=current.get("fallback") if isinstance(current.get("fallback"), dict) else None,
        context_window_override=context_window_override,
        path=settings_path,
    )
    _render_llm_configuration_saved(
        args,
        provider=provider,
        api_base=api_base,
        api_key=api_key,
        model=model,
        settings_path=written,
        secret_location=secret_location,
    )
    if not bool(getattr(args, "check", True)):
        return 0

    client = LLMClient(written)
    try:
        status = client.configuration_status()
        if not status.get("ready", False):
            _render_llm_setup_required_rich(status, args=args)
            return 2
        result = await client.selftest()
    finally:
        await client.aclose()
    check = next(iter(result.values()), {"ok": False, "error": "no configured endpoint"})
    if check.get("ok"):
        _cli_console(args).print(
            Panel(
                Text(f"连接检查通过（{check.get('latency_ms', 0)} ms）。"),
                title="模型连接可用",
                border_style="green",
                expand=True,
            )
        )
        return 0
    _cli_console(args).print(
        Panel(
            Text(
                "连接检查失败："
                f"{check.get('error') or '未知 provider 错误'}\n"
                "配置已保留。修正后重新运行 configure-llm 即可再次检查。"
            ),
            title="模型连接未通过",
            border_style="bright_red",
            expand=True,
        )
    )
    return 1


def _workflow_profile_preview(
    current: dict[str, Any],
    *,
    mode: str,
    preset: str,
    literature_preset: str,
    t4_mode: str,
    proposal_tracks: str,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Build a display-only workflow profile without writing a workspace file."""

    previous_settings = current.get("settings") if isinstance(current.get("settings"), dict) else {}
    if mode == "auto":
        settings = dict(AUTO_PRESETS[preset])
    else:
        settings = {
            "literature_preset": str(previous_settings.get("literature_preset") or "standard_research"),
            "t4_mode": str(previous_settings.get("t4_mode") or "auto"),
            "publication_orientation": "pending_user",
            "survey_policy": "ask",
            "writing_style": "pending_user",
            "proposal_tracks": str(previous_settings.get("proposal_tracks") or "one"),
        }
    settings.update(
        {
            "literature_preset": literature_preset,
            "t4_mode": t4_mode,
            "proposal_tracks": proposal_tracks,
            "startup_setup_confirmed": True,
        }
    )
    if mode == "auto" and str(settings.get("publication_orientation") or "") == "ccf_cs":
        existing_template = normalize_ccf_template_id(str(previous_settings.get("template_id") or ""))
        selected_template = normalize_ccf_template_id(str(template_id or ""))
        available_templates = available_ccf_template_ids(Path(__file__).resolve().parents[1])
        if not selected_template and str(previous_settings.get("publication_orientation") or "") == "ccf_cs":
            selected_template = existing_template
        settings.update(
            {
                "template_family": "ccf",
                "template_id": selected_template if selected_template in available_templates else "",
                "writing_language": "en",
                "template_selection_source": "explicit_configuration" if template_id else "preserved" if selected_template in available_templates else "pending_t1",
            }
        )
    return {
        "mode": mode,
        "preset": preset,
        "settings": settings,
        "selection_source": "configure_workflow_preview",
    }


def _workflow_change_impacts(previous: dict[str, Any], proposed: dict[str, Any]) -> list[str]:
    """Describe future-only profile changes without claiming an automatic rerun."""

    before = previous.get("settings") if isinstance(previous.get("settings"), dict) else {}
    after = proposed.get("settings") if isinstance(proposed.get("settings"), dict) else {}
    impacts: list[str] = []
    if previous.get("mode") != proposed.get("mode"):
        impacts.append("工作方式会影响未来常规 Gate 是否可自动通过；失败恢复、研究范围变化与外部执行仍必须人工确认。")
    if before.get("literature_preset") != after.get("literature_preset"):
        impacts.append("文献覆盖默认值会在下次 T2 参数 Gate 使用；已经检索或阅读的材料会保留，不会自动重跑。")
    if before.get("t4_mode") != after.get("t4_mode"):
        impacts.append("T4 探索力度会在下次 T4 pre-run confirmation 使用；已生成的 Candidate、评分和谱系不会变化。")
    if before.get("proposal_tracks") != after.get("proposal_tracks"):
        impacts.append("Proposal 数量只影响下一次 T4 Gate1 的推进策略；已有 Proposal track 不会合并或覆盖。")
    if before.get("template_id") != after.get("template_id"):
        impacts.append("LaTeX 模板只影响之后进入的 Survey/T8；已经生成的 TeX 不会被静默重写。")
    if not impacts:
        impacts.append("设置没有实质变化；确认只会刷新这份工作区级默认设置记录。")
    return impacts


async def configure_workflow_command(args: argparse.Namespace) -> int:
    """Inspect or revise future workflow defaults without touching research artifacts."""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    current = load_workflow_mode(workspace_dir)

    requested_mode = str(getattr(args, "workflow_mode", "") or "").strip().casefold()
    requested_preset = str(getattr(args, "auto_preset", "") or "").strip()
    requested_literature = str(getattr(args, "literature_preset", "") or "").strip()
    requested_t4 = str(getattr(args, "auto_t4_mode", "") or "").strip().casefold()
    requested_tracks = str(getattr(args, "proposal_tracks", "") or "").strip().casefold()
    requested_template = normalize_ccf_template_id(str(getattr(args, "ccf_template", "") or ""))
    request = str(getattr(args, "request", "") or "").strip()

    llm_client: LLMClient | None = None
    try:
        candidate = LLMClient(Path(args.model_settings).resolve())
        if candidate.configuration_status().get("ready", False):
            llm_client = candidate
        else:
            await candidate.aclose()
    except Exception:
        llm_client = None
    human = _build_human_interface(runtime_settings, llm_client=llm_client)

    try:
        if not any((requested_mode, requested_preset, requested_literature, requested_t4, requested_tracks, requested_template, request)):
            question = (
                "<!-- researchos_workflow_settings:"
                + json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + " -->\n"
                + "请输入“确认”保留当前设置，或直接描述希望调整的模式、文献覆盖、T4 探索或 Proposal 数量。"
            )
            request = await human.ask_clarification(
                question=question,
                suggestions=["确认", "协作模式，综述均衡覆盖，深入探索", "自动 UTD/IS 研究流程，分别写两份 Proposal"],
            )

        current_settings = current.get("settings") if isinstance(current.get("settings"), dict) else {}
        mode = requested_mode or str(current.get("mode") or "copilot")
        preset = requested_preset or str(current.get("preset") or "research_ccf")
        # Naming an Auto preset is an intentional profile change. Start the
        # preview from that preset so the Rich comparison and saved file agree;
        # a separate explicit flag can still override any individual knob.
        preset_defaults = AUTO_PRESETS.get(preset, current_settings) if requested_preset else current_settings
        literature_preset = requested_literature or str(preset_defaults.get("literature_preset") or "standard_research")
        t4_mode = requested_t4 or str(preset_defaults.get("t4_mode") or "auto")
        proposal_tracks = requested_tracks or str(preset_defaults.get("proposal_tracks") or "one")
        template_id = requested_template

        if request:
            deterministic_mode = parse_workflow_mode_answer(request)
            semantic_mode = None
            if deterministic_mode is None and isinstance(human, CLIHumanInterface):
                semantic_mode = parse_workflow_mode_proposal(await human.interpret_workflow_mode(request))
            mode_choice = deterministic_mode or semantic_mode
            if mode_choice is not None:
                mode, preset, selected_t4 = mode_choice
                # A named mode preset is meaningful by itself. Rebase the
                # non-explicit defaults before parsing any more detailed
                # request text, then let that text override only the stated
                # settings below.
                named_defaults = AUTO_PRESETS[preset]
                if not requested_literature:
                    literature_preset = str(named_defaults["literature_preset"])
                if not requested_t4:
                    t4_mode = selected_t4 or str(named_defaults["t4_mode"])
                if not requested_tracks:
                    proposal_tracks = str(named_defaults["proposal_tracks"])
            deterministic_setup = parse_auto_execution_setup_answer(
                request,
                current_preset=literature_preset,
                current_t4_mode=t4_mode,
                current_proposal_tracks=proposal_tracks,
            )
            semantic_setup = None
            if deterministic_setup is None and isinstance(human, CLIHumanInterface):
                semantic_setup = parse_execution_setup_proposal(
                    await human.interpret_workflow_setup(request),
                    current_preset=literature_preset,
                    current_t4_mode=t4_mode,
                    current_proposal_tracks=proposal_tracks,
                )
            setup_choice = deterministic_setup or semantic_setup
            if setup_choice is not None:
                literature_preset, t4_mode, proposal_tracks = setup_choice
            if not template_id:
                template_id = parse_available_ccf_template_answer(
                    request,
                    ccf_template_entries(repo_root=Path(__file__).resolve().parents[1], available_only=True),
                )

        if mode not in {"auto", "copilot"}:
            raise ValueError("工作方式必须是 auto 或 copilot")
        if preset not in AUTO_PRESETS:
            raise ValueError("Auto preset 无效；请选择已列出的 research/survey profile")
        if literature_preset not in {"standard_research", "survey_balanced", "survey_exhaustive"}:
            raise ValueError("文献覆盖必须是 standard_research、survey_balanced 或 survey_exhaustive")
        if t4_mode not in {"auto", "quick", "standard", "deep"}:
            raise ValueError("T4 探索必须是 auto、quick、standard 或 deep")
        if proposal_tracks not in {"one", "top2"}:
            raise ValueError("Proposal 数量必须是 one 或 top2")
        orientation = str(AUTO_PRESETS[preset].get("publication_orientation") or "") if mode == "auto" else ""
        available_templates = available_ccf_template_ids(Path(__file__).resolve().parents[1])
        if template_id and template_id not in available_templates:
            raise ValueError("CCF 模板必须是当前本机已安装的会议 template_id。")
        if template_id and orientation != "ccf_cs":
            raise ValueError("--ccf-template 只能用于 Auto + CCF/CS 预设。")
        if orientation == "ccf_cs" and not template_id:
            current_template = normalize_ccf_template_id(str(current_settings.get("template_id") or ""))
            current_orientation = str(current_settings.get("publication_orientation") or "")
            if current_orientation == "ccf_cs" and current_template in available_templates:
                template_id = current_template
            elif bool(getattr(args, "yes", False)) or not sys.stdin.isatty():
                raise ValueError("Auto + CCF/CS 必须指定具体会议模板；请传入 --ccf-template <template_id>，不会降级到 basic_en。")
            else:
                entries = ccf_template_entries(repo_root=Path(__file__).resolve().parents[1], available_only=True)
                if not entries:
                    raise ValueError("当前安装没有可用 CCF/CS 模板；请检查 latex_templete/ccf-latex-templates。")
                feedback = ""
                for _attempt in range(3):
                    template_question = (
                        "<!-- researchos_workflow_ccf_template_selector -->\n"
                        + (f"上次输入未识别：{feedback}\n" if feedback else "")
                        + "请选择未来 Survey 与 T8 复用的具体 CCF/CS 会议 LaTeX 模板。"
                    )
                    answer = await human.ask_clarification(question=template_question, suggestions=[])
                    template_id = parse_available_ccf_template_answer(answer, entries)
                    if template_id in available_templates:
                        break
                    feedback = "请直接输入上表编号、会议名或 template id。"
                if template_id not in available_templates:
                    raise ValueError("未识别 CCF/CS 模板；请输入面板编号、会议名或 --ccf-template <template_id>。")

        proposed = _workflow_profile_preview(
            current,
            mode=mode,
            preset=preset,
            literature_preset=literature_preset,
            t4_mode=t4_mode,
            proposal_tracks=proposal_tracks,
            template_id=template_id or None,
        )
        _cli_console(args).print(
            workflow_settings_panel(
                proposed,
                previous=current,
                title="确认工作流设置变更",
                impacts=_workflow_change_impacts(current, proposed),
                border_style="bright_yellow",
            )
        )
        if not bool(getattr(args, "yes", False)):
            if not sys.stdin.isatty():
                _cli_console(args).print(
                    Panel(
                        Text("非交互模式不会写入工作流设置。请添加 --yes 显式确认。"),
                        title="等待确认",
                        border_style="bright_yellow",
                        expand=True,
                    )
                )
                return 2
            answer = input("确认保存? [y/N]: ").strip().casefold()
            if answer not in {"y", "yes", "确认", "是"}:
                _cli_console(args).print("未保存任何设置。")
                return 0

        saved = configure_workflow_mode(
            workspace_dir,
            mode=mode,
            preset=preset,
            literature_preset=literature_preset,
            t4_mode=t4_mode,
            proposal_tracks=proposal_tracks,
            template_id=template_id or None,
            startup_setup_confirmed=True,
            selection_source="configure_workflow",
        )
        _cli_console(args).print(
            workflow_settings_panel(
                saved,
                title="工作流设置已保存",
                impacts=("已保存到 _runtime/workflow_mode.json；已有研究产物未被修改。",),
                border_style="green",
            )
        )
        return 0
    except (HumanInputUnavailable, ValueError) as exc:
        _cli_console(args).print(
            Panel(Text(str(exc)), title="工作流设置未保存", border_style="bright_yellow", expand=True)
        )
        return 2
    finally:
        if llm_client is not None:
            await llm_client.aclose()


def _dependency_selftest() -> dict[str, dict[str, Any]]:
    """检查本地关键运行依赖。

    设计目标：
    - 把“跑到 T3/T9 才发现 pdfplumber 缺失”这种问题前移到启动阶段；
    - 既能在 `selftest` 命令里作为硬失败，也能在 startup selftest 里作为早期警告。
    """

    def _spec_ok(module_name: str) -> bool:
        return importlib.util.find_spec(module_name) is not None

    return {
        "pdf_processing": {
            "ok": _spec_ok("pdfplumber"),
            "required_for": ["T3", "T9", "extract_paper_sections", "extract_pdf_text"],
            "module": "pdfplumber",
            "hint": "pip install -r requirements.txt",
        }
    }


def _selftest_compact_error(value: object, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "未返回具体原因"
    if len(text) <= limit:
        return text
    boundaries = [match.end() for match in re.finditer(r"[。！？；;](?:\s|$)|\.\s", text) if match.end() <= limit]
    if boundaries:
        return text[: boundaries[-1]].strip()
    return "请使用 configure-llm 检查模型设置，或添加 --verbose 查看完整诊断。"


def _render_selftest_summary(
    args: argparse.Namespace,
    *,
    llm_results: dict[str, dict[str, Any]],
    dependency_results: dict[str, dict[str, Any]],
    title: str,
) -> None:
    """Render connection and local prerequisites without raw YAML diagnostics."""

    table = lightweight_ruled_table(header_style="bold cyan", expand=True)
    table.add_column("状态", width=10)
    table.add_column("检查项", min_width=20, max_width=34, overflow="fold")
    table.add_column("结果", min_width=34, max_width=88, overflow="fold")
    all_ok = True
    for name, item in llm_results.items():
        ok = bool(item.get("ok"))
        all_ok = all_ok and ok
        if ok:
            latency = item.get("latency_ms")
            detail = "连接可用" + (f" · {latency} ms" if latency is not None else "")
        else:
            detail = "连接失败：" + _selftest_compact_error(item.get("error"))
        table.add_row(
            Text("通过" if ok else "未通过", style="green" if ok else "bold red"),
            "模型连接" if name == "default" else f"模型连接 · {name}",
            detail,
        )
    dependency_labels = {
        "pdf_processing": "PDF 文本处理",
    }
    for name, item in dependency_results.items():
        ok = bool(item.get("ok"))
        all_ok = all_ok and ok
        module = str(item.get("module") or name)
        if ok:
            detail = f"{module} 已可用"
        else:
            hint = str(item.get("hint") or "请检查本地依赖")
            detail = f"{module} 不可用；{hint}"
        table.add_row(
            Text("通过" if ok else "未通过", style="green" if ok else "bold red"),
            dependency_labels.get(name, name),
            detail,
        )
    message = (
        "环境已就绪，可以开始本次研究流程。"
        if all_ok
        else "部分检查未通过。请先修复上方项目，再重新运行；当前 workspace 不会被推进。"
    )
    _cli_console(args).print(
        Panel(
            Group(table, Text(message, style="green" if all_ok else "yellow")),
            title=title,
            border_style="green" if all_ok else "bright_yellow",
            expand=True,
        )
    )


def _emit_startup_ui(
    *,
    args: argparse.Namespace,
    runtime_settings: RuntimeSettings,
    workspace_dir: Path | None,
    show_banner: bool = True,
    show_summary: bool = True,
    skill_roots: list[Path] | None = None,
    skill_count: int | None = None,
    mcp_server_count: int = 0,
    mcp_tool_count: int = 0,
) -> None:
    """打印 CLI 启动动画与启动摘要。"""

    if show_banner and not getattr(args, "_startup_banner_emitted", False):
        if _startup_banner_enabled(args, runtime_settings):
            show_startup_banner(
                args.command,
                no_banner=getattr(args, "no_banner", False),
                default_no_banner=runtime_settings.ui.no_banner,
                no_color=runtime_settings.ui.no_color,
            )
        # All command paths share a single banner. Runtime commands call this
        # helper again after discovery to add their startup summary only.
        args._startup_banner_emitted = True
    if not show_summary:
        return
    summary = render_startup_summary(
        workspace_dir=workspace_dir,
        state_machine=Path(args.state_machine).resolve() if hasattr(args, "state_machine") else None,
        gates=Path(args.gates).resolve() if hasattr(args, "gates") and args.gates else None,
        model_settings=Path(args.model_settings).resolve() if hasattr(args, "model_settings") else None,
        skill_roots=skill_roots,
        skill_count=skill_count,
        mcp_server_count=mcp_server_count,
        mcp_tool_count=mcp_tool_count,
        verbose=bool(getattr(args, "verbose", False) or runtime_settings.ui.verbose),
        no_color=runtime_settings.ui.no_color,
    )
    if summary and not _is_quiet_args(args, runtime_settings):
        print(summary)


async def _prepare_runtime(
    args: argparse.Namespace,
    workspace_dir: Path,
    *,
    require_llm: bool = True,
) -> PreparedRuntime:
    """为 CLI 运行模式准备公共依赖。

    两种运行模式共享同一套启动检查：
    - 注册 builtin / skill tools；
    - 校验正式 agent 的 tool 是否齐全；
    - 构造 LLMClient 并按需跑 endpoint selftest。

    ``require_llm=False`` is deliberately narrow.  It is used only by direct
    ``run-task`` entry points whose entire implementation is a local,
    deterministic T5 compiler.  The client object is still supplied to keep
    the runner interface uniform, but an incomplete provider configuration
    cannot prevent that no-model repair from running.  Full pipeline runs and
    every LLM-capable task retain the ordinary setup and endpoint checks.
    """

    # MCP server configuration can reference credentials from the same .env
    # file as model_settings.yaml. Load it before MCP discovery, rather than
    # only when LLMClient is constructed later in this function.
    load_dotenv_for_model_settings(Path(args.model_settings).resolve())
    register_builtin_task_checkers()
    skill_roots = _resolve_skill_roots(args, workspace_dir)
    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    discovered_skills = discover_skills_from_roots(skill_roots)
    registry = _build_tool_registry(skill_roots, runtime_settings, discovered_skills=discovered_skills)
    mcp_server_count, mcp_tool_count, mcp_clients = await _maybe_register_mcp_tools(args, registry)
    _validate_agent_tools(registry)
    try:
        llm_client = LLMClient(Path(args.model_settings).resolve())
        if require_llm:
            llm_client = await _ensure_llm_is_ready(args, llm_client)
    except BaseException:
        for client in reversed(mcp_clients):
            close = getattr(client, "aclose", None)
            if callable(close):
                await close()
        raise
    try:
        if require_llm:
            await _maybe_run_selftest(args, llm_client)
    except BaseException:
        # A startup selftest can create aiohttp sessions before a provider
        # reports its first failure. Close them on this early exit so a
        # recoverable Skill/provider pause does not leak client warnings.
        close = getattr(llm_client, "aclose", None)
        if callable(close):
            await close()
        for mcp_client in reversed(mcp_clients):
            close_mcp = getattr(mcp_client, "aclose", None)
            if callable(close_mcp):
                await close_mcp()
        raise
    return PreparedRuntime(
        skill_roots=skill_roots,
        skill_count=len(discovered_skills),
        registry=registry,
        llm_client=llm_client,
        mcp_server_count=mcp_server_count,
        mcp_tool_count=mcp_tool_count,
        mcp_clients=mcp_clients,
    )


async def run_command(args: argparse.Namespace) -> int:
    """完整 pipeline 模式入口。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).resolve()
    entry_error = _validate_pipeline_workspace_entry(args, workspace_dir)
    if entry_error is not None:
        return entry_error
    ensure_workspace_layout(workspace_dir, runtime_settings)
    if any(
        getattr(args, name, None)
        for name in ("workflow_mode", "auto_preset", "auto_t4_mode")
    ):
        existing_workflow = load_workflow_mode(workspace_dir)
        requested_auto_preset = getattr(args, "auto_preset", None)
        configure_workflow_mode(
            workspace_dir,
            mode=(
                getattr(args, "workflow_mode", None)
                or ("auto" if requested_auto_preset else str(existing_workflow.get("mode") or "copilot"))
            ),
            preset=requested_auto_preset,
            t4_mode=getattr(args, "auto_t4_mode", None),
            selection_source="command_line",
        )
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_summary=False,
    )
    install_signal_handlers()
    state_machine = StateMachine(
        Path(args.state_machine).resolve(),
        Path(args.gates).resolve() if args.gates else None,
    )
    definition_errors = state_machine.validate_definition()
    if definition_errors:
        _render_state_machine_definition_error(args, state_machine, definition_errors)
        return 2

    start_task = _resolve_pipeline_start_task(args)
    if start_task:
        prepare_code = _prepare_pipeline_start_workspace(
            workspace_dir=workspace_dir,
            state_machine=state_machine,
            start_task=start_task,
            from_workspace=Path(args.from_workspace).resolve() if getattr(args, "from_workspace", None) else None,
            project_id=args.project_id,
            quiet=_is_quiet_args(args, runtime_settings),
        )
        if prepare_code != 0:
            return prepare_code

    resume_from_task = str(getattr(args, "from_task", "") or "").strip()
    if bool(getattr(args, "resume", False)) and getattr(args, "from_workspace", None):
        import_code = _prepare_resume_workspace_import(
            workspace_dir=workspace_dir,
            state_machine=state_machine,
            from_workspace=Path(args.from_workspace).resolve(),
            requested_task=resume_from_task or None,
            quiet=_is_quiet_args(args, runtime_settings),
        )
        if import_code != 0:
            return import_code
    if resume_from_task:
        reentry_code = _prepare_resume_from_task(
            workspace_dir=workspace_dir,
            state_machine=state_machine,
            start_task=resume_from_task,
            quiet=_is_quiet_args(args, runtime_settings),
        )
        if reentry_code != 0:
            return reentry_code
    elif bool(getattr(args, "resume", False)):
        checkpoint_code = _prepare_literature_resume_checkpoint(
            workspace_dir=workspace_dir,
            state_machine=state_machine,
            source_import=bool(getattr(args, "from_workspace", None)),
            quiet=_is_quiet_args(args, runtime_settings),
        )
        if checkpoint_code != 0:
            return checkpoint_code

    # `project.yaml` is the workspace's durable research identity.  Resolve
    # it after all optional import/re-entry preparation so a legacy CLI
    # default never becomes the active state identity.
    effective_project_id = resolve_workspace_project_id(workspace_dir, args.project_id)

    try:
        prepared = await _prepare_runtime(args, workspace_dir)
    except Exception as exc:
        return _render_runtime_preparation_failure(
            args,
            message="模型连接或本地依赖暂时不可用。检查模型设置和依赖后重新运行或使用 resume。",
            error=exc,
        )
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_banner=False,
        skill_roots=prepared.skill_roots,
        skill_count=prepared.skill_count,
        mcp_server_count=prepared.mcp_server_count,
        mcp_tool_count=prepared.mcp_tool_count,
    )
    try:
        runner = CompletePipelineRunner(
            workspace=workspace_dir,
            state_machine=state_machine,
            llm_client=prepared.llm_client,
            tool_registry=prepared.registry,
            skill_roots=prepared.skill_roots,
            human_interface=_build_human_interface(runtime_settings, llm_client=prepared.llm_client),
            runtime_settings=runtime_settings,
        )
        return await runner.run(project_id=effective_project_id, resume=getattr(args, "resume", False))
    finally:
        await prepared.aclose()


async def run_t8_command(args: argparse.Namespace) -> int:
    """Accept a modern T5 handoff and continue the existing workspace through T8."""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    state_machine = StateMachine(
        Path(args.state_machine).resolve(),
        Path(args.gates).resolve() if args.gates else None,
    )
    definition_errors = state_machine.validate_definition()
    if definition_errors:
        _render_state_machine_definition_error(args, state_machine, definition_errors)
        return 2

    try:
        receipt = accept_and_ingest_t5_handoff(
            workspace_dir,
            allow_partial=not bool(getattr(args, "require_ready", False)),
        )
    except Exception as exc:
        print(f"T5-to-T8 handoff ingestion failed: {exc}", file=sys.stderr)
        return 2
    if not receipt.get("ok"):
        print("T5-to-T8 handoff rejected:", file=sys.stderr)
        for issue in receipt.get("errors", []) or []:
            if isinstance(issue, dict):
                print(
                    f"- {issue.get('code')}: {issue.get('path')} - {issue.get('message')}",
                    file=sys.stderr,
                )
        return 3

    print(
        "[T5→T8] 已接收 external_executor/executor_research_report.md，"
        f"导入 {receipt.get('metric_count', 0)} 条指标和 "
        f"{receipt.get('claim_mapping_count', 0)} 条初步 claim 映射。",
        flush=True,
    )
    if bool(getattr(args, "validate_only", False)):
        print("[T5→T8] 校验与结构化导入完成；--validate-only 未启动 T8。", flush=True)
        return 0

    state_result = prepare_t8_state(workspace_dir, receipt)
    if not state_result.get("should_run"):
        print(
            f"[T5→T8] 无需重新启动：{state_result.get('action')} "
            f"({state_result.get('current_task')}, {state_result.get('status')})",
            flush=True,
        )
        return 0
    print(
        f"[T5→T8] 状态已准备为 {state_result.get('current_task')}；现在委托现有完整 pipeline runner。",
        flush=True,
    )
    args.resume = True
    return await run_command(args)


async def run_smoke_command(args: argparse.Namespace) -> int:
    """真实 pipeline smoke 模式：小规模覆盖 + 当前全局 LLM。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_summary=False,
    )
    install_signal_handlers()
    state_machine = StateMachine(
        Path(args.state_machine).resolve(),
        Path(args.gates).resolve() if args.gates else None,
    )
    _apply_smoke_llm_overrides(state_machine, tier=args.tier, profile=args.profile)
    definition_errors = state_machine.validate_definition()
    if definition_errors:
        _render_state_machine_definition_error(args, state_machine, definition_errors)
        return 2

    _write_smoke_literature_params(
        workspace_dir,
        active_pool_max=args.active_pool_max,
        deep_read_target=args.deep_read_target,
        abstract_sweep=args.abstract_sweep,
        manuscript_language=args.manuscript_language,
        include_chinese_literature=args.include_chinese_literature,
        force=bool(args.force_smoke_params),
        quiet=_is_quiet_args(args, runtime_settings),
    )
    start_task = str(args.start_task or "").strip() or "T2"
    prepare_code = _prepare_pipeline_start_workspace(
        workspace_dir=workspace_dir,
        state_machine=state_machine,
        start_task=start_task,
        from_workspace=Path(args.from_workspace).resolve() if getattr(args, "from_workspace", None) else None,
        project_id=args.project_id,
        quiet=_is_quiet_args(args, runtime_settings),
    )
    if prepare_code != 0:
        return prepare_code
    _ensure_smoke_project_direction(workspace_dir)

    try:
        prepared = await _prepare_runtime(args, workspace_dir)
    except Exception as exc:
        return _render_runtime_preparation_failure(
            args,
            message="模型连接或本地依赖暂时不可用。检查模型设置和依赖后重新运行或使用 resume。",
            error=exc,
        )
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_banner=False,
        skill_roots=prepared.skill_roots,
        skill_count=prepared.skill_count,
        mcp_server_count=prepared.mcp_server_count,
        mcp_tool_count=prepared.mcp_tool_count,
    )
    if _is_quiet_args(args, runtime_settings):
        print(f"[Smoke] start_task={start_task}, llm=global", flush=True)
    else:
        print(
            "[Smoke] 已启动真实快速联调："
            f"start_task={start_task}, llm=global, active_pool_max={args.active_pool_max}, "
            f"deep_read_target={args.deep_read_target}, abstract_sweep={args.abstract_sweep}",
            flush=True,
        )
    try:
        runner = CompletePipelineRunner(
            workspace=workspace_dir,
            state_machine=state_machine,
            llm_client=prepared.llm_client,
            tool_registry=prepared.registry,
            skill_roots=prepared.skill_roots,
            human_interface=_build_human_interface(runtime_settings, llm_client=prepared.llm_client),
            runtime_settings=runtime_settings,
        )
        return await runner.run(
            project_id=resolve_workspace_project_id(workspace_dir, args.project_id),
            resume=False,
        )
    finally:
        await prepared.aclose()


def _apply_smoke_llm_overrides(state_machine: StateMachine, *, tier: str, profile: str | None = None) -> None:
    """Keep legacy smoke overrides readable; the default runtime uses one model."""

    for node in state_machine.nodes.values():
        if node.terminal or (node.agent is None and node.skill is None):
            continue
        llm_block = dict(node.llm or {})
        llm_block["tier"] = tier
        if profile:
            llm_block["profile"] = profile
        node.llm = llm_block


def _ensure_smoke_project_direction(workspace_dir: Path) -> None:
    """Make init-workspace's minimal topic usable by T2 smoke runs.

    `init-workspace --topic` writes a `topic` field, while Scout prompts and
    seed inspection primarily look for `research_direction` / `direction`.
    Smoke mode should be able to run from that minimal template without asking
    for human clarification, so we bridge the field only when no explicit
    direction is already present.
    """

    project_path = workspace_dir / "project.yaml"
    if not project_path.exists():
        return
    try:
        payload = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    if str(payload.get("research_direction") or payload.get("direction") or "").strip():
        return
    topic = str(payload.get("topic") or payload.get("project_topic") or "").strip()
    if not topic or topic in {"（暂无）", "(none)", "none", "N/A"}:
        return
    payload["research_direction"] = topic
    project_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _write_smoke_literature_params(
    workspace_dir: Path,
    *,
    active_pool_max: int,
    deep_read_target: int,
    abstract_sweep: int,
    manuscript_language: str,
    include_chinese_literature: str,
    force: bool,
    quiet: bool = False,
) -> None:
    """Write workspace-local T2/T3 parameters for quick real integration runs."""

    literature_dir = workspace_dir / "literature"
    literature_dir.mkdir(parents=True, exist_ok=True)
    params_path = literature_dir / "literature_params.json"
    confirmation_path = literature_dir / "literature_params_confirmation.json"
    if params_path.exists() and not force:
        if not quiet:
            print(
                "[Smoke] 已存在 literature/literature_params.json，保留现有参数；"
                "如需覆盖请加 --force-smoke-params。",
                flush=True,
            )
        if not confirmation_path.exists():
            _write_smoke_literature_confirmation(workspace_dir, params_path, confirmation_path)
        return

    deep_min = max(1, min(int(deep_read_target), 3))
    deep_max = max(int(deep_read_target), int(deep_read_target) + 1)
    # Smoke is also used to exercise recovery and output contracts with a
    # deliberately tiny real pool.  Do not silently replace the user's
    # requested cap with the formal-run minimum.
    active_pool = max(1, int(active_pool_max))
    abstract_num = max(0, int(abstract_sweep))
    payload = {
        "semantics": "workspace_literature_coverage_parameters_for_t2_t3",
        "selected_option": "smoke",
        "selected_label": "Smoke 快速联调",
        "profile": "smoke",
        "smoke_mode": True,
        "t2_finalize": {
            "active_pool_max": active_pool,
            "screened_active_pool_cap": min(active_pool, 20),
            "snowball_active_pool_cap": min(active_pool, 5),
            "finish_finalize_min_raw": 10,
            "access_audit_top_n": min(active_pool, 20),
            "pre_active_light_backfill_max": min(active_pool * 2, 40),
            "snowball_max_sources": 3,
            "snowball_refs_per_source": 3,
            "snowball_max_candidates": 8,
        },
        "reader": {
            "deep_read_min": deep_min,
            "deep_read_target": int(deep_read_target),
            "deep_read_max": deep_max,
            "require_deep_read_target": False,
            "probe_pool": max(int(deep_read_target), 5),
            "mainline_screened_cap": min(active_pool, 20),
            "bridge_deep_floor": 1,
            "bridge_screened_cap": 2,
            "bridge_pool_cap": 4,
            "citation_hub_slots": 1,
            "abstract_sweep": {
                "lite_paper_num": abstract_num,
                "sources": ["papers_verified", "papers_dedup", "papers_backlog"],
                "include_metadata_only": False,
                "metadata_replacement_policy": "skip_metadata_only_in_smoke_mode",
            },
        },
        "literature_quality": {
            "enabled": True,
            "manuscript_language": manuscript_language,
            "include_chinese_literature": include_chinese_literature,
            "chinese_literature_policy": "review_flag_only",
        },
        "selected_summary": {
            "active_pool_max": active_pool,
            "deep_read_min": deep_min,
            "deep_read_target": int(deep_read_target),
            "deep_read_max": deep_max,
            "require_deep_read_target": False,
            "abstract_sweep_target": abstract_num,
            "manuscript_language": manuscript_language,
            "include_chinese_literature": include_chinese_literature,
        },
        "confirmation_summary": (
            "Smoke 快速联调：小候选池、小精读目标、少量摘要轻读；"
            "用于验证流程/工具/输出，不用于正式研究质量判断。"
        ),
        "captured": {},
        "resource_backfill_policy": {
            "retained_candidates": "small smoke pool for real integration debugging",
            "user_visible_budget_semantics": "smoke targets, not formal coverage targets",
            "metadata_only": "metadata-only records do not count as smoke evidence",
        },
        "parameter_meanings": {
            "active_pool_max": "Smoke 保留候选数上限。",
            "deep_read_target": "Smoke 精读目标；默认不要求读满正式目标。",
            "abstract_sweep.lite_paper_num": "Smoke 摘要轻读数量。",
        },
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    params_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_smoke_literature_confirmation(workspace_dir, params_path, confirmation_path)
    if not quiet:
        print(
            "[Smoke] 已写入快速联调参数："
            "literature/literature_params.json, literature/literature_params_confirmation.json",
            flush=True,
        )


def _write_smoke_literature_confirmation(
    workspace_dir: Path,
    params_path: Path,
    confirmation_path: Path,
) -> None:
    try:
        params = json.loads(params_path.read_text(encoding="utf-8"))
    except Exception:
        params = {}
    payload = {
        "semantics": "human_final_confirmed_t2_literature_parameters_before_scout",
        "task_id": "T2-PARAM-CONFIRM-GATE",
        "gate_id": "t2_literature_param_confirm_gate",
        "selected_option": "confirm_start_t2",
        "confirmed_to_start_t2": True,
        "captured": {"smoke_mode": "true"},
        "next_task": "T2",
        "human_interaction_id": "smoke_auto_confirm",
        "selected_parameters_summary": params.get("selected_summary") or {},
        "confirmation_summary": params.get("confirmation_summary") or "Smoke auto-confirmed.",
        "parameter_source": "literature/literature_params.json",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    confirmation_path.parent.mkdir(parents=True, exist_ok=True)
    confirmation_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_pipeline_start_task(args: argparse.Namespace) -> str | None:
    """Resolve `run --start-task` / `run --from` startup semantics."""

    start_task = str(getattr(args, "start_task", "") or "").strip()
    from_workspace = str(getattr(args, "from_workspace", "") or "").strip()
    if bool(getattr(args, "resume", False)):
        return None
    if start_task:
        return resolve_public_stage_alias(start_task)
    if from_workspace:
        print("[进度] run --from 未指定 --start-task，默认从 T2 开始。", flush=True)
        return "T2"
    return None


def _prepare_pipeline_start_workspace(
    *,
    workspace_dir: Path,
    state_machine: StateMachine,
    start_task: str,
    from_workspace: Path | None,
    project_id: str,
    quiet: bool = False,
) -> int:
    """Prepare a full pipeline workspace that starts from an intermediate task."""

    requested_start_task = resolve_public_stage_alias(start_task)
    if requested_start_task not in state_machine.nodes:
        print(f"Unknown --start-task: {requested_start_task}")
        return 2
    if state_machine.nodes[requested_start_task].terminal:
        print(f"--start-task cannot be terminal state: {requested_start_task}")
        return 2

    state_path = workspace_dir / "state.yaml"
    if state_path.exists():
        print(
            "目标 workspace 已存在 state.yaml；为避免覆盖已有运行状态，请使用 resume，"
            "或换一个新的 --workspace。",
            flush=True,
        )
        return 2

    source_state: StateYaml | None = None
    if from_workspace is not None:
        if not from_workspace.exists():
            print(f"--from workspace 不存在: {from_workspace}")
            return 2
        if from_workspace.resolve() == workspace_dir.resolve():
            print("--from 不能指向当前 --workspace；请使用不同的新 workspace。")
            return 2
        _copy_task_inputs_from_workspace(
            task_id=requested_start_task,
            from_workspace=from_workspace,
            workspace_dir=workspace_dir,
            quiet=quiet,
        )
        source_state_path = from_workspace / "state.yaml"
        if source_state_path.exists():
            try:
                source_state = StateYaml.load_yaml(source_state_path)
            except Exception as exc:
                print(f"[warning] 无法读取来源 state.yaml，仍会从 {requested_start_task} 初始化状态: {exc}")

    migration_target = (
        _migration_literature_start_target(workspace_dir, requested_start_task)
        if from_workspace is not None
        else None
    )
    start_task = migration_target[0] if migration_target is not None else requested_start_task

    # Imported T3 may have a usable reading queue but lack a historical
    # search summary. The coverage Gate is where that researcher decision is
    # made, so do not hide it behind old report prerequisites.
    if start_task != "T2-COVERAGE-GATE":
        ok, err = validate_prerequisites(workspace_dir, start_task)
        if not ok:
            print(f"Prerequisites not met for {start_task}: {err}")
            if from_workspace is None:
                print("Hint: use --from <other-workspace> to copy upstream artifacts.")
            return 3

    state = _build_start_task_state(
        start_task=start_task,
        project_id=resolve_workspace_project_id(workspace_dir, project_id),
        source_state=source_state,
        source_history_boundary_task=requested_start_task,
    )
    t4_import_reselection: dict[str, object] | None = None
    if from_workspace is not None and requested_start_task in {"T4", "T4-GATE1"}:
        # A new workspace is allowed to reuse an existing Candidate Population
        # and its researcher-facing cards, but it must never inherit the source
        # workspace's active Gate1 decision as authorization to enter T4.5.
        # Keep that receipt in this new workspace's audit history and establish
        # the same confirmation boundary used by explicit T4 resume re-entry.
        try:
            t4_import_reselection = _archive_active_t4_selection_for_reentry(
                workspace_dir,
                state,
                requested_task=requested_start_task,
                reason="workspace_import_T4_reopens_candidate_decision",
            )
        except OSError as exc:
            print(
                "Unable to initialize T4-GATE1 selection without risking the imported selection record: "
                f"{exc}"
            )
            return 2
        # Both documented import entry points mean “compare the imported
        # Portfolio and choose here”. They are intentionally aliases, rather
        # than a request to re-run T4 or consume the source selection.
        start_task = "T4-GATE1"
        state.current_task = start_task
        state.status = "RUNNING"
        state.pending_gate = None
        state.paused_at = None
        state.last_error = None
        state.task_context["workspace_import_t4_reselection"] = {
            "schema_version": "1.0.0",
            "semantics": "workspace_import_t4_gate1_reselection",
            "source_workspace": str(from_workspace),
            "requested_task": requested_start_task,
            "initialized_at": datetime.now(timezone.utc).isoformat(),
            "t4_reselection": t4_import_reselection,
        }
    if migration_target is not None:
        state.task_context["workspace_import_decision"] = {
            "requested_task": requested_start_task,
            "decision_task": start_task,
            "reason": migration_target[1],
            "source_workspace": str(from_workspace),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    state.dump_yaml(state_path)
    if quiet:
        print(f"[Pipeline] state={start_task}", flush=True)
    else:
        if migration_target is not None:
            print(f"[进度] 已导入来源材料；{migration_target[1]}", flush=True)
        elif t4_import_reselection is not None:
            archived = str(t4_import_reselection.get("archived_selection") or "无活动选择记录")
            print(
                "[进度] 已导入 T4 Candidate Portfolio 并打开新的 Gate1 选择；"
                f"来源 workspace 的活动选择已归档到 {archived}，不会自动进入 T4.5。",
                flush=True,
            )
        else:
            print(f"[进度] 已初始化 pipeline state: current_task={start_task}", flush=True)
    return 0


def _prepare_resume_from_task(
    *,
    workspace_dir: Path,
    state_machine: StateMachine,
    start_task: str,
    quiet: bool = False,
) -> int:
    """Safely re-enter an existing workspace at a declared state-machine task.

    This is intentionally separate from ``run --start-task``.  The latter
    initializes a new workspace (optionally copying inputs from another
    workspace); this helper preserves the current workspace and its history,
    validates the target task's real prerequisites, and records why a pending
    gate was deliberately bypassed.
    """

    requested_task = resolve_public_stage_alias(start_task)
    literature_target = _migration_literature_start_target(workspace_dir, requested_task)
    # T2/T3 are researcher-facing literature re-entry points. Reopen their
    # scope decision before doing work, including legacy workspaces that lack
    # a previously persisted parameter record or an old search summary.
    start_task = literature_target[0] if literature_target is not None else requested_task
    if start_task not in state_machine.nodes:
        print(f"Unknown --from-task: {requested_task}")
        return 2
    if state_machine.nodes[start_task].terminal:
        print(f"--from-task cannot target a terminal state: {start_task}")
        return 2
    state_path = workspace_dir / "state.yaml"
    if not state_path.exists():
        print("--from-task requires an existing workspace state.yaml; use run --start-task for a new workspace.")
        return 2
    # The coverage Gate is itself the recovery surface for older T2 outputs.
    # Requiring every historical summary before it can be shown would hide the
    # decision a researcher needs to repair or expand the corpus.
    t4_gate1_reentry = start_task in {"T4", "T4-GATE1"}
    if start_task != "T2-COVERAGE-GATE" and not t4_gate1_reentry:
        ok, err = validate_prerequisites(workspace_dir, start_task)
        if not ok:
            print(f"Prerequisites not met for --from-task {start_task}: {err}")
            return 3
    try:
        state = StateYaml.load_yaml(state_path)
    except Exception as exc:
        print(f"Unable to load existing state.yaml for --from-task: {exc}")
        return 2

    t4_reselection: dict[str, object] | None = None
    if t4_gate1_reentry:
        try:
            t4_reselection = _archive_active_t4_selection_for_reentry(
                workspace_dir,
                state,
                requested_task=requested_task,
            )
        except OSError as exc:
            print(
                "Unable to reopen T4-GATE1 Candidate selection without risking the existing selection record: "
                f"{exc}"
            )
            return 2
        # Both public entry points mean "return to the existing Portfolio and
        # choose again".  They must never run T4, consume a previous Gate1
        # receipt, or replay an old confirmed directive before rendering the
        # human decision surface.
        start_task = "T4-GATE1"

    prior_task = state.current_task
    reentry = {
        "from_task": prior_task,
        "to_task": start_task,
        "requested_task": requested_task,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": "explicit_cli_resume_from_task",
        "cleared_pending_gate": state.pending_gate.gate_id if state.pending_gate else None,
    }
    if t4_reselection is not None:
        reentry["t4_reselection"] = t4_reselection
    history = state.task_context.get("manual_reentries")
    records = list(history) if isinstance(history, list) else []
    records.append(reentry)
    state.task_context["manual_reentries"] = records[-20:]
    state.current_task = start_task
    state.status = "PAUSED"
    state.pending_gate = None
    state.paused_at = datetime.now(timezone.utc).isoformat()
    state.last_error = None
    state.dump_yaml(state_path)
    if literature_target is not None:
        message = f"[进度] 已受校验地从 {prior_task} 重入 {requested_task}；{literature_target[1]}"
    elif t4_reselection is not None:
        archived = str(t4_reselection.get("archived_selection") or "无活动选择记录")
        message = (
            f"[进度] 已从 {prior_task} 重入 T4-GATE1；旧的 Gate1 选择已归档到 {archived}。"
            "将复用当前 Candidate Portfolio 并重新打开候选选择；历史确认只保留审计记录，不会自动进入 T4.5。"
        )
    else:
        message = f"[进度] 已受校验地从 {prior_task} 重入 {start_task}；下一步将按该节点正常执行。"
    print(f"[Pipeline] resume_from_task={start_task}" if quiet else message, flush=True)
    return 0


def _archive_active_t4_selection_for_reentry(
    workspace_dir: Path,
    state: StateYaml,
    *,
    requested_task: str,
    reason: str = "resume_from_task_T4_reopens_candidate_decision",
) -> dict[str, object] | None:
    """Reopen T4 Gate1 without letting an old confirmed selection auto-advance.

    Explicit T4 re-entry, including an imported new workspace starting at T4
    or T4-GATE1, is a researcher request to revisit the idea decision surface.
    Native T4 treats an active selection receipt and an accepted directive
    confirmation as authorization to proceed. Move the active receipt into
    immutable history, clear stale in-memory authorization, and record a new
    confirmation boundary. Candidate Populations, dossiers, downstream
    artifacts, historical confirmations, and the archived selection all remain
    available for audit.
    """

    selection_path = workspace_dir / "ideation" / "_gate1_user_selection.json"
    active_operation_keys = (
        "t4_operation_request",
        "t4_pending_directive",
        "human_iteration_directive",
        "t4_resumed_confirmed_directive",
        "t4_recovery_request",
    )
    cleared_operation_keys = [key for key in active_operation_keys if key in state.task_context]

    reopened_at = datetime.now(timezone.utc).isoformat()
    archived_selection: str | None = None
    receipt_path: str | None = None
    if selection_path.is_file():
        history_dir = workspace_dir / "ideation" / "evolution" / "selection_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive_path = history_dir / f"{timestamp}_gate1_user_selection.json"
        # Same-workspace rename preserves the original record byte-for-byte;
        # only its role changes from active authorization to history.
        selection_path.replace(archive_path)
        archived_selection = str(archive_path.relative_to(workspace_dir))
        receipt = {
            "schema_version": "1.0.0",
            "semantics": "t4_explicit_reselection_reentry",
            "requested_at": reopened_at,
            "requested_task": requested_task,
            "reopened_gate_task": "T4-GATE1",
            "confirmation_not_before": reopened_at,
            "archived_active_selection": archived_selection,
            "cleared_operation_keys": cleared_operation_keys,
            "reason": reason,
        }
        receipt_file = history_dir / f"{timestamp}_reselection_receipt.json"
        receipt_file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path = str(receipt_file.relative_to(workspace_dir))

    for key in active_operation_keys:
        state.task_context.pop(key, None)

    state.task_context["t4_gate1_reselection"] = {
        "schema_version": "1.0.0",
        "semantics": "t4_explicit_gate1_reselection_boundary",
        "requested_task": requested_task,
        "reopened_at": reopened_at,
        "confirmation_not_before": reopened_at,
        "archived_active_selection": archived_selection,
        "receipt": receipt_path,
    }
    return {
        "archived_selection": archived_selection,
        "receipt": receipt_path,
        "cleared_operation_keys": cleared_operation_keys,
        "confirmation_not_before": reopened_at,
        "reopened_gate_task": "T4-GATE1",
    }


def _migration_literature_start_target(workspace_dir: Path, current_task: str) -> tuple[str, str] | None:
    """Choose the first scope decision when importing or explicitly re-entering T2/T3."""

    if current_task not in {"T2", "T3"}:
        return None
    if not (workspace_dir / "project.yaml").is_file():
        return None

    if current_task == "T2":
        return (
            "T2-PARAM-GATE",
            "这是导入或显式重入 T2；下一步会先完整选择本次 T2/T3 参数，不会沿用来源 workspace 的参数直接检索。",
        )

    queue_paths = (
        workspace_dir / "literature" / "deep_read_queue.jsonl",
        workspace_dir / "literature" / "deep_read_queue_pending.jsonl",
    )
    if any(path.is_file() for path in queue_paths):
        return (
            "T2-COVERAGE-GATE",
            "这是导入或显式重入 T3；下一步会先复查检索覆盖，可继续当前精读队列、定向补检，或返回调整 T2/T3 参数。",
        )
    return (
        "T2-PARAM-GATE",
        "导入的 T3 材料没有保留阅读队列；下一步会先完整选择 T2/T3 参数并安全重建 T2 检索范围。",
    )


def _valid_literature_parameter_record(path: Path) -> bool:
    """Return whether an existing workspace can show its saved T2 choice."""

    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and bool(str(value.get("selected_option") or "").strip())


def _ordinary_literature_resume_target(workspace_dir: Path, current_task: str) -> tuple[str, str] | None:
    """Offer a lightweight continue-or-adjust decision for the same workspace."""

    if current_task not in {"T2", "T3"} or not (workspace_dir / "project.yaml").is_file():
        return None
    params_available = _valid_literature_parameter_record(
        workspace_dir / "literature" / "literature_params.json"
    )
    if current_task == "T2":
        if params_available:
            return (
                "T2-PARAM-CONFIRM-GATE",
                "下一步会显示当前 T2/T3 参数；确认继续不会新检索，只有选择重新配置才会改变范围。",
            )
        return (
            "T2-PARAM-GATE",
            "当前 workspace 没有可确认的参数记录；下一步会先完整选择 T2/T3 参数。",
        )

    queue_paths = (
        workspace_dir / "literature" / "deep_read_queue.jsonl",
        workspace_dir / "literature" / "deep_read_queue_pending.jsonl",
    )
    if any(path.is_file() for path in queue_paths):
        return (
            "T2-COVERAGE-GATE",
            "下一步会确认继续当前阅读队列，或按你的选择补检/调整参数；选择继续不会新增检索。",
        )
    if params_available:
        return (
            "T2-PARAM-CONFIRM-GATE",
            "当前阅读队列未保留；下一步会确认参数后安全重建 T2 范围。",
        )
    return (
        "T2-PARAM-GATE",
        "当前阅读队列和参数记录均未保留；下一步会先完整选择 T2/T3 参数。",
    )


def _prepare_literature_resume_checkpoint(
    *,
    workspace_dir: Path,
    state_machine: StateMachine,
    source_import: bool = False,
    quiet: bool = False,
) -> int:
    """Restore T2/T3's continue-or-adjust decision before a resume."""

    state_path = workspace_dir / "state.yaml"
    if not state_path.is_file():
        return 0
    try:
        state = StateYaml.load_yaml(state_path)
    except Exception as exc:
        print(f"Unable to load existing state.yaml for literature resume checkpoint: {exc}")
        return 2

    current_task = resolve_public_stage_alias(str(state.current_task or ""))
    literature_target = (
        _migration_literature_start_target(workspace_dir, current_task)
        if source_import
        else _ordinary_literature_resume_target(workspace_dir, current_task)
    )
    if literature_target is None:
        return 0
    target_task, message = literature_target
    if target_task not in state_machine.nodes:
        return 0
    pending_gate_id = state.pending_gate.gate_id if state.pending_gate is not None else ""
    if pending_gate_id and pending_gate_id != "runtime_recovery_gate":
        return 0

    record = {
        "from_task": current_task,
        "to_task": target_task,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": (
            "resume_from_workspace_literature_scope_checkpoint"
            if source_import
            else "ordinary_resume_literature_scope_checkpoint"
        ),
        "decision_reason": message,
        "cleared_pending_gate": pending_gate_id or None,
        "prior_error": str(state.last_error or ""),
    }
    history = state.task_context.get("literature_resume_checkpoints")
    records = list(history) if isinstance(history, list) else []
    records.append(record)
    state.task_context["literature_resume_checkpoints"] = records[-20:]
    state.current_task = target_task
    state.status = "PAUSED"
    state.pending_gate = None
    state.paused_at = datetime.now(timezone.utc).isoformat()
    state.last_error = None
    state.dump_yaml(state_path)

    print(
        f"[Pipeline] literature_resume_checkpoint={target_task}"
        if quiet
        else (
            f"[进度] 从来源 workspace 导入后重入 {current_task}：{message}"
            if source_import
            else f"[进度] {current_task} 恢复决策：{message}"
        ),
        flush=True,
    )
    return 0


def _prepare_resume_workspace_import(
    *,
    workspace_dir: Path,
    state_machine: StateMachine,
    from_workspace: Path,
    requested_task: str | None,
    quiet: bool = False,
) -> int:
    """Merge missing declared inputs from another workspace before ``resume``.

    Unlike ``run --from``, this preserves the target state/history and never
    treats source state as merge input. It is useful when a paused debug
    workspace needs newly declared input directories, such as T4 paper notes.
    """

    state_path = workspace_dir / "state.yaml"
    if not state_path.exists():
        print("resume --from requires an existing state.yaml; use run --from for a new workspace.")
        return 2
    if not from_workspace.exists():
        print(f"--from workspace 不存在: {from_workspace}")
        return 2
    if from_workspace.resolve() == workspace_dir.resolve():
        print("--from 不能指向当前 --workspace；请使用不同的来源 workspace。")
        return 2
    try:
        state = StateYaml.load_yaml(state_path)
    except Exception as exc:
        print(f"Unable to load existing state.yaml for resume --from: {exc}")
        return 2
    task_id = resolve_public_stage_alias(requested_task or state.current_task)
    if task_id not in state_machine.nodes:
        print(f"Unknown import task for resume --from: {task_id}")
        return 2
    copied = _copy_task_inputs_from_workspace(
        task_id=task_id,
        from_workspace=from_workspace,
        workspace_dir=workspace_dir,
        quiet=quiet,
        preserve_existing_files=True,
    )
    if not quiet:
        print(
            f"[导入] resume 前已从 {from_workspace} 为 {task_id} 合并 {len(copied)} 项前置材料；"
            "当前 workspace 的状态和已有文件保持不变。",
            flush=True,
        )
    return 0


def _copy_task_inputs_from_workspace(
    *,
    task_id: str,
    from_workspace: Path,
    workspace_dir: Path,
    quiet: bool = False,
    preserve_existing_files: bool = False,
) -> list[str]:
    """Merge a task's complete import closure from another workspace.

    ``task_import_paths`` expands public downstream stages to the whole
    ``literature/`` asset tree.  The state-machine input list remains narrow
    for execution, but an import must not leave a newly initialized empty note
    directory in place of the source project's real paper corpus.
    """

    copied: list[str] = []
    for rel_path in task_import_paths(task_id):
        src = from_workspace / rel_path
        dst = workspace_dir / rel_path
        if not src.exists():
            continue
        merge_result = merge_workspace_artifact(
            src,
            dst,
            preserve_existing_files=preserve_existing_files,
        )
        copied_files = int(merge_result.get("copied_files") or 0)
        updated_files = int(merge_result.get("updated_files") or 0)
        if not copied_files and not updated_files:
            continue
        if not quiet:
            print(f"copied: {rel_path} ({copied_files} new, {updated_files} refreshed)", flush=True)
        copied.append(str(rel_path))

    # A source workspace can still carry historic paper_notes* paths or
    # catalog JSON colocated with bridge notes.  Normalize only the imported
    # target after merging; source state and artifacts are never mutated.
    migrate_workspace_note_directories(workspace_dir)
    migrate_legacy_bridge_catalogs(workspace_dir)
    migrate_legacy_literature_paths(workspace_dir)
    build_literature_manifest(workspace_dir, write=True)
    if not quiet and not copied:
        print("[导入] 来源工作区没有此阶段可导入的新前置材料。", flush=True)
    return copied


def _prepare_single_task_import(
    *,
    workspace_dir: Path,
    task_id: str,
    from_workspace: Path | None,
    quiet: bool = False,
) -> tuple[str, Path | None, int]:
    """Resolve a single-task alias and copy missing inputs before LLM startup.

    Importing is filesystem-only work.  It must succeed even when a provider is
    unavailable, otherwise a user cannot inspect or retry the prepared task.
    Existing target artifacts are intentionally preserved.
    """

    try:
        canonical_task_id = SingleTaskRunner._normalize_task_id(task_id)
    except ValueError as exc:
        print(str(exc))
        return task_id, None, 2
    if from_workspace is None:
        return canonical_task_id, None, 0
    if not from_workspace.exists():
        print(f"--from workspace 不存在: {from_workspace}")
        return canonical_task_id, None, 2
    if from_workspace.resolve() == workspace_dir.resolve():
        print("--from 不能指向当前 --workspace；请使用不同的来源 workspace。")
        return canonical_task_id, None, 2
    copied = _copy_task_inputs_from_workspace(
        task_id=canonical_task_id,
        from_workspace=from_workspace,
        workspace_dir=workspace_dir,
        quiet=quiet,
        preserve_existing_files=True,
    )
    if not quiet:
        print(
            f"[导入] 已从 {from_workspace} 准备 {canonical_task_id} 的前置材料"
            f"（新增 {len(copied)} 项；原 workspace 未修改）。",
            flush=True,
        )
    return canonical_task_id, None, 0


def _build_start_task_state(
    *,
    start_task: str,
    project_id: str,
    source_state: StateYaml | None,
    source_history_boundary_task: str | None = None,
) -> StateYaml:
    if source_state is None:
        return StateYaml(project_id=project_id, current_task=start_task, status="RUNNING")

    state = StateYaml(
        # The source state is execution history, not an authority for the new
        # workspace's research identity.  In particular it may contain the
        # historical CLI default `demo-project`.
        project_id=project_id,
        current_task=start_task,
        status="RUNNING",
        budget_cumulative=source_state.budget_cumulative,
        task_context={},
    )
    kept_tasks: set[str] = set()
    history_boundary = source_history_boundary_task or start_task
    for entry in source_state.history:
        if entry.task == history_boundary:
            break
        state.history.append(entry)
        kept_tasks.add(entry.task)
    state.iteration_count = {
        task: count for task, count in source_state.iteration_count.items() if task in kept_tasks
    }
    state.iteration_history = {
        task: entries for task, entries in source_state.iteration_history.items() if task in kept_tasks
    }
    return state


async def run_task_command(args: argparse.Namespace) -> int:
    """单 task 模式入口。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_summary=False,
    )
    install_signal_handlers()
    from_workspace = Path(args.from_workspace).resolve() if args.from_workspace else None
    task_id, runner_from_workspace, import_code = _prepare_single_task_import(
        workspace_dir=workspace_dir,
        task_id=args.task_id.strip(),
        from_workspace=from_workspace,
        quiet=_is_quiet_args(args, runtime_settings),
    )
    if import_code != 0:
        return import_code
    try:
        prepared = await _prepare_runtime(
            args,
            workspace_dir,
            require_llm=task_id not in {"T5-REBOOST-GATE", "T5-SPECIALIZE-EXECUTOR-SKILLS"},
        )
    except Exception as exc:
        return _render_runtime_preparation_failure(
            args,
            message="模型连接或本地依赖暂时不可用。检查模型设置和依赖后重新运行此任务。",
            error=exc,
        )
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_banner=False,
        skill_roots=prepared.skill_roots,
        skill_count=prepared.skill_count,
        mcp_server_count=prepared.mcp_server_count,
        mcp_tool_count=prepared.mcp_tool_count,
    )
    try:
        try:
            runner = SingleTaskRunner(
                workspace=workspace_dir,
                task_id=task_id,
                llm_client=prepared.llm_client,
                tool_registry=prepared.registry,
                skill_roots=prepared.skill_roots,
                from_workspace=runner_from_workspace,
                override_profile=args.profile,
                human_interface=_build_human_interface(runtime_settings, llm_client=prepared.llm_client),
                runtime_settings=runtime_settings,
                allow_legacy=bool(getattr(args, "allow_legacy", False)),
            )
        except ValueError as exc:
            print(str(exc))
            return 2
        return await runner.run()
    finally:
        await prepared.aclose()


_SKILL_EXECUTE_ANSWERS = {"执行", "开始", "运行", "确认执行", "yes", "y", "run", "execute", "start"}
_SKILL_PAUSE_ANSWERS = {"暂停", "稍后", "退出", "取消", "不执行", "no", "n", "pause", "cancel", "stop"}
_SKILL_INTAKE_CONTINUE_ANSWERS = {"1", "继续", "继续收集缺失材料", "continue", "resume"}
_SKILL_INTAKE_PAUSE_ANSWERS = _SKILL_PAUSE_ANSWERS | {
    "2",
    "暂停并保留会话",
    "pause and keep session",
    "keep session",
    "exit",
    "q",
}


def _normalized_skill_answer(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _selected_reference_answer(value: str, suggestions: list[str]) -> str:
    """Resolve a numbered/ordinal CLI reply to the suggestion it displayed.

    The number has no intrinsic action meaning.  It is only a stable reference
    to the first, second, and so on suggestion rendered on this particular
    page.  Callers then interpret the returned *suggestion text* according to
    their own domain semantics.
    """

    raw = _normalized_skill_answer(value)
    compact = re.sub(r"[\s\[\]（）()、.。,:：;；_-]+", "", raw)
    selected_index: int | None = None
    numeric = re.fullmatch(r"\[?(\d+)\]?", raw)
    if numeric:
        selected_index = int(numeric.group(1))
    else:
        ordinal_aliases = {
            "第一个": 1,
            "第一条": 1,
            "第一项": 1,
            "第1个": 1,
            "第1条": 1,
            "第1项": 1,
            "第二个": 2,
            "第二条": 2,
            "第二项": 2,
            "第2个": 2,
            "第2条": 2,
            "第2项": 2,
            "第三个": 3,
            "第三条": 3,
            "第三项": 3,
            "第3个": 3,
            "第3条": 3,
            "第3项": 3,
        }
        for alias, index in ordinal_aliases.items():
            if compact == alias or compact == f"按{alias}参考回答继续":
                selected_index = index
                break
    if selected_index is None or not 1 <= selected_index <= len(suggestions):
        return value
    return str(suggestions[selected_index - 1])


def _skill_confirmation_action(value: str) -> str | None:
    """Interpret the content of a selected Skill confirmation suggestion."""

    normalized = _normalized_skill_answer(value)
    # Test pausing/negation before the positive action: ``不执行`` must never
    # become execution merely because it contains the word ``执行``.
    if normalized in _SKILL_PAUSE_ANSWERS or any(
        token in normalized for token in ("暂停", "取消", "不执行", "pause", "cancel", "stop")
    ):
        return "pause"
    if normalized in _SKILL_EXECUTE_ANSWERS or any(
        token in normalized for token in ("执行", "开始", "运行", "run", "execute", "start")
    ):
        return "execute"
    return None


def _skill_user_confirms_execution(value: str) -> bool:
    return _skill_confirmation_action(value) == "execute"


def _skill_user_paused(value: str) -> bool:
    return _skill_confirmation_action(value) == "pause"


def _skill_result_waits_for_human_input(result: AgentResult) -> bool:
    """Recognize an in-workflow ``ask_human`` pause without masking errors.

    ``AgentRunner`` deliberately represents an unavailable human response as
    ``STOP_INTERRUPTED`` so pipeline state machines can pause safely.  A
    standalone guided session must preserve the same semantics in its durable
    session record; it must not turn the pause into a failed output contract.
    """

    if result.stop_reason != AgentResult.STOP_INTERRUPTED:
        return False
    detail = " ".join(str(value or "") for value in (result.error, result.message)).casefold()
    return "human input unavailable" in detail or "human_input_unavailable" in detail


def _parse_skill_intake_followup_action(value: str) -> str | None:
    """Parse the numbered continue/pause control without leaking it into other gates."""

    normalized = _normalized_skill_answer(value)
    if normalized in _SKILL_INTAKE_CONTINUE_ANSWERS:
        return "continue"
    if normalized in _SKILL_INTAKE_PAUSE_ANSWERS:
        return "pause"
    return None


async def run_skill_command(args: argparse.Namespace) -> int:
    """Run a Skill through guided intake, explicit confirmation, then execution."""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).resolve()
    skill_roots = _resolve_skill_roots(args, workspace_dir)
    try:
        requested_skill = resolve_skill(args.skill_name, skill_roots)
    except Exception as exc:
        print(f"Skill 启动前检查失败: {exc}", file=sys.stderr)
        return 2
    if not is_standalone_skill(requested_skill):
        print(
            f"Skill '{requested_skill.name}' 不支持独立运行；`run-skill` 已在启动模型和创建工作区前安全停止。\n"
            + _managed_skill_route_text(requested_skill, workspace_dir)
        )
        return 2
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_summary=False,
    )
    install_signal_handlers()

    # A human at an interactive terminal should not have to discover a hidden
    # flag before the system starts collecting the material it explicitly says
    # it needs. Automation/pipes remain noninteractive by default.
    interactive_session = bool(
        getattr(args, "interactive", False)
        or (sys.stdin.isatty() and not getattr(args, "non_interactive", False))
    )

    # The deterministic check always precedes runtime preparation. A
    # noninteractive invocation keeps this as the complete missing-input
    # behavior, so it remains safe and resumable without a provider.
    try:
        skill = resolve_skill(args.skill_name, skill_roots)
        interaction = parse_skill_interaction(skill.metadata)
        workflow = parse_skill_workflow(skill.metadata)
        session_id = args.session_id or skill.name
        previous = load_session(workspace_dir, session_id)
        request = " ".join(args.request).strip()
        if args.resume and not request and previous:
            request = str(previous.get("request", "")).strip()
        if interactive_session and not request:
            # First show a deterministic, Skill-specific material scan. Asking
            # for a task before that screen made a fresh guided session feel
            # like an unexplained free-form chat prompt.
            initial_readiness = check_skill_readiness(
                skill_name=skill.name,
                metadata=skill.metadata,
                workspace=workspace_dir,
                request="",
            )
            initial_packet = prepare_skill_intake_packet(initial_readiness)
            initial_session_file, _initial_session = record_readiness(
                workspace=workspace_dir,
                session_id=session_id,
                skill_name=skill.name,
                skill_path=skill.skill_dir,
                readiness=initial_readiness,
                resume=bool(args.resume),
                intake_packet_path=initial_packet,
                workflow=workflow,
            )
            print(
                _render_skill_readiness_for_cli(
                    args,
                    skill_name=skill.name,
                    session_id=session_id,
                    session_file=initial_session_file,
                    readiness=initial_readiness,
                )
            )
            prompt = (
                interaction.request_prompt
                if interaction is not None
                else "请说明希望这个 Skill 完成什么。"
            )
            request = await _build_human_interface(runtime_settings).ask_clarification(
                question=f"{prompt} 输入“暂停”会保留本次启动检查，稍后可用同一 session 继续。"
            )
            if _skill_user_paused(request):
                record_skill_execution_confirmation_pending(
                    workspace=workspace_dir,
                    session_id=session_id,
                    message="用户在说明任务前暂停；启动检查和材料清单已保留。",
                    input_ready=False,
                )
                print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
                return 0
        readiness = check_skill_readiness(
            skill_name=skill.name,
            metadata=skill.metadata,
            workspace=workspace_dir,
            request=request,
        )
        intake_packet = prepare_skill_intake_packet(readiness)
        session_file, _session = record_readiness(
            workspace=workspace_dir,
            session_id=session_id,
            skill_name=skill.name,
            skill_path=skill.skill_dir,
            readiness=readiness,
            resume=bool(args.resume),
            intake_packet_path=intake_packet,
            workflow=workflow,
        )
    except Exception as exc:
        print(f"Skill 启动前检查失败: {exc}", file=sys.stderr)
        return 2

    print(
        _render_skill_readiness_for_cli(
            args,
            skill_name=skill.name,
            session_id=session_id,
            session_file=session_file,
            readiness=readiness,
        )
    )
    prepared = None
    human: HumanInterface | None = None
    if not readiness.ready:
        if not interactive_session:
            return 2
        if not sys.stdin.isatty():
            print(
                "当前终端无法接收回答；已保存准备状态。补充材料后，请在可交互终端用同一恢复标识继续。"
            )
            return 2
        if interaction is None or interaction.mode != "guided":
            print("当前 Skill 尚未声明可自动检查的材料要求，无法开始引导式材料准备。", file=sys.stderr)
            return 2
        try:
            prepared = await _prepare_runtime(args, workspace_dir)
            human = _build_human_interface(runtime_settings, llm_client=prepared.llm_client)
            intake_round = 1
            while not readiness.ready:
                record_input_collection_started(workspace_dir, session_id)
                intake_result = await run_skill_intake(
                    skill_name=skill.name,
                    interaction=interaction,
                    user_request=request,
                    workspace=workspace_dir,
                    tool_registry=prepared.registry,
                    llm_client=prepared.llm_client,
                    human_interface=human,
                    session_id=session_id,
                    intake_packet_path=(
                        str(intake_packet.relative_to(workspace_dir)) if intake_packet is not None else ""
                    ),
                    runtime_settings=runtime_settings,
                    llm_profile=args.profile,
                    intake_round=intake_round,
                )
                readiness = check_skill_readiness(
                    skill_name=skill.name,
                    metadata=skill.metadata,
                    workspace=workspace_dir,
                    request=request,
                )
                intake_packet = prepare_skill_intake_packet(readiness)
                session_file, _session = record_readiness(
                    workspace=workspace_dir,
                    session_id=session_id,
                    skill_name=skill.name,
                    skill_path=skill.skill_dir,
                    readiness=readiness,
                    resume=True,
                    intake_packet_path=intake_packet,
                    workflow=workflow,
                )
                intake_message = (
                    f"第 {intake_round} 轮材料准备完成，所需材料已齐全；等待确认是否执行 Skill。"
                    if readiness.ready
                    else f"第 {intake_round} 轮材料准备后仍缺少内容；系统会继续逐项询问，或等待你暂停。"
                )
                record_input_collection_finished(
                    workspace=workspace_dir,
                    session_id=session_id,
                    ready=readiness.ready,
                    message=intake_message,
                )
                print(
                    _render_skill_readiness_for_cli(
                        args,
                        skill_name=skill.name,
                        session_id=session_id,
                        session_file=session_file,
                        readiness=readiness,
                    )
                )
                if not intake_result.ok:
                    print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
                    await prepared.aclose()
                    prepared = None
                    return 2
                if readiness.ready:
                    break
                intake_action: str | None = None
                while intake_action is None:
                    action = await human.ask_clarification(
                        question=(
                            "当前材料还不足以开始。输入“继续”让系统继续准备缺少的材料；"
                            "输入“暂停”保留当前会话，稍后再继续。"
                        ),
                        suggestions=["继续准备材料", "暂停并保留会话"],
                    )
                    intake_action = _parse_skill_intake_followup_action(action)
                    if intake_action is None:
                        print("请选择“继续”或“暂停”；系统不会在未确认时自动继续。")
                if intake_action == "pause":
                    record_skill_execution_confirmation_pending(
                        workspace=workspace_dir,
                        session_id=session_id,
                        message="人工在材料未齐时暂停；会话保留为 WAITING_INPUT。",
                        input_ready=False,
                    )
                    print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
                    await prepared.aclose()
                    prepared = None
                    return 2
                intake_round += 1
        except Exception as exc:
            if prepared is not None:
                await prepared.aclose()
                prepared = None
            if isinstance(exc, LLMConfigurationWizardError):
                _render_llm_configuration_wizard_failure(args, exc)
                print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
                return exc.exit_code
            record_runtime_pause(workspace=workspace_dir, session_id=session_id, error=exc)
            print(
                "Skill 的材料准备暂时中断，当前进度已保存。修复运行环境或补充材料后，可用同一恢复标识继续。",
                file=sys.stderr,
            )
            print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
            return 1

    if interactive_session and not getattr(args, "yes", False):
        confirmation_human = human or _build_human_interface(runtime_settings)
        skill_confirmation_suggestions = ["执行当前 Skill", "暂停，稍后使用 --resume 继续"]
        record_skill_execution_confirmation_pending(
            workspace=workspace_dir,
            session_id=session_id,
            message="输入已就绪，等待人工确认是否开始执行当前 Skill。",
            input_ready=True,
        )
        try:
            while True:
                decision = await confirmation_human.ask_clarification(
                    question=(
                        f"Skill `{skill.name}` 的初始输入已通过检查。是否现在执行？\n"
                        "输入“执行”开始；输入“暂停”只保留已整理的材料和会话。"
                    ),
                    suggestions=skill_confirmation_suggestions,
                )
                decision = _selected_reference_answer(
                    decision,
                    skill_confirmation_suggestions,
                )
                if _skill_user_confirms_execution(decision):
                    break
                if _skill_user_paused(decision):
                    record_skill_execution_confirmation_pending(
                        workspace=workspace_dir,
                        session_id=session_id,
                        message="人工确认材料已就绪，但选择暂不执行。",
                        input_ready=True,
                    )
                    print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
                    if prepared is not None:
                        await prepared.aclose()
                        prepared = None
                    return 0
                print("请明确输入“执行”或“暂停”；系统不会把模糊回答当作执行授权。")
        except HumanInputUnavailable as exc:
            record_skill_execution_confirmation_pending(
                workspace=workspace_dir,
                session_id=session_id,
                message=f"等待执行确认：{exc}",
                input_ready=True,
            )
            print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
            if prepared is not None:
                await prepared.aclose()
                prepared = None
            return 2

    if prepared is None:
        try:
            prepared = await _prepare_runtime(args, workspace_dir)
        except Exception as exc:
            if isinstance(exc, LLMConfigurationWizardError):
                _render_llm_configuration_wizard_failure(args, exc)
                print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
                return exc.exit_code
            record_runtime_pause(workspace=workspace_dir, session_id=session_id, error=exc)
            message = (
                "运行环境暂时不可用，当前进度已保存。检查模型设置或本地依赖后，使用同一 "
                f"`--session-id {session_id} --resume` 继续。"
            )
            if getattr(args, "verbose", False):
                message += f"\n诊断：{exc}"
            print(message, file=sys.stderr)
            print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
            return 1
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_banner=False,
        skill_roots=prepared.skill_roots,
        skill_count=prepared.skill_count,
        mcp_server_count=prepared.mcp_server_count,
        mcp_tool_count=prepared.mcp_tool_count,
    )

    try:
        # Resolve again from the prepared roots so custom tool registration and
        # the execution object are guaranteed to use the same skill source.
        skill = resolve_skill(args.skill_name, prepared.skill_roots)
        outputs_expected = expected_outputs_from_metadata(skill.metadata, workspace_dir)
        human = human or _build_human_interface(runtime_settings, llm_client=prepared.llm_client)
        record_run_started(workspace_dir, session_id)
        result = await run_skill(
            skill=skill,
            user_request=request or f"Execute skill '{skill.name}'.",
            workspace=workspace_dir,
            tool_registry=prepared.registry,
            llm_client=prepared.llm_client,
            human_interface=human,
            outputs_expected=outputs_expected,
            llm_profile=args.profile,
            runtime_settings=runtime_settings,
            skill_session_path=str(session_file.relative_to(workspace_dir)),
            skill_session_id=session_id,
            selected_inputs=readiness.selected_inputs,
            workspace_mode=readiness.workspace_mode,
            intake_packet_path=(
                str(intake_packet.relative_to(workspace_dir)) if intake_packet is not None else ""
            ),
            resume=bool(getattr(args, "resume", False)),
        )
    finally:
        await prepared.aclose()
    if _skill_result_waits_for_human_input(result):
        record_human_input_pause(
            workspace=workspace_dir,
            session_id=session_id,
            result=result,
            outputs_expected=outputs_expected,
        )
        print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
        return 2
    if result.stop_reason in {AgentResult.STOP_INTERRUPTED, AgentResult.STOP_HUMAN_REJECT}:
        # Cancellation, a resumable tool pause, or an explicit human stop is
        # not a failed final-output contract. Preserve the current session and
        # let the researcher resume after reviewing the durable trace.
        record_run_result(
            workspace=workspace_dir,
            session_id=session_id,
            result=result,
            outputs_expected=outputs_expected,
        )
        print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
        return 2
    if outputs_expected:
        ok, errors = validate_declared_outputs(workspace_dir, outputs_expected)
        if not ok:
            error_text = errors if isinstance(errors, str) else "; ".join(str(item) for item in (errors or []))
            result.ok = False
            result.stop_reason = AgentResult.STOP_ERROR
            result.error = "Skill output validation failed: " + error_text
            result.message = result.error

    result_session = record_run_result(
        workspace=workspace_dir,
        session_id=session_id,
        result=result,
        outputs_expected=outputs_expected,
    )

    print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
    return 0 if result.ok else 1


async def selftest_command(args: argparse.Namespace) -> int:
    """LLM endpoint 自检。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=None,
        show_summary=False,
    )
    client = LLMClient(Path(args.model_settings).resolve())
    try:
        status = client.configuration_status()
        if not status.get("ready", False):
            _render_llm_setup_required(status)
            return 2
        llm_results = await client.selftest(args.profile or None)
    finally:
        await client.aclose()
    dependency_results = _dependency_selftest()
    _render_selftest_summary(
        args,
        llm_results=llm_results,
        dependency_results=dependency_results,
        title="系统检查",
    )
    llm_ok = all(item.get("ok") for item in llm_results.values())
    deps_ok = all(item.get("ok") for item in dependency_results.values())
    return 0 if (llm_ok and deps_ok) else 1


def init_workspace_command(args: argparse.Namespace) -> int:
    """初始化一个标准 workspace。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=workspace_dir,
        show_summary=False,
    )
    result: WorkspaceInitResult = initialize_workspace(
        workspace_dir,
        create_project_file=not args.no_project_file,
        project_id=args.project_id,
        topic=args.topic or "",
        force_project_file=args.force_project_file,
        runtime_dir_name=runtime_settings.workspace.runtime_dir,
    )
    workflow_profile = None
    if any(getattr(args, name, None) for name in ("workflow_mode", "auto_preset", "auto_t4_mode")):
        requested_auto_preset = getattr(args, "auto_preset", None)
        workflow_profile = configure_workflow_mode(
            workspace_dir,
            mode=getattr(args, "workflow_mode", None) or ("auto" if requested_auto_preset else "copilot"),
            preset=requested_auto_preset,
            t4_mode=getattr(args, "auto_t4_mode", None),
            selection_source="command_line",
        )
    print(
        yaml.safe_dump(
            {
                "ok": True,
                "workspace": str(result.workspace_dir),
                "created_dirs": result.created_dirs,
            "project_file": str(result.project_file) if result.project_file else None,
            "workflow_mode": workflow_profile.get("mode") if workflow_profile else "pending_t1_selection",
            "workflow_preset": workflow_profile.get("preset") if workflow_profile else None,
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return 0


def doctor_command(args: argparse.Namespace) -> int:
    """Deterministic local environment check for Native and Docker mode."""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    workspace_dir = Path(args.workspace).expanduser().resolve()

    checks: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append((status, name, detail))

    try:
        import researchos

        add("OK", "package", f"researchos {getattr(researchos, '__version__', 'unknown')} loaded")
    except Exception as exc:
        add("ERROR", "package", f"failed to import researchos: {exc}")

    try:
        runtime_path = resolve_runtime_config_path(system_config_path("runtime.yaml"))
        if runtime_path.exists():
            add("OK", "runtime config", str(runtime_path.resolve()))
        else:
            add("WARN", "runtime config", f"{runtime_path} not found; built-in defaults will be used")
        state_machine = StateMachine(
            Path(args.state_machine).resolve(),
            Path(args.gates).resolve() if args.gates else None,
        )
        definition_errors = state_machine.validate_definition()
        if definition_errors:
            add("ERROR", "state machine", "; ".join(definition_errors[:3]))
        else:
            add("OK", "state machine", str(Path(args.state_machine).resolve()))
    except Exception as exc:
        add("ERROR", "state machine", str(exc))

    user_config = os.getenv("RESEARCHOS_MODEL_SETTINGS") or os.getenv("RESEARCHOS_CONFIG") or os.getenv("RESEARCHOS_USER_SETTINGS") or "config/model_settings.yaml"
    if Path(user_config).exists():
        add("OK", "model settings", str(Path(user_config).resolve()))
    else:
        add("WARN", "model settings", f"{user_config} not found; run `configure-llm` before starting a project")

    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        probe = workspace_dir / ".researchos_write_test"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        add("OK", "workspace", f"writable: {workspace_dir}")
        write_runtime_environment(workspace_dir, runtime_settings.workspace.runtime_dir)
    except Exception as exc:
        add("ERROR", "workspace", f"not writable: {workspace_dir} ({exc})")

    deps = _dependency_selftest()
    pdf_ok = deps["pdf_processing"]["ok"]
    add("OK" if pdf_ok else "ERROR", "PDF tools", "pdfplumber available" if pdf_ok else "pdfplumber missing")

    latex_settings = runtime_settings.latex
    if bool(getattr(args, "allow_docker_latex", False)) and not latex_settings.allow_docker_fallback:
        latex_settings = LatexSettings(
            default_backend=latex_settings.default_backend,
            allow_docker_fallback=True,
            docker_image=latex_settings.docker_image,
        )
    latex_preflight = latex_backend_preflight(latex_settings)
    if latex_preflight.get("ok"):
        backend = latex_preflight.get("selected_backend")
        detail = str(latex_preflight.get("reason") or backend)
        if latex_preflight.get("image"):
            detail += f"; image={latex_preflight['image']}"
        add("OK", "LaTeX backend", detail)
    else:
        detail = str(latex_preflight.get("message") or latex_preflight.get("reason") or "no usable PDF compiler")
        add("WARN", "LaTeX backend", detail)

    docker_version = command_version("docker", "--version")
    if docker_version:
        add("INFO", "Docker", docker_version)
    else:
        add(
            "INFO",
            "Docker",
            "CLI not found; Core/Compose runs are unaffected, only explicit Docker backends are unavailable",
        )

    codex_version = command_version("codex", "--version")
    claude_version = command_version("claude", "--version") or command_version("claude-code", "--version")
    add("INFO", "Codex CLI", codex_version or "not found; only needed if selected as external executor")
    add("INFO", "Claude Code", claude_version or "not found; only needed if selected as external executor")

    key_names = [
        "SILICONFLOW_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
    ]
    present_keys = [name for name in key_names if os.getenv(name)]
    if present_keys:
        add("OK", "LLM keys", ", ".join(present_keys))
    else:
        add("WARN", "LLM keys", "no provider API key detected; LLM stages will wait/fail until configured")
    if os.getenv("S2_API_KEY"):
        add("OK", "paper API", "S2_API_KEY configured")
    else:
        add("INFO", "paper API", "S2_API_KEY not configured; some enrichment will be limited")

    env = collect_runtime_environment(workspace_dir)
    add("INFO", "runtime mode", f"{env['runtime_mode']} (containerized={env['containerized']})")
    if env.get("workspace_host_hint") and env["workspace_host_hint"] != str(workspace_dir):
        add("INFO", "host workspace hint", str(env["workspace_host_hint"]))

    print("ResearchOS Doctor\n")
    for status, name, detail in checks:
        print(f"[{status:<5}] {name}: {detail}")

    errors = [item for item in checks if item[0] == "ERROR"]
    if errors:
        print("\nResult: ResearchOS Core has blocking issues.")
        return 1
    runtime_label = "Docker/Compose Mode" if env.get("containerized") else "Native Mode"
    print(f"\nResult: ResearchOS Core is ready. Optional warnings do not block {runtime_label}.")
    return 0


def _t45_public_package_check(workspace: Path) -> dict[str, Any]:
    """Report the public T4.5 package state without changing FSM node meaning.

    Internally, ``T4.5`` is the novelty/collision-audit node.  A passing audit
    deliberately continues through ``T4.5-FORMALIZE`` and ``T4.5-REVIEW``
    before it authorizes T5.  Treating an audit-only success as a complete
    researcher-facing plan was a recurrent source of confusing diagnostics:
    ``validate --task T4.5`` could be green while the blueprint, hypotheses,
    experiment plan, Proposal, or accepted orientation review did not exist.

    This helper preserves the state machine's granular node contract while
    making the CLI's public ``T4.5`` report a truthful composite phase view.
    It is read-only and does not reinterpret a non-passing novelty verdict as
    an error: in that branch formalization is correctly not yet authorized.
    """

    audit_path = workspace / "ideation" / "novelty_audit.md"
    if not audit_path.is_file() or audit_path.stat().st_size <= 0:
        return {
            "ok": False,
            "status": "audit_missing",
            "formalization_required": False,
            "errors": "Missing ideation/novelty_audit.md",
            "semantics": "T4.5 audit must exist before its public phase state can be determined",
        }
    try:
        audit_text = audit_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "ok": False,
            "status": "audit_unreadable",
            "formalization_required": False,
            "errors": f"Cannot read ideation/novelty_audit.md: {exc}",
            "semantics": "T4.5 audit must be readable before its public phase state can be determined",
        }
    verdict = extract_final_gate_verdict(audit_text)
    audit_passed = is_passing_final_gate_verdict(verdict, allow_legacy=True)
    if not audit_passed:
        return {
            "ok": True,
            "status": "audit_complete_formalization_not_authorized",
            "formalization_required": False,
            "audit_final_gate_verdict": verdict or "missing_or_unrecognized",
            "errors": None,
            "semantics": (
                "The novelty/collision audit is the completed T4.5 node. "
                "Because its Final Gate Verdict is not a formalization-pass verdict, "
                "hypotheses, Proposal, and T5 authority are intentionally not required yet."
            ),
        }
    package_ok, package_error = _validate_t45_post_novelty_formalization(workspace, audit_path)
    return {
        "ok": package_ok,
        "status": "formalization_complete" if package_ok else "audit_passed_formalization_pending_or_invalid",
        "formalization_required": True,
        "audit_final_gate_verdict": verdict,
        "errors": package_error,
        "semantics": (
            "A passing novelty/collision audit authorizes, but does not itself create, the full T4.5 research package. "
            "The package is complete only after T4.5-FORMALIZE and T4.5-REVIEW produce and accept the source-bound "
            "blueprint, claim registry, hypotheses, experiment plan, Proposal, orientation review, and formalization receipt."
        ),
    }


def validate_command(args: argparse.Namespace) -> int:
    """校验指定 task 的输入或产物。"""

    register_builtin_task_checkers()
    workspace = Path(args.workspace).resolve()
    state_machine_path = Path(args.state_machine).resolve()
    task_id = args.task
    if task_id is None:
        state = StateYaml.load_yaml((workspace / "state.yaml").resolve())
        task_id = state.current_task
    task_id = resolve_public_stage_alias(str(task_id))

    scope = str(getattr(args, "scope", "outputs") or "outputs").strip().lower()
    checks: dict[str, dict[str, Any]] = {}

    if scope in {"inputs", "all"}:
        input_ok, input_errors = validate_prerequisites(workspace, task_id)
        checks["inputs"] = {
            "ok": input_ok,
            "errors": input_errors,
            "semantics": "required task inputs and prerequisite contracts, including Literature Artifact Contract when declared",
        }

    if scope in {"outputs", "all"}:
        declared_outputs = build_declared_outputs_from_state_machine(state_machine_path, task_id)
        output_ok, output_errors = validate_task_artifacts(
            workspace,
            task_id,
            declared_outputs=declared_outputs,
        )
        if not output_ok and task_id == "T8-SECTION-PLAN":
            from .runtime.manuscript_recovery import can_repair_t8_section_plan, repair_t8_section_plan_outputs

            if can_repair_t8_section_plan(workspace):
                repair_ok, repair_err = asyncio.run(repair_t8_section_plan_outputs(workspace))
                if repair_ok:
                    output_ok, output_errors = validate_task_artifacts(
                        workspace,
                        task_id,
                        declared_outputs=declared_outputs,
                    )
                else:
                    output_errors = f"{output_errors}; T8-SECTION-PLAN deterministic repair failed: {repair_err}"
        checks["outputs"] = {
            "ok": output_ok,
            "errors": output_errors,
            "semantics": "declared task outputs and stage-specific artifact validators",
        }
        # ``T4.5`` is the name researchers use for the whole decision-to-plan
        # phase, while it remains only the novelty-audit node inside the
        # state machine.  Include an explicit composite package check here so
        # a green audit cannot be mistaken for a green hypothesis/Proposal
        # package or T5 authorization.  Granular node validation remains in
        # `checks.outputs`; the additional key names the later formalization
        # boundary rather than silently changing the node's own semantics.
        if task_id == "T4.5":
            checks["t45_research_package"] = _t45_public_package_check(workspace)

    ok = all(item.get("ok") is True for item in checks.values())
    errors = "; ".join(
        f"{name}: {item.get('errors')}"
        for name, item in checks.items()
        if item.get("errors")
    ) or None
    print(
        yaml.safe_dump(
            {
                "ok": ok,
                "task": task_id,
                "scope": scope,
                "errors": errors,
                "checks": checks,
                **(
                    {
                        "note": (
                            "T4.5 output validation reports both the novelty-audit node and the public full research-package state. "
                            "A passing audit alone does not authorize T5."
                        )
                    }
                    if task_id == "T4.5" and scope in {"outputs", "all"}
                    else {}
                ),
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return 0 if ok else 1


def audit_survey_command(args: argparse.Namespace) -> int:
    """Re-run the deterministic T3.6 audit without starting an LLM Agent.

    This is intentionally distinct from ``validate --task T3.6-ASSEMBLE``:
    validation checks the stored artifact, while this command regenerates the
    audit after a concrete TeX/BibTeX/state repair and reports exact failures.
    """

    workspace = Path(args.workspace).resolve()
    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace,
        allowed_read_prefixes=["drafts/survey/", "literature/", "user_seeds/", "ideation/"],
        allowed_write_prefixes=["drafts/survey/"],
    )
    result = asyncio.run(AuditSurveyCoverageTool(policy).execute())
    audit = result.data if isinstance(result.data, dict) else {}
    failures = [
        {"name": item.get("name"), "detail": item.get("detail")}
        for item in audit.get("checks", [])
        if isinstance(item, dict) and item.get("level") == "FAIL" and item.get("passed") is False
    ]
    print(
        yaml.safe_dump(
            {
                "ok": result.ok,
                "workspace": str(workspace),
                "audit_path": "drafts/survey/survey_audit.json",
                "failure_count": len(failures),
                "failures": failures,
                "message": result.content,
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return 0 if result.ok else 1


def validate_config_command(args: argparse.Namespace) -> int:
    """校验 workflow/gate/runtime 配置的一致性。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    config_dir = Path("config").resolve()
    state_machine = StateMachine(
        Path(args.state_machine).resolve(),
        Path(args.gates).resolve() if args.gates else None,
    )
    errors = state_machine.validate_definition()
    payload = {
        "ok": not errors,
        "state_machine": str(Path(args.state_machine).resolve()),
        "gates": str(Path(args.gates).resolve()) if args.gates else None,
        "contract_source": str(task_io_contract_source()),
        "runtime": {
            "config_path": str(resolve_runtime_config_path(system_config_path("runtime.yaml")).resolve()),
            "workspace_default_root": runtime_settings.workspace.default_root,
            "runtime_dir": runtime_settings.workspace.runtime_dir,
            "log_level": runtime_settings.logging.level,
            "log_json": runtime_settings.logging.json,
            "human_backend": runtime_settings.human_interface.backend,
            "enable_trace": runtime_settings.debug.enable_trace,
            "no_banner": runtime_settings.ui.no_banner,
            "web_fetch_allowed_schemes": list(runtime_settings.web_fetch.allowed_schemes),
            "web_fetch_allowed_hosts": list(runtime_settings.web_fetch.allowed_hosts),
        },
        "parameter_audit": build_config_audit_summary(config_dir),
        "errors": errors,
    }
    if runtime_settings.ui.quiet:
        print(
            yaml.safe_dump(
                {
                    "ok": payload["ok"],
                    "state_machine": payload["state_machine"],
                    "contract_source": payload["contract_source"],
                    "errors": errors,
                },
                allow_unicode=True,
                sort_keys=False,
            )
        )
        return 0 if not errors else 1
    print(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    return 0 if not errors else 1


async def specialize_executor_skills_command(args: argparse.Namespace) -> int:
    """Compile or validate the project-specific external executor skill suite."""

    from .skills.project_specialization import specialize_project_skills
    from .skills.project_specialization.task_adapter import write_deterministic_project_skill_specialization_execution

    workspace = Path(args.workspace).resolve()
    dry_run = bool(getattr(args, "dry_run", False))
    validate_only = bool(getattr(args, "validate_only", False))
    deterministic = bool(getattr(args, "deterministic", False))
    if dry_run or deterministic:
        result = specialize_project_skills(
            workspace=workspace,
            dry_run=dry_run,
            validate_only=False,
        )
    elif validate_only:
        result = specialize_project_skills(
            workspace=workspace,
            validate_only=True,
        )
    else:
        print(
            "The LLM-backed project Skill specialization path is now the ResearchOS task:\n"
            "python -m researchos.cli run-task T5-SPECIALIZE-EXECUTOR-SKILLS "
            f"--workspace {workspace}\n\n"
            "Use --deterministic, --dry-run, or --validate-only here only for offline repair "
            "or deterministic validation."
        )
        return 2
    report = result.report or {}
    if deterministic and not dry_run and result.status != "failed":
        write_deterministic_project_skill_specialization_execution(workspace=workspace)
    print(f"Project Skill Specialization: {result.status}")
    method = report.get("specialization_method") or ("dry_run" if dry_run else "deterministic_validation")
    print(f"Method: {method}")
    llm_info = report.get("llm_specialization") if isinstance(report.get("llm_specialization"), dict) else {}
    if llm_info.get("enabled") is True:
        print(
            "LLM: "
            f"{llm_info.get('model', 'n/a')} via {llm_info.get('endpoint', 'n/a')} "
            f"({llm_info.get('skills_specialized', 0)} skills)"
        )
    print("Context: external_executor/project_skill_context.yaml")
    print(f"Skills: {report.get('skills_specialized', 0)}/{report.get('skills_total', 13)}")
    print(f"Required uncertain fields: {len(report.get('required_uncertain_fields') or [])}")
    print("Report: external_executor/report/skill_specialization_report.json")
    if result.status == "failed" and result.errors:
        first = result.errors[0]
        print(f"First error: {first.get('code', 'error')} - {first.get('message', '')}")
    return 1 if result.status == "failed" else 0


def status_command(args: argparse.Namespace) -> int:
    """Render a compact workspace status; raw state is opt-in for debugging."""

    workspace = Path(args.workspace).resolve()
    state_path = workspace / "state.yaml"
    if not state_path.exists():
        _render_workspace_entry_panel(
            args,
            title="Workspace 已准备",
            message="尚未启动 pipeline，因此没有可显示的执行状态。运行新项目后，status 会显示当前步骤和下一步。",
            workspace=workspace,
            border_style="cyan",
        )
        return 0
    if not state_path.is_file():
        _render_workspace_entry_panel(
            args,
            title="项目状态无效",
            message="state.yaml 不是普通文件，无法安全读取项目状态。",
            workspace=workspace,
            border_style="bright_red",
        )
        return 2
    try:
        state = StateYaml.load_yaml(state_path)
    except Exception as exc:
        _render_workspace_entry_panel(
            args,
            title="项目状态无法读取",
            message=f"state.yaml 无法解析：{exc}",
            workspace=workspace,
            border_style="bright_red",
        )
        return 2
    if bool(getattr(args, "detail", False)):
        print(yaml.safe_dump(model_dump(state, mode="json"), allow_unicode=True, sort_keys=False))
        return 0

    rows = [
        ("项目", state.project_id or workspace.name),
        ("当前步骤", state.current_task or "未开始"),
        ("状态", state.status or "未知"),
    ]
    gate = state.pending_gate
    if gate is not None:
        presentation = gate.presentation if isinstance(gate.presentation, dict) else {}
        title = str(presentation.get("_title") or gate.gate_id or "需要确认")
        description = " ".join(str(presentation.get("_description") or "").split())
        rows.append(("等待确认", title))
        if description:
            rows.append(("说明", description[:220]))
    if state.last_error:
        rows.append(("最近提示", public_error_summary(state.last_error)))

    if state.status in {"PAUSED", "WAITING_HUMAN"}:
        next_step = f"python -m researchos.cli resume --workspace {workspace}"
    elif state.status == "COMPLETED":
        next_step = "项目已完成。可用 workspace-status 查看其他项目。"
    else:
        next_step = f"python -m researchos.cli run --workspace {workspace}"
    rows.append(("下一步", next_step))

    table = Table(title="ResearchOS · 当前项目", box=box.SIMPLE_HEAVY, show_header=False, expand=False)
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(max_width=100, overflow="fold")
    for label, value in rows:
        table.add_row(label, value)
    console = Console(no_color=bool(getattr(args, "_effective_no_color", getattr(args, "no_color", False))))
    console.print(table)
    print("提示：使用 `status --detail` 查看原始 state.yaml。")
    return 0


def _researchos_workspace_processes() -> dict[Path, list[dict[str, str]]]:
    """Best-effort map of local ResearchOS CLI processes to workspace paths.

    This is deliberately advisory: state.yaml and event files remain the
    durable truth. Process inspection is only used to make the workspace
    overview actionable when the command runs on the same Linux host.
    """

    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return {}
    matches: dict[Path, list[dict[str, str]]] = {}
    for proc_dir in proc_root.iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            argv = (proc_dir / "cmdline").read_bytes().decode("utf-8", errors="replace").split("\0")
            cwd = Path(os.readlink(proc_dir / "cwd"))
            stat_tail = (proc_dir / "stat").read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()
            process_state = stat_tail[0] if stat_tail else "?"
        except OSError:
            continue
        if "researchos.cli" not in argv:
            continue
        workspace_value = ""
        for index, value in enumerate(argv):
            if value == "--workspace" and index + 1 < len(argv):
                workspace_value = argv[index + 1]
                break
            if value.startswith("--workspace="):
                workspace_value = value.split("=", 1)[1]
                break
        if not workspace_value:
            continue
        workspace = Path(workspace_value)
        if not workspace.is_absolute():
            workspace = cwd / workspace
        try:
            workspace = workspace.resolve()
        except OSError:
            continue
        command = next((value for value in argv if value in {"run", "resume", "run-task", "run-skill"}), "researchos")
        matches.setdefault(workspace, []).append(
            {"pid": proc_dir.name, "command": command, "state": process_state}
        )
    return matches


def _recent_workspace_event(workspace: Path) -> tuple[float | None, str | None]:
    events_dir = workspace / "_runtime" / "events"
    try:
        newest = max(events_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime)
        return newest.stat().st_mtime, newest.name
    except (OSError, ValueError):
        return None, None


def _age_label(timestamp: float | None, *, now: float) -> str:
    if timestamp is None:
        return "无事件"
    seconds = max(0, int(now - timestamp))
    if seconds < 60:
        return f"{seconds}s 前"
    if seconds < 3600:
        return f"{seconds // 60}m 前"
    if seconds < 86400:
        return f"{seconds // 3600}h 前"
    return f"{seconds // 86400}d 前"


def _collect_workspace_status(workspace_root: Path) -> list[dict[str, str]]:
    """Collect durable state plus advisory local activity for every workspace."""

    root = Path(workspace_root).resolve()
    now = datetime.now(timezone.utc).timestamp()
    process_map = _researchos_workspace_processes()
    rows: list[dict[str, str]] = []
    if not root.is_dir():
        return rows
    workspaces = {item.resolve() for item in root.iterdir() if item.is_dir()}
    for process_workspace in process_map:
        try:
            relative = process_workspace.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) == 1:
            workspaces.add(process_workspace)
    for workspace in sorted(workspaces, key=lambda item: item.name.casefold()):
        state_path = workspace / "state.yaml"
        processes = process_map.get(workspace.resolve(), [])
        event_time, event_name = _recent_workspace_event(workspace)
        if not state_path.is_file():
            if processes:
                process_text = ", ".join(
                    f"{item['command']}#{item['pid']}[{item.get('state', '?')}]" for item in processes[:3]
                )
                active_count = sum(1 for item in processes if item.get("state") not in {"T", "t", "Z", "X"})
                rows.append(
                    {
                        "workspace": workspace.name,
                        "project_id": "-",
                        "task": "-",
                        "state": "NO_STATE",
                        "activity": f"孤儿进程：执行中 {active_count}，停止/挂起 {len(processes) - active_count}",
                        "last_event": _age_label(event_time, now=now),
                        "gate": "-",
                        "detail": (
                            "不要直接 resume；先核查该目录是否被移动、清理或指向错误 workspace。"
                            f" 进程：{process_text}"
                        ),
                    }
                )
            continue
        try:
            state = StateYaml.load_yaml(state_path)
        except Exception as exc:
            rows.append(
                {
                    "workspace": workspace.name,
                    "project_id": "-",
                    "task": "-",
                    "state": "INVALID",
                    "activity": "state.yaml 无法解析",
                    "last_event": _age_label(event_time, now=now),
                    "gate": "-",
                    "detail": str(exc).replace("\n", " ")[:160],
                }
            )
            continue
        if processes:
            process_text = ", ".join(
                f"{item['command']}#{item['pid']}[{item.get('state', '?')}]" for item in processes[:3]
            )
            active_processes = [item for item in processes if item.get("state") not in {"T", "t", "Z", "X"}]
            activity = f"本机执行：{process_text}" if active_processes else f"本机进程已停止/挂起（{len(processes)}）"
            process_detail = f"进程：{process_text}"
        elif state.status == "RUNNING" and event_time is not None and now - event_time <= 120:
            activity = "最近有事件；未发现可匹配本机进程"
            process_detail = ""
        elif state.status == "RUNNING":
            activity = "可能已失联：RUNNING 但无本机进程/近期事件"
            process_detail = ""
        elif state.status == "WAITING_HUMAN":
            activity = "等待人工 Gate 输入"
            process_detail = ""
        elif state.status == "PAUSED":
            activity = "已暂停，可用 resume 继续"
            process_detail = ""
        elif state.status == "COMPLETED":
            activity = "已完成"
            process_detail = ""
        else:
            activity = "失败，查看 last_error / trace"
            process_detail = ""
        gate = state.pending_gate.gate_id if state.pending_gate else "-"
        last_history = state.history[-1] if state.history else None
        detail = ""
        if state.last_error:
            detail = public_error_summary(state.last_error)
        elif last_history and last_history.error:
            detail = " ".join(last_history.error.split())[:160]
        elif process_detail:
            detail = process_detail
        elif event_name:
            detail = f"事件：{event_name}"
        rows.append(
            {
                "workspace": workspace.name,
                "project_id": state.project_id,
                "task": state.current_task,
                "state": state.status,
                "activity": activity,
                "last_event": _age_label(event_time, now=now),
                "gate": gate,
                "detail": detail or "-",
            }
        )
    return rows


def workspace_status_command(args: argparse.Namespace) -> int:
    """Render a concise multi-workspace operational overview."""

    workspace_root = Path(args.workspace_root).resolve()
    rows = _collect_workspace_status(workspace_root)
    if not rows:
        print(f"未在 {workspace_root} 下找到含 state.yaml 的 workspace。")
        return 0
    no_color = bool(getattr(args, "_effective_no_color", getattr(args, "no_color", False)))
    terminal_width = max(80, min(160, shutil.get_terminal_size(fallback=(120, 40)).columns))
    compact = terminal_width < 118
    table = lightweight_ruled_table(
        title=f"ResearchOS · Workspace Status ({workspace_root})",
        header_style="bold cyan",
        expand=False,
    )
    show_detail = bool(getattr(args, "verbose", False)) and not compact
    table.add_column("Workspace", max_width=20 if compact else 24, no_wrap=True, overflow="ellipsis")
    table.add_column("Task", max_width=14 if compact else 17, no_wrap=True, overflow="ellipsis")
    table.add_column("State", max_width=12 if compact else 14, no_wrap=True)
    table.add_column("Activity", max_width=24 if compact else 32, overflow="fold")
    if not compact:
        table.add_column("Last event", max_width=11, no_wrap=True)
        table.add_column("Gate", max_width=18, overflow="fold")
    if show_detail:
        table.add_column("Detail", max_width=42, overflow="fold")
    state_styles = {
        "RUNNING": "bright_green",
        "WAITING_HUMAN": "yellow",
        "PAUSED": "yellow",
        "COMPLETED": "green",
        "FAILED": "bright_red",
        "INVALID": "bright_red",
        "NO_STATE": "bright_red",
    }
    for row in rows:
        values = [
            row["workspace"],
            row["task"],
            f"[{state_styles.get(row['state'], 'white')}]{row['state']}[/]",
            row["activity"],
        ]
        if not compact:
            values.extend([row["last_event"], row["gate"]])
        if show_detail:
            values.append(row["detail"])
        table.add_row(*values)
    Console(
        force_terminal=not no_color,
        color_system=None if no_color else "truecolor",
        no_color=no_color,
        width=terminal_width,
    ).print(table)
    print(
        "说明：进程匹配仅覆盖本机且命令行明确带 --workspace 的 ResearchOS 进程；"
        "state.yaml 与 _runtime/events 才是可恢复事实源。使用 --verbose 显示最后错误/事件文件。"
    )
    return 0


def trace_command(args: argparse.Namespace) -> int:
    """打印指定 run_id 对应的 trace 文件。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    trace_path = (runtime_settings.traces_dir(Path(args.workspace)) / f"{args.run_id}.jsonl").resolve()
    if not trace_path.exists():
        print(f"Trace not found: {trace_path}")
        return 1
    if args.raw:
        print(trace_path.read_text(encoding="utf-8"))
    else:
        print(render_trace_for_humans(trace_path))
    return 0


def list_skills_command(args: argparse.Namespace) -> int:
    """List every discoverable standalone skill and its guided interaction mode."""
    workspace_dir = Path(args.workspace).resolve()
    skills_roots = _resolve_skill_roots(args, workspace_dir)

    all_skills = []
    try:
        discovered = discover_skills_from_roots(skills_roots)
    except Exception as e:
        print(f"Failed to discover skills: {e}", file=sys.stderr)
        return 1

    standalone_skills = [skill for skill in discovered.values() if is_standalone_skill(skill)]
    pipeline_skills = [skill for skill in discovered.values() if not is_standalone_skill(skill)]
    executor_templates: list[Any] = []
    if bool(getattr(args, "include_managed", False)):
        executor_root = Path(__file__).resolve().parents[1] / "skills" / "external_executor_skills"
        try:
            executor_templates = list(discover_skills_from_roots([executor_root]).values())
        except Exception as exc:
            print(f"无法读取外部执行器 Skill 模板: {exc}", file=sys.stderr)
            return 1
    for skill in ordered_skills(standalone_skills):
        interaction = parse_skill_interaction(skill.metadata)
        skill_info = {
            "name": skill.name,
            "description": skill.description,
            "path": str(skill.skill_dir),
            "tools": skill.allowed_tools,
            "capability_profiles": list(skill.capability_profiles),
            "execution_scope": skill.execution_scope,
            "llm_connection": "global",
            "max_steps": skill.metadata.get("max_steps"),
            "max_tokens_total": skill.metadata.get("max_tokens_total"),
            "interaction": {
                "mode": interaction.mode,
                "language": interaction.language,
                "request_required": interaction.request_required,
                "required_inputs": [
                    {
                        "id": requirement.key,
                        "label": requirement.label,
                        "paths": list(requirement.paths),
                    }
                    for requirement in interaction.required_inputs
                ],
                "optional_inputs": [
                    {
                        "id": requirement.key,
                        "label": requirement.label,
                        "paths": list(requirement.paths),
                    }
                    for requirement in interaction.optional_inputs
                ],
                "outputs": [
                    {
                        "id": output.key,
                        "label": output.label,
                        "path": output.path,
                    }
                    for output in interaction.outputs
                ],
            }
            if interaction
            else {"mode": "legacy"},
        }
        all_skills.append(skill_info)

    # 输出结果
    if not all_skills:
        print("No skills found.")
        return 0

    if args.verbose:
        payload: dict[str, Any] = {"catalog": catalog_entries(standalone_skills), "skills": all_skills}
        if bool(getattr(args, "include_managed", False)):
            payload["managed_modules"] = [
                {
                    "name": skill.name,
                    "execution_scope": skill.execution_scope,
                    "execution_owner": skill.execution_owner,
                    "route": _managed_skill_route_text(skill, workspace_dir),
                }
                for skill in sorted(pipeline_skills + executor_templates, key=lambda item: item.name)
            ]
        print(yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
        ))
    else:
        print(_render_skill_catalog_for_cli(args, skills=standalone_skills, workspace=workspace_dir))
        if bool(getattr(args, "include_managed", False)):
            _print_managed_skill_catalog_for_cli(
                args,
                workspace=workspace_dir,
                pipeline_skills=pipeline_skills,
                executor_templates=executor_templates,
            )

    return 0


def audit_skills_command(args: argparse.Namespace) -> int:
    """Audit all repository-owned Skill contracts without starting an LLM."""

    repo_root = Path(__file__).resolve().parents[1]
    report = audit_skill_suite(
        repo_root,
        check_script_help=bool(getattr(args, "check_script_help", False)),
        check_interactions=(
            bool(getattr(args, "check_interactions", False))
            or bool(getattr(args, "check_user_journeys", False))
        ),
        check_user_journeys=bool(getattr(args, "check_user_journeys", False)),
    )
    if bool(getattr(args, "json", False)):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_skill_suite_audit(report))
    return 0 if report.get("status") == "pass" else 1


def browse_skills_command(args: argparse.Namespace) -> int:
    """Interactive terminal browser for guided standalone Skills.

    The browser deliberately remains line-based and copyable: it works through
    SSH, tmux, redirected logs, and ordinary terminals without relying on
    terminal-private control sequences.
    """

    workspace_dir = Path(args.workspace).resolve()
    try:
        discovered = discover_skills_from_roots(_resolve_skill_roots(args, workspace_dir))
    except Exception as exc:
        print(f"Failed to discover skills: {exc}", file=sys.stderr)
        return 1
    skills = ordered_skills(skill for skill in discovered.values() if is_standalone_skill(skill))
    if not skills:
        print("没有找到可浏览的 Skill。")
        return 0
    print(_render_skill_catalog_for_cli(args, skills=skills, workspace=workspace_dir))
    by_index = {index: skill for index, skill in enumerate(skills, start=1)}
    index_by_name = {skill.name: index for index, skill in by_index.items()}
    print(_skill_browser_help())
    while True:
        try:
            command = input("Skill> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出 Skill 浏览。")
            return 0
        if not command or command.lower() in {"q", "quit", "exit"}:
            return 0
        lowered = command.casefold()
        if lowered in {"help", "h", "?", "帮助"}:
            print(_skill_browser_help())
            continue
        if lowered in {"all", "list", "全部"}:
            print(
                _render_skill_catalog_for_cli(
                    args,
                    skills=skills,
                    workspace=workspace_dir,
                    index_by_name=index_by_name,
                )
            )
            continue
        category_prefixes = ("category ", "分类 ")
        category_prefix = next((prefix for prefix in category_prefixes if lowered.startswith(prefix)), None)
        if category_prefix:
            category = command[len(category_prefix):].strip()
            matches = skills_in_category(skills, category)
            if not matches:
                print(f"未找到分类“{category}”。可输入 `all` 查看分类，或使用 `search <关键词>`。")
                continue
            print(
                _render_skill_catalog_for_cli(
                    args,
                    skills=matches,
                    workspace=workspace_dir,
                    index_by_name=index_by_name,
                    heading="ResearchOS · Skill 分类筛选",
                    notice=f"筛选：分类“{category}” · {len(matches)}/{len(skills)} 个 Skill；序号保持全目录编号。",
                )
            )
            continue
        search_prefixes = ("search ", "搜索 ")
        search_prefix = next((prefix for prefix in search_prefixes if lowered.startswith(prefix)), None)
        if search_prefix:
            query = command[len(search_prefix):].strip()
            ranked_matches = search_skill_matches(skills, query)
            matches = [skill for skill, _reason in ranked_matches]
            if not matches:
                print(f"没有匹配“{query}”的 Skill。可尝试 `search 文献`、`search 写作`、`search citation`。")
                continue
            print(
                _render_skill_catalog_for_cli(
                    args,
                    skills=matches,
                    workspace=workspace_dir,
                    index_by_name=index_by_name,
                    heading="ResearchOS · Skill 搜索结果",
                    notice=_skill_search_notice(query, ranked_matches, total=len(skills)),
                )
            )
            continue
        run_requested = command.lower().startswith("run ")
        target = command[4:].strip() if run_requested else command
        skill = None
        if target.isdigit():
            skill = by_index.get(int(target))
        else:
            skill = next((item for item in skills if item.name == target), None)
        if skill is None:
            ranked_matches = search_skill_matches(skills, target)
            matches = [item for item, _reason in ranked_matches]
            if matches:
                action = "请选择精确序号后启动" if run_requested else "可输入序号查看详情，或使用 `run <序号>` 启动"
                print(
                    _render_skill_catalog_for_cli(
                        args,
                        skills=matches,
                        workspace=workspace_dir,
                        index_by_name=index_by_name,
                        heading="ResearchOS · Skill 搜索结果",
                        notice=_skill_search_notice(target, ranked_matches, total=len(skills)) + f"；{action}。",
                    )
                )
            else:
                print("未找到该 Skill。可直接输入关键词进行本地模糊搜索，例如 `文献`、`Idea`、`论文写作`。")
            continue
        if not run_requested:
            print(
                _render_skill_description_for_cli(
                    args,
                    skill_name=skill.name,
                    skill_path=skill.skill_dir,
                    description=skill.description,
                    interaction=parse_skill_interaction(skill.metadata),
                    workflow=parse_skill_workflow(skill.metadata),
                    capability_profiles=skill.capability_profiles,
                    tools=skill.allowed_tools,
                    execution_scope=skill.execution_scope,
                    execution_owner=skill.execution_owner,
                )
            )
            print(f"启动：run {next(index for index, item in by_index.items() if item.name == skill.name)}")
            continue
        args.command = "run-skill"
        args.skill_name = skill.name
        args.request = []
        args.profile = None
        args.session_id = None
        args.resume = False
        args.interactive = True
        args.startup_selftest = False
        args.skip_startup_selftest = False
        return _run_async_cli_command(args, run_skill_command(args))


def _skill_browser_help() -> str:
    return (
        "输入序号或 Skill 名称查看详情；`run <序号或名称>` 启动引导式会话。\n"
        "可直接输入关键词进行中英文模糊搜索，也可用 `search <关键词>` / `搜索 <关键词>`；`category <分类>` / `分类 <分类>`，`all` 返回全目录。\n"
        "示例：`文献`、`Idea`、`search citation`、`分类 论文写作`、`run 10`；输入 `help` 查看本提示，`q` 退出。"
    )


def _skill_search_notice(query: str, ranked_matches: list[tuple[Any, str]], *, total: int) -> str:
    preview = "；".join(f"{skill.name}: {reason}" for skill, reason in ranked_matches[:3])
    suffix = f"匹配依据：{preview}" if preview else "本地索引未返回可解释匹配依据"
    return f"筛选：关键词“{query}” · {len(ranked_matches)}/{total} 个 Skill；序号保持全目录编号。{suffix}"


def describe_skill_command(args: argparse.Namespace) -> int:
    """Render a full, deterministic input/output contract for one skill."""

    workspace_dir = Path(args.workspace).resolve()
    try:
        skill = resolve_skill(args.skill_name, _resolve_skill_roots(args, workspace_dir))
        print(
            _render_skill_description_for_cli(
                args,
                skill_name=skill.name,
                skill_path=skill.skill_dir,
                description=skill.description,
                interaction=parse_skill_interaction(skill.metadata),
                workflow=parse_skill_workflow(skill.metadata),
                capability_profiles=skill.capability_profiles,
                tools=skill.allowed_tools,
                execution_scope=skill.execution_scope,
                execution_owner=skill.execution_owner,
                managed_route=(
                    _managed_skill_route_text(skill, workspace_dir)
                    if not is_standalone_skill(skill)
                    else ""
                ),
            )
        )
    except Exception as exc:
        print(f"无法读取 Skill 描述: {exc}", file=sys.stderr)
        return 2
    return 0


def skill_status_command(args: argparse.Namespace) -> int:
    """Show persistent guided-skill sessions without contacting an LLM."""

    workspace_dir = Path(args.workspace).resolve()
    entries = list(iter_sessions(workspace_dir))
    if args.skill_name:
        entries = [entry for entry in entries if entry[1].get("skill_name") == args.skill_name]
    if not entries:
        print("没有找到 Skill 会话。")
        return 0
    print(_render_skill_status_for_cli(args, workspace=workspace_dir, entries=entries))
    return 0


def _add_shared_cli_options(
    parser: argparse.ArgumentParser,
    runtime_settings: RuntimeSettings,
    *,
    use_defaults: bool,
) -> None:
    """给主 parser 或子命令 parser 注入共享参数。

    这么做是为了同时支持两种用户习惯：
    - `researchos --workspace ./ws run-task HELLO`
    - `researchos run-task --workspace ./ws HELLO`

    纯 `argparse` 默认只接受前一种；把共享选项也挂到子命令上后，
    后一种写法也能工作，CLI 体验更接近日常命令行工具。
    """

    default = argparse.SUPPRESS if not use_defaults else None
    parser.add_argument(
        "--workspace",
        default=runtime_settings.workspace.default_root if use_defaults else default,
    )
    parser.add_argument(
        "--project-id",
        default=None if use_defaults else default,
    )
    parser.add_argument(
        "--state-machine",
        default=str(system_config_path("state_machine.yaml")) if use_defaults else default,
    )
    parser.add_argument(
        "--gates",
        default=str(system_config_path("gates.yaml")) if use_defaults else default,
    )
    parser.add_argument(
        "--model-settings",
        default="config/model_settings.yaml" if use_defaults else default,
        help="LLM provider, URL, key, model, and retry settings",
    )
    parser.add_argument(
        "--mcp-config",
        default="config/mcp.yaml" if use_defaults else default,
    )
    parser.add_argument(
        "--mcp-connector",
        default=None if use_defaults else default,
        help="可选：MCP 连接函数，格式为 package.module:attr 或 package.module.attr",
    )
    parser.add_argument("--skills-root", action="append", default=None if use_defaults else default)
    parser.add_argument(
        "--log-level",
        default=runtime_settings.logging.level if use_defaults else default,
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        default=False if use_defaults else default,
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False if use_defaults else default,
        help="只显示关键状态、暂停、错误和最终结果；完整时间线写入 _runtime/logs/researchos.log",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False if use_defaults else default,
        help="显示更多工具摘要；仍不显示完整 prompt/response",
    )
    parser.add_argument(
        "--verbosity",
        choices=["concise", "normal", "detailed"],
        default=runtime_settings.ui.verbosity if use_defaults else default,
        help="科研过程展示密度；默认 normal。",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False if use_defaults else default,
        help="禁用终端颜色和 ANSI 控制字符。",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        default=False if use_defaults else default,
        help="除持久化 JSONL 外，同时向 stdout 输出每条结构化科研过程事件；不建议与交互 Gate 混用。",
    )


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""

    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    parser = argparse.ArgumentParser(prog="researchos")
    _add_shared_cli_options(parser, runtime_settings, use_defaults=True)

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-workspace", help="初始化标准 workspace")
    _add_shared_cli_options(init_parser, runtime_settings, use_defaults=False)
    init_parser.add_argument("--topic", default="")
    init_parser.add_argument("--no-project-file", action="store_true")
    init_parser.add_argument("--force-project-file", action="store_true")
    init_parser.add_argument("--workflow-mode", choices=["auto", "copilot"], default=None)
    init_parser.add_argument("--auto-preset", choices=sorted(AUTO_PRESETS), default=None)
    init_parser.add_argument("--auto-t4-mode", choices=["standard", "quick", "deep", "auto"], default=None)

    run_parser = subparsers.add_parser("run", help="运行完整 pipeline")
    _add_shared_cli_options(run_parser, runtime_settings, use_defaults=False)
    run_parser.add_argument(
        "--from",
        dest="from_workspace",
        default=None,
        help="从另一个 workspace 复制 --start-task 的前置 artifact；未指定 --start-task 时默认从 T2 开始",
    )
    run_parser.add_argument(
        "--start-task",
        "--from-task",
        dest="start_task",
        default=None,
        help="从指定状态机节点开始完整 pipeline；--from-task 是面向 run --from 的兼容别名，例如 T2、T3、T4.5、T8-STYLE-GATE",
    )
    run_parser.add_argument("--startup-selftest", action="store_true")
    run_parser.add_argument("--skip-startup-selftest", action="store_true")
    run_parser.add_argument("--workflow-mode", choices=["auto", "copilot"], default=None)
    run_parser.add_argument("--auto-preset", choices=sorted(AUTO_PRESETS), default=None)
    run_parser.add_argument("--auto-t4-mode", choices=["standard", "quick", "deep", "auto"], default=None)

    smoke_parser = subparsers.add_parser("run_smoke", help="运行真实 pipeline 快速联调模式")
    _add_shared_cli_options(smoke_parser, runtime_settings, use_defaults=False)
    smoke_parser.add_argument(
        "--from",
        dest="from_workspace",
        default=None,
        help="从另一个 workspace 复制 --start-task 的前置 artifact；未指定 --start-task 时默认从 T2 开始",
    )
    smoke_parser.add_argument(
        "--start-task",
        default="T2",
        help="smoke 起始状态机节点，默认 T2；也可用 T3/T4/T8-STYLE-GATE 等真实节点",
    )
    smoke_parser.add_argument("--active-pool-max", type=int, default=20)
    smoke_parser.add_argument("--deep-read-target", type=int, default=3)
    smoke_parser.add_argument("--abstract-sweep", type=int, default=5)
    smoke_parser.add_argument("--tier", default="standard", help=argparse.SUPPRESS)
    smoke_parser.add_argument(
        "--profile",
        default=None,
        help=argparse.SUPPRESS,
    )
    smoke_parser.add_argument(
        "--manuscript-language",
        default="auto",
        choices=["auto", "en", "zh", "mixed"],
    )
    smoke_parser.add_argument(
        "--include-chinese-literature",
        default="auto",
        choices=["auto", "true", "false"],
    )
    smoke_parser.add_argument(
        "--force-smoke-params",
        action="store_true",
        help="覆盖已有 literature/literature_params.json 和确认文件",
    )
    smoke_parser.add_argument("--startup-selftest", action="store_true")
    smoke_parser.add_argument("--skip-startup-selftest", action="store_true")

    resume_parser = subparsers.add_parser("resume", help="恢复已暂停的 pipeline")
    _add_shared_cli_options(resume_parser, runtime_settings, use_defaults=False)
    resume_parser.add_argument(
        "--from-task",
        default=None,
        help="在当前 workspace 中受校验地从指定任务重入，例如 T4；T2 会先确认/重选阅读参数，T3 会先复查检索覆盖",
    )
    resume_parser.add_argument(
        "--from",
        dest="from_workspace",
        default=None,
        help="恢复前从另一个 workspace 合并当前或 --from-task 的缺失声明输入；不合并 state/history，也不修改来源 workspace",
    )
    resume_parser.add_argument("--startup-selftest", action="store_true")
    resume_parser.add_argument("--skip-startup-selftest", action="store_true")
    resume_parser.add_argument("--workflow-mode", choices=["auto", "copilot"], default=None)
    resume_parser.add_argument("--auto-preset", choices=sorted(AUTO_PRESETS), default=None)
    resume_parser.add_argument("--auto-t4-mode", choices=["standard", "quick", "deep", "auto"], default=None)

    run_t8_parser = subparsers.add_parser(
        "run-t8",
        help="接收现代 T5 外部执行 handoff，并在同一 workspace 中直接运行完整 T8",
    )
    _add_shared_cli_options(run_t8_parser, runtime_settings, use_defaults=False)
    run_t8_parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验并生成 T8 结构化输入，不改变 state.yaml 或启动 Writer",
    )
    run_t8_parser.add_argument(
        "--require-ready",
        action="store_true",
        help="只接受 writer_handoff_validation.status=ready；默认也接受带明确约束的 partial",
    )
    run_t8_parser.add_argument("--startup-selftest", action="store_true")
    run_t8_parser.add_argument("--skip-startup-selftest", action="store_true")

    run_task_parser = subparsers.add_parser("run-task", help="只运行一个 task")
    _add_shared_cli_options(run_task_parser, runtime_settings, use_defaults=False)
    run_task_parser.add_argument("task_id")
    run_task_parser.add_argument(
        "--from",
        dest="from_workspace",
        default=None,
        help="从另一个 workspace 复制当前 task 的前置 artifact",
    )
    run_task_parser.add_argument(
        "--profile",
        default=None,
        help=argparse.SUPPRESS,
    )
    run_task_parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help="允许显式运行 LEGACY-T5-PILOT / LEGACY-T6-NOVELTY 旧内部实验节点",
    )
    run_task_parser.add_argument("--startup-selftest", action="store_true")
    run_task_parser.add_argument("--skip-startup-selftest", action="store_true")

    status_parser = subparsers.add_parser("status", help="查看当前状态")
    _add_shared_cli_options(status_parser, runtime_settings, use_defaults=False)
    status_parser.add_argument("--detail", action="store_true", help="输出完整原始 state.yaml（用于调试）")

    workspace_status_parser = subparsers.add_parser("workspace-status", help="总览多个 workspace 的状态与本机活动")
    _add_shared_cli_options(workspace_status_parser, runtime_settings, use_defaults=False)
    workspace_status_parser.add_argument(
        "--workspace-root",
        default="./workspace",
        help="包含多个 workspace 子目录的根目录，默认 ./workspace",
    )

    selftest_parser = subparsers.add_parser("selftest", help="检查 LLM endpoint 连通性")
    _add_shared_cli_options(selftest_parser, runtime_settings, use_defaults=False)
    selftest_parser.add_argument("--profile", action="append", help=argparse.SUPPRESS)

    configure_llm_parser = subparsers.add_parser(
        "configure-llm",
        help="配置并测试唯一的 LLM 连接",
    )
    _add_shared_cli_options(configure_llm_parser, runtime_settings, use_defaults=False)
    configure_llm_parser.add_argument(
        "--provider",
        help="Provider preset; run configure-llm interactively to choose from the full list",
    )
    configure_llm_parser.add_argument(
        "--api-base",
        help="API URL override; required only for openai_compatible",
    )
    configure_llm_parser.add_argument("--api-key", help="API key; prefer interactive input to avoid shell history")
    configure_llm_parser.add_argument(
        "--hide-api-key",
        action="store_true",
        help="交互式配置时隐藏 API key 输入；默认明文显示，便于核对粘贴内容",
    )
    configure_llm_parser.add_argument("--model", help="Model name used for every stage")
    configure_llm_parser.add_argument(
        "--context-window",
        help="可信的模型/网关上下文上限，如 128k、256k、1m；auto 恢复自动探测",
    )
    configure_llm_parser.add_argument("--key-storage", choices=("config", "env"), help="Store the key in model settings or .env")
    configure_llm_parser.add_argument("--skip-check", dest="check", action="store_false", help="保存后不做连通性检查")
    configure_llm_parser.set_defaults(check=True)

    workflow_parser = subparsers.add_parser(
        "configure-workflow",
        help="查看或修改后续流程的模式与默认执行设置",
    )
    _add_shared_cli_options(workflow_parser, runtime_settings, use_defaults=False)
    workflow_parser.add_argument("--workflow-mode", choices=["auto", "copilot"], default=None)
    workflow_parser.add_argument("--auto-preset", choices=sorted(AUTO_PRESETS), default=None)
    workflow_parser.add_argument(
        "--literature-preset",
        choices=["standard_research", "survey_balanced", "survey_exhaustive"],
        default=None,
    )
    workflow_parser.add_argument("--auto-t4-mode", choices=["standard", "quick", "deep", "auto"], default=None)
    workflow_parser.add_argument("--proposal-tracks", choices=["one", "top2"], default=None)
    workflow_parser.add_argument(
        "--ccf-template",
        choices=sorted(available_ccf_template_ids(Path(__file__).resolve().parents[1])),
        default=None,
        help="Auto + CCF/CS 的具体会议模板；在 T1、Survey 与 T8 复用",
    )
    workflow_parser.add_argument(
        "--request",
        default=None,
        help="用自然语言描述调整；LLM 解析有限模式/预设/覆盖/T4/Proposal，会议模板按本地可用枚举核验，保存前仍会确认",
    )
    workflow_parser.add_argument("--yes", action="store_true", help="非交互模式下明确确认保存")

    doctor_parser = subparsers.add_parser("doctor", help="检查 Native/Docker 运行环境")
    _add_shared_cli_options(doctor_parser, runtime_settings, use_defaults=False)
    doctor_parser.add_argument(
        "--allow-docker-latex",
        action="store_true",
        help="诊断时临时允许 Docker LaTeX fallback；runtime.yaml 已启用时无需传入",
    )

    trace_parser = subparsers.add_parser("trace", help="查看某次 run 的 trace")
    _add_shared_cli_options(trace_parser, runtime_settings, use_defaults=False)
    trace_parser.add_argument("run_id")
    trace_parser.add_argument("--raw", action="store_true", help="直接输出原始 JSONL")

    validate_parser = subparsers.add_parser("validate", help="校验 task 输入或产物")
    _add_shared_cli_options(validate_parser, runtime_settings, use_defaults=False)
    validate_parser.add_argument("--task")
    validate_parser.add_argument(
        "--scope",
        choices=["outputs", "inputs", "all"],
        default="outputs",
        help=(
            "校验范围。outputs 保持历史行为，只检查已生成产物；inputs 检查前置输入和 prerequisite contract；"
            "all 同时检查二者。"
        ),
    )

    survey_audit_parser = subparsers.add_parser("audit-survey", help="无模型重跑 T3.6 Survey 覆盖审计")
    _add_shared_cli_options(survey_audit_parser, runtime_settings, use_defaults=False)

    validate_config_parser = subparsers.add_parser("validate-config", help="校验状态机与 runtime 配置")
    _add_shared_cli_options(validate_config_parser, runtime_settings, use_defaults=False)

    specialize_parser = subparsers.add_parser(
        "specialize-executor-skills",
        help="生成或校验项目专属 external executor skill suite",
    )
    _add_shared_cli_options(specialize_parser, runtime_settings, use_defaults=False)
    specialize_mode = specialize_parser.add_mutually_exclusive_group()
    specialize_mode.add_argument("--dry-run", action="store_true", help="只构建和校验，不发布产物")
    specialize_mode.add_argument("--validate-only", action="store_true", help="只校验现有专属化产物")
    specialize_mode.add_argument(
        "--deterministic",
        action="store_true",
        help="发布无需 LLM 的 schema-validated 项目 Skill suite；用于修复旧 T5 workspace 或离线环境",
    )
    specialize_parser.add_argument("--profile", help=argparse.SUPPRESS)
    specialize_parser.add_argument("--tier", default="standard", help=argparse.SUPPRESS)

    run_skill_parser = subparsers.add_parser("run-skill", help="启动或恢复一个带输入检查的独立 Skill")
    _add_shared_cli_options(run_skill_parser, runtime_settings, use_defaults=False)
    run_skill_parser.add_argument("skill_name")
    run_skill_parser.add_argument("request", nargs="*")
    run_skill_parser.add_argument("--profile", help=argparse.SUPPRESS)
    run_skill_parser.add_argument(
        "--session-id",
        help="可恢复会话标识；默认使用 Skill 名称。并行处理多个稿件时请显式指定。",
    )
    run_skill_parser.add_argument(
        "--resume",
        action="store_true",
        help="从同一 Skill 会话恢复；若未提供新请求，沿用上次保存的请求。",
    )
    run_skill_parser.add_argument(
        "--interactive",
        action="store_true",
        help="强制启用终端引导式材料收集；交互终端默认已启用。",
    )
    run_skill_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="禁用默认终端互动；缺输入时仅写入可恢复 WAITING_INPUT 会话。",
    )
    run_skill_parser.add_argument(
        "--yes",
        action="store_true",
        help="输入已通过检查后直接执行；仅用于显式授权的自动化或批处理。",
    )
    run_skill_parser.add_argument("--startup-selftest", action="store_true")
    run_skill_parser.add_argument("--skip-startup-selftest", action="store_true")

    list_skills_parser = subparsers.add_parser("list-skills", help="列出可独立启动的 Skill")
    _add_shared_cli_options(list_skills_parser, runtime_settings, use_defaults=False)
    list_skills_parser.add_argument(
        "--include-managed",
        action="store_true",
        help="额外展示由 pipeline 或外部执行器管理、不能通过 run-skill 直接启动的模块。",
    )

    audit_skills_parser = subparsers.add_parser(
        "audit-skills",
        help="无模型审计公共与外部执行器 Skill 契约、资源引用和脚本语法",
    )
    _add_shared_cli_options(audit_skills_parser, runtime_settings, use_defaults=False)
    audit_skills_parser.add_argument(
        "--check-script-help",
        action="store_true",
        help="额外逐个执行外部 Skill 脚本的 --help import smoke。",
    )
    audit_skills_parser.add_argument(
        "--check-interactions",
        action="store_true",
        help="逐个验证独立 Skill 的说明页/空工作区就绪检查，以及受管理模块的安全路由；不调用模型。",
    )
    audit_skills_parser.add_argument(
        "--check-user-journeys",
        action="store_true",
        help=(
            "在隔离临时 workspace 中逐个真实启动独立 Skill 的初始无模型路径，"
            "核验缺任务/缺文件提示、持久会话和恢复命令；包含 --check-interactions。"
        ),
    )
    audit_skills_parser.add_argument(
        "--json",
        action="store_true",
        help="输出完整 JSON 审计记录。",
    )

    browse_skills_parser = subparsers.add_parser("browse-skills", help="以终端卡片浏览、查看并启动 Skill")
    _add_shared_cli_options(browse_skills_parser, runtime_settings, use_defaults=False)

    describe_skill_parser = subparsers.add_parser("describe-skill", help="查看一个 Skill 的上传、输出与恢复契约")
    _add_shared_cli_options(describe_skill_parser, runtime_settings, use_defaults=False)
    describe_skill_parser.add_argument("skill_name")

    skill_status_parser = subparsers.add_parser("skill-status", help="查看 workspace 中可恢复的 Skill 会话")
    _add_shared_cli_options(skill_status_parser, runtime_settings, use_defaults=False)
    skill_status_parser.add_argument("skill_name", nargs="?", help="可选：仅查看指定 Skill")

    return parser


def _run_task_requests_full_t8(args: argparse.Namespace) -> bool:
    """Treat only the public `run-task T8` form as the complete T5-to-T8 bridge."""

    return args.command == "run-task" and str(getattr(args, "task_id", "") or "").strip().upper() == "T8"


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    _emit_environment_warnings()
    runtime_settings = load_runtime_settings(system_config_path("runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    # Show the common entry panel for every actual CLI command. Commands that
    # later prepare a workspace reuse the same helper for a richer summary,
    # but never replay the animation or hide the initial debug surface.
    workspace_arg = getattr(args, "workspace", None)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=Path(workspace_arg).resolve() if workspace_arg else None,
        show_summary=False,
    )
    # Skill listing/status commands do not otherwise receive RuntimeSettings.
    # Preserve the same effective `--no-color` policy used by pipeline runs.
    args._effective_no_color = runtime_settings.ui.no_color
    configure_logging(level=args.log_level, json_logs=runtime_settings.logging.json)
    if args.command == "init-workspace":
        return init_workspace_command(args)
    if args.command == "run":
        args.resume = False
        return _run_async_cli_command(args, run_command(args))
    if args.command == "run_smoke":
        args.resume = False
        return _run_async_cli_command(args, run_smoke_command(args))
    if args.command == "resume":
        args.resume = True
        return _run_async_cli_command(args, run_command(args))
    if args.command == "run-t8":
        return _run_async_cli_command(args, run_t8_command(args))
    if args.command == "run-task":
        if _run_task_requests_full_t8(args):
            return _run_async_cli_command(args, run_t8_command(args))
        return _run_async_cli_command(args, run_task_command(args))
    if args.command == "run-skill":
        return _run_async_cli_command(args, run_skill_command(args))
    if args.command == "list-skills":
        return list_skills_command(args)
    if args.command == "audit-skills":
        return audit_skills_command(args)
    if args.command == "browse-skills":
        return browse_skills_command(args)
    if args.command == "describe-skill":
        return describe_skill_command(args)
    if args.command == "skill-status":
        return skill_status_command(args)
    if args.command == "status":
        return status_command(args)
    if args.command == "workspace-status":
        return workspace_status_command(args)
    if args.command == "doctor":
        return doctor_command(args)
    if args.command == "selftest":
        return _run_async_cli_command(args, selftest_command(args))
    if args.command == "configure-llm":
        return _run_async_cli_command(args, configure_llm_command(args))
    if args.command == "configure-workflow":
        return _run_async_cli_command(args, configure_workflow_command(args))
    if args.command == "trace":
        return trace_command(args)
    if args.command == "validate":
        return validate_command(args)
    if args.command == "audit-survey":
        return audit_survey_command(args)
    if args.command == "validate-config":
        return validate_config_command(args)
    if args.command == "specialize-executor-skills":
        return _run_async_cli_command(args, specialize_executor_skills_command(args))
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
