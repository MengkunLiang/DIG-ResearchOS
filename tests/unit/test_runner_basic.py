import pytest

from researchos.runtime.agent import Agent, AgentResult, AgentSpec, ExecutionContext
from researchos.runtime.orchestrator import AgentRunner
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


class MinimalAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(name="test", model_tier="medium", tool_names=["echo", "finish_task"])
        )

    def system_prompt(self, ctx):
        return "You are a test agent."

    def initial_user_message(self, ctx):
        return "echo then finish"


@pytest.fixture
def registry():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


@pytest.mark.asyncio
async def test_happy_path_echo_then_finish(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(tool_calls=[FakeToolCall(name="echo", arguments={"text": "hi"}, id="tc1")])
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "done"}, id="tc2")]
                )
            ),
        ]
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T0", run_id="r1")
    runner = AgentRunner(MinimalAgent(), registry, llm, MockHumanInterface())
    result = await runner.run(ctx)
    assert result.ok
    assert result.stop_reason == AgentResult.STOP_FINISHED


@pytest.mark.asyncio
async def test_empty_reply_storm_stops(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(message=FakeLLMMessage()),
            FakeRawCompletion(message=FakeLLMMessage()),
            FakeRawCompletion(message=FakeLLMMessage()),
        ]
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T0", run_id="r2")
    runner = AgentRunner(MinimalAgent(), registry, llm, MockHumanInterface())
    result = await runner.run(ctx)
    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_ERROR


@pytest.mark.asyncio
async def test_tool_param_validation_fails_gracefully(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="echo", arguments={"wrong_field": "hi"}, id="tc1")]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(tool_calls=[FakeToolCall(name="echo", arguments={"text": "hi"}, id="tc2")])
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "done"}, id="tc3")]
                )
            ),
        ]
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T0", run_id="r3")
    runner = AgentRunner(MinimalAgent(), registry, llm, MockHumanInterface())
    result = await runner.run(ctx)
    assert result.ok

