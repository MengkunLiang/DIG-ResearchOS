from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from researchos.cli import PreparedRuntime, main
from researchos.cli_runners import CompletePipelineRunner, SingleTaskRunner
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.agent import AgentResult
from researchos.schemas.state import StateYaml, TaskHistoryEntry
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
    state_text = (tmp_workspace / "state.yaml").read_text(encoding="utf-8")
    assert "COMPLETED" in state_text
    assert "DONE" in state_text


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
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

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
            "--no-banner",
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


def test_cli_init_workspace_creates_standard_tree(tmp_path: Path):
    workspace = tmp_path / "workspace"

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "--project-id",
            "demo-init",
            "init-workspace",
            "--topic",
            "runtime verification",
        ]
    )

    assert exit_code == 0
    assert (workspace / "_runtime" / "traces").exists()
    assert (workspace / "literature" / "paper_notes").exists()
    assert (workspace / "project.yaml").exists()


def test_cli_init_workspace_accepts_shared_options_after_subcommand(tmp_path: Path):
    workspace = tmp_path / "workspace"

    exit_code = main(
        [
            "--no-banner",
            "init-workspace",
            "--workspace",
            str(workspace),
            "--project-id",
            "demo-init",
            "--topic",
            "runtime verification",
        ]
    )

    assert exit_code == 0
    assert (workspace / "_runtime" / "traces").exists()
    assert (workspace / "project.yaml").exists()


def test_cli_trace_renders_human_readable_output(tmp_path: Path, capsys):
    workspace = tmp_path / "workspace"
    trace_dir = workspace / "_runtime" / "traces"
    trace_dir.mkdir(parents=True)
    (trace_dir / "demo.jsonl").write_text(
        "\n".join(
            [
                '{"seq":1,"ts":"2026-01-01T00:00:00+00:00","type":"run_start","payload":{"run_id":"demo","agent_name":"hello","project_id":"p1","task_id":"HELLO","workspace_dir":"/tmp/ws"}}',
                '{"seq":2,"ts":"2026-01-01T00:00:01+00:00","type":"message","payload":{"role":"assistant","content":"done","step":1,"metadata":{}}}',
                '{"seq":3,"ts":"2026-01-01T00:00:02+00:00","type":"run_end","payload":{"ok":true,"stop_reason":"finished","steps_used":1}}',
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["--workspace", str(workspace), "trace", "demo"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "RUN START" in captured.out
    assert "RUN END" in captured.out


def test_single_task_runner_injects_resume_extra_from_failed_history(tmp_workspace: Path):
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="T3",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )
    state = StateYaml(project_id="demo-project", current_task="T3")
    state.history.append(
        TaskHistoryEntry(
            task="T3",
            run_id="T3_single_prev",
            status="FAILED",
            started_at="2026-01-01T00:00:00Z",
        )
    )

    extra: dict[str, object] = {}
    runner._inject_resume_extra(extra, state)

    assert extra["is_resume"] is True
    assert extra["resumed_from_run_id"] == "T3_single_prev"
    assert extra["resume_reason"] == "retry_after_failure"


def test_single_task_record_finished_preserves_recoverable_stop_reason(tmp_workspace: Path):
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="T3",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )
    state = StateYaml(project_id="demo-project", current_task="T3")
    state = runner._record_started(state, "T3_single_run")
    result = AgentResult(
        ok=False,
        message="max steps",
        outputs_produced={},
        steps_used=10,
        tokens_in=11,
        tokens_out=12,
        cost_usd=0.0,
        duration_seconds=1.0,
        stop_reason=AgentResult.STOP_MAX_STEPS,
        error="Reached maximum allowed steps; paused so you can resume.",
    )

    state = runner._record_finished(state, result)

    assert state.status == "PAUSED"
    assert state.history[-1].status == "INTERRUPTED"
    assert state.history[-1].stop_reason == AgentResult.STOP_MAX_STEPS
    assert state.history[-1].tokens == 23
    assert state.last_error == result.error
