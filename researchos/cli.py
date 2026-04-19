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
from .runtime.llm_client import LLMClient
from .runtime.logger import configure_logging
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
from .tools.human_gate import CLIHumanInterface
from .tools.registry import ToolRegistry


def ensure_workspace_layout(workspace_dir: Path) -> None:
    """创建 runtime 运行所需的固定目录。"""

    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "_runtime" / "traces").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "_runtime" / "logs").mkdir(parents=True, exist_ok=True)


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

    if not getattr(args, "startup_selftest", False):
        return
    results = await llm_client.selftest()
    failed = {name: item for name, item in results.items() if not item.get("ok")}
    print(yaml.safe_dump({"startup_selftest": results}, allow_unicode=True, sort_keys=False))
    if failed:
        raise SystemExit("LLM startup selftest failed")


async def _prepare_runtime(args: argparse.Namespace, workspace_dir: Path) -> tuple[list[Path], ToolRegistry, LLMClient]:
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
    _validate_agent_tools(registry)
    _maybe_check_docker_availability()
    llm_client = LLMClient(Path(args.model_routing).resolve())
    await _maybe_run_selftest(args, llm_client)
    return skill_roots, registry, llm_client


async def run_command(args: argparse.Namespace) -> int:
    """完整 pipeline 模式入口。"""

    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir)
    install_signal_handlers()
    skill_roots, registry, llm_client = await _prepare_runtime(args, workspace_dir)
    state_machine = StateMachine(
        Path(args.state_machine).resolve(),
        Path(args.gates).resolve() if args.gates else None,
    )
    runner = CompletePipelineRunner(
        workspace=workspace_dir,
        state_machine=state_machine,
        llm_client=llm_client,
        tool_registry=registry,
        skill_roots=skill_roots,
    )
    return await runner.run(project_id=args.project_id, resume=getattr(args, "resume", False))


async def run_task_command(args: argparse.Namespace) -> int:
    """单 task 模式入口。"""

    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir)
    install_signal_handlers()
    skill_roots, registry, llm_client = await _prepare_runtime(args, workspace_dir)
    _ = skill_roots  # 单 task 当前仍只跑正式 agent，不直接消费 skill roots。
    from_workspace = Path(args.from_workspace).resolve() if args.from_workspace else None
    runner = SingleTaskRunner(
        workspace=workspace_dir,
        task_id=args.task_id.strip(),
        llm_client=llm_client,
        tool_registry=registry,
        from_workspace=from_workspace,
        override_profile=args.profile,
    )
    return await runner.run()


async def run_skill_command(args: argparse.Namespace) -> int:
    """独立运行一个 skill。"""

    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir)
    install_signal_handlers()
    skill_roots, registry, llm_client = await _prepare_runtime(args, workspace_dir)

    skill = resolve_skill(args.skill_name, skill_roots)
    outputs_expected = {
        name: workspace_dir / rel_path
        for name, rel_path in (skill.metadata.get("outputs_expected") or {}).items()
    }
    human = CLIHumanInterface()
    result = await run_skill(
        skill=skill,
        user_request=" ".join(args.request).strip() or f"Execute skill '{skill.name}'.",
        workspace=workspace_dir,
        tool_registry=registry,
        llm_client=llm_client,
        human_interface=human,
        outputs_expected=outputs_expected,
        llm_profile=args.profile,
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

    client = LLMClient(Path(args.model_routing).resolve())
    results = await client.selftest(args.profile or None)
    print(yaml.safe_dump(results, allow_unicode=True, sort_keys=False))
    return 0 if all(item.get("ok") for item in results.values()) else 1


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


def status_command(args: argparse.Namespace) -> int:
    """输出 workspace 当前的 state.yaml。"""

    state = StateYaml.load_yaml((Path(args.workspace) / "state.yaml").resolve())
    print(yaml.safe_dump(model_dump(state, mode="json"), allow_unicode=True, sort_keys=False))
    return 0


def trace_command(args: argparse.Namespace) -> int:
    """打印指定 run_id 对应的 trace 文件。"""

    trace_path = (Path(args.workspace) / "_runtime" / "traces" / f"{args.run_id}.jsonl").resolve()
    print(trace_path.read_text(encoding="utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(prog="researchos")
    parser.add_argument("--workspace", default="./workspace")
    parser.add_argument("--project-id", default="demo-project")
    parser.add_argument("--state-machine", default="config/state_machine.yaml")
    parser.add_argument("--gates", default="config/gates.yaml")
    parser.add_argument("--model-routing", default="config/model_routing.yaml")
    parser.add_argument("--skills-root", action="append")
    parser.add_argument("--log-level", default="INFO")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="运行完整 pipeline")
    run_parser.add_argument("--startup-selftest", action="store_true")

    resume_parser = subparsers.add_parser("resume", help="恢复已暂停的 pipeline")
    resume_parser.add_argument("--startup-selftest", action="store_true")

    run_task_parser = subparsers.add_parser("run-task", help="只运行一个 task")
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

    subparsers.add_parser("status", help="查看当前状态")

    selftest_parser = subparsers.add_parser("selftest", help="检查 LLM endpoint 连通性")
    selftest_parser.add_argument("--profile", action="append")

    trace_parser = subparsers.add_parser("trace", help="查看某次 run 的 trace")
    trace_parser.add_argument("run_id")

    validate_parser = subparsers.add_parser("validate", help="校验 task 产物")
    validate_parser.add_argument("--task")

    run_skill_parser = subparsers.add_parser("run-skill", help="独立运行一个 skill")
    run_skill_parser.add_argument("skill_name")
    run_skill_parser.add_argument("request", nargs="*")
    run_skill_parser.add_argument("--profile")
    run_skill_parser.add_argument("--startup-selftest", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(level=args.log_level)
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
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
