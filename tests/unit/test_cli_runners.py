from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from researchos.cli import main
from researchos.cli_runners import CompletePipelineRunner, SingleTaskRunner
from researchos.orchestration.state_machine import StateMachine
from researchos.testing.mocks import (
    FakeLLMMessage,
    FakeRawCompletion,
    FakeToolCall,
    MockLLMClient,
)
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _hello_llm() -> MockLLMClient:
    """构造一组能驱动 HelloAgent 完成任务的 mock LLM 响应。"""

    return MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={"path": "hello.txt", "content": "Hello, Runtime!"},
                            id="tc_write",
                        )
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="finish_task",
                            arguments={"summary": "hello finished"},
                            id="tc_finish",
                        )
                    ]
                )
            ),
        ]
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


@pytest.mark.asyncio
async def test_single_task_runner_runs_hello_happy_path(tmp_workspace: Path):
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="HELLO",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )

    exit_code = await runner.run()

    assert exit_code == 0
    assert (tmp_workspace / "hello.txt").read_text(encoding="utf-8") == "Hello, Runtime!"


@pytest.mark.asyncio
async def test_complete_pipeline_runner_advances_until_completed(tmp_workspace: Path):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: HELLO
        states:
          HELLO:
            agent: hello
            outputs:
              hello_file: hello.txt
            next_on_success: done
          done:
            terminal: true
        """,
    )
    runner = CompletePipelineRunner(
        workspace=tmp_workspace,
        state_machine=StateMachine(config),
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )

    exit_code = await runner.run(project_id="demo-project")

    assert exit_code == 0
    assert (tmp_workspace / "state.yaml").exists()
    assert "COMPLETED" in (tmp_workspace / "state.yaml").read_text(encoding="utf-8")


def test_cli_run_task_command_dispatches(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    observed: dict[str, object] = {}

    async def fake_prepare_runtime(args, workspace_dir):
        observed["workspace"] = workspace_dir
        return [], ToolRegistry(), object()

    async def fake_run(self):
        observed["task_id"] = self.task_id
        observed["from_workspace"] = self.from_workspace
        observed["profile"] = self.override_profile
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.SingleTaskRunner.run", fake_run)

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "run-task",
            "HELLO",
            "--profile",
            "audit",
        ]
    )

    assert exit_code == 0
    assert observed["workspace"] == workspace.resolve()
    assert observed["task_id"] == "HELLO"
    assert observed["from_workspace"] is None
    assert observed["profile"] == "audit"
