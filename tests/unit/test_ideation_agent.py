"""T4 Ideation Agent 单元测试。

测试覆盖：
1. AgentSpec配置
2. system_prompt生成
3. initial_user_message生成
4. validate_outputs - 成功场景
5. validate_outputs - 各种失败场景
"""

from __future__ import annotations

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

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


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

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4",
        run_id="test-run-total-budget",
    )

    ok, err = ideation_agent.validate_outputs(ctx)
    assert not ok
    assert "总成本" in err or "over_budget" in err
