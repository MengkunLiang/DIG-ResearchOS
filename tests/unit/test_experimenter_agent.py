"""Unit tests for T6 Experimenter Agent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext


@pytest.fixture
def experimenter_agent():
    """创建ExperimenterAgent实例。"""
    return ExperimenterAgent()


@pytest.fixture
def mock_workspace(tmp_path: Path):
    """创建模拟workspace。"""
    # 创建project.yaml
    project_data = {
        "research_direction": "测试研究方向",
        "domain": "NLP",
        "constraints": {"max_budget_usd": 100.0},
    }
    project_path = tmp_path / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 创建ideation目录和文件
    ideation_dir = tmp_path / "ideation"
    ideation_dir.mkdir()

    # 创建exp_plan.yaml
    exp_plan_data = {
        "experiments": [
            {
                "name": "baseline_experiment",
                "hypothesis_ref": "H1",
                "description": "Baseline实验",
                "dataset": "test_dataset",
                "baseline_methods": ["method1"],
                "our_method": {"name": "our_method", "description": "我们的方法"},
                "metrics": ["accuracy"],
                "compute_estimate": {"gpu_hours": 2, "gpu_type": "V100"},
                "success_criteria": [{"metric": "accuracy", "threshold": 0.8}],
            }
        ]
    }
    exp_plan_path = ideation_dir / "exp_plan.yaml"
    exp_plan_path.write_text(yaml.dump(exp_plan_data, allow_unicode=True), encoding="utf-8")

    # 创建hypotheses.md
    hypotheses_path = ideation_dir / "hypotheses.md"
    hypotheses_path.write_text("## H1\n\n测试假设内容", encoding="utf-8")

    return tmp_path


def test_experimenter_agent_spec(experimenter_agent):
    """测试ExperimenterAgent的AgentSpec配置。"""
    spec = experimenter_agent.spec

    assert spec.name == "experimenter"
    assert spec.model_tier == "medium"
    assert spec.max_steps == 100
    assert spec.max_tokens_total == 500_000
    assert spec.max_wall_seconds == 14400
    assert spec.temperature == 0.3
    assert spec.prompt_template == "experimenter.j2"

    # 检查工具
    expected_tools = [
        "read_file",
        "write_file",
        "list_files",
        "bash_run",
        "docker_exec",
        "finish_task",
    ]
    assert set(spec.tool_names) == set(expected_tools)

    # 检查权限
    assert "" in spec.allowed_read_prefixes
    assert "ideation/" in spec.allowed_read_prefixes
    assert "experiments/" in spec.allowed_read_prefixes
    assert "experiments/" in spec.allowed_write_prefixes


def test_experimenter_system_prompt(experimenter_agent, mock_workspace):
    """测试system prompt生成。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    prompt = experimenter_agent.system_prompt(ctx)

    # 检查关键内容
    assert "Experimenter Agent" in prompt
    assert "实验执行" in prompt
    assert "results_summary.json" in prompt
    assert "iteration_log.md" in prompt
    assert "测试研究方向" in prompt  # 项目信息
    assert "NLP" in prompt  # 领域


def test_experimenter_initial_user_message(experimenter_agent, mock_workspace):
    """测试初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    message = experimenter_agent.initial_user_message(ctx)

    assert "T6" in message
    assert "实验执行" in message
    assert "exp_plan.yaml" in message
    assert "results_summary.json" in message


def test_validate_outputs_success(experimenter_agent, mock_workspace):
    """测试输出校验（成功场景）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    # 创建experiments目录和输出文件
    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    # 创建results_summary.json
    results_data = {
        "exp_plan_ref": "ideation/exp_plan.yaml",
        "total_experiments": 1,
        "completed": 1,
        "failed": 0,
        "experiments": [
            {
                "experiment_id": "exp_baseline_20260419_120000",
                "name": "baseline_experiment",
                "hypothesis_ref": "H1",
                "status": "DONE",
                "metrics": {"accuracy": 0.85},
                "duration_seconds": 3600,
                "run_dir": "experiments/runs/exp_baseline_20260419_120000",
            }
        ],
    }
    results_path = experiments_dir / "results_summary.json"
    results_path.write_text(json.dumps(results_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 创建iteration_log.md
    log_content = """# 实验迭代日志

## 实验概览

- 总实验数：1
- 成功：1
- 失败：0

## Iteration 1

### 实验1: baseline_experiment
- **假设**: H1
- **状态**: DONE
- **结果**: accuracy: 0.85

## 结论

实验成功完成。
"""
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text(log_content, encoding="utf-8")

    # 校验
    ok, err = experimenter_agent.validate_outputs(ctx)
    assert ok, f"校验失败: {err}"


def test_validate_outputs_missing_results(experimenter_agent, mock_workspace):
    """测试输出校验（缺少results_summary.json）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    # 创建experiments目录但不创建results_summary.json
    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    # 只创建iteration_log.md
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text("# 实验迭代日志\n\n测试内容", encoding="utf-8")

    # 校验应该失败
    ok, err = experimenter_agent.validate_outputs(ctx)
    assert not ok
    assert "results_summary.json" in err


def test_validate_outputs_invalid_json(experimenter_agent, mock_workspace):
    """测试输出校验（results_summary.json格式错误）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    # 创建experiments目录
    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    # 创建格式错误的results_summary.json
    results_path = experiments_dir / "results_summary.json"
    results_path.write_text("invalid json content", encoding="utf-8")

    # 创建iteration_log.md
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text("# 实验迭代日志\n\n测试内容", encoding="utf-8")

    # 校验应该失败
    ok, err = experimenter_agent.validate_outputs(ctx)
    assert not ok
    assert "解析失败" in err


def test_validate_outputs_no_experiments(experimenter_agent, mock_workspace):
    """测试输出校验（没有实验结果）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    # 创建experiments目录
    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    # 创建空的results_summary.json
    results_data = {
        "exp_plan_ref": "ideation/exp_plan.yaml",
        "total_experiments": 0,
        "completed": 0,
        "failed": 0,
        "experiments": [],
    }
    results_path = experiments_dir / "results_summary.json"
    results_path.write_text(json.dumps(results_data, indent=2), encoding="utf-8")

    # 创建iteration_log.md
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text("# 实验迭代日志\n\n测试内容", encoding="utf-8")

    # 校验应该失败
    ok, err = experimenter_agent.validate_outputs(ctx)
    assert not ok
    assert "至少1个实验结果" in err


def test_validate_outputs_missing_required_fields(experimenter_agent, mock_workspace):
    """测试输出校验（实验结果缺少必需字段）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test_run_001",
        mode=None,
        extra={},
    )

    # 创建experiments目录
    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    # 创建缺少必需字段的results_summary.json
    results_data = {
        "exp_plan_ref": "ideation/exp_plan.yaml",
        "total_experiments": 1,
        "completed": 1,
        "failed": 0,
        "experiments": [
            {
                "experiment_id": "exp_baseline_20260419_120000",
                # 缺少 status 字段
                "name": "baseline_experiment",
            }
        ],
    }
    results_path = experiments_dir / "results_summary.json"
    results_path.write_text(json.dumps(results_data, indent=2), encoding="utf-8")

    # 创建iteration_log.md
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text("# 实验迭代日志\n\n测试内容", encoding="utf-8")

    # 校验应该失败
    ok, err = experimenter_agent.validate_outputs(ctx)
    assert not ok
    assert "缺少字段" in err
    assert "status" in err
