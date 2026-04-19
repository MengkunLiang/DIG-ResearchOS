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
import importlib
from pathlib import Path
import signal
import subprocess
import sys

import yaml

from .agents.registry import AGENT_REGISTRY
from .cli_runners import CompletePipelineRunner, SingleTaskRunner
from .orchestration.state_machine import StateMachine
from .pydantic_compat import model_dump
from .runtime.agent import AgentResult
from .runtime.cli_ui import format_startup_summary, show_startup_banner
from .runtime.config import RuntimeSettings, load_runtime_settings
from .runtime.llm_client import LLMClient
from .runtime.logger import configure_file_logging, configure_logging
from .runtime.trace import render_trace_for_humans
from .runtime.workspace import WorkspaceInitResult, initialize_workspace
from .schemas.state import StateYaml
from .schemas.validator import (
    build_declared_outputs_from_state_machine,
    register_builtin_task_checkers,
    validate_declared_outputs,
    validate_task_artifacts,
)
from .skills.loader import register_skill_tools, resolve_skill
from .skills.runner import run_skill
from .tools.builtin import register_builtin_tools
from .tools.human_gate import CLIHumanInterface, HumanInterface
from .tools.mcp_adapter import load_mcp_server_configs, register_mcp_servers
from .tools.registry import ToolRegistry


def ensure_workspace_layout(workspace_dir: Path, runtime_settings: RuntimeSettings) -> None:
    """创建 runtime 运行所需的固定目录。"""

    initialize_workspace(
        workspace_dir,
        create_project_file=False,
        runtime_dir_name=runtime_settings.workspace.runtime_dir,
    )


def _configure_workspace_logging(
    args: argparse.Namespace,
    workspace_dir: Path,
    runtime_settings: RuntimeSettings,
) -> None:
    """把进程日志同时写入 workspace 内的 `_runtime/logs/`。"""

    configure_file_logging(
        runtime_settings.logs_dir(workspace_dir) / "researchos.log",
        level=args.log_level,
    )


def _build_human_interface(runtime_settings: RuntimeSettings) -> HumanInterface:
    """按 runtime 配置构造人机接口。

    当前 runtime 只实现了 CLI backend，因此对未知 backend 直接 fail fast，
    避免用户以为自己已经切到了一个并不存在的 Web/API 模式。
    """

    backend = runtime_settings.human_interface.backend.lower().strip()
    if backend in {"", "cli"}:
        return CLIHumanInterface()
    raise SystemExit(f"Unsupported human_interface.backend: {runtime_settings.human_interface.backend}")


@dataclass
class PreparedRuntime:
    """CLI 启动后交给各命令使用的公共依赖。"""

    skill_roots: list[Path]
    registry: ToolRegistry
    llm_client: LLMClient
    mcp_server_count: int = 0
    mcp_tool_count: int = 0


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


def _build_tool_registry(skill_roots: list[Path]) -> ToolRegistry:
    """构造本次 CLI 运行用到的完整 ToolRegistry。"""

    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_skill_tools(registry, skill_roots)
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


def _any_registered_agent_uses_any_tool(tool_names: set[str]) -> bool:
    """判断当前 registry 中的正式 agent 是否依赖某类高门槛工具。"""

    for agent_cls in AGENT_REGISTRY.values():
        if tool_names & set(agent_cls().spec.tool_names):
            return True
    return False


def _maybe_check_docker_availability() -> None:
    """按需检查 Docker 是否可用。

    现阶段正式 agent 只有 Hello，不会触发这个检查；但把逻辑先固化在 CLI 里，
    后续 T5/T7/T9 agent 落地后无需再改主入口。
    """

    if not _any_registered_agent_uses_any_tool({"docker_exec", "latex_compile"}):
        return
    result = subprocess.run(
        ["docker", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit("Docker not available but some registered agents require it")


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
    results = await llm_client.selftest()
    failed = {name: item for name, item in results.items() if not item.get("ok")}
    print(yaml.safe_dump({"startup_selftest": results}, allow_unicode=True, sort_keys=False))
    if failed:
        raise SystemExit("LLM startup selftest failed")


def _emit_startup_ui(
    *,
    args: argparse.Namespace,
    workspace_dir: Path | None,
    show_banner: bool = True,
    show_summary: bool = True,
    skill_roots: list[Path] | None = None,
    mcp_server_count: int = 0,
    mcp_tool_count: int = 0,
) -> None:
    """打印 CLI 启动动画与启动摘要。"""

    if show_banner:
        show_startup_banner(args.command, no_banner=getattr(args, "no_banner", False))
    if not show_summary:
        return
    summary = format_startup_summary(
        workspace_dir=workspace_dir,
        state_machine=Path(args.state_machine).resolve() if hasattr(args, "state_machine") else None,
        gates=Path(args.gates).resolve() if hasattr(args, "gates") and args.gates else None,
        model_routing=Path(args.model_routing).resolve() if hasattr(args, "model_routing") else None,
        skill_roots=skill_roots,
        mcp_server_count=mcp_server_count,
        mcp_tool_count=mcp_tool_count,
    )
    if summary:
        print(summary)


async def _prepare_runtime(args: argparse.Namespace, workspace_dir: Path) -> PreparedRuntime:
    """为 CLI 运行模式准备公共依赖。

    两种运行模式共享同一套启动检查：
    - 注册 builtin / skill tools；
    - 校验正式 agent 的 tool 是否齐全；
    - 必要时检查 Docker；
    - 构造 LLMClient 并按需跑 endpoint selftest。
    """

    register_builtin_task_checkers()
    skill_roots = _resolve_skill_roots(args, workspace_dir)
    registry = _build_tool_registry(skill_roots)
    mcp_server_count, mcp_tool_count = await _maybe_register_mcp_tools(args, registry)
    _validate_agent_tools(registry)
    _maybe_check_docker_availability()
    llm_client = LLMClient(Path(args.model_routing).resolve())
    await _maybe_run_selftest(args, llm_client)
    return PreparedRuntime(
        skill_roots=skill_roots,
        registry=registry,
        llm_client=llm_client,
        mcp_server_count=mcp_server_count,
        mcp_tool_count=mcp_tool_count,
    )


async def run_command(args: argparse.Namespace) -> int:
    """完整 pipeline 模式入口。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(args=args, workspace_dir=workspace_dir, show_summary=False)
    install_signal_handlers()
    prepared = await _prepare_runtime(args, workspace_dir)
    state_machine = StateMachine(
        Path(args.state_machine).resolve(),
        Path(args.gates).resolve() if args.gates else None,
    )
    definition_errors = state_machine.validate_definition()
    if definition_errors:
        raise SystemExit(
            "State machine definition is invalid:\n" + "\n".join(f"- {item}" for item in definition_errors)
        )
    _emit_startup_ui(
        args=args,
        workspace_dir=workspace_dir,
        show_banner=False,
        skill_roots=prepared.skill_roots,
        mcp_server_count=prepared.mcp_server_count,
        mcp_tool_count=prepared.mcp_tool_count,
    )
    runner = CompletePipelineRunner(
        workspace=workspace_dir,
        state_machine=state_machine,
        llm_client=prepared.llm_client,
        tool_registry=prepared.registry,
        skill_roots=prepared.skill_roots,
        human_interface=_build_human_interface(runtime_settings),
        runtime_settings=runtime_settings,
    )
    return await runner.run(project_id=args.project_id, resume=getattr(args, "resume", False))


async def run_task_command(args: argparse.Namespace) -> int:
    """单 task 模式入口。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(args=args, workspace_dir=workspace_dir, show_summary=False)
    install_signal_handlers()
    prepared = await _prepare_runtime(args, workspace_dir)
    _emit_startup_ui(
        args=args,
        workspace_dir=workspace_dir,
        show_banner=False,
        skill_roots=prepared.skill_roots,
        mcp_server_count=prepared.mcp_server_count,
        mcp_tool_count=prepared.mcp_tool_count,
    )
    from_workspace = Path(args.from_workspace).resolve() if args.from_workspace else None
    runner = SingleTaskRunner(
        workspace=workspace_dir,
        task_id=args.task_id.strip(),
        llm_client=prepared.llm_client,
        tool_registry=prepared.registry,
        from_workspace=from_workspace,
        override_profile=args.profile,
        human_interface=_build_human_interface(runtime_settings),
        runtime_settings=runtime_settings,
    )
    return await runner.run()


async def run_skill_command(args: argparse.Namespace) -> int:
    """独立运行一个 skill。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(args=args, workspace_dir=workspace_dir, show_summary=False)
    install_signal_handlers()
    prepared = await _prepare_runtime(args, workspace_dir)
    _emit_startup_ui(
        args=args,
        workspace_dir=workspace_dir,
        show_banner=False,
        skill_roots=prepared.skill_roots,
        mcp_server_count=prepared.mcp_server_count,
        mcp_tool_count=prepared.mcp_tool_count,
    )

    skill = resolve_skill(args.skill_name, prepared.skill_roots)
    outputs_expected = {
        name: workspace_dir / rel_path
        for name, rel_path in (skill.metadata.get("outputs_expected") or {}).items()
    }
    human = _build_human_interface(runtime_settings)
    result = await run_skill(
        skill=skill,
        user_request=" ".join(args.request).strip() or f"Execute skill '{skill.name}'.",
        workspace=workspace_dir,
        tool_registry=prepared.registry,
        llm_client=prepared.llm_client,
        human_interface=human,
        outputs_expected=outputs_expected,
        llm_profile=args.profile,
        runtime_settings=runtime_settings,
    )
    if outputs_expected:
        ok, errors = validate_declared_outputs(workspace_dir, outputs_expected)
        if not ok:
            result.ok = False
            result.stop_reason = AgentResult.STOP_ERROR
            result.error = "Skill output validation failed: " + "; ".join(errors)
            result.message = result.error

    print(
        yaml.safe_dump(
            {
                "ok": result.ok,
                "stop_reason": result.stop_reason,
                "trace_file": str(result.trace_file) if result.trace_file else None,
                "outputs": {k: str(v) for k, v in result.outputs_produced.items()},
                "error": result.error,
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return 0 if result.ok else 1


async def selftest_command(args: argparse.Namespace) -> int:
    """LLM endpoint 自检。"""

    _emit_startup_ui(args=args, workspace_dir=None, show_summary=False)
    client = LLMClient(Path(args.model_routing).resolve())
    results = await client.selftest(args.profile or None)
    print(yaml.safe_dump(results, allow_unicode=True, sort_keys=False))
    return 0 if all(item.get("ok") for item in results.values()) else 1


def init_workspace_command(args: argparse.Namespace) -> int:
    """初始化一个标准 workspace。"""

    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir, runtime_settings)
    _configure_workspace_logging(args, workspace_dir, runtime_settings)
    _emit_startup_ui(args=args, workspace_dir=workspace_dir, show_summary=False)
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
            "workspace_default_root": runtime_settings.workspace.default_root,
            "runtime_dir": runtime_settings.workspace.runtime_dir,
            "log_level": runtime_settings.logging.level,
            "log_json": runtime_settings.logging.json,
            "human_backend": runtime_settings.human_interface.backend,
        },
        "errors": errors,
    }
    print(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    return 0 if not errors else 1


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
        default="config/state_machine.yaml" if use_defaults else default,
    )
    parser.add_argument(
        "--gates",
        default="config/gates.yaml" if use_defaults else default,
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
    run_parser.add_argument("--startup-selftest", action="store_true")
    run_parser.add_argument("--skip-startup-selftest", action="store_true")

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
    run_task_parser.add_argument("--startup-selftest", action="store_true")
    run_task_parser.add_argument("--skip-startup-selftest", action="store_true")

    status_parser = subparsers.add_parser("status", help="查看当前状态")
    _add_shared_cli_options(status_parser, runtime_settings, use_defaults=False)

    selftest_parser = subparsers.add_parser("selftest", help="检查 LLM endpoint 连通性")
    _add_shared_cli_options(selftest_parser, runtime_settings, use_defaults=False)
    selftest_parser.add_argument("--profile", action="append")

    trace_parser = subparsers.add_parser("trace", help="查看某次 run 的 trace")
    _add_shared_cli_options(trace_parser, runtime_settings, use_defaults=False)
    trace_parser.add_argument("run_id")
    trace_parser.add_argument("--raw", action="store_true", help="直接输出原始 JSONL")

    validate_parser = subparsers.add_parser("validate", help="校验 task 产物")
    _add_shared_cli_options(validate_parser, runtime_settings, use_defaults=False)
    validate_parser.add_argument("--task")

    validate_config_parser = subparsers.add_parser("validate-config", help="校验状态机与 runtime 配置")
    _add_shared_cli_options(validate_config_parser, runtime_settings, use_defaults=False)

    run_skill_parser = subparsers.add_parser("run-skill", help="独立运行一个 skill")
    _add_shared_cli_options(run_skill_parser, runtime_settings, use_defaults=False)
    run_skill_parser.add_argument("skill_name")
    run_skill_parser.add_argument("request", nargs="*")
    run_skill_parser.add_argument("--profile")
    run_skill_parser.add_argument("--startup-selftest", action="store_true")
    run_skill_parser.add_argument("--skip-startup-selftest", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    runtime_settings = load_runtime_settings(Path("config/runtime.yaml"))
    configure_logging(level=args.log_level, json_logs=runtime_settings.logging.json)
    if args.command == "init-workspace":
        return init_workspace_command(args)
    if args.command == "run":
        args.resume = False
        return asyncio.run(run_command(args))
    if args.command == "resume":
        args.resume = True
        return asyncio.run(run_command(args))
    if args.command == "run-task":
        return asyncio.run(run_task_command(args))
    if args.command == "run-skill":
        return asyncio.run(run_skill_command(args))
    if args.command == "status":
        return status_command(args)
    if args.command == "selftest":
        return asyncio.run(selftest_command(args))
    if args.command == "trace":
        return trace_command(args)
    if args.command == "validate":
        return validate_command(args)
    if args.command == "validate-config":
        return validate_config_command(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
