from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import signal
import sys

import yaml

from .agents.hello import HelloAgent
from .orchestration.state_machine import StateMachine
from .runtime.logger import configure_logging
from .runtime.orchestrator import AgentRunner
from .runtime.llm_client import LLMClient
from .schemas.state import StateYaml
from .tools.builtin import register_builtin_tools
from .tools.human_gate import CLIHumanInterface
from .tools.registry import ToolRegistry


AGENTS = {"hello": HelloAgent}


def ensure_workspace_layout(workspace_dir: Path) -> None:
    (workspace_dir / "_runtime" / "traces").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "_runtime" / "logs").mkdir(parents=True, exist_ok=True)


def install_signal_handlers() -> None:
    loop = asyncio.get_running_loop()

    def cancel_all() -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, cancel_all)
        except NotImplementedError:
            signal.signal(sig, lambda *_: cancel_all())


async def run_command(args: argparse.Namespace) -> int:
    workspace_dir = Path(args.workspace).resolve()
    ensure_workspace_layout(workspace_dir)
    install_signal_handlers()

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

    if current_node.agent is None:
        print(f"当前任务 {state.current_task} 没有绑定 agent。")
        return 1
    agent_cls = AGENTS[current_node.agent]
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        agent_cls(),
        registry,
        LLMClient(Path(args.model_routing).resolve()),
        human,
    )
    try:
        result = await runner.run(ctx)
    except (asyncio.CancelledError, KeyboardInterrupt):
        state = sm.mark_interrupted(state)
        state.dump_yaml(state_path)
        print("任务已暂停，可用 `researchos resume` 恢复。")
        return 130

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

    print(yaml.safe_dump(
        {
            "ok": result.ok,
            "stop_reason": result.stop_reason,
            "state_status": state.status,
            "current_task": state.current_task,
            "trace_file": str(result.trace_file) if result.trace_file else None,
            "outputs": {k: str(v) for k, v in result.outputs_produced.items()},
        },
        allow_unicode=True,
        sort_keys=False,
    ))
    return 0 if result.ok else 1


async def selftest_command(args: argparse.Namespace) -> int:
    client = LLMClient(Path(args.model_routing).resolve())
    results = await client.selftest(args.profile or None)
    print(yaml.safe_dump(results, allow_unicode=True, sort_keys=False))
    return 0 if all(item.get("ok") for item in results.values()) else 1


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
    parser.add_argument("--log-level", default="INFO")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--resume", action="store_true")

    subparsers.add_parser("resume")
    subparsers.add_parser("status")
    selftest_parser = subparsers.add_parser("selftest")
    selftest_parser.add_argument("--profile", action="append")

    trace_parser = subparsers.add_parser("trace")
    trace_parser.add_argument("run_id")
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
    if args.command == "status":
        return status_command(args)
    if args.command == "selftest":
        return asyncio.run(selftest_command(args))
    if args.command == "trace":
        return trace_command(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
