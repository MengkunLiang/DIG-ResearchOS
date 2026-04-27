import pytest

from researchos.runtime.agent import Agent, AgentResult, AgentSpec, BudgetOverride, ExecutionContext
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


class SyncPreHookAgent(Agent):
    def __init__(self):
        def sync_pre_hook(_ctx):
            return False, "pre-hook blocked run"

        super().__init__(
            AgentSpec(
                name="sync-prehook-test",
                model_tier="medium",
                tool_names=["finish_task"],
                pre_hooks=[sync_pre_hook],
            )
        )

    def system_prompt(self, ctx):
        return "You are a test agent."

    def initial_user_message(self, ctx):
        return "finish"


class RecordingLLMClient(MockLLMClient):
    def __init__(self, responses):
        super().__init__(responses=responses)
        self.chat_kwargs: list[dict] = []

    async def chat(self, **kwargs):
        self.chat_kwargs.append(kwargs)
        return await super().chat(**kwargs)


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


@pytest.mark.asyncio
async def test_runner_passes_global_llm_timeout_and_retry_settings(tmp_workspace, registry, monkeypatch):
    monkeypatch.setattr(
        "researchos.runtime.orchestrator.get_global_timeout",
        lambda: {"llm_call": 77, "max_agent_runtime": 999, "max_tool_call": 30},
    )
    monkeypatch.setattr(
        "researchos.runtime.orchestrator.get_retry_policy",
        lambda: {"llm_retries": 4, "llm_retry_delay": 1.25},
    )

    llm = RecordingLLMClient(
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
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T0", run_id="r_cfg")
    runner = AgentRunner(MinimalAgent(), registry, llm, MockHumanInterface())
    result = await runner.run(ctx)

    assert result.ok
    assert llm.chat_kwargs
    assert all(item["timeout"] == 77 for item in llm.chat_kwargs)
    assert all(item["max_retries_per_model"] == 4 for item in llm.chat_kwargs)
    assert all(item["retry_base_delay"] == 1.25 for item in llm.chat_kwargs)


@pytest.mark.asyncio
async def test_budget_extension_gate_allows_t5_to_continue(tmp_workspace, registry):
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
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T5",
        run_id="r_budget_extend",
        budget_override=BudgetOverride(max_steps=0),
    )
    human = MockHumanInterface(gate_choices=[{"option_id": "extend", "captured": {}}])
    runner = AgentRunner(MinimalAgent(), registry, llm, human)
    runner.budget_escalation_policy = {
        "enabled": True,
        "tasks": ["T5"],
        "max_extensions_per_run": 1,
        "steps_increase_ratio": 1.0,
        "token_increase_ratio": 0.5,
        "wall_seconds_increase_ratio": 0.5,
    }

    result = await runner.run(ctx)

    assert result.ok
    assert any(call[0] == "gate" for call in human.calls)


@pytest.mark.asyncio
async def test_budget_extension_gate_can_stop_run(tmp_workspace, registry):
    llm = MockLLMClient(responses=[])
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T5",
        run_id="r_budget_stop",
        budget_override=BudgetOverride(max_steps=0),
    )
    human = MockHumanInterface(gate_choices=[{"option_id": "stop", "captured": {}}])
    runner = AgentRunner(MinimalAgent(), registry, llm, human)
    runner.budget_escalation_policy = {
        "enabled": True,
        "tasks": ["T5"],
        "max_extensions_per_run": 1,
        "steps_increase_ratio": 1.0,
        "token_increase_ratio": 0.5,
        "wall_seconds_increase_ratio": 0.5,
    }

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_BUDGET
    assert any(call[0] == "gate" for call in human.calls)


@pytest.mark.asyncio
async def test_sync_pre_hook_failure_is_reported_cleanly(tmp_workspace, registry):
    llm = MockLLMClient(responses=[])
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T9",
        run_id="r_sync_hook",
    )
    runner = AgentRunner(SyncPreHookAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_ERROR
    assert result.error == "pre-hook blocked run"
