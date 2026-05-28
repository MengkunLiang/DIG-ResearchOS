"""T4 Ideation Agent 单元测试。

测试覆盖：
1. AgentSpec配置
2. system_prompt生成
3. initial_user_message生成
4. validate_outputs - 成功场景
5. validate_outputs - 各种失败场景
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.ideation import IdeationAgent
from researchos.runtime.agent import ExecutionContext


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时workspace。"""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()

    # 创建必需的目录结构
    (workspace / "literature").mkdir()
    (workspace / "ideation").mkdir()

    return workspace


@pytest.fixture
def ideation_agent():
    """创建Ideation Agent实例。"""
    return IdeationAgent()


def write_valid_idea_rationales(workspace: Path, refs: list[str] | None = None) -> None:
    """写入覆盖假设anchor的idea依据与决策链记录。"""
    refs = refs or ["H1"]
    scorecard = {
        "version": "1.0",
        "ideas": [
            {
                "idea": {
                    "id": "D1",
                    "title": "测试假设依据",
                    "pitch": "基于综述缺口提出一个可验证假设。",
                    "core_claim": "目标机制可以改善可观测指标。",
                    "target_problem": "现有方法在目标场景下存在明确短板。",
                    "mechanism": "通过正则化梯度范数改善稀疏用户嵌入质量",
                    "prediction": "在稀疏用户子群上 Recall@20 提升 5%+",
                    "counterfactual": "如果机制不成立，选择性噪声关闭后指标应无显著差异",
                    "mechanism_family": "selective noise application",
                },
                "hypothesis_refs": refs,
                "source": {
                    "idea_origin": "free_reasoning",
                    "constraint_status": "mainline",
                    "from_synthesis_section": "literature/synthesis.md: Q1",
                    "from_missing_area": "missing_areas.md: 需要机制验证",
                    "from_seed_idea": False,
                    "derived_from_previous": None,
                    "supporting_papers": [
                        {
                            "title": "Test Paper",
                            "claim_used": "现有方法在目标约束下存在缺口。",
                        }
                    ],
                    "trigger_observation": "对比表显示基线未覆盖该约束。",
                },
                "selection_rationale": {
                    "novelty_reason": "现有工作没有系统验证该机制。",
                    "feasibility_reason": "可以用小规模数据和基线先做验证。",
                    "impact_reason": "该问题影响后续系统可靠性。",
                    "evaluability_reason": "可以用accuracy和cost指标验证。",
                    "paper_story": "问题、方法和实验链路清楚。",
                },
                "closest_baselines": [
                    {
                        "name": "baseline1",
                        "similarity": "都处理同一目标任务。",
                        "difference": "本idea强调机制验证和成本约束。",
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
                    "selected_reason": [
                        "和研究问题最一致",
                        "有清晰baseline和指标",
                    ],
                    "selected_by": "user",
                    "user_feedback": "继续聚焦这个可验证方向。",
                },
                "risks": [
                    {
                        "risk": "机制收益不明显",
                        "early_signal": "pilot指标接近baseline",
                        "mitigation": "增加消融和错误分析",
                        "kill_criteria": "若不优于简单baseline则停止",
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
                    "pitch": "把已有方法直接迁移到新场景。",
                    "core_claim": "简单迁移也许可以提升指标。",
                    "target_problem": "另一个较弱的问题设定。",
                    "mechanism": "直接迁移在新场景中复用已有表示偏置影响目标指标",
                    "prediction": "如果迁移偏置有用，新场景accuracy应相对baseline提升",
                    "counterfactual": "如果迁移偏置无效，替换为简单baseline后指标不会下降",
                    "mechanism_family": "direct transfer",
                },
                "hypothesis_refs": [],
                "source": {
                    "idea_origin": "seed_refinement",
                    "constraint_status": "mainline",
                    "from_synthesis_section": "literature/synthesis.md: Q2",
                    "from_missing_area": "missing_areas.md: 评价指标不清楚",
                    "from_seed_idea": False,
                    "derived_from_previous": None,
                    "supporting_papers": [
                        {
                            "title": "Nearby Paper",
                            "claim_used": "已有方法已经覆盖主要机制。",
                        }
                    ],
                    "trigger_observation": "该方向来自对一个弱缺口的直接外推。",
                },
                "selection_rationale": {
                    "novelty_reason": "新颖性较弱。",
                    "feasibility_reason": "实现可行但贡献有限。",
                    "impact_reason": "影响范围较窄。",
                    "evaluability_reason": "缺少稳定评价指标。",
                    "paper_story": "论文故事更像工程迁移。",
                },
                "closest_baselines": [
                    {
                        "name": "Nearby Paper",
                        "similarity": "机制和目标都很接近。",
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
                    "rejection_reason": [
                        "和已有工作太接近",
                        "缺少清晰评价指标",
                    ],
                    "can_revisit_if": "如果找到更强的差异化机制和数据集，可以重访。",
                },
                "risks": [
                    {
                        "risk": "创新性不足",
                        "early_signal": "T4.5发现高重叠工作",
                        "mitigation": "重新寻找机制差异",
                        "kill_criteria": "若差异只剩应用场景变化则放弃",
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
    }
    (workspace / "ideation" / "idea_scorecard.yaml").write_text(
        yaml.safe_dump(scorecard, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_family_distribution.md").write_text(
        """## Mechanism Family Distribution

### Family: selective noise application
- Candidates: D1
- Mechanism similarity notes: single candidate, no overlap concern

### Family: direct transfer
- Candidates: D2
- Mechanism similarity notes: single candidate, no overlap concern

## Summary

- Total candidates: 2
- Distinct families: 2
- Families with multiple candidates: 0

## Recommended for Gate1 review

Both families are distinct. D1 focuses on mechanism verification while D2 is a direct transfer approach.
""",
        encoding="utf-8",
    )
    (workspace / "ideation" / "_candidate_directions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "candidates": [
                    {
                        "idea_id": "D1",
                        "title": "测试假设依据",
                        "idea_origin": "free_reasoning",
                        "constraint_status": "mainline",
                        "basis_summary": "LLM 综合 synthesis、comparison_table 和最终研究问题后提出的主线候选方向。",
                    },
                    {
                        "idea_id": "D1b",
                        "title": "基于证据的替代候选",
                        "idea_origin": "evidence_driven",
                        "constraint_status": "mainline",
                        "basis_summary": "从 paper notes 的共同限制和实验可行性出发形成的第二个主线候选。",
                    },
                    {
                        "idea_id": "D2",
                        "title": "被淘汰方向",
                        "idea_origin": "seed_refinement",
                        "constraint_status": "mainline",
                        "basis_summary": "由用户 seed idea 细化而来，但因新颖性和评价链条不足被淘汰。",
                    },
                    {
                        "idea_id": "S1",
                        "title": "反向操作补充候选",
                        "idea_origin": "reverse_operation",
                        "constraint_status": "supplement",
                        "basis_summary": "作为 coverage supplement，检查移除关键机制时指标是否下降。",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "rejected_ideas.md").write_text(
        """# Rejected / Deferred Ideas

## D2: 被淘汰方向

- **Status**: rejected
- **Why rejected**:
  - 和已有工作太接近，差异主要是应用场景变化。
  - 缺少清晰评价指标，难以形成完整论文故事。
- **Closest existing work**: Nearby Paper，机制和目标都很接近。
- **Missing evidence / metric**: 缺少稳定评价指标和强差异化机制。
- **Can revisit if**: 如果找到更强的差异化机制和数据集，可以重访。
- **Cheap pilot that was not chosen**: 小规模proxy set不足以证明新颖贡献。
""",
        encoding="utf-8",
    )
    (workspace / "ideation" / "gate_decisions.json").write_text(
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
                        "user_feedback": "继续聚焦这个可验证方向。",
                        "rationale": ["D1更可评估", "D2和已有工作太接近"],
                        "resulting_artifacts": [
                            "ideation/idea_scorecard.yaml",
                            "ideation/rejected_ideas.md",
                        ],
                    },
                    {
                        "gate_id": "T4-DECIDE-2",
                        "action": "confirm_plan",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": [],
                        "deferred_idea_ids": [],
                        "selected_by": "user",
                        "user_feedback": "确认计划。",
                        "rationale": ["实验预算可控", "指标和baseline清楚"],
                        "resulting_artifacts": [
                            "ideation/hypotheses.md",
                            "ideation/exp_plan.yaml",
                            "ideation/risks.md",
                        ],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "idea_rationales.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea_id": "D1",
                        "hypothesis_refs": refs,
                        "title": "测试假设依据",
                        "idea_summary": "基于综述缺口提出一个可验证假设。",
                        "basis": {
                            "source_questions": ["Q1"],
                            "literature_observations": [
                                {
                                    "claim": "现有方法在目标场景下存在明确短板。",
                                    "source": "synthesis.md: Q1 / [p1]",
                                    "strength": "direct",
                                }
                            ],
                            "missing_area_links": ["missing_areas.md: 需要机制验证"],
                            "comparison_table_signals": ["comparison_table.csv: 基线未覆盖该约束"],
                            "seed_idea_links": [],
                            "lens_insights": ["causal: 可用accuracy指标验证机制差异"],
                        },
                        "reasoning": "文献缺口和对比表共同指向该机制，因此形成可实验验证的假设。",
                        "confidence": "medium",
                        "limitations": ["仍需T4.5进行新颖性审计"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_ideation_agent_spec(ideation_agent):
    """测试Ideation Agent的AgentSpec配置。"""
    spec = ideation_agent.spec
    assert spec.name == "ideation"
    assert spec.model_tier == "heavy"
    assert spec.llm_profile == "ideation_deep"
    assert "read_file" in spec.tool_names
    assert "write_file" in spec.tool_names
    assert "ask_human" in spec.tool_names
    assert "finish_task" in spec.tool_names
    assert spec.temperature == 0.75
    assert "literature/" in spec.allowed_read_prefixes
    assert "ideation/" in spec.allowed_read_prefixes
    assert "_runtime/resume/" in spec.allowed_read_prefixes
    assert "ideation/" in spec.allowed_write_prefixes
    assert spec.structured_outputs["ideation/idea_rationales.json"] == "idea_rationales"
    assert spec.structured_outputs["ideation/idea_scorecard.yaml"] == "idea_scorecard"
    assert spec.structured_outputs["ideation/gate_decisions.json"] == "gate_decisions"


def test_ideation_system_prompt(ideation_agent, temp_workspace):
    """测试system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test research direction",
        "keywords": ["test", "research"],
        "constraints": {
            "max_budget_usd": 500,
            "compute_resources": {"allow_gpu": True},
        },
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建synthesis.md
    syn_path = temp_workspace / "literature" / "synthesis.md"
    syn_path.write_text("# Test Synthesis\n\n" + "x" * 1000)

    # 创建missing_areas.md
    missing_path = temp_workspace / "literature" / "missing_areas.md"
    missing_path.write_text("# Missing Areas\n\nSome gaps...")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    prompt = ideation_agent.system_prompt(ctx)
    assert "Ideation Agent" in prompt or "假设生成" in prompt
    assert "Test research direction" in prompt
    assert "Gate" in prompt or "两轮" in prompt


def test_ideation_initial_user_message(ideation_agent, temp_workspace):
    """测试初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    msg = ideation_agent.initial_user_message(ctx)
    assert "T4" in msg or "假设生成" in msg
    assert "Gate" in msg or "两轮" in msg


def test_validate_outputs_success(ideation_agent, temp_workspace):
    """测试输出校验（成功场景）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 1000},
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md（带anchor）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设

### 背景
这是背景，需要足够长的内容来通过500字符的验证。我们基于文献综述发现了一个重要的研究缺口，
现有方法在处理大规模数据时存在效率问题，而我们提出的方法可以显著提升性能。
这个假设基于多篇论文的观察，包括Smith et al. 2023和Jones et al. 2024的工作。
我们相信这个方向具有重要的理论和实践价值。

### 核心假设
我们假设通过引入新的注意力机制，可以在保持准确率的同时将推理速度提升2倍以上。
这个假设是可验证的，我们将通过在ImageNet数据集上的实验来验证。
具体来说，我们将实现一个新的高效注意力模块。

### 预期结果
如果假设成立，我们预期在ImageNet-1k验证集上达到80%以上的top-1准确率，
同时推理时间不超过100ms每张图片。这将比现有最好的方法快2倍。

### 风险
主要风险是新机制可能导致训练不稳定。如果出现这种情况，我们将尝试调整学习率和优化器。

## H2: 第二个假设

### 背景
第二个假设关注模型的泛化能力，我们观察到现有方法在分布外数据上表现不佳。
"""
    hyp_path.write_text(hyp_content)

    # 创建exp_plan.yaml（符合schema）
    plan_path = temp_workspace / "ideation" / "exp_plan.yaml"
    plan_data = {
        "goal": "验证假设H1",
        "experiments": [
            {
                "id": "exp1",
                "name": "Baseline",
                "title": "基线实验",
                "hypothesis_ref": "#H1,#H2",
                "datasets": [{"name": "test", "split": "val", "size": 1000}],
                "baselines": [{"name": "baseline1", "source": "paper", "why": "standard"}],
                "our_method": {
                    "name": "OurMethod",
                    "description": "Our approach",
                    "key_difference": "Different from baseline",
                },
                "metrics": [
                    {"name": "accuracy", "primary": True, "target": 0.8}
                ],
                "success_criteria": [
                    {"metric": "accuracy", "threshold": 0.8, "comparison": ">="}
                ],
                "steps": [
                    {"step": 1, "action": "Prepare data", "details": "Download dataset"}
                ],
                "compute_estimate": {
                    "gpu_hours": 50,
                    "gpu_type": "A100",
                    "estimated_cost_usd": 150,
                },
                "expected_duration_days": 3,
            }
        ],
    }
    plan_path.write_text(yaml.dump(plan_data))

    # 创建risks.md（至少3条风险）
    risks_path = temp_workspace / "ideation" / "risks.md"
    risks_content = """# Top 3 风险

## 风险1: 数据不足
- **描述**: 数据集可能太小
- **Early Signal**: 训练loss不下降
- **Mitigation**: 使用数据增强
- **Kill Criteria**: 如果3天内无进展

## 风险2: 计算资源不足
- **描述**: GPU时间可能不够
- **Early Signal**: 训练速度慢
- **Mitigation**: 优化代码
- **Kill Criteria**: 如果超出预算

## 风险3: 基线难以复现
- **描述**: 基线结果可能无法复现
- **Early Signal**: 结果偏差大
- **Mitigation**: 联系原作者
- **Kill Criteria**: 如果1周内无法复现
"""
    risks_path.write_text(risks_content)
    write_valid_idea_rationales(temp_workspace, refs=["H1", "H2"])

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_missing_idea_rationales(ideation_agent, temp_workspace):
    """测试输出校验（缺少idea依据记录）。"""
    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}}))

    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Baseline",
                        "title": "基线实验",
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
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-missing-rationales",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "idea_rationales.json" in err


def test_validate_outputs_missing_idea_scorecard(ideation_agent, temp_workspace):
    """测试输出校验（缺少候选idea scorecard）。"""
    (temp_workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 1000}})
    )
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(
            {
                "goal": "验证假设H1",
                "experiments": [
                    {
                        "id": "exp1",
                        "name": "Baseline",
                        "title": "基线实验",
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
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    write_valid_idea_rationales(temp_workspace)
    (temp_workspace / "ideation" / "idea_scorecard.yaml").unlink()

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-missing-scorecard",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "idea_scorecard.yaml" in err


def test_validate_outputs_missing_hypothesis_anchor(ideation_agent, temp_workspace):
    """测试输出校验（缺少假设anchor）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md（没有anchor）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = "# 研究假设\n\n这是一些假设内容，但没有H1/H2等anchor。" + "x" * 500
    hyp_path.write_text(hyp_content)

    # 创建其他必需文件
    plan_path = temp_workspace / "ideation" / "exp_plan.yaml"
    plan_path.write_text("goal: test\nexperiments: []\n")

    risks_path = temp_workspace / "ideation" / "risks.md"
    risks_path.write_text("## 风险1\n## 风险2\n## 风险3\n")
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "anchor" in err or "假设" in err


def test_validate_outputs_invalid_exp_plan(ideation_agent, temp_workspace):
    """测试输出校验（exp_plan不符合schema）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = "# 研究假设\n\n## H1: 假设1\n\n内容..." + "x" * 500
    hyp_path.write_text(hyp_content)

    # 创建exp_plan.yaml（缺少必需字段）
    plan_path = temp_workspace / "ideation" / "exp_plan.yaml"
    plan_path.write_text("goal: test\n")  # 缺少experiments字段

    # 创建risks.md
    risks_path = temp_workspace / "ideation" / "risks.md"
    risks_path.write_text("## 风险1\n## 风险2\n## 风险3\n")
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "schema" in err or "实验" in err


def test_validate_outputs_budget_exceeded(ideation_agent, temp_workspace):
    """测试输出校验（预算超限）。"""
    # 创建project.yaml（预算很小）
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 100},  # 只有$100预算
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容..." + "x" * 500)

    # 创建exp_plan.yaml（计算成本超出预算）
    plan_path = temp_workspace / "ideation" / "exp_plan.yaml"
    plan_data = {
        "goal": "验证假设H1",
        "experiments": [
            {
                "id": "exp1",
                "name": "Expensive Experiment",
                "title": "昂贵的实验",
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
                    "gpu_hours": 100,  # 100h * $3 = $300，超过$100预算的85%
                    "gpu_type": "A100",
                    "estimated_cost_usd": 300,
                },
                "expected_duration_days": 7,
            }
        ],
    }
    plan_path.write_text(yaml.dump(plan_data))

    # 创建risks.md
    risks_path = temp_workspace / "ideation" / "risks.md"
    risks_path.write_text("## 风险1\n## 风险2\n## 风险3\n")
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "预算" in err or "成本" in err or "budget" in err.lower()


def test_validate_outputs_total_budget_exceeded_even_when_single_experiments_fit(
    ideation_agent,
    temp_workspace,
):
    """总预算必须校验，不能只看单个实验是否低于85%。"""
    (temp_workspace / "project.yaml").write_text(
        yaml.dump({"research_direction": "Test", "constraints": {"max_budget_usd": 100}})
    )
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n" + "内容" * 260
    )
    plan_data = {
        "goal": "验证假设H1",
        "total_estimated_cost_usd": 108.0,
        "budget_check": {"over_budget": True},
        "experiments": [
            {
                "id": f"exp{i}",
                "name": f"Experiment {i}",
                "title": f"实验{i}",
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
                    "estimated_cost_usd": cost,
                },
                "expected_duration_days": 2,
            }
            for i, cost in enumerate([30, 24, 18, 36], start=1)
        ],
    }
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(yaml.dump(plan_data))
    (temp_workspace / "ideation" / "risks.md").write_text(
        "## 风险1\n内容\n## 风险2\n内容\n## 风险3\n内容\n"
    )
    write_valid_idea_rationales(temp_workspace)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-total-budget",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "总成本" in err or "over_budget" in err
