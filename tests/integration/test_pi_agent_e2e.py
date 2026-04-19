"""T1 PI Agent 集成测试（端到端）。

使用MockLLMClient模拟完整的agent执行流程。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from researchos.agents.pi import PIAgent
from researchos.runtime.agent import ExecutionContext


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时workspace。"""
    workspace = tmp_path / "test_pi_integration"
    workspace.mkdir()
    return workspace


@pytest.fixture
def pi_agent():
    """创建PI Agent实例。"""
    return PIAgent()


def test_pi_agent_init_mode_integration(pi_agent, temp_workspace):
    """测试T1 init模式的完整流程（不依赖真实LLM）。

    这个测试验证：
    1. Agent能正确初始化
    2. system_prompt和initial_user_message能正确生成
    3. validate_outputs能正确校验输出
    """
    # 准备执行上下文
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-pi-init",
        task_id="T1",
        run_id="test-run-init",
        mode="init",
        extra={"user_topic": "discrete diffusion language models"},
        outputs_expected={
            "project": temp_workspace / "project.yaml",
        },
    )

    # 验证system_prompt生成
    system_prompt = pi_agent.system_prompt(ctx)
    assert len(system_prompt) > 0
    assert "discrete diffusion language models" in system_prompt

    # 验证initial_user_message生成
    initial_message = pi_agent.initial_user_message(ctx)
    assert len(initial_message) > 0
    assert "discrete diffusion language models" in initial_message

    # 模拟agent产出（手动创建输出文件）
    project_data = {
        "project_id": "test-pi-init",
        "research_direction": "discrete diffusion language models",
        "research_question": "How to improve discrete diffusion models?",
        "keywords": ["discrete diffusion", "language model", "generation"],
        "constraints": {
            "max_budget_usd": 100.0,
            "compute_resources": {
                "allow_gpu": True,
                "max_memory_gb": 16,
            },
        },
        "created_at": datetime.now().isoformat(),
    }

    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 创建seed文件
    seed_dir = temp_workspace / "user_seeds"
    seed_dir.mkdir()

    # seed_papers.jsonl
    (seed_dir / "seed_papers.jsonl").write_text(
        '{"title": "Discrete Diffusion Models", "authors": ["Author A"], "year": 2023, "role": "anchor", "why_relevant": "Core paper"}\n',
        encoding="utf-8",
    )

    # seed_ideas.md
    (seed_dir / "seed_ideas.md").write_text(
        "# Initial Ideas\n\n- Improve factorization in discrete diffusion\n- Reduce gap between continuous and discrete models\n",
        encoding="utf-8",
    )

    # seed_constraints.md
    (seed_dir / "seed_constraints.md").write_text(
        "# Constraints\n\n- Must use GPU\n- Budget: $100\n- Deadline: 3 months\n",
        encoding="utf-8",
    )

    # 验证输出
    ok, err = pi_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"

    # 验证文件内容
    assert project_path.exists()
    loaded_project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    assert loaded_project["project_id"] == "test-pi-init"
    assert loaded_project["research_direction"] == "discrete diffusion language models"
    assert len(loaded_project["keywords"]) >= 1

    # 验证seed文件
    assert (seed_dir / "seed_papers.jsonl").exists()
    assert (seed_dir / "seed_ideas.md").exists()
    assert (seed_dir / "seed_constraints.md").exists()


def test_pi_agent_evaluate_mode_integration(pi_agent, temp_workspace):
    """测试T7.5 evaluate模式的完整流程。"""
    # 准备输入文件
    (temp_workspace / "experiments").mkdir()
    (temp_workspace / "ideation").mkdir()

    # results_summary.json
    results_summary = {
        "core_metrics": {
            "accuracy": {"value": 0.85, "target": 0.80, "achieved": True},
            "perplexity": {"value": 15.2, "target": 20.0, "achieved": True},
            "speed": {"value": 100, "target": 120, "achieved": False},
        },
        "overall_success_rate": 0.67,
    }

    import json

    (temp_workspace / "experiments" / "results_summary.json").write_text(
        json.dumps(results_summary, indent=2), encoding="utf-8"
    )

    # iteration_log.md
    (temp_workspace / "experiments" / "iteration_log.md").write_text(
        "# Iteration Log\n\n## Iteration 1\n- Tried baseline\n- Results: 0.75 accuracy\n\n## Iteration 2\n- Improved model\n- Results: 0.85 accuracy\n",
        encoding="utf-8",
    )

    # exp_plan.yaml
    exp_plan = {
        "hypothesis": "Discrete diffusion can achieve better performance",
        "experiments": [
            {"name": "baseline", "description": "Run baseline model"},
            {"name": "improved", "description": "Run improved model"},
        ],
    }

    (temp_workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.dump(exp_plan, allow_unicode=True), encoding="utf-8"
    )

    # 准备执行上下文
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-pi-eval",
        task_id="T7.5",
        run_id="test-run-eval",
        mode="evaluate",
        outputs_expected={
            "decision": temp_workspace / "evaluation" / "evaluation_decision.md",
        },
    )

    # 验证system_prompt生成
    system_prompt = pi_agent.system_prompt(ctx)
    assert len(system_prompt) > 0
    assert "Situation" in system_prompt

    # 验证initial_user_message生成
    initial_message = pi_agent.initial_user_message(ctx)
    assert len(initial_message) > 0
    assert "评估" in initial_message or "evaluate" in initial_message.lower()

    # 模拟agent产出
    eval_dir = temp_workspace / "evaluation"
    eval_dir.mkdir()

    decision_content = """
# 实验评估报告

## Situation: B (部分达标)

### 核心指标评估
- accuracy: 达标 (0.85 vs 0.80)
- perplexity: 达标 (15.2 vs 20.0)
- speed: 未达标 (100 vs 120)

达标率: 66.7%

### 分析
实验在准确率和困惑度上达到了预期目标，但在速度上存在不足。

## 后续Options

### Option 1: 继续优化实验 (推荐)
- 回到T7，针对速度指标进行优化
- 预计需要1-2轮迭代
- 风险：可能仍无法完全达标

### Option 2: 推进写作
- 直接进入T8，诚实报告当前结果和局限性
- 优势：节省时间，部分结果仍有价值
- 风险：审稿人可能质疑速度不足

### Option 3: 调整实验设计
- 回到T4，重新设计实验以平衡准确率和速度
- 适用于发现了新的trade-off
"""

    (eval_dir / "evaluation_decision.md").write_text(decision_content, encoding="utf-8")

    # 验证输出
    ok, err = pi_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"

    # 验证文件内容
    decision_path = eval_dir / "evaluation_decision.md"
    assert decision_path.exists()
    content = decision_path.read_text(encoding="utf-8")
    assert "Situation" in content
    assert "Option" in content
    assert "B" in content or "部分达标" in content


def test_pi_agent_init_mode_minimal_seeds(pi_agent, temp_workspace):
    """测试init模式下用户提供最少信息的情况。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-pi-minimal",
        task_id="T1",
        run_id="test-run-minimal",
        mode="init",
        extra={"user_topic": "machine learning"},
        outputs_expected={
            "project": temp_workspace / "project.yaml",
        },
    )

    # 模拟用户只提供最少信息
    project_data = {
        "project_id": "test-pi-minimal",
        "research_direction": "machine learning",
        "keywords": ["ml"],
        "created_at": datetime.now().isoformat(),
    }

    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 创建空的seed文件
    seed_dir = temp_workspace / "user_seeds"
    seed_dir.mkdir()
    (seed_dir / "seed_papers.jsonl").write_text("", encoding="utf-8")
    (seed_dir / "seed_ideas.md").write_text("暂无\n", encoding="utf-8")
    (seed_dir / "seed_constraints.md").write_text("暂无\n", encoding="utf-8")

    # 验证输出
    ok, err = pi_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_pi_agent_spec_configuration(pi_agent):
    """测试PI Agent的配置是否符合规范。"""
    spec = pi_agent.spec

    # 验证基本配置
    assert spec.name == "pi"
    assert spec.model_tier == "heavy"
    assert spec.max_steps >= 20
    assert spec.max_tokens_total >= 50_000
    assert spec.temperature <= 0.5  # PI应该比较保守

    # 验证工具配置
    required_tools = {"read_file", "write_file", "ask_human", "finish_task"}
    assert required_tools.issubset(set(spec.tool_names))

    # 验证路径权限
    assert "" in spec.allowed_write_prefixes  # 可以写workspace根目录
    assert "user_seeds/" in spec.allowed_write_prefixes
    assert "evaluation/" in spec.allowed_write_prefixes

    # 验证prompt模板
    assert spec.prompt_template == "pi.j2"
