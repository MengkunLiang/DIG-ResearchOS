from __future__ import annotations

"""ResearchOS 命令行入口。

这里统一封装 runtime 的几个主要使用场景：
- `run` / `resume`：完整 pipeline 模式，走 StateMachine；
- `run-task`：单 task 调试模式，只跑一个 T-stage；
- `run-skill`：独立运行一个 skill；
- `validate` / `status` / `trace` / `selftest`：辅助诊断命令。
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys

import yaml

# 加载 .env 文件（如果存在）
try:
    from dotenv import load_dotenv
    # 尝试从多个位置加载 .env
    for env_path in [
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",
        Path.home() / ".env",
    ]:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break
except ImportError:
    pass  # python-dotenv 未安装，跳过

from .agents.registry import AGENT_REGISTRY
from .cli_runners import CompletePipelineRunner, SingleTaskRunner
from .orchestration.task_io_contract import get_task_io
from .orchestration.state_machine import StateMachine
from .pydantic_compat import model_dump
from .runtime.agent import AgentResult
from .runtime.config_audit import build_config_audit_summary
from .runtime.cli_ui import format_startup_summary, show_startup_banner
from .runtime.config import LatexSettings, RuntimeSettings, UISettings, load_runtime_settings, resolve_runtime_config_path
from .runtime.environment import (
    collect_runtime_environment,
    command_version,
    write_runtime_environment,
)
from .runtime.llm_client import LLMClient
from .runtime.logger import configure_file_logging, configure_logging
from .runtime.system_config import system_config_path
from .runtime.trace import render_trace_for_humans
from .runtime.workspace import WorkspaceInitResult, initialize_workspace
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
from .skills.catalog import (
    catalog_entries,
    ordered_skills,
    render_skill_catalog,
    render_skill_catalog_rich,
    search_skill_matches,
    search_skills,
    skills_in_category,
)
from .skills.loader import discover_skills_from_roots, register_skill_tools, resolve_skill
from .skills.runner import run_skill, run_skill_intake
from .skills.session import (
    iter_sessions,
    load_session,
    record_skill_execution_confirmation_pending,
    record_input_collection_finished,
    record_input_collection_started,
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
)
from .tools.latex_compile import latex_backend_preflight
from .tools.mcp_adapter import load_mcp_server_configs, register_mcp_servers
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
    conda_env_name = os.getenv("CONDA_DEFAULT_ENV")
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

    if warnings and conda_env_name:
        warnings.append(
            f"建议优先使用 `conda run -n {conda_env_name} python -m researchos.cli ...` "
            "或修正 PATH 顺序后再运行。"
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
    if _skill_ui_uses_color(args):
        return render_readiness_panel_rich(
            skill_name=skill_name,
            session_id=session_id,
            session_file=session_file,
            readiness=readiness,
        )
    return render_readiness_panel(
        skill_name=skill_name,
        session_id=session_id,
        session_file=session_file,
        readiness=readiness,
    )


def _render_skill_completion_for_cli(args: argparse.Namespace, *, workspace: Path, session_id: str) -> str:
    if _skill_ui_uses_color(args):
        return render_skill_completion_panel_rich(workspace=workspace, session_id=session_id)
    return render_skill_completion_panel(workspace=workspace, session_id=session_id)


def _render_skill_description_for_cli(
    args: argparse.Namespace,
    *,
    skill_name: str,
    skill_path: Path,
    description: str,
    interaction: Any,
) -> str:
    if _skill_ui_uses_color(args):
        return render_skill_description_rich(
            skill_name=skill_name,
            skill_path=skill_path,
            description=description,
            interaction=interaction,
        )
    return render_skill_description(
        skill_name=skill_name,
        skill_path=skill_path,
        description=description,
        interaction=interaction,
    )


def _render_skill_catalog_for_cli(
    args: argparse.Namespace,
    *,
    skills: Any,
    workspace: Path,
    index_by_name: dict[str, int] | None = None,
    heading: str = "ResearchOS · 独立 Skill 目录",
    notice: str | None = None,
) -> str:
    if _skill_ui_uses_color(args):
        return render_skill_catalog_rich(
            skills=skills,
            workspace=workspace,
            index_by_name=index_by_name,
            heading=heading,
            notice=notice,
        )
    return render_skill_catalog(
        skills=skills,
        workspace=workspace,
        index_by_name=index_by_name,
        heading=heading,
        notice=notice,
    )


def _render_skill_status_for_cli(args: argparse.Namespace, *, workspace: Path, entries: Any) -> str:
    if _skill_ui_uses_color(args):
        return render_skill_status_panel_rich(workspace=workspace, entries=entries)
    return render_skill_status_panel(workspace=workspace, entries=entries)


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
        return CLIHumanInterface(
            t2_parameter_interpreter=interpreter,
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

    async def aclose(self) -> None:
        close = getattr(self.llm_client, "aclose", None)
        if not callable(close):
            return
        result = close()
        if hasattr(result, "__await__"):
            await result


def install_signal_handlers() -> None:
    """把 Ctrl-C / SIGTERM 转成 asyncio task cancel，便于优雅暂停。"""

    loop = asyncio.get_running_loop()

    def cancel_all() -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, cancel_all)
        except NotImplementedError:
            signal.signal(sig, lambda *_: cancel_all())


def _resolve_skill_roots(args: argparse.Namespace, workspace_dir: Path) -> list[Path]:
    """计算 skill 搜索根目录。

    默认会尝试：
    - 当前工作目录下的 `skills/`
    - workspace 下的 `skills/`
    用户也可以通过 `--skills-root` 追加自定义路径。
    """

    raw_roots = list(args.skills_root or ["skills"])
    candidates: list[Path] = []
    seen: set[Path] = set()
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


async def _maybe_register_mcp_tools(args: argparse.Namespace, registry: ToolRegistry) -> tuple[int, int]:
    """按需从 MCP 配置中注册远端工具。

    当前仓库不强绑具体 MCP SDK，因此 CLI 只负责：
    1. 读 `config/mcp.yaml`；
    2. 若用户通过 `--mcp-connector` 提供了连接函数，就调用它完成注册；
    3. 若只给了配置没给 connector，则静默跳过并在启动摘要里表现为 0 个 MCP server/tool。
    """

    config_path = Path(args.mcp_config).resolve()
    if not config_path.exists():
        return 0, 0

    server_configs = load_mcp_server_configs(config_path)
    if not server_configs or not args.mcp_connector:
        return len(server_configs), 0

    connector = _load_object(args.mcp_connector)
    before = set(registry.available_names())
    await register_mcp_servers(registry, server_configs, connector)
    after = set(registry.available_names())
    return len(server_configs), len(after - before)


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


def _startup_banner_enabled(args: argparse.Namespace, runtime_settings: RuntimeSettings) -> bool:
    return not (
        _is_quiet_args(args, runtime_settings)
        or bool(getattr(args, "no_banner", False))
        or bool(runtime_settings.ui.no_banner)
    )


async def _maybe_run_selftest(args: argparse.Namespace, llm_client: LLMClient) -> None:
    """按需执行 endpoint 自检。"""

    if getattr(args, "skip_startup_selftest", False):
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
    print(
        yaml.safe_dump(
            {
                "startup_selftest": {
                    "llm": llm_results,
                    "dependencies": dependency_results,
                }
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    if any(not item.get("ok") for item in dependency_results.values()):
        print(
            "[startup-warning] PDF/文献处理依赖不完整；T3/T9 可能在运行中失败。"
            " 建议先执行 `researchos selftest` 检查并补齐依赖。",
            file=sys.stderr,
        )
    if failed:
        raise SystemExit("LLM startup selftest failed")


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
    summary = format_startup_summary(
        workspace_dir=workspace_dir,
        state_machine=Path(args.state_machine).resolve() if hasattr(args, "state_machine") else None,
        gates=Path(args.gates).resolve() if hasattr(args, "gates") and args.gates else None,
        model_routing=Path(args.model_routing).resolve() if hasattr(args, "model_routing") else None,
        skill_roots=skill_roots,
        skill_count=skill_count,
        mcp_server_count=mcp_server_count,
        mcp_tool_count=mcp_tool_count,
    )
    if summary and not _is_quiet_args(args, runtime_settings):
        print(summary)


async def _prepare_runtime(args: argparse.Namespace, workspace_dir: Path) -> PreparedRuntime:
    """为 CLI 运行模式准备公共依赖。

    两种运行模式共享同一套启动检查：
    - 注册 builtin / skill tools；
    - 校验正式 agent 的 tool 是否齐全；
    - 构造 LLMClient 并按需跑 endpoint selftest。
    """

    register_builtin_task_checkers()
    skill_roots = _resolve_skill_roots(args, workspace_dir)
    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    discovered_skills = discover_skills_from_roots(skill_roots)
    registry = _build_tool_registry(skill_roots, runtime_settings, discovered_skills=discovered_skills)
    mcp_server_count, mcp_tool_count = await _maybe_register_mcp_tools(args, registry)
    _validate_agent_tools(registry)
    llm_client = LLMClient(Path(args.model_routing).resolve())
    try:
        await _maybe_run_selftest(args, llm_client)
    except BaseException:
        # A startup selftest can create aiohttp sessions before a provider
        # reports its first failure. Close them on this early exit so a
        # recoverable Skill/provider pause does not leak client warnings.
        close = getattr(llm_client, "aclose", None)
        if callable(close):
            await close()
        raise
    return PreparedRuntime(
        skill_roots=skill_roots,
        skill_count=len(discovered_skills),
        registry=registry,
        llm_client=llm_client,
        mcp_server_count=mcp_server_count,
        mcp_tool_count=mcp_tool_count,
    )


async def run_command(args: argparse.Namespace) -> int:
    """完整 pipeline 模式入口。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
    definition_errors = state_machine.validate_definition()
    if definition_errors:
        raise SystemExit(
            "State machine definition is invalid:\n" + "\n".join(f"- {item}" for item in definition_errors)
        )

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

    try:
        prepared = await _prepare_runtime(args, workspace_dir)
    except Exception as exc:
        print(
            "运行环境尚未就绪；修复 provider、配置或依赖后重新运行或 resume。\n"
            f"原因：{exc}",
            file=sys.stderr,
        )
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
        runner = CompletePipelineRunner(
            workspace=workspace_dir,
            state_machine=state_machine,
            llm_client=prepared.llm_client,
            tool_registry=prepared.registry,
            skill_roots=prepared.skill_roots,
            human_interface=_build_human_interface(runtime_settings, llm_client=prepared.llm_client),
            runtime_settings=runtime_settings,
        )
        return await runner.run(project_id=args.project_id, resume=getattr(args, "resume", False))
    finally:
        await prepared.aclose()


async def run_smoke_command(args: argparse.Namespace) -> int:
    """真实 pipeline smoke 模式：小规模覆盖 + medium LLM tier。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
        raise SystemExit(
            "State machine definition is invalid:\n" + "\n".join(f"- {item}" for item in definition_errors)
        )

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
        print(
            "运行环境尚未就绪；修复 provider、配置或依赖后重新运行或 resume。\n"
            f"原因：{exc}",
            file=sys.stderr,
        )
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
    if _is_quiet_args(args, runtime_settings):
        print(f"[Smoke] start_task={start_task}, tier={args.tier}", flush=True)
    else:
        print(
            "[Smoke] 已启动真实快速联调："
            f"start_task={start_task}, tier={args.tier}, active_pool_max={args.active_pool_max}, "
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
        return await runner.run(project_id=args.project_id, resume=False)
    finally:
        await prepared.aclose()


def _apply_smoke_llm_overrides(state_machine: StateMachine, *, tier: str, profile: str | None = None) -> None:
    """Temporarily lower all agent nodes to the smoke LLM tier."""

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
    active_pool = max(10, int(active_pool_max))
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
    if start_task:
        return start_task
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

    if start_task not in state_machine.nodes:
        print(f"Unknown --start-task: {start_task}")
        return 2
    if state_machine.nodes[start_task].terminal:
        print(f"--start-task cannot be terminal state: {start_task}")
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
            task_id=start_task,
            from_workspace=from_workspace,
            workspace_dir=workspace_dir,
            quiet=quiet,
        )
        source_state_path = from_workspace / "state.yaml"
        if source_state_path.exists():
            try:
                source_state = StateYaml.load_yaml(source_state_path)
            except Exception as exc:
                print(f"[warning] 无法读取来源 state.yaml，仍会从 {start_task} 初始化状态: {exc}")

    ok, err = validate_prerequisites(workspace_dir, start_task)
    if not ok:
        print(f"Prerequisites not met for {start_task}: {err}")
        if from_workspace is None:
            print("Hint: use --from <other-workspace> to copy upstream artifacts.")
        return 3

    state = _build_start_task_state(
        start_task=start_task,
        project_id=project_id,
        source_state=source_state,
    )
    state.dump_yaml(state_path)
    if quiet:
        print(f"[Pipeline] state={start_task}", flush=True)
    else:
        print(f"[进度] 已初始化 pipeline state: current_task={start_task}", flush=True)
    return 0


def _copy_task_inputs_from_workspace(
    *,
    task_id: str,
    from_workspace: Path,
    workspace_dir: Path,
    quiet: bool = False,
) -> None:
    """Copy task input artifacts from another workspace for full-pipeline restart."""

    io_spec = get_task_io(task_id)
    for rel_path in io_spec["inputs"].values():
        src = from_workspace / rel_path
        dst = workspace_dir / rel_path
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        if not quiet:
            print(f"copied: {rel_path}", flush=True)


def _build_start_task_state(
    *,
    start_task: str,
    project_id: str,
    source_state: StateYaml | None,
) -> StateYaml:
    if source_state is None:
        return StateYaml(project_id=project_id, current_task=start_task, status="RUNNING")

    state = StateYaml(
        project_id=source_state.project_id or project_id,
        current_task=start_task,
        status="RUNNING",
        budget_cumulative=source_state.budget_cumulative,
        task_context={},
    )
    kept_tasks: set[str] = set()
    for entry in source_state.history:
        if entry.task == start_task:
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

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
    try:
        prepared = await _prepare_runtime(args, workspace_dir)
    except Exception as exc:
        print(
            "运行环境尚未就绪；修复 provider、配置或依赖后重新运行此任务。\n"
            f"原因：{exc}",
            file=sys.stderr,
        )
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
    from_workspace = Path(args.from_workspace).resolve() if args.from_workspace else None
    try:
        try:
            runner = SingleTaskRunner(
                workspace=workspace_dir,
                task_id=args.task_id.strip(),
                llm_client=prepared.llm_client,
                tool_registry=prepared.registry,
                from_workspace=from_workspace,
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


def _normalized_skill_answer(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _skill_user_confirms_execution(value: str) -> bool:
    return _normalized_skill_answer(value) in _SKILL_EXECUTE_ANSWERS


def _skill_user_paused(value: str) -> bool:
    return _normalized_skill_answer(value) in _SKILL_PAUSE_ANSWERS


async def run_skill_command(args: argparse.Namespace) -> int:
    """Run a Skill through guided intake, explicit confirmation, then execution."""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
    skill_roots = _resolve_skill_roots(args, workspace_dir)
    try:
        skill = resolve_skill(args.skill_name, skill_roots)
        interaction = parse_skill_interaction(skill.metadata)
        session_id = args.session_id or skill.name
        previous = load_session(workspace_dir, session_id)
        request = " ".join(args.request).strip()
        if args.resume and not request and previous:
            request = str(previous.get("request", "")).strip()
        if interactive_session and not request:
            prompt = (
                interaction.request_prompt
                if interaction is not None
                else "请说明希望这个 Skill 完成什么。"
            )
            request = await _build_human_interface(runtime_settings).ask_clarification(question=prompt)
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
                "当前没有可交互终端；已保留 WAITING_INPUT 会话。请上传文件后以同一会话恢复，"
                "或在可交互终端恢复该会话以启动材料收集。"
            )
            return 2
        if interaction is None or interaction.mode != "guided":
            print("当前 Skill 没有 guided 输入契约，无法启动受限材料收集。", file=sys.stderr)
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
                )
                intake_message = (
                    f"第 {intake_round} 轮材料收集完成，已通过确定性输入检查；等待人工确认是否执行 Skill。"
                    if readiness.ready
                    else f"第 {intake_round} 轮材料收集后仍有缺口；将继续由 intake Agent 逐项询问，或由人工暂停。"
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
                action = await human.ask_clarification(
                    question=(
                        "当前材料仍不足以启动该 Skill。输入“继续”让系统开始下一轮定向材料收集；"
                        "输入“暂停”保留当前会话，稍后再继续。"
                    ),
                    suggestions=["继续收集缺失材料", "暂停并保留会话"],
                )
                if _skill_user_paused(action):
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
            record_runtime_pause(workspace=workspace_dir, session_id=session_id, error=exc)
            print(
                "Skill 交互式材料收集尚未完成，已保留会话；修复运行环境或在同一会话继续补充。\n"
                f"原因：{exc}",
                file=sys.stderr,
            )
            print(_render_skill_completion_for_cli(args, workspace=workspace_dir, session_id=session_id))
            return 1

    if interactive_session and not getattr(args, "yes", False):
        confirmation_human = human or _build_human_interface(runtime_settings)
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
                    suggestions=["执行当前 Skill", "暂停，稍后使用 --resume 继续"],
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
            record_runtime_pause(workspace=workspace_dir, session_id=session_id, error=exc)
            print(
                "Skill 运行环境尚未就绪，已保留会话；修复 provider、配置或依赖后使用同一 "
                f"`--session-id {session_id} --resume` 重试。\n原因：{exc}",
                file=sys.stderr,
            )
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
        )
    finally:
        await prepared.aclose()
    if outputs_expected:
        ok, errors = validate_declared_outputs(workspace_dir, outputs_expected)
        if not ok:
            result.ok = False
            result.stop_reason = AgentResult.STOP_ERROR
            result.error = "Skill output validation failed: " + "; ".join(errors)
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

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    runtime_settings = _runtime_settings_for_args(runtime_settings, args)
    _emit_startup_ui(
        args=args,
        runtime_settings=runtime_settings,
        workspace_dir=None,
        show_summary=False,
    )
    client = LLMClient(Path(args.model_routing).resolve())
    try:
        llm_results = await client.selftest(args.profile or None)
    finally:
        await client.aclose()
    dependency_results = _dependency_selftest()
    print(
        yaml.safe_dump(
            {
                "llm": llm_results,
                "dependencies": dependency_results,
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    llm_ok = all(item.get("ok") for item in llm_results.values())
    deps_ok = all(item.get("ok") for item in dependency_results.values())
    return 0 if (llm_ok and deps_ok) else 1


def init_workspace_command(args: argparse.Namespace) -> int:
    """初始化一个标准 workspace。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
    print(
        yaml.safe_dump(
            {
                "ok": True,
                "workspace": str(result.workspace_dir),
                "created_dirs": result.created_dirs,
                "project_file": str(result.project_file) if result.project_file else None,
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return 0


def doctor_command(args: argparse.Namespace) -> int:
    """Deterministic local environment check for Native and Docker mode."""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
        runtime_path = resolve_runtime_config_path(Path("config/runtime.yaml"))
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

    user_config = os.getenv("RESEARCHOS_CONFIG") or os.getenv("RESEARCHOS_USER_SETTINGS") or "config/user_settings.yaml"
    if Path(user_config).exists():
        add("OK", "user settings", str(Path(user_config).resolve()))
    else:
        add("WARN", "user settings", f"{user_config} not found; checked-in defaults remain active")

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


def validate_command(args: argparse.Namespace) -> int:
    """校验指定 task 的产物。"""

    register_builtin_task_checkers()
    workspace = Path(args.workspace).resolve()
    state_machine_path = Path(args.state_machine).resolve()
    task_id = args.task
    if task_id is None:
        state = StateYaml.load_yaml((workspace / "state.yaml").resolve())
        task_id = state.current_task

    declared_outputs = build_declared_outputs_from_state_machine(state_machine_path, task_id)
    ok, errors = validate_task_artifacts(
        workspace,
        task_id,
        declared_outputs=declared_outputs,
    )
    if not ok and task_id == "T8-SECTION-PLAN":
        from .runtime.manuscript_recovery import can_repair_t8_section_plan, repair_t8_section_plan_outputs

        if can_repair_t8_section_plan(workspace):
            repair_ok, repair_err = asyncio.run(repair_t8_section_plan_outputs(workspace))
            if repair_ok:
                ok, errors = validate_task_artifacts(
                    workspace,
                    task_id,
                    declared_outputs=declared_outputs,
                )
            else:
                errors = f"{errors}; T8-SECTION-PLAN deterministic repair failed: {repair_err}"
    print(
        yaml.safe_dump(
            {"ok": ok, "task": task_id, "errors": errors},
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return 0 if ok else 1


def validate_config_command(args: argparse.Namespace) -> int:
    """校验 workflow/gate/runtime 配置的一致性。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
        "runtime": {
            "config_path": str(resolve_runtime_config_path(Path("config/runtime.yaml")).resolve()),
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
    """Compile the project-specific external executor skill suite."""

    from .skills.project_specialization import specialize_project_skills, specialize_project_skills_with_llm

    workspace = Path(args.workspace).resolve()
    dry_run = bool(getattr(args, "dry_run", False))
    validate_only = bool(getattr(args, "validate_only", False))
    if dry_run:
        result = specialize_project_skills(
            workspace=workspace,
            dry_run=dry_run,
            validate_only=False,
        )
    elif validate_only:
        result = await specialize_project_skills_with_llm(
            workspace=workspace,
            llm_client=None,
            validate_only=True,
        )
    else:
        llm_client = LLMClient(Path(args.model_routing).resolve())
        try:
            result = await specialize_project_skills_with_llm(
                workspace=workspace,
                llm_client=llm_client,
                profile=getattr(args, "profile", None),
                tier=getattr(args, "tier", "medium"),
            )
        finally:
            await llm_client.aclose()
    report = result.report or {}
    print(f"Project Skill Specialization: {result.status}")
    method = report.get("specialization_method") or ("dry_run" if dry_run else "deterministic_validation")
    print(f"Method: {method}")
    llm_info = report.get("llm_specialization") if isinstance(report.get("llm_specialization"), dict) else {}
    if llm_info:
        print(
            "LLM: "
            f"{llm_info.get('model', 'n/a')} via {llm_info.get('endpoint', 'n/a')} "
            f"({llm_info.get('skills_specialized', 0)} skills)"
        )
    print("Context: external_executor/project_skill_context.yaml")
    print(f"Skills: {report.get('skills_specialized', 0)}/{report.get('skills_total', 13)}")
    print(f"Required uncertain fields: {len(report.get('required_uncertain_fields') or [])}")
    print("Report: external_executor/skill_specialization_report.json")
    if result.status == "failed" and result.errors:
        first = result.errors[0]
        print(f"First error: {first.get('code', 'error')} - {first.get('message', '')}")
    return 1 if result.status == "failed" else 0


def status_command(args: argparse.Namespace) -> int:
    """输出 workspace 当前的 state.yaml。"""

    state = StateYaml.load_yaml((Path(args.workspace) / "state.yaml").resolve())
    print(yaml.safe_dump(model_dump(state, mode="json"), allow_unicode=True, sort_keys=False))
    return 0


def trace_command(args: argparse.Namespace) -> int:
    """打印指定 run_id 对应的 trace 文件。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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

    for skill in ordered_skills(discovered.values()):
        interaction = parse_skill_interaction(skill.metadata)
        skill_info = {
            "name": skill.name,
            "description": skill.description,
            "path": str(skill.skill_dir),
            "tools": skill.allowed_tools,
            "model_tier": skill.metadata.get("model_tier") or skill.metadata.get("tier", "medium"),
            "llm_profile": skill.metadata.get("llm_profile"),
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
        print(yaml.safe_dump(
            {"catalog": catalog_entries(discovered.values()), "skills": all_skills},
            allow_unicode=True,
            sort_keys=False,
        ))
    else:
        print(f"Found {len(all_skills)} skill(s) / 发现 {len(all_skills)} 个可识别 Skill：\n")
        print(_render_skill_catalog_for_cli(args, skills=discovered.values(), workspace=workspace_dir))

    return 0


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
    skills = ordered_skills(discovered.values())
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
            skill = discovered.get(target)
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
        return asyncio.run(run_skill_command(args))


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
        default="demo-project" if use_defaults else default,
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
        "--model-routing",
        default="config/model_routing.yaml" if use_defaults else default,
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

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    parser = argparse.ArgumentParser(prog="researchos")
    _add_shared_cli_options(parser, runtime_settings, use_defaults=True)

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-workspace", help="初始化标准 workspace")
    _add_shared_cli_options(init_parser, runtime_settings, use_defaults=False)
    init_parser.add_argument("--topic", default="")
    init_parser.add_argument("--no-project-file", action="store_true")
    init_parser.add_argument("--force-project-file", action="store_true")

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
        default=None,
        help="从指定状态机节点开始完整 pipeline，例如 T2、T3、T8-STYLE-GATE",
    )
    run_parser.add_argument("--startup-selftest", action="store_true")
    run_parser.add_argument("--skip-startup-selftest", action="store_true")

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
    smoke_parser.add_argument("--tier", default="medium", choices=["light", "medium", "heavy"])
    smoke_parser.add_argument(
        "--profile",
        default=None,
        help="可选：覆盖 LLM profile；不填则只把所有节点 tier 降到 medium",
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
    resume_parser.add_argument("--startup-selftest", action="store_true")
    resume_parser.add_argument("--skip-startup-selftest", action="store_true")

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
        help="覆盖 LLM profile，例如 cheap_fast / deep_reasoning / audit",
    )
    run_task_parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help="允许显式运行 LEGACY-T5-PILOT / LEGACY-T6-NOVELTY / LEGACY-T7-FULL 旧内部实验节点",
    )
    run_task_parser.add_argument("--startup-selftest", action="store_true")
    run_task_parser.add_argument("--skip-startup-selftest", action="store_true")

    status_parser = subparsers.add_parser("status", help="查看当前状态")
    _add_shared_cli_options(status_parser, runtime_settings, use_defaults=False)

    selftest_parser = subparsers.add_parser("selftest", help="检查 LLM endpoint 连通性")
    _add_shared_cli_options(selftest_parser, runtime_settings, use_defaults=False)
    selftest_parser.add_argument("--profile", action="append")

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

    validate_parser = subparsers.add_parser("validate", help="校验 task 产物")
    _add_shared_cli_options(validate_parser, runtime_settings, use_defaults=False)
    validate_parser.add_argument("--task")

    validate_config_parser = subparsers.add_parser("validate-config", help="校验状态机与 runtime 配置")
    _add_shared_cli_options(validate_config_parser, runtime_settings, use_defaults=False)

    specialize_parser = subparsers.add_parser(
        "specialize-executor-skills",
        help="调用 LLM 生成项目专属 external executor skill suite",
    )
    _add_shared_cli_options(specialize_parser, runtime_settings, use_defaults=False)
    specialize_parser.add_argument("--dry-run", action="store_true", help="只构建和校验，不发布产物")
    specialize_parser.add_argument("--validate-only", action="store_true", help="只校验现有专属化产物")
    specialize_parser.add_argument("--profile", help="覆盖本次 skill 专属化使用的 LLM profile")
    specialize_parser.add_argument("--tier", default="medium", help="本次 skill 专属化使用的 LLM tier，默认 medium")

    run_skill_parser = subparsers.add_parser("run-skill", help="启动或恢复一个带输入检查的独立 Skill")
    _add_shared_cli_options(run_skill_parser, runtime_settings, use_defaults=False)
    run_skill_parser.add_argument("skill_name")
    run_skill_parser.add_argument("request", nargs="*")
    run_skill_parser.add_argument("--profile")
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

    list_skills_parser = subparsers.add_parser("list-skills", help="列出所有可用的 skills")
    _add_shared_cli_options(list_skills_parser, runtime_settings, use_defaults=False)

    browse_skills_parser = subparsers.add_parser("browse-skills", help="以终端卡片浏览、查看并启动 Skill")
    _add_shared_cli_options(browse_skills_parser, runtime_settings, use_defaults=False)

    describe_skill_parser = subparsers.add_parser("describe-skill", help="查看一个 Skill 的上传、输出与恢复契约")
    _add_shared_cli_options(describe_skill_parser, runtime_settings, use_defaults=False)
    describe_skill_parser.add_argument("skill_name")

    skill_status_parser = subparsers.add_parser("skill-status", help="查看 workspace 中可恢复的 Skill 会话")
    _add_shared_cli_options(skill_status_parser, runtime_settings, use_defaults=False)
    skill_status_parser.add_argument("skill_name", nargs="?", help="可选：仅查看指定 Skill")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    _emit_environment_warnings()
    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
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
        return asyncio.run(run_command(args))
    if args.command == "run_smoke":
        args.resume = False
        return asyncio.run(run_smoke_command(args))
    if args.command == "resume":
        args.resume = True
        return asyncio.run(run_command(args))
    if args.command == "run-task":
        return asyncio.run(run_task_command(args))
    if args.command == "run-skill":
        return asyncio.run(run_skill_command(args))
    if args.command == "list-skills":
        return list_skills_command(args)
    if args.command == "browse-skills":
        return browse_skills_command(args)
    if args.command == "describe-skill":
        return describe_skill_command(args)
    if args.command == "skill-status":
        return skill_status_command(args)
    if args.command == "status":
        return status_command(args)
    if args.command == "doctor":
        return doctor_command(args)
    if args.command == "selftest":
        return asyncio.run(selftest_command(args))
    if args.command == "trace":
        return trace_command(args)
    if args.command == "validate":
        return validate_command(args)
    if args.command == "validate-config":
        return validate_config_command(args)
    if args.command == "specialize-executor-skills":
        return asyncio.run(specialize_executor_skills_command(args))
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
