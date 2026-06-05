"""T1 PI Agent 单元测试。

测试覆盖：
1. init模式基本流程
2. evaluate模式基本流程
3. validate_outputs - init模式
4. validate_outputs - evaluate模式
5. 边界情况处理
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
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def pi_agent():
    """创建PI Agent实例。"""
    return PIAgent()


def test_pi_agent_init_mode_spec(pi_agent):
    """测试PI Agent的AgentSpec配置。"""
    spec = pi_agent.spec
    assert spec.name == "pi"
    assert spec.model_tier == "heavy"
    assert "read_file" in spec.tool_names
    assert "write_file" in spec.tool_names
    assert "ask_human" in spec.tool_names
    assert "finish_task" in spec.tool_names
    assert spec.temperature == 0.3
    assert "" in spec.allowed_write_prefixes
    assert "literature/" in spec.allowed_read_prefixes
    assert "literature/" in spec.allowed_write_prefixes
    assert "user_seeds/" in spec.allowed_write_prefixes
    assert "evaluation/" in spec.allowed_write_prefixes
    assert spec.structured_outputs["literature/bridge_domain_plan.json"] == "bridge_domain_plan"


def test_pi_agent_system_prompt_init_mode(pi_agent, temp_workspace):
    """测试init模式的system prompt生成。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        extra={"user_topic": "discrete diffusion language models"},
    )

    prompt = pi_agent.system_prompt(ctx)

    # 检查prompt包含关键内容
    assert "PI Agent" in prompt or "项目初始化" in prompt
    assert "三轮对话" in prompt
    assert "discrete diffusion language models" in prompt
    assert "project.yaml" in prompt
    assert "启动补充 gate" in prompt
    assert "先结合该回答，再扫描" in prompt
    assert "write_structured_file(path=\"literature/bridge_domain_plan.json\"" in prompt
    assert "在任何 ask_human 之前，先调用" not in prompt


def test_pi_agent_system_prompt_evaluate_mode(pi_agent, temp_workspace):
    """测试evaluate模式的system prompt生成。"""
    # 创建必要的目录和文件
    (temp_workspace / "experiments").mkdir()
    (temp_workspace / "ideation").mkdir()
    (temp_workspace / "experiments" / "results_summary.json").write_text("{}")
    (temp_workspace / "experiments" / "iteration_log.md").write_text("# Log")
    (temp_workspace / "ideation" / "exp_plan.yaml").write_text("plan: test")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T7.5",
        run_id="test-run-2",
        mode="evaluate",
    )

    prompt = pi_agent.system_prompt(ctx)

    # 检查prompt包含关键内容
    assert "评估" in prompt or "evaluate" in prompt.lower()
    assert "Situation" in prompt
    assert "Option" in prompt


def test_pi_agent_initial_user_message_init(pi_agent, temp_workspace):
    """测试init模式的initial_user_message。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        extra={"user_topic": "test topic"},
    )

    message = pi_agent.initial_user_message(ctx)

    # initial_user_message 不包含 user_topic（topic 在 system_prompt 中作为背景信息）
    assert "T1" in message or "初始化" in message


def test_pi_agent_initial_user_message_evaluate(pi_agent, temp_workspace):
    """测试evaluate模式的initial_user_message。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T7.5",
        run_id="test-run-2",
        mode="evaluate",
    )

    message = pi_agent.initial_user_message(ctx)

    assert "T7.5" in message or "评估" in message
    assert "results_summary" in message or "实验结果" in message


def test_validate_init_outputs_success(pi_agent, temp_workspace):
    """测试init模式validate_outputs成功情况。"""
    # 创建符合要求的输出
    project_data = {
        "project_id": "test-project",
        "research_direction": "Test research direction for validation",
        "keywords": ["test", "validation"],
        "created_at": datetime.now().isoformat(),
    }

    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 创建seed文件
    seed_dir = temp_workspace / "user_seeds"
    seed_dir.mkdir()
    (seed_dir / "seed_papers.jsonl").write_text("")
    (seed_dir / "seed_ideas.md").write_text("# Ideas")
    (seed_dir / "seed_constraints.md").write_text("# Constraints")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        outputs_expected={"project": project_path},
    )

    ok, err = pi_agent.validate_outputs(ctx)

    assert ok, f"Validation failed: {err}"
    assert err is None


def test_validate_init_outputs_missing_project(pi_agent, temp_workspace):
    """测试init模式缺少project.yaml的情况。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        outputs_expected={"project": temp_workspace / "project.yaml"},
    )

    ok, err = pi_agent.validate_outputs(ctx)

    assert not ok
    assert "project.yaml" in err


def test_validate_init_outputs_missing_seed_files(pi_agent, temp_workspace):
    """测试init模式缺少seed文件的情况。

    注意：seed 文件现在是可选的，所以验证应该通过。
    """
    # 只创建project.yaml，不创建seed文件
    project_data = {
        "project_id": "test-project",
        "research_direction": "Test research direction for validation",
        "keywords": ["test"],
        "created_at": datetime.now().isoformat(),
    }

    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        outputs_expected={"project": project_path},
    )

    # seed 文件是可选的，验证应该通过
    ok, err = pi_agent.validate_outputs(ctx)

    assert ok, f"验证应该通过，因为 seed 文件是可选的。错误: {err}"


def test_validate_init_outputs_invalid_schema(pi_agent, temp_workspace):
    """测试init模式project.yaml不符合schema的情况。"""
    # 创建不符合schema的project.yaml（缺少必需字段）
    project_data = {
        "project_id": "test-project",
        # 缺少 research_direction 和 created_at
    }

    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 创建seed文件
    seed_dir = temp_workspace / "user_seeds"
    seed_dir.mkdir()
    (seed_dir / "seed_papers.jsonl").write_text("")
    (seed_dir / "seed_ideas.md").write_text("")
    (seed_dir / "seed_constraints.md").write_text("")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        outputs_expected={"project": project_path},
    )

    ok, err = pi_agent.validate_outputs(ctx)

    assert not ok
    assert "schema" in err.lower()


def test_validate_evaluate_outputs_success(pi_agent, temp_workspace):
    """测试evaluate模式validate_outputs成功情况。"""
    # 创建evaluation_decision.md
    eval_dir = temp_workspace / "evaluation"
    eval_dir.mkdir()

    decision_content = """
# 实验评估报告

## Situation: A (全面达标)

核心指标达标率: 85%

## 后续Options

### Option 1: 推进T8写作
next_task: T8-WRITE
建议直接进入论文写作阶段。
"""

    (eval_dir / "evaluation_decision.md").write_text(decision_content, encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T7.5",
        run_id="test-run-2",
        mode="evaluate",
        outputs_expected={"decision": eval_dir / "evaluation_decision.md"},
    )

    ok, err = pi_agent.validate_outputs(ctx)

    assert ok, f"Validation failed: {err}"
    assert err is None


def test_validate_evaluate_outputs_missing_situation(pi_agent, temp_workspace):
    """测试evaluate模式缺少Situation判定的情况。"""
    eval_dir = temp_workspace / "evaluation"
    eval_dir.mkdir()

    # 缺少Situation关键字
    decision_content = """
# 实验评估报告

核心指标达标率: 85%

## 后续Options
Option 1: 推进写作
"""

    (eval_dir / "evaluation_decision.md").write_text(decision_content, encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T7.5",
        run_id="test-run-2",
        mode="evaluate",
        outputs_expected={"decision": eval_dir / "evaluation_decision.md"},
    )

    ok, err = pi_agent.validate_outputs(ctx)

    assert not ok
    assert "Situation" in err


def test_validate_evaluate_outputs_missing_options(pi_agent, temp_workspace):
    """测试evaluate模式缺少Options建议的情况。"""
    eval_dir = temp_workspace / "evaluation"
    eval_dir.mkdir()

    # 缺少Options关键字
    decision_content = """
# 实验评估报告

## Situation: A (全面达标)

核心指标达标率: 85%
"""

    (eval_dir / "evaluation_decision.md").write_text(decision_content, encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T7.5",
        run_id="test-run-2",
        mode="evaluate",
        outputs_expected={"decision": eval_dir / "evaluation_decision.md"},
    )

    ok, err = pi_agent.validate_outputs(ctx)

    assert not ok
    assert "Option" in err or "建议" in err


def test_validate_evaluate_outputs_missing_next_task(pi_agent, temp_workspace):
    """测试evaluate模式缺少 next_task 的情况。"""
    eval_dir = temp_workspace / "evaluation"
    eval_dir.mkdir()

    decision_content = """
# 实验评估报告

## Situation: B (部分成功)

## 后续Options

### Option 1: 回到实验
建议继续补实验。
"""

    (eval_dir / "evaluation_decision.md").write_text(decision_content, encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T7.5",
        run_id="test-run-2",
        mode="evaluate",
        outputs_expected={"decision": eval_dir / "evaluation_decision.md"},
    )

    ok, err = pi_agent.validate_outputs(ctx)

    assert not ok
    assert "next_task" in err


def test_validate_init_outputs_seed_ensemble_with_paper_info(pi_agent, temp_workspace):
    """测试 init 模式 seed_ensemble 包含论文信息的情况（应该失败）。"""
    # 创建包含论文信息的 seed_ensemble（错误情况）
    # 这种情况下 schema 验证会先失败（缺少 tier1_seeds），或者自定义验证检测到论文信息
    project_data = {
        "project_id": "test-project",
        "research_direction": "Test research direction",
        "keywords": ["test"],
        "created_at": datetime.now().isoformat(),
        "seed_ensemble": {
            "source": "arxiv",
            "title": "Some Paper",
            "authors": ["Author A"]
        }
    }

    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        outputs_expected={"project": project_path},
    )

    ok, err = pi_agent.validate_outputs(ctx)
    # 验证应该失败，因为 seed_ensemble 格式错误
    assert not ok
    # 错误可能是 schema 验证（缺少 tier1_seeds）或自定义验证（检测到论文信息）
    assert "tier1_seeds" in err or "论文信息" in err or "paper" in err.lower()


def test_validate_init_outputs_seed_ensemble_valid(pi_agent, temp_workspace):
    """测试 init 模式 seed_ensemble 格式正确的情况（应该成功）。"""
    project_data = {
        "project_id": "test-project",
        "research_direction": "Test research direction",
        "keywords": ["test"],
        "created_at": datetime.now().isoformat(),
        "seed_ensemble": {
            "tier1_seeds": [42, 123, 456],
            "tier2_seeds": [789],
            "tier3_seeds": [999]
        }
    }

    project_path = temp_workspace / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test-project",
        task_id="T1",
        run_id="test-run-1",
        mode="init",
        outputs_expected={"project": project_path},
    )

    ok, err = pi_agent.validate_outputs(ctx)
    assert ok, f"Validation should pass: {err}"
