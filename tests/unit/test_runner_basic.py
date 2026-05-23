import json

import pytest
import yaml

from researchos.agents.ideation import IdeationAgent
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


def write_valid_t4_artifacts(workspace):
    (workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}})
    )
    ideation_dir = workspace / "ideation"
    ideation_dir.mkdir(exist_ok=True)
    (ideation_dir / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n"
        + "这是一个可验证的研究假设，包含明确指标、背景、预期结果和风险。" * 40
    )
    (ideation_dir / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "total_estimated_cost_usd": 30.0,
                "budget_check": {"over_budget": False},
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Budgeted Experiment",
                        "title": "预算内实验",
                        "hypothesis_ref": "#H1",
                        "datasets": [{"name": "test", "split": "val", "size": 1000}],
                        "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                        "our_method": {
                            "name": "OurMethod",
                            "description": "Our approach",
                            "key_difference": "Different",
                        },
                        "metrics": [{"name": "accuracy", "primary": True, "target": 0.8}],
                        "success_criteria": [
                            {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                        ],
                        "steps": [{"step": 1, "action": "Run", "details": "Run experiment"}],
                        "compute_estimate": {
                            "gpu_hours": 10,
                            "gpu_type": "A100",
                            "estimated_cost_usd": 30,
                        },
                        "expected_duration_days": 2,
                    }
                ],
            }
        )
    )
    (ideation_dir / "risks.md").write_text(
        "# Top 3 风险\n\n## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    (ideation_dir / "idea_rationales.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea_id": "D1",
                        "hypothesis_refs": ["H1"],
                        "title": "假设1依据",
                        "idea_summary": "基于综述缺口提出预算内实验假设。",
                        "basis": {
                            "source_questions": ["Q1"],
                            "literature_observations": [
                                {
                                    "claim": "现有方法在目标约束下存在缺口。",
                                    "source": "synthesis.md: Q1 / [p1]",
                                    "strength": "direct",
                                }
                            ],
                            "missing_area_links": ["missing_areas.md: 需要验证"],
                            "comparison_table_signals": [],
                            "seed_idea_links": [],
                            "lens_insights": ["resource: 可在预算内验证"],
                        },
                        "reasoning": "输入材料共同指向一个可验证且预算内的假设。",
                        "confidence": "medium",
                        "limitations": ["仍需新颖性审计"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


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
async def test_runner_recovers_textual_dsml_tool_calls(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content=(
                        "我先调用 echo。\n"
                        "<｜DSML｜invoke name=\"echo\">"
                        "<｜DSML｜parameter name=\"text\">hi</｜DSML｜parameter>"
                        "</｜DSML｜invoke>"
                    )
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content=(
                        "任务完成。\n"
                        "<｜DSML｜invoke name=\"finish_task\">"
                        "<｜DSML｜parameter name=\"summary\">done</｜DSML｜parameter>"
                        "</｜DSML｜invoke>"
                    )
                )
            ),
        ]
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T0", run_id="r_dsml")
    runner = AgentRunner(MinimalAgent(), registry, llm, MockHumanInterface())
    result = await runner.run(ctx)
    assert result.ok
    assert result.stop_reason == AgentResult.STOP_FINISHED


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
async def test_t4_resume_prefinalize_skips_llm_when_artifacts_validate(tmp_workspace, registry):
    write_valid_t4_artifacts(tmp_workspace)
    llm = MockLLMClient(responses=[])
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T4",
        run_id="r_t4_resume_prefinalize",
        outputs_expected={
            "hypotheses": tmp_workspace / "ideation" / "hypotheses.md",
            "exp_plan": tmp_workspace / "ideation" / "exp_plan.yaml",
            "risks": tmp_workspace / "ideation" / "risks.md",
        },
    )
    runner = AgentRunner(IdeationAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert result.ok
    assert result.metadata["completion_mode"] == "t4_resume_prefinalize"
    assert llm.call_count == 0


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
