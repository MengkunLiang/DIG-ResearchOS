from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

# 允许直接用 `python scripts/debug_hello_agent.py` 运行，而不要求用户先做
# `pip install -e .`。这对 README 中的最小调试命令和集成测试都很重要。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from researchos.agents.hello import HelloAgent
from researchos.runtime.agent import ExecutionContext
from researchos.runtime.logger import configure_logging
from researchos.runtime.orchestrator import AgentRunner
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def build_mock_llm() -> MockLLMClient:
    return MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(tool_calls=[FakeToolCall(name="echo", arguments={"text": "hello"}, id="tc1")]),
                prompt_tokens=10,
                completion_tokens=5,
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(tool_calls=[FakeToolCall(name="echo", arguments={"text": "world"}, id="tc2")]),
                prompt_tokens=10,
                completion_tokens=5,
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={"path": "hello.txt", "content": "Hello, Runtime!"},
                            id="tc3",
                        )
                    ]
                ),
                prompt_tokens=10,
                completion_tokens=5,
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "Hello agent completed"}, id="tc4")]
                ),
                prompt_tokens=10,
                completion_tokens=5,
            ),
        ]
    )


async def main_async(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    (workspace / "_runtime" / "traces").mkdir(parents=True, exist_ok=True)
    (workspace / "_runtime" / "logs").mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry()
    register_builtin_tools(registry)
    llm = build_mock_llm() if args.mock else None
    if llm is None:
        raise SystemExit("当前脚本最小实现只支持 --mock")

    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="hello-demo",
        task_id="HELLO",
        run_id="hello_debug_run",
        outputs_expected={"hello_file": workspace / "hello.txt"},
    )
    runner = AgentRunner(HelloAgent(), registry, llm, MockHumanInterface())
    result = await runner.run(ctx)
    print(
        {
            "ok": result.ok,
            "stop_reason": result.stop_reason,
            "steps": result.steps_used,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "cost_usd": result.cost_usd,
            "outputs": {k: str(v) for k, v in result.outputs_produced.items()},
            "trace_file": str(result.trace_file) if result.trace_file else None,
        }
    )
    return 0 if result.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--workspace", default="./workspace")
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
