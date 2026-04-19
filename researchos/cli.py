from __future__ import annotations

"""ResearchOS 命令行入口。"""

import argparse
import asyncio
from pathlib import Path
import signal
import sys

import yaml

from .agents.registry import AGENT_REGISTRY
from .orchestration.state_machine import StateMachine
from .runtime.agent import AgentResult, ExecutionContext
from .runtime.llm_client import LLMClient
from .runtime.logger import configure_logging
from .runtime.orchestrator import AgentRunner
from .schemas.state import StateYaml
from .schemas.validator import (
    build_declared_outputs_from_state_machine,
    register_builtin_task_checkers,
    validate_declared_outputs,
    validate_task_artifacts,
)
from .skills.agent import SkillAgent
from .skills.loader import register_skill_tools, resolve_skill
from .skills.runner import run_skill
from .tools.builtin import register_builtin_tools
from .tools.human_gate import CLIHumanInterface
from .tools.registry import ToolRegistry


def ensure_workspace_layout(workspace_dir: Path) -> None:
    """创建 runtime 运行所需的固定目录。"""
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


async def _maybe_run_selftest(args: argparse.Namespace, llm_client: LLMClient) -> None:
    """按需执行 endpoint 自检。"""
    if not getattr(args, "startup_selftest", False):
        return
    results = await llm_client.selftest()
    failed = {name: item for name, item in results.items() if not item.get("ok")}
    print(yaml.safe_dump({"startup_selftest": results}, allow_unicode=True, sort_keys=False))
    if failed:
        raise SystemExit("LLM startup selftest failed")


def _declared_outputs_for_task(sm: StateMachine, task_id: str) -> dict[str, str]:
    node = sm.nodes.get(task_id)
    return dict(node.outputs or {}) if node else {}


def _apply_runtime_output_validation(
    *,
    result: AgentResult,
    workspace_dir: Path,
    task_id: str,
    declared_outputs: dict[str, str],
) -> AgentResult:
    """在 agent 自己的 validate_outputs 之外，再做一层 runtime 级校验。"""
    if not result.ok:
        return result

    ok, errors = validate_task_artifacts(
        workspace_dir,
        task_id,
        declared_outputs=declared_outputs or None,
    )
    if ok:
        return result

    result.ok = False
    result.stop_reason = AgentResult.STOP_ERROR
    result.error = "Runtime artifact validation failed: " + "; ".join(errors)
    result.message = result.error
    return result


def _build_runner_for_node(
    *,
    node,
    ctx: ExecutionContext,
    registry: ToolRegistry,
    llm_client: LLMClient,
    human: CLIHumanInterface,
    skill_roots: list[Path],
) -> AgentRunner:
    """根据当前节点是普通 agent 还是 skill，创建 AgentRunner。"""
    if node.agent is not None:
        agent_cls = AGENT_REGISTRY.get(node.agent)
        if agent_cls is None:
            raise SystemExit(f"Unknown agent '{node.agent}' for task {node.task_id}")
        agent = agent_cls()
    elif node.skill is not None:
        skill = resolve_skill(node.skill, skill_roots)
        ctx.extra.setdefault("skill_dir", str(skill.skill_dir))
        agent = SkillAgent(
            skill=skill,
            available_tools=set(registry.available_names()),
            llm_profile=ctx.llm_override.profile,
        )
    else:
        raise SystemExit(f"Task {node.task_id} has neither agent nor skill configured")

    return AgentRunner(agent, registry, llm_client, human)


async def run_command(args: argparse.Namespace) -> int:
    """运行或恢复一个状态机 task。"""
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir)
    install_signal_handlers()
    register_builtin_task_checkers()

    skill_roots = _resolve_skill_roots(args, workspace_dir)
    registry = _build_tool_registry(skill_roots)
    _validate_agent_tools(registry)

    llm_client = LLMClient(Path(args.model_routing).resolve())
    await _maybe_run_selftest(args, llm_client)

    sm = StateMachine(
        Path(args.state_machine).resolve(),
        Path(args.gates).resolve() if args.gates else None,
    )
    state_path = workspace_dir / "state.yaml"
    state = sm.create_initial_state(project_id=args.project_id) if not state_path.exists() else None
    if state is None:
        state = StateYaml.load_yaml(state_path)
    if args.resume and state.status not in {"PAUSED", "WAITING_HUMAN"}:
        print("当前状态不是 PAUSED/WAITING_HUMAN，无法 resume。")
        return 1

    human = CLIHumanInterface()
    if state.pending_gate is not None:
        gate_result = await human.present_gate(
            gate_id=state.pending_gate.gate_id,
            presentation=state.pending_gate.presentation,
            options=state.pending_gate.options,
        )
        state = sm.resolve_pending_gate(state, gate_result)
        state.dump_yaml(state_path)

    current_node = sm.nodes[state.current_task]
    if current_node.terminal:
        state.status = "COMPLETED" if state.status != "FAILED" else state.status
        state.dump_yaml(state_path)
        print(yaml.safe_dump(state.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
        return 0 if state.status == "COMPLETED" else 1

    ctx = sm.build_execution_context(workspace_dir, state)
    state = sm.start_task(state, ctx.run_id)
    state.dump_yaml(state_path)

    runner = _build_runner_for_node(
        node=current_node,
        ctx=ctx,
        registry=registry,
        llm_client=llm_client,
        human=human,
        skill_roots=skill_roots,
    )
    try:
        result = await runner.run(ctx)
    except (asyncio.CancelledError, KeyboardInterrupt):
        state = sm.mark_interrupted(state)
        state.dump_yaml(state_path)
        print("任务已暂停，可用 `researchos resume` 恢复。")
        return 130

    result = _apply_runtime_output_validation(
        result=result,
        workspace_dir=workspace_dir,
        task_id=ctx.task_id,
        declared_outputs=_declared_outputs_for_task(sm, ctx.task_id),
    )

    state = sm.advance(state, result, workspace_dir=workspace_dir)
    state.dump_yaml(state_path)
    if state.pending_gate is not None:
        gate_result = await human.present_gate(
            gate_id=state.pending_gate.gate_id,
            presentation=state.pending_gate.presentation,
            options=state.pending_gate.options,
        )
        state = sm.resolve_pending_gate(state, gate_result)
        state.dump_yaml(state_path)

    print(
        yaml.safe_dump(
            {
                "ok": result.ok,
                "stop_reason": result.stop_reason,
                "state_status": state.status,
                "current_task": state.current_task,
                "trace_file": str(result.trace_file) if result.trace_file else None,
                "outputs": {k: str(v) for k, v in result.outputs_produced.items()},
                "error": result.error,
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    return 0 if result.ok else 1


async def run_skill_command(args: argparse.Namespace) -> int:
    """独立运行一个 skill。"""
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir)
    install_signal_handlers()
    register_builtin_task_checkers()

    skill_roots = _resolve_skill_roots(args, workspace_dir)
    registry = _build_tool_registry(skill_roots)
    _validate_agent_tools(registry)

    llm_client = LLMClient(Path(args.model_routing).resolve())
    await _maybe_run_selftest(args, llm_client)

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
    print(yaml.safe_dump({"ok": ok, "task": task_id, "errors": errors}, allow_unicode=True, sort_keys=False))
    return 0 if ok else 1


def status_command(args: argparse.Namespace) -> int:
    state = StateYaml.load_yaml((Path(args.workspace) / "state.yaml").resolve())
    print(yaml.safe_dump(state.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
    return 0


def trace_command(args: argparse.Namespace) -> int:
    trace_path = (Path(args.workspace) / "_runtime" / "traces" / f"{args.run_id}.jsonl").resolve()
    print(trace_path.read_text(encoding="utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="researchos")
    parser.add_argument("--workspace", default="./workspace")
    parser.add_argument("--project-id", default="demo-project")
    parser.add_argument("--state-machine", default="config/state_machine.yaml")
    parser.add_argument("--gates", default="config/gates.yaml")
    parser.add_argument("--model-routing", default="config/model_routing.yaml")
    parser.add_argument("--skills-root", action="append")
    parser.add_argument("--log-level", default="INFO")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--resume", action="store_true")
    run_parser.add_argument("--startup-selftest", action="store_true")

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--startup-selftest", action="store_true")

    subparsers.add_parser("status")

    selftest_parser = subparsers.add_parser("selftest")
    selftest_parser.add_argument("--profile", action="append")

    trace_parser = subparsers.add_parser("trace")
    trace_parser.add_argument("run_id")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--task")

    run_skill_parser = subparsers.add_parser("run-skill")
    run_skill_parser.add_argument("skill_name")
    run_skill_parser.add_argument("request", nargs="*")
    run_skill_parser.add_argument("--profile")
    run_skill_parser.add_argument("--startup-selftest", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(level=args.log_level)
    if args.command == "run":
        return asyncio.run(run_command(args))
    if args.command == "resume":
        args.resume = True
        return asyncio.run(run_command(args))
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
