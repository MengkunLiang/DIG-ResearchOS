import json

import pytest
import yaml

from researchos.agents.ideation import IdeationAgent
from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.agents.reader import ReaderAgent
from researchos.agents.writer import WriterAgent
from researchos.runtime.agent import (
    Agent,
    AgentResult,
    AgentSpec,
    BudgetOverride,
    ExecutionContext,
    resolve_effective_config,
)
from researchos.runtime.orchestrator import AgentRunner
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def _long_text(seed: str, repeat: int = 90) -> str:
    return (seed + " ") * repeat


def _write_t4_stage_visibility_artifacts(ideation_dir):
    pass1_candidates = [
        {
            "id": "D1",
            "title": "假设1依据",
            "generation_stage": "mainline",
            "idea_origin": "free_reasoning",
            "constraint_status": "mainline",
            "pitch": "基于综述缺口提出预算内实验假设。",
            "core_claim": "目标机制可以改善可观测指标。",
            "mechanism": "通过正则化梯度范数改善稀疏用户嵌入质量",
            "prediction": "在稀疏用户子群上 Recall@20 提升 5%+",
            "counterfactual": "如果机制不成立，选择性噪声关闭后指标应无显著差异",
            "basis_summary": "LLM 综合 synthesis、comparison_table 和预算约束后提出的主线候选方向。",
        },
        {
            "id": "D1b",
            "title": "证据驱动替代候选",
            "generation_stage": "mainline",
            "idea_origin": "evidence_driven",
            "constraint_status": "mainline",
            "pitch": "从 paper notes 的共同限制形成替代方向。",
            "core_claim": "证据驱动的机制干预能改善目标指标。",
            "mechanism": "针对共同失败模式调整训练信号可降低目标误差",
            "prediction": "目标失败子群上的 accuracy 相对 baseline 提升",
            "counterfactual": "若失败来自数据噪声而非训练信号，干预不会改善子群指标",
            "basis_summary": "从 paper notes 的共同限制和实验可行性出发形成的第二个主线候选。",
        },
        {
            "id": "D2",
            "title": "被淘汰方向",
            "generation_stage": "mainline",
            "idea_origin": "seed_refinement",
            "constraint_status": "mainline",
            "pitch": "直接迁移已有方法。",
            "core_claim": "简单迁移可能提升指标。",
            "mechanism": "直接迁移复用已有表示偏置，在新场景中可能影响目标指标",
            "prediction": "如果迁移偏置有效，新场景accuracy应相对baseline提升",
            "counterfactual": "如果迁移偏置无效，替换为简单baseline后指标不会下降",
            "basis_summary": "由 seed idea 细化而来，但因新颖性和评价链条不足被淘汰。",
        },
        {
            "id": "S1",
            "title": "反向操作补充候选",
            "generation_stage": "supplement",
            "idea_origin": "reverse_operation",
            "constraint_status": "supplement",
            "pitch": "检查移除关键机制时指标是否下降。",
            "core_claim": "反向操作可以检验机制是否必要。",
            "mechanism": "移除常规增强后若指标不降说明原增强并非关键机制",
            "prediction": "关闭增强后目标指标保持稳定或仅轻微下降",
            "counterfactual": "若增强确实必要，关闭后目标指标显著下降",
            "basis_summary": "作为 coverage supplement，检查移除关键机制时指标是否下降。",
        },
    ]
    pass2_reviews = [
        {
            "idea_id": "D1",
            "screening_recommendation": "proceed",
            "visible_to_gate": True,
            "counterfactual_check": "independent",
            "counterfactual_note": "抽掉最近论文后仍有独立机制论证。",
            "nearest_prior_work": {"work": "Smith2024", "distance": "moderate"},
            "novelty_signal": "adjacent_zone",
            "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "medium"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "improvement",
                "routine_risk": False,
                "reframe_needed": False,
                "why": "有明确机制和预算内实验。",
            },
            "grounding_notes": ["可以进入 Gate1。"],
            "selection_warning": "none",
        },
        {
            "idea_id": "D1b",
            "screening_recommendation": "defer_recommended",
            "visible_to_gate": True,
            "counterfactual_check": "survives_weakened",
            "counterfactual_note": "抽掉最近工作后仍成立但证据会弱化。",
            "nearest_prior_work": {"work": "Nearby Paper", "distance": "distant"},
            "novelty_signal": "no_nearby_cluster",
            "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "high_uncertainty"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "improvement",
                "routine_risk": False,
                "reframe_needed": True,
                "why": "需要进一步收紧机制。",
            },
            "grounding_notes": ["可见但建议暂缓。"],
            "selection_warning": "若选择需要重构机制。",
        },
        {
            "idea_id": "D2",
            "screening_recommendation": "reject_recommended",
            "visible_to_gate": True,
            "counterfactual_check": "collapses",
            "counterfactual_note": "抽掉最相近工作后只剩应用迁移。",
            "nearest_prior_work": {"work": "Nearby Paper", "distance": "very_close"},
            "novelty_signal": "marginal_zone",
            "novelty_check": {"prior_art": "closest_known", "closest_baselines": ["Nearby Paper"], "novelty_risk": "low"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "routine",
                "routine_risk": True,
                "reframe_needed": True,
                "why": "贡献更像应用迁移。",
            },
            "grounding_notes": ["Pass2 建议淘汰，但 Gate1 仍可见。"],
            "selection_warning": "若选择必须先重构 contribution character。",
        },
        {
            "idea_id": "S1",
            "screening_recommendation": "revise_before_selection",
            "visible_to_gate": True,
            "counterfactual_check": "survives_weakened",
            "counterfactual_note": "作为反向操作仍可服务机制检验。",
            "nearest_prior_work": {"work": "none", "distance": "none_found"},
            "novelty_signal": "no_nearby_cluster",
            "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "medium"},
            "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []},
            "contribution_check": {
                "contribution_type": "improvement",
                "routine_risk": False,
                "reframe_needed": True,
                "why": "需要从普通消融重构成机制检验。",
            },
            "grounding_notes": ["补充候选可见。"],
            "selection_warning": "选择前要说明为何不是普通消融。",
        },
    ]
    (ideation_dir / "_pass1_forward_candidates.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "raw_forward_generation_candidates_visible_to_gate",
                "candidates": pass1_candidates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ideation_dir / "_pass2_grounding_review.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "grounding_review_flags_not_deletion_or_final_quality_gate",
                "reviews": pass2_reviews,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ideation_dir / "_candidate_directions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "gate_visible_candidate_pool_after_grounding_review",
                "candidates": [
                    {
                        **candidate,
                        "pass2_screening": {
                            "screening_recommendation": next(
                                review["screening_recommendation"]
                                for review in pass2_reviews
                                if review["idea_id"] == candidate["id"]
                            ),
                            "visible_to_gate": True,
                            "selection_warning": next(
                                review["selection_warning"]
                                for review in pass2_reviews
                                if review["idea_id"] == candidate["id"]
                            ),
                        },
                        "gate_visibility": "visible",
                        "can_select_despite_risk": True,
                    }
                    for candidate in pass1_candidates
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ideation_dir / "_gate1_selection_brief.md").write_text(
        "# Gate1 Selection Brief\n\n"
        "## Pass1 candidates\n\n"
        "- D1: 假设1依据，Pass2 proceed。\n"
        "- D1b: 证据驱动替代候选，Pass2 defer_recommended，仍可选择但需要重构机制。\n"
        "- D2: 被淘汰方向，Pass2 reject_recommended，仍可选择但必须重构 contribution character。\n"
        "- S1: 反向操作补充候选，Pass2 revise_before_selection，适合作为补充。\n\n"
        "## Pass2 warnings\n\n"
        "- D1: none。\n"
        "- D1b: 若选择需要重构机制。\n"
        "- D2: 若选择必须先重构 contribution character。\n"
        "- S1: 选择前要说明为何不是普通消融。\n\n"
        "## Merge options\n\n"
        "- 合并 D1+D1b：用 D1 的清晰机制吸收 D1b 的失败子群证据。\n"
        "- 合并 D1+S1：把 S1 作为 D1 的反向操作消融。\n\n"
        "## 集中度提示\n\n"
        "候选没有过度集中在单一论文；这是软提示。\n\n"
        "## Origin 分布\n\n"
        "free_reasoning: 1; evidence_driven: 1; seed_refinement: 1; reverse_operation: 1。\n\n"
        "## Novelty-Utility 谱系排布\n\n"
        "高新颖高风险: S1；中新颖高可行: D1, D1b；低新颖高可行: D2。\n\n"
        "用户可选择 D1、选择 D2 并重构、合并 D1+D1b、合并 D1+S1、新想法或重新分析。\n",
        encoding="utf-8",
    )


def write_valid_t8_section_plan_inputs(workspace):
    drafts = workspace / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (workspace / "project.yaml").write_text("target_venue: neurips2026\n", encoding="utf-8")
    (drafts / "outline.md").write_text(
        "# Outline\n\n## Introduction\nFrame the problem.\n\n## Method\nDescribe the mechanism.\n\n"
        "## Experiments\nReport results.\n",
        encoding="utf-8",
    )
    (drafts / "manuscript_resource_index.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "artifacts": [
                    {"path": "ideation/hypotheses.md"},
                    {"path": "experiments/results_summary.json"},
                ],
                "bib_keys": ["smith2024"],
                "result_metrics": [{"metric": "accuracy", "value": 0.82}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sections = [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]
    (drafts / "section_plan.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "sections": [
                    {
                        "id": section,
                        "title": section.title(),
                        "required_inputs": ["drafts/manuscript_resource_index.json"],
                        "available_inputs": ["drafts/manuscript_resource_index.json"],
                        "missing_inputs": [],
                        "cdr_responsibility": "mechanical section responsibility seed",
                    }
                    for section in sections
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (drafts / "evidence_plan.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "claim_slots": [
                    {
                        "slot_id": "intro_problem_gap",
                        "section": "introduction",
                        "cdr_field": "problem_frame",
                        "candidate_evidence": ["ideation/hypotheses.md"],
                    },
                    {
                        "slot_id": "experiments_main_result",
                        "section": "experiments",
                        "cdr_field": "evaluation_mode",
                        "candidate_evidence": ["experiments/results_summary.json"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (drafts / "figure_table_plan.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "planned_visuals": [
                    {
                        "figure_id": "fig:main_results",
                        "intended_section": "experiments",
                        "source_artifacts": ["experiments/results_summary.json"],
                    },
                    {
                        "table_id": "tab:main_results",
                        "intended_section": "experiments",
                        "source_artifacts": ["experiments/results_summary.json"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (drafts / "alignment_matrix.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "alignment_matrix_seed_not_final_scientific_judgment",
                "rows": [
                    {
                        "cid": "C1",
                        "hypothesis": "H1",
                        "motivation": "test motivation",
                        "contribution": "test contribution",
                        "contribution_type": "improvement",
                        "related_gap": {"papers": ["smith2024"], "tension": "test tension"},
                        "counterfactual": "independent",
                        "counterfactual_note": "test counterfactual note",
                        "nearest_prior_work": {"work": "smith2024", "distance": "moderate"},
                        "novelty_signal": "adjacent_zone",
                        "design_choice": "test design choice",
                        "experiment": {"rq": "RQ1", "result_metric": "accuracy"},
                        "analysis": "test analysis",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (drafts / "paper_state.json").write_text(
        json.dumps({"semantics": "old_invalid_state", "sections": {}}),
        encoding="utf-8",
    )


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


class AlwaysInvalidAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(
                name="always-invalid",
                model_tier="medium",
                tool_names=["finish_task"],
                max_validation_retries=2,
            )
        )

    def system_prompt(self, ctx):
        return "You always fail validation."

    def initial_user_message(self, ctx):
        return "finish"

    def validate_outputs(self, ctx):
        return False, "missing artifact"


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


class RecoverablePreHookAgent(Agent):
    def __init__(self):
        def sync_pre_hook(_ctx):
            return False, "WAITING_ENVIRONMENT: docker missing"

        super().__init__(
            AgentSpec(
                name="recoverable-prehook-test",
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
                            "cdr_tuple": {
                                "problem_frame": "稀疏用户推荐中的扰动策略缺少活跃度感知设计。",
                                "design_rationale": "活跃度不同的用户嵌入承受噪声的能力不同，因此应按用户活跃度调节扰动强度。",
                                "artifact": "一个按用户活跃度选择扰动强度的图推荐训练模块。",
                                "design_principles": ["按子群风险分配扰动", "用消融隔离机制"],
                                "data_view": "用户按交互密度划分的推荐子群。",
                                "evaluation_mode": "主指标加稀疏用户子群消融。",
                                "contribution_type": "improvement",
                                "boundary_conditions": ["稀疏度差异明显的数据集"],
                                "cross_paper_tension": ["均匀扰动和子群稳健性之间的张力"],
                            },
                            "contribution_strength": 4,
                        },
                        "hypothesis_refs": ["H1"],
                        "source": {
                            "idea_origin": "free_reasoning",
                            "constraint_status": "mainline",
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
                            "contribution_character": "如果该假设成立，领域将从统一扰动默认设定转向按用户活跃度分配增强强度的设计原则。",
                        },
                        "closest_baselines": [
                            {
                                "name": "baseline1",
                                "similarity": "都处理目标任务。",
                                "difference": "本idea强调机制验证。",
                            }
                        ],
                        "counterfactual_check": "independent",
                        "counterfactual_note": "抽掉单篇相关论文后，该方向仍可由综述张力独立推出。",
                        "nearest_prior_work": {"work": "baseline1", "distance": "moderate"},
                        "novelty_signal": "adjacent_zone",
                        "scores": {
                            "novelty": 4,
                            "feasibility": 4,
                            "impact": 4,
                            "evaluability": 5,
                            "differentiation": 3,
                            "cost": 5,
                            "contribution_strength": 4,
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
                            "id": "D1b",
                            "title": "证据驱动替代候选",
                            "pitch": "从 paper notes 的共同限制形成替代方向。",
                            "core_claim": "证据驱动的机制干预能改善目标指标。",
                            "target_problem": "共同失败模式尚未被验证。",
                            "mechanism": "针对共同失败模式调整训练信号可降低目标误差",
                            "prediction": "目标失败子群上的 accuracy 相对 baseline 提升",
                            "counterfactual": "若失败来自数据噪声而非训练信号，干预不会改善子群指标",
                            "mechanism_family": "failure-mode intervention",
                            "cdr_tuple": {
                                "problem_frame": "共同失败模式尚未被验证。",
                                "design_rationale": "从失败模式出发能更直接定位机制。",
                                "artifact": "失败模式干预模块。",
                                "design_principles": ["机制定位"],
                                "data_view": "失败子群验证集。",
                                "evaluation_mode": "子群指标加消融。",
                                "contribution_type": "improvement",
                                "boundary_conditions": ["失败模式可观测"],
                            },
                            "contribution_strength": 2,
                        },
                        "hypothesis_refs": [],
                        "source": {
                            "idea_origin": "evidence_driven",
                            "constraint_status": "mainline",
                            "from_synthesis_section": "literature/synthesis.md: Q1",
                            "from_missing_area": "missing_areas.md: 失败子群",
                            "from_seed_idea": False,
                            "derived_from_previous": None,
                            "supporting_papers": [
                                {
                                    "title": "Failure Paper",
                                    "claim_used": "存在共同失败模式。",
                                }
                            ],
                            "trigger_observation": "paper notes 显示共同失败模式但机制未验证。",
                        },
                        "selection_rationale": {
                            "novelty_reason": "需要进一步收紧机制。",
                            "feasibility_reason": "可用小规模失败子群测试。",
                            "impact_reason": "有潜在价值但尚不如 D1 清楚。",
                            "evaluability_reason": "可评价但指标链较弱。",
                            "contribution_character": "如果成立，会把失败模式从现象描述推进到可干预机制。",
                        },
                        "closest_baselines": [],
                        "counterfactual_check": "survives_weakened",
                        "counterfactual_note": "抽掉相关论文后仍可成立但支撑变弱。",
                        "nearest_prior_work": {"work": "Failure Paper", "distance": "moderate"},
                        "novelty_signal": "adjacent_zone",
                        "scores": {
                            "novelty": 3,
                            "feasibility": 3,
                            "impact": 3,
                            "evaluability": 3,
                            "differentiation": 3,
                            "cost": 4,
                            "contribution_strength": 2,
                        },
                        "decision": {
                            "status": "deferred",
                            "rejection_reason": ["Pass2 建议暂缓，需要进一步收紧机制。"],
                            "can_revisit_if": "如果 D1 pilot 失败但失败子群信号强，可以重访。",
                        },
                        "risks": [
                            {
                                "risk": "机制过宽",
                                "early_signal": "多个失败解释都成立",
                                "mitigation": "缩小子群",
                                "kill_criteria": "无法形成单一反事实",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "failure subset",
                            "baseline": "baseline1",
                            "metric": ["accuracy"],
                            "expected_signal": "失败子群指标提升",
                            "estimated_cost_usd": 6.0,
                        },
                    },
                    {
                        "idea": {
                            "id": "D2",
                            "title": "被淘汰方向",
                            "pitch": "直接迁移已有方法。",
                            "core_claim": "简单迁移可能提升指标。",
                            "target_problem": "较弱问题设定。",
                            "mechanism": "直接迁移复用已有表示偏置，在新场景中可能影响目标指标",
                            "prediction": "如果迁移偏置有效，新场景accuracy应相对baseline提升",
                            "counterfactual": "如果迁移偏置无效，替换为简单baseline后指标不会下降",
                            "mechanism_family": "direct transfer",
                            "cdr_tuple": {
                                "problem_frame": "简单场景迁移缺少设计差异。",
                                "design_rationale": "复用已有表示可能不改变领域设计原则。",
                                "artifact": "场景迁移 baseline。",
                                "data_view": "常规任务数据。",
                                "evaluation_mode": "普通主指标比较。",
                                "contribution_type": "routine",
                                "boundary_conditions": ["仅适合记录为被拒候选"],
                            },
                            "contribution_strength": 1,
                        },
                        "hypothesis_refs": [],
                        "source": {
                            "idea_origin": "seed_refinement",
                            "constraint_status": "mainline",
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
                            "contribution_character": "如果成立也主要是应用迁移，不能改变领域的设计判断。",
                        },
                        "closest_baselines": [
                            {
                                "name": "Nearby Paper",
                                "similarity": "机制和目标接近。",
                                "difference": "差异主要是场景变化。",
                            }
                        ],
                        "counterfactual_check": "collapses",
                        "counterfactual_note": "抽掉 Nearby Paper 后，该方向基本只剩应用迁移。",
                        "nearest_prior_work": {"work": "Nearby Paper", "distance": "very_close"},
                        "novelty_signal": "marginal_zone",
                        "scores": {
                            "novelty": 2,
                            "feasibility": 4,
                            "impact": 2,
                            "evaluability": 2,
                            "differentiation": 2,
                            "cost": 4,
                            "contribution_strength": 1,
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
                    {
                        "idea": {
                            "id": "S1",
                            "title": "反向操作补充候选",
                            "pitch": "检查移除关键机制时指标是否下降。",
                            "core_claim": "反向操作可以检验机制是否必要。",
                            "target_problem": "常规增强是否真是必要机制。",
                            "mechanism": "移除常规增强后若指标不降说明原增强并非关键机制",
                            "prediction": "关闭增强后目标指标保持稳定或仅轻微下降",
                            "counterfactual": "若增强确实必要，关闭后目标指标显著下降",
                            "mechanism_family": "reverse operation",
                            "cdr_tuple": {
                                "problem_frame": "常规增强是否真是必要机制。",
                                "design_rationale": "反向操作可区分机制必要性和表面增益。",
                                "artifact": "反向操作实验。",
                                "design_principles": ["必要性检验"],
                                "data_view": "主验证集。",
                                "evaluation_mode": "消融式机制检验。",
                                "contribution_type": "improvement",
                                "boundary_conditions": ["增强可被独立关闭"],
                            },
                            "contribution_strength": 2,
                        },
                        "hypothesis_refs": [],
                        "source": {
                            "idea_origin": "reverse_operation",
                            "constraint_status": "supplement",
                            "from_synthesis_section": "literature/synthesis.md: mechanism cluster",
                            "from_missing_area": "none",
                            "from_seed_idea": False,
                            "derived_from_previous": None,
                            "supporting_papers": [
                                {
                                    "title": "Ablation Paper",
                                    "claim_used": "增强常被默认开启。",
                                }
                            ],
                            "trigger_observation": "多个方法默认添加增强但缺少必要性检验。",
                        },
                        "selection_rationale": {
                            "novelty_reason": "作为机制补充有价值。",
                            "feasibility_reason": "反向操作成本低。",
                            "impact_reason": "单独成文较弱。",
                            "evaluability_reason": "消融可测。",
                            "contribution_character": "如果成立，会削弱现有方法对默认增强必要性的解释。",
                        },
                        "closest_baselines": [],
                        "counterfactual_check": "survives_weakened",
                        "counterfactual_note": "抽掉消融论文后仍可作为机制检验但支撑较弱。",
                        "nearest_prior_work": {"work": "Ablation Paper", "distance": "distant"},
                        "novelty_signal": "no_nearby_cluster",
                        "scores": {
                            "novelty": 3,
                            "feasibility": 5,
                            "impact": 2,
                            "evaluability": 5,
                            "differentiation": 3,
                            "cost": 5,
                            "contribution_strength": 2,
                        },
                        "decision": {
                            "status": "deferred",
                            "rejection_reason": ["适合作为 D1 的消融补充，不单独作为主线。"],
                            "can_revisit_if": "如果反向操作出现强信号，可以合并进主假设。",
                        },
                        "risks": [
                            {
                                "risk": "只是普通消融",
                                "early_signal": "没有机制解释",
                                "mitigation": "绑定反事实预测",
                                "kill_criteria": "无法区分机制必要性",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "test validation set",
                            "baseline": "baseline1",
                            "metric": ["accuracy"],
                            "expected_signal": "关闭增强后指标变化可解释",
                            "estimated_cost_usd": 4.0,
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
        "\n## D1b: 证据驱动替代候选\n\n"
        "- **Status**: deferred\n"
        "- **Why deferred**:\n"
        "  - Pass2 建议暂缓，需要进一步收紧机制。\n"
        "  - 该方向仍保留在 Gate1 候选池，用户可以选择并要求重构。\n"
        "- **Can revisit if**: 如果 D1 pilot 失败但失败子群信号强，可以重访。\n"
        "\n## S1: 反向操作补充候选\n\n"
        "- **Status**: deferred\n"
        "- **Why deferred**:\n"
        "  - 更适合作为 D1 的消融补充，不单独作为主线。\n"
        "  - Gate1 仍可选择或合并，例如合并 D1+S1。\n"
        "- **Can revisit if**: 如果反向操作出现强信号，可以合并进主假设。\n"
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
    _write_t4_stage_visibility_artifacts(ideation_dir)
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
                        "deferred_idea_ids": ["D1b", "S1"],
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


def write_valid_t45_artifacts(workspace):
    (workspace / "project.yaml").write_text("research_direction: Test\n", encoding="utf-8")
    ideation_dir = workspace / "ideation"
    ideation_dir.mkdir(exist_ok=True)
    (ideation_dir / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n内容...\n",
        encoding="utf-8",
    )
    audit_text = (
        "# 新颖性审计报告\n\n"
        "## H1: 假设1\n\n"
        "### 搜索策略\n- 查询1: adaptive retrieval memory agent\n\n"
        "### 相似工作分析\n"
        "#### High Overlap（高度重叠）\n无高度重叠的工作。\n\n"
        "#### Medium Overlap（中度重叠）\n无中度重叠的工作。\n\n"
        "### 新颖性判定\n"
        "**新颖性等级**: Level 2 - 中度新颖\n\n"
        "**判定理由**:\n该假设与已有工作存在相关性，但机制、目标和验证方式不同。"
        "审计结果建议继续进入实验，同时保留补充 baseline 的风险提示。"
        "这里补足足够长的说明，避免被长度校验误判。\n\n"
        "### Collision Axis\n"
        "- Collision level: no true collision; nearest work shares task but not design rationale.\n\n"
        "### Ambition Axis\n"
        "- Ambition: medium-high because it changes how perturbation strength is allocated across user groups.\n"
        "- contribution_type: improvement\n\n"
        "### Contribution Distance\n"
        "- Distance: meaningful design-rationale distance from uniform perturbation baselines.\n\n"
        "### Final Gate Verdict\n"
        "- Verdict: proceed to T7/T8 with explicit boundary conditions, no routine contribution risk.\n\n"
    )
    (ideation_dir / "novelty_audit.md").write_text(audit_text * 8, encoding="utf-8")
    tuple_dir = ideation_dir / "_mechanism_tuples"
    tuple_dir.mkdir()
    (tuple_dir / "H1.json").write_text('{"source_id":"H1"}\n', encoding="utf-8")
    design_tuple_dir = ideation_dir / "_design_rationale_tuples"
    design_tuple_dir.mkdir()
    (design_tuple_dir / "H1.json").write_text(
        json.dumps(
            {
                "source_id": "H1",
                "problem_frame": "稀疏用户扰动缺少活跃度感知",
                "design_rationale": "不同活跃度用户需要不同扰动强度",
                "artifact": "adaptive perturbation module",
                "contribution_type": "improvement",
                "boundary_conditions": ["推荐数据有明显稀疏子群"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def write_valid_t3_artifacts(workspace):
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    queue_rows = []
    for idx in range(18):
        paper_id = f"paper{idx}"
        queue_rows.append(
            {
                "paper_id": paper_id,
                "normalized_id": paper_id,
                "queue_rank": idx + 1,
                "title": f"Paper {idx}",
                "seed_priority": False,
            }
        )
        (notes_dir / f"{paper_id}.md").write_text(
            f"""# {paper_id}

- **ID**: {paper_id}
- **Authors**: A, B
- **Venue**: TestConf (2026)
- **DOI/arXiv**: arxiv:2601.{idx:05d}
- **Citations**: N/A
- **Verification**: metadata_verified (confidence: 0.95)
- **Status**: [FULL-TEXT]

## 1. Problem & Motivation
problem

## 2. Method Overview
method

## 3. Key Results
- Accuracy: 88.1 [Evidence: Table 1]

## 4. Claims vs Evidence
| Claim | Evidence | Strength |
|-------|----------|----------|
| test | Table 1 | Strong |

## 5. Limitations
- limitation

## 6. Relevance to Our Research
- relevant

## 7. Technical Details Worth Noting
- detail

## 8. Strengths
- strength

## 9. Weaknesses / Gaps
- gap

## 10. Key Quotes
> "quote"

## 11. My Questions
- question

## 12. Reading Coverage
- **PDF source**: literature/pdfs/{paper_id}.pdf
- **Pages read**: 1-10 / 10
- **Extraction calls**: extract_pdf_text pages 1-10
- **Truncation**: none
- **Status rationale**: All PDF pages were read without truncation.

## 13. Mechanism Claim
- **Stated mechanism**: The method improves performance through better feature extraction
- **Evidence type**: ablation_supported
- **Supporting artifact**: Table 1

## 14. Design Rationale
- **Rationale**: The method is designed to test whether the claimed feature extraction path explains sparse recommendation gains.
- **Rationale evidence**: Table 1 and the reported ablation connect the artifact to the stated mechanism.
- **Rationale weakness**: The note remains synthetic and cannot settle whether the mechanism generalizes.

## 15. Artifact & Design Principles
- **Artifact type**: model component
- **Artifact description**: A lightweight perturbation component for representation learning.
- **Design principles**: isolate the mechanism; compare against a simple control.

## 16. Data View & Evaluation Mode
- **Data view**: recommendation interactions split by sparsity.
- **Evaluation mode**: accuracy and ablation evidence.
- **Validity concern**: aggregate metrics may hide subgroup failures.

## 17. Contribution Type
- **Contribution type**: improvement
- **Contribution character**: It improves the design rationale for sparse recommendation robustness.
- **Why not routine**: It tests mechanism-specific behavior rather than only changing an application domain.

## 18. Boundary Conditions
- **Works when**: sparse users have distinct interaction patterns.
- **May fail when**: all users have dense histories.
- **Untested boundary**: cold-start users without any interactions.

## 19. Cross-Paper Tension
- **Tension**: Uniform perturbation claims compete with subgroup-specific robustness needs.
- **Competing rationale**: Some baselines imply a single perturbation policy is sufficient.
- **Idea fuel**: Test whether adaptive perturbation changes sparse subgroup behavior.
""",
            encoding="utf-8",
        )
    (literature / "deep_read_queue.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in queue_rows) + "\n",
        encoding="utf-8",
    )
    (literature / "comparison_table.csv").write_text(
        "id,title,year,evidence_level\npaper0,Paper 0,2026,FULL_TEXT\n",
        encoding="utf-8",
    )
    (literature / "related_work.bib").write_text(
        "@article{paper0,\n  title={Paper 0},\n  year={2026}\n}\n",
        encoding="utf-8",
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
async def test_max_steps_tail_check_offers_extension_gate(tmp_workspace, registry):
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
        task_id="T3",
        run_id="r_step_tail_extend",
        budget_override=BudgetOverride(max_steps=1),
    )
    human = MockHumanInterface(gate_choices=[{"option_id": "extend", "captured": {}}])
    runner = AgentRunner(MinimalAgent(), registry, llm, human)
    runner.budget_escalation_policy = {
        "enabled": True,
        "tasks": [],
        "max_extensions_per_run": 1,
        "steps_increase_ratio": 1.0,
        "token_increase_ratio": 0.5,
        "wall_seconds_increase_ratio": 0.5,
    }

    result = await runner.run(ctx)

    assert result.ok
    assert any(call[0] == "gate" for call in human.calls)


@pytest.mark.asyncio
async def test_max_steps_tail_check_pauses_when_user_stops(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(tool_calls=[FakeToolCall(name="echo", arguments={"text": "hi"}, id="tc1")])
            ),
        ]
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T3",
        run_id="r_step_tail_stop",
        budget_override=BudgetOverride(max_steps=1),
    )
    human = MockHumanInterface(gate_choices=[{"option_id": "stop", "captured": {}}])
    runner = AgentRunner(MinimalAgent(), registry, llm, human)
    runner.budget_escalation_policy = {
        "enabled": True,
        "tasks": [],
        "max_extensions_per_run": 1,
        "steps_increase_ratio": 1.0,
    }

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_MAX_STEPS
    assert "paused" in (result.error or "")
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
async def test_validation_retry_exhaustion_pauses_for_resume(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "bad"}, id="tc1")]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "bad again"}, id="tc2")]
                )
            ),
        ]
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T3", run_id="r_validation_pause")
    runner = AgentRunner(AlwaysInvalidAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_INTERRUPTED
    assert "Validation failed 2 times" in (result.error or "")


@pytest.mark.asyncio
async def test_validation_retry_extension_gate_allows_repair_continue(tmp_workspace, registry):
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "bad"}, id="tc1")]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "bad again"}, id="tc2")]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "bad final"}, id="tc3")]
                )
            ),
        ]
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T8-SECTION-PLAN", run_id="r_validation_extend")
    human = MockHumanInterface(gate_choices=[{"option_id": "extend", "captured": {}}])
    runner = AgentRunner(AlwaysInvalidAgent(), registry, llm, human)
    runner.budget_escalation_policy = {
        "enabled": True,
        "tasks": [],
        "max_validation_extensions_per_run": 1,
        "validation_retry_increase": 1,
    }

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_INTERRUPTED
    assert "Validation failed 3 times" in (result.error or "")
    gate_calls = [call for call in human.calls if call[0] == "gate"]
    assert gate_calls
    assert gate_calls[0][1]["gate_id"] == "runtime_validation_retry_extension"


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
                                    "## 贡献空间地图\n"
                                    "The contribution-space map separates three design-rationale positions rather than "
                                    "ranking papers by a single performance frontier. [paper_0] and [paper_1] treat "
                                    "perturbation as a general regularizer, [paper_2] and [paper_3] imply that user "
                                    "subgroups may require different controls, and [paper_4] is useful for checking "
                                    "whether the same effect survives under a different implementation. This section "
                                    "therefore frames T4 opportunities as choices about problem frame, artifact design, "
                                    "evaluation mode, and boundary conditions, not as a deterministic opportunity map. "
                                    "A serious T4 idea should explain which design rationale it changes and what "
                                    "evidence would falsify that change.\n\n"
                                    "## 技术趋势\n"
                                    "The trend across these notes is a shift from adding larger components toward "
                                    "testing when the claimed mechanism is actually needed. Recent papers in the pool "
                                    "place more emphasis on ablations, subgroup behavior, and simpler controls. The "
                                    "trend is not yet a conclusion; it is a working reading of the evidence that T4 "
                                    "should preserve as an explicit uncertainty.\n\n"
                                    "## 跨论文矛盾与张力\n"
                                    "The key cross-paper contradiction is that some papers treat uniform perturbation "
                                    "as sufficient while others report subgroup-sensitive failure modes. [paper_0] and "
                                    "[paper_1] make broad mechanism claims, while [paper_2], [paper_3], and [paper_4] "
                                    "suggest that aggregate metrics can hide sparse-user behavior. This tension should "
                                    "feed ideation by asking whether the artifact should adapt to user activity, whether "
                                    "the evaluation mode should privilege subgroup evidence, and which boundary "
                                    "conditions would make the rationale fail.\n\n"
                                    "## 邻接领域可迁移机制\n"
                                    "The current mock corpus has limited explicit adjacent-domain coverage, so this "
                                    "section records the transfer boundary instead of inventing an external field. "
                                    "The transferable mechanism suggested by the notes is subgroup-sensitive control: "
                                    "[paper_2] and [paper_3] imply that sparse-user behavior may require adaptive "
                                    "rather than uniform perturbation. This is only a synthesis hint for T4, not a "
                                    "finished idea or a claim that another domain has already solved the problem. "
                                    "A later ideation step should verify whether this adjacent-transfer framing "
                                    "survives comparison with [paper_0], [paper_1], and [paper_4].\n\n"
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
async def test_t35_workbench_reuses_fresh_staged_artifacts(tmp_workspace, registry):
    literature = tmp_workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "paper_0.md").write_text("# Paper 0\n\n## 1. Problem & Motivation\nx\n", encoding="utf-8")
    (literature / "comparison_table.csv").write_text("id,title\npaper_0,Paper 0\n", encoding="utf-8")
    (literature / "missing_areas.md").write_text("# missing\n", encoding="utf-8")
    (literature / "synthesis_workbench.json").write_text('{"items":[]}\n', encoding="utf-8")
    (literature / "synthesis_outline.md").write_text("# outline\n", encoding="utf-8")
    (literature / "synthesis_draft.md").write_text("# draft\n", encoding="utf-8")

    runner = AgentRunner(T35PrefinalizeAgent(), registry, MockLLMClient([]), MockHumanInterface())
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T3.5",
        run_id="r_t35_reuse",
        mode="synthesize",
    )

    reused = await runner._maybe_prepare_t35_before_llm(
        ctx,
        runner._default_policy_factory(ctx, resolve_effective_config(runner.agent.spec, ctx)),
    )

    assert reused is True
    assert ctx.extra["t35_workbench_reused"] is True


@pytest.mark.asyncio
async def test_t3_resume_prefinalize_skips_llm_when_artifacts_validate(tmp_workspace, registry):
    write_valid_t3_artifacts(tmp_workspace)
    llm = MockLLMClient(responses=[])
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T3",
        run_id="r_t3_resume_prefinalize",
        mode="read",
        outputs_expected={
            "paper_notes_dir": tmp_workspace / "literature" / "paper_notes",
            "comparison_table": tmp_workspace / "literature" / "comparison_table.csv",
            "related_work_bib": tmp_workspace / "literature" / "related_work.bib",
        },
    )
    runner = AgentRunner(ReaderAgent(mode="read"), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert result.ok
    assert result.metadata["completion_mode"] == "t3_resume_prefinalize"
    assert llm.call_count == 0


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
            "idea_scorecard": tmp_workspace / "ideation" / "idea_scorecard.yaml",
            "idea_rationales": tmp_workspace / "ideation" / "idea_rationales.json",
            "gate_decisions": tmp_workspace / "ideation" / "gate_decisions.json",
            "rejected_ideas": tmp_workspace / "ideation" / "rejected_ideas.md",
            "family_distribution": tmp_workspace / "ideation" / "_family_distribution.md",
            "candidate_directions": tmp_workspace / "ideation" / "_candidate_directions.json",
        },
    )
    runner = AgentRunner(IdeationAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert result.ok
    assert result.metadata["completion_mode"] == "t4_resume_prefinalize"
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_t45_resume_prefinalize_skips_llm_when_artifacts_validate(tmp_workspace, registry):
    write_valid_t45_artifacts(tmp_workspace)
    llm = MockLLMClient(responses=[])
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T4.5",
        run_id="r_t45_resume_prefinalize",
        outputs_expected={
            "novelty_audit": tmp_workspace / "ideation" / "novelty_audit.md",
            "mechanism_tuples_dir": tmp_workspace / "ideation" / "_mechanism_tuples",
        },
    )
    runner = AgentRunner(NoveltyAuditorAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert result.ok
    assert result.metadata["completion_mode"] == "t45_resume_prefinalize"
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_t8_section_plan_prefinalize_repairs_invalid_state_without_llm(tmp_workspace, registry):
    write_valid_t8_section_plan_inputs(tmp_workspace)
    llm = MockLLMClient(responses=[])
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T8-SECTION-PLAN",
        run_id="r_t8_section_plan_prefinalize",
        mode="section_plan",
        outputs_expected={
            "paper_state": tmp_workspace / "drafts" / "paper_state.json",
            "section_outlines": tmp_workspace / "drafts" / "section_outlines",
        },
        extra={"phase": "section_plan"},
    )
    runner = AgentRunner(WriterAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert result.ok
    assert llm.call_count == 0
    assert result.metadata["completion_mode"] == "t8_section_plan_prefinalize"
    state = json.loads((tmp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    assert state["semantics"] == "shared_state_for_section_by_section_writing_not_final_claims"
    assert state["sections"]["methodology"]["file"] == "drafts/sections/methodology.tex"


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


@pytest.mark.asyncio
async def test_recoverable_pre_hook_pauses_for_resume(tmp_workspace, registry):
    llm = MockLLMClient(responses=[])
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T7",
        run_id="r_recoverable_hook",
    )
    runner = AgentRunner(RecoverablePreHookAgent(), registry, llm, MockHumanInterface())

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == AgentResult.STOP_INTERRUPTED
    assert "WAITING_ENVIRONMENT" in result.error
    assert llm.call_count == 0


def test_tool_timeout_uses_docker_specific_cap(tmp_workspace, registry):
    runner = AgentRunner(MinimalAgent(), registry, MockLLMClient(responses=[]), MockHumanInterface())
    runner.global_timeout = {"max_tool_call": 180, "docker_operation": 7200}

    class _Tool:
        timeout_seconds = 7200

    assert runner._timeout_for_tool("docker_exec", _Tool()) == 7200


def test_task_start_summary_includes_task_goal(tmp_workspace, registry, capsys):
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T9",
        run_id="r_summary",
        outputs_expected={"pdf": tmp_workspace / "submission" / "bundle" / "main.pdf"},
        mode=None,
    )
    runner = AgentRunner(MinimalAgent(), registry, MockLLMClient(responses=[]), MockHumanInterface())
    eff = resolve_effective_config(runner.agent.spec, ctx)

    runner._print_task_start_summary(ctx, eff)

    out = capsys.readouterr().out
    assert "任务: T9" in out
    assert "目标: 构建投稿包" in out
    assert "submission/bundle/main.pdf" in out
