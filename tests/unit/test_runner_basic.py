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


class AskHumanAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(name="ask-human-test", model_tier="medium", tool_names=["ask_human", "finish_task"])
        )

    def system_prompt(self, ctx):
        return "You ask for human input."

    def initial_user_message(self, ctx):
        return "ask human"


class T35PrefinalizeAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(
                name="reader",
                model_tier="medium",
                tool_names=["write_file", "finish_task"],
                allowed_read_prefixes=["", "literature/"],
                allowed_write_prefixes=["literature/"],
            )
        )

    def system_prompt(self, ctx):
        return "synthesize"

    def initial_user_message(self, ctx):
        return "build synthesis"

    def validate_outputs(self, ctx):
        from researchos.agents.reader import ReaderAgent

        return ReaderAgent(mode="synthesize").validate_outputs(ctx)


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


def test_agent_runner_caps_pdf_tool_context_metadata():
    runner = AgentRunner(
        MinimalAgent(),
        ToolRegistry(),
        MockLLMClient(responses=[]),
        MockHumanInterface(),
    )
    content = "\n".join(
        [
            "[PDF extraction metadata]",
            "- total_pages: 10",
            "- preview_truncated_by_max_chars: false",
            "- covers_full_pdf: true",
            "- complete_pdf_read: true",
            "- next_start_page: none",
            "- note: If preview_truncated_by_max_chars=true, re-read a narrower page range before marking the note FULL-TEXT.",
            "",
            "x" * 60000,
        ]
    )

    capped, metadata = runner._cap_tool_content_for_context("extract_pdf_text", content)

    assert metadata == {
        "original_chars": len(content),
        "shown_chars": 50000,
        "reason": "tool_context_content_limit",
    }
    assert "- preview_truncated_by_max_chars: true" in capped
    assert "- complete_pdf_read: false" in capped
    assert "- covers_full_pdf: false" in capped
    assert "runtime_context_truncated: true" in capped


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
    (ideation_dir / "idea_scorecard.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea": {
                            "id": "D1",
                            "title": "假设1依据",
                            "pitch": "基于综述缺口提出预算内实验假设。",
                            "core_claim": "目标机制可以改善可观测指标。",
                            "target_problem": "现有方法在目标约束下存在缺口。",
                            "mechanism": "通过正则化梯度范数改善稀疏用户嵌入质量",
                            "prediction": "在稀疏用户子群上 Recall@20 提升 5%+",
                            "counterfactual": "如果机制不成立，选择性噪声关闭后指标应无显著差异",
                            "mechanism_family": "selective noise application",
                        },
                        "hypothesis_refs": ["H1"],
                        "source": {
                            "from_synthesis_section": "literature/synthesis.md: Q1",
                            "from_missing_area": "missing_areas.md: 需要验证",
                            "from_seed_idea": False,
                            "derived_from_previous": None,
                            "supporting_papers": [
                                {
                                    "title": "Test Paper",
                                    "claim_used": "现有方法在目标约束下存在缺口。",
                                }
                            ],
                            "trigger_observation": "输入材料共同指向该机制。",
                        },
                        "selection_rationale": {
                            "novelty_reason": "现有工作没有系统验证该机制。",
                            "feasibility_reason": "可以用小规模实验验证。",
                            "impact_reason": "该问题影响系统可靠性。",
                            "evaluability_reason": "指标和baseline清楚。",
                            "paper_story": "问题、方法和实验链路清楚。",
                        },
                        "closest_baselines": [
                            {
                                "name": "baseline1",
                                "similarity": "都处理目标任务。",
                                "difference": "本idea强调机制验证。",
                            }
                        ],
                        "scores": {
                            "novelty": 4,
                            "feasibility": 4,
                            "impact": 4,
                            "evaluability": 5,
                            "differentiation": 3,
                            "cost": 5,
                            "paper_shapability": 4,
                        },
                        "decision": {
                            "status": "selected",
                            "selected_reason": ["预算内", "指标清楚"],
                            "selected_by": "user",
                            "user_feedback": "确认该方向。",
                        },
                        "risks": [
                            {
                                "risk": "机制收益不明显",
                                "early_signal": "pilot接近baseline",
                                "mitigation": "增加消融",
                                "kill_criteria": "不优于baseline则停止",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "test validation set",
                            "baseline": "baseline1",
                            "metric": ["accuracy", "cost"],
                            "expected_signal": "同等成本下accuracy提升",
                            "estimated_cost_usd": 10.0,
                        },
                    },
                    {
                        "idea": {
                            "id": "D2",
                            "title": "被淘汰方向",
                            "pitch": "直接迁移已有方法。",
                            "core_claim": "简单迁移可能提升指标。",
                            "target_problem": "较弱问题设定。",
                            "mechanism": "see core_claim",
                            "prediction": "qualitative: outperforms baseline",
                            "counterfactual": "no clear counterfactual",
                            "mechanism_family": "direct transfer",
                        },
                        "hypothesis_refs": [],
                        "source": {
                            "from_synthesis_section": "literature/synthesis.md: Q2",
                            "from_missing_area": "missing_areas.md: 指标不清",
                            "from_seed_idea": False,
                            "derived_from_previous": None,
                            "supporting_papers": [
                                {
                                    "title": "Nearby Paper",
                                    "claim_used": "已有方法覆盖主要机制。",
                                }
                            ],
                            "trigger_observation": "弱缺口直接外推。",
                        },
                        "selection_rationale": {
                            "novelty_reason": "新颖性弱。",
                            "feasibility_reason": "可做但贡献有限。",
                            "impact_reason": "影响范围窄。",
                            "evaluability_reason": "评价指标不清。",
                            "paper_story": "论文故事不足。",
                        },
                        "closest_baselines": [
                            {
                                "name": "Nearby Paper",
                                "similarity": "机制和目标接近。",
                                "difference": "差异主要是场景变化。",
                            }
                        ],
                        "scores": {
                            "novelty": 2,
                            "feasibility": 4,
                            "impact": 2,
                            "evaluability": 2,
                            "differentiation": 2,
                            "cost": 4,
                            "paper_shapability": 2,
                        },
                        "decision": {
                            "status": "rejected",
                            "rejection_reason": ["和已有工作太接近"],
                            "can_revisit_if": "找到更强差异化机制。",
                        },
                        "risks": [
                            {
                                "risk": "创新性不足",
                                "early_signal": "高重叠工作",
                                "mitigation": "寻找机制差异",
                                "kill_criteria": "只有场景变化则放弃",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "small proxy set",
                            "baseline": "Nearby Paper",
                            "metric": ["accuracy"],
                            "expected_signal": "需要显著优于已有方法",
                            "estimated_cost_usd": 8.0,
                        },
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    (ideation_dir / "rejected_ideas.md").write_text(
        "# Rejected / Deferred Ideas\n\n"
        "## D2: 被淘汰方向\n\n"
        "- **Status**: rejected\n"
        "- **Why rejected**:\n"
        "  - 和已有工作太接近。\n"
        "- **Closest existing work**: Nearby Paper。\n"
        "- **Missing evidence / metric**: 缺少强差异化机制。\n"
        "- **Can revisit if**: 找到更强差异化机制。\n"
        "- **Cheap pilot that was not chosen**: proxy实验不足以证明贡献。\n"
    )
    (ideation_dir / "_family_distribution.md").write_text(
        "## Mechanism Family Distribution\n\n"
        "### Family: selective noise application\n"
        "- Candidates: D1\n"
        "- Mechanism similarity notes: single candidate\n\n"
        "### Family: direct transfer\n"
        "- Candidates: D2\n"
        "- Mechanism similarity notes: single candidate\n\n"
        "## Summary\n\n"
        "- Total candidates: 2\n"
        "- Distinct families: 2\n"
        "- Families with multiple candidates: 0\n\n"
        "## Recommended for Gate1 review\n\n"
        "Both families are distinct.\n"
    )
    (ideation_dir / "gate_decisions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "decisions": [
                    {
                        "gate_id": "T4-DECIDE-1",
                        "action": "select_direction",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": ["D2"],
                        "deferred_idea_ids": [],
                        "selected_by": "user",
                        "user_feedback": "确认该方向。",
                        "rationale": ["D1预算内且指标清楚", "D2和已有工作太接近"],
                    },
                    {
                        "gate_id": "T4-DECIDE-2",
                        "action": "confirm_plan",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": [],
                        "deferred_idea_ids": [],
                        "selected_by": "user",
                        "user_feedback": "确认计划。",
                        "rationale": ["实验预算可控"],
                    },
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
async def test_runner_pauses_on_unavailable_ask_human_without_second_llm_call(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="ask_human",
                            arguments={"question": "请选择", "suggestions": ["确认"]},
                            id="tc1",
                        )
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "bad"}, id="tc2")]
                )
            ),
        ]
    )
    human = MockHumanInterface(clarification_answer="")
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T4", run_id="r_human_pause")
    runner = AgentRunner(AskHumanAgent(), registry, llm, human)

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_INTERRUPTED
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_t35_workbench_prepares_artifacts_then_llm_writes_final(tmp_workspace, registry):
    literature = tmp_workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    for index in range(6):
        (notes_dir / f"paper_{index}.md").write_text(
            f"""# Paper {index}

- **ID**: paper_{index}
- **Venue**: TestConf (2025)
- **Status**: [FULL-TEXT]

## 2. Method Overview
Graph contrastive method for robust sparse recommendation.

## 3. Key Results
- Accuracy: 88.{index} [Evidence: p.4]

## 5. Limitations
- Sparse setting not fully explored.

## 6. Relevance to Our Research
- Directly related to sparse recommendation robustness.

## 7. Technical Details Worth Noting
- Lightweight perturbation strategy.

## 9. Weaknesses / Gaps
- Missing adaptive perturbation analysis.

## 11. My Questions
- Can adaptive perturbation improve sparse generalization?
""",
            encoding="utf-8",
        )
    (literature / "comparison_table.csv").write_text(
        "id,title,year,venue,method_family,dataset,key_metric,metric_value\n",
        encoding="utf-8",
    )
    (literature / "missing_areas.md").write_text("# 缺口\n稀疏鲁棒性不足。\n", encoding="utf-8")
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "literature/synthesis.md",
                                "content": (
                                    "# 文献综合\n\n"
                                    "## 方法家族分类\n"
                                    "LLM revised synthesis using [paper_0], [paper_1], [paper_2], [paper_3], [paper_4]. "
                                    "The first family groups papers that explicitly manipulate the target mechanism "
                                    "and report measurable effects under comparable evaluation settings. "
                                    "The second family contains lighter baselines that are useful as controls because "
                                    "they expose whether the proposed mechanism is necessary or merely correlated with "
                                    "performance. The final synthesis distinguishes these families by their causal "
                                    "claim, evidence strength, implementation cost, and failure conditions. "
                                    "This paragraph is intentionally long enough to exercise the validator while "
                                    "remaining a mock LLM-written synthesis rather than a deterministic tool draft.\n\n"
                                    "## 共同假设\n"
                                    "A shared assumption is that the measured improvement comes from the stated "
                                    "mechanism rather than from incidental regularization, data filtering, or budget "
                                    "changes. [paper_0] and [paper_1] support the mechanism claim but leave parts of "
                                    "the causal path under-tested. [paper_2] and [paper_3] report related observations "
                                    "with different controls. The final paper should therefore challenge the assumption "
                                    "with an experiment that separates the mechanism from the surrounding method. "
                                    "A second assumption is that aggregate metrics are representative of the target "
                                    "subgroups. The notes suggest this may be false because several papers mention "
                                    "failure modes that only appear under specific conditions.\n\n"
                                    "## 性能-效率前沿\n"
                                    "The frontier remains under-specified because the notes do not consistently report "
                                    "the same cost metrics. [paper_0] gives the clearest performance signal, [paper_1] "
                                    "is a likely efficient control, and [paper_4] is useful for checking whether the "
                                    "same effect survives under a different implementation. A serious T4 idea should "
                                    "therefore report both the primary task metric and at least one resource metric. "
                                    "This makes the eventual hypothesis evaluable without requiring a full-scale run.\n\n"
                                    "## 技术趋势\n"
                                    "The trend across these notes is a shift from adding larger components toward "
                                    "testing when the claimed mechanism is actually needed. Recent papers in the pool "
                                    "place more emphasis on ablations, subgroup behavior, and simpler controls. The "
                                    "trend is not yet a conclusion; it is a working reading of the evidence that T4 "
                                    "should preserve as an explicit uncertainty.\n\n"
                                    "## 可操作研究问题\n"
                                    "Q1: Which observable condition separates cases where the stated mechanism helps "
                                    "from cases where a simpler baseline is sufficient? Related papers include "
                                    "[paper_0], [paper_1], [paper_2], [paper_3], and [paper_4]. Q2: What is the "
                                    "cheapest pilot that can falsify the mechanism claim before T5 spends a larger "
                                    "budget? These questions are actionable because they name measurable outcomes, "
                                    "control papers, and failure criteria.\n"
                                ),
                            },
                            id="tc1",
                        )
                    ]
                )
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
        task_id="T3.5",
        run_id="r_t35_workbench",
        mode="synthesize",
        outputs_expected={"synthesis": literature / "synthesis.md"},
    )
    runner = AgentRunner(T35PrefinalizeAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert result.ok
    assert "completion_mode" not in result.metadata
    assert llm.call_count == 2
    assert (literature / "synthesis_workbench.json").exists()
    assert (literature / "synthesis.md").exists()
    assert "LLM revised synthesis" in (literature / "synthesis.md").read_text(encoding="utf-8")


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
