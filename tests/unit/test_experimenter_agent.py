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
    assert spec.max_steps == 2000
    assert spec.max_tokens_total == 60_000_000
    assert spec.max_wall_seconds == 14400
    assert spec.temperature == 0.3
    assert spec.prompt_template == "experimenter.j2"

    # 检查工具（包含 write_structured_file）
    expected_tools = [
        "read_file",
        "write_file",
        "write_structured_file",
        "list_files",
        "append_file",
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
        task_id="T7",
        run_id="test_run_001",
        mode="full",
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
        task_id="T7",
        run_id="test_run_001",
        mode="full",  # full mode returns T7
        extra={},
    )

    message = experimenter_agent.initial_user_message(ctx)

    assert "T7" in message
    assert "实验任务" in message  # full mode says "实验任务"
    assert "exp_plan.yaml" in message
    assert "results_summary.json" in message


def test_experimenter_initial_user_message_resume_pilot(experimenter_agent, mock_workspace):
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T5",
        run_id="test_run_resume",
        mode="pilot",
        extra={"resume_mode": True},
    )

    message = experimenter_agent.initial_user_message(ctx)
    assert "继续 T5 Pilot" in message
    assert "pilot/pilot_code" in message


def test_experimenter_system_prompt_resume_state(experimenter_agent, mock_workspace):
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T5",
        run_id="test_run_resume",
        mode="pilot",
        extra={
            "resume_mode": True,
            "resume_state_path": "pilot/pilot_resume_state.json",
            "resume_existing_outputs": ["pilot_plan"],
            "resume_missing_outputs": ["pilot_results", "motivation_validation"],
            "resume_existing_code_files": ["pilot/pilot_code/run_pilot.py"],
            "resume_has_existing_code": True,
            "resume_reason": "retry_after_failure",
        },
    )

    prompt = experimenter_agent.system_prompt(ctx)
    assert "当前已有进度" in prompt
    assert "pilot/pilot_resume_state.json" in prompt
    assert "已有代码文件" in prompt
    assert "恢复运行要求" in prompt


def test_validate_outputs_success(experimenter_agent, mock_workspace):
    """测试输出校验（成功场景）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T7",
        run_id="test_run_001",
        mode="full",
        extra={},
    )

    # 创建experiments目录和输出文件
    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    # 创建results_summary.json（包含完整的 seed_runs 和 tier 信息）
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
                "tier": "headline",  # full 模式需要 tier 字段
                "status": "DONE",
                "metrics": {"accuracy": 0.85},
                "seed_runs": [  # headline 需要 3 个 seed
                    {"seed": 42, "accuracy": 0.85},
                    {"seed": 43, "accuracy": 0.86},
                    {"seed": 44, "accuracy": 0.84},
                ],
                "quality_status": "ok",
                "duration_seconds": 3600,
                "run_dir": "experiments/runs/exp_baseline_20260419_120000",
            }
        ],
    }
    results_path = experiments_dir / "results_summary.json"
    results_path.write_text(json.dumps(results_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 创建ablations.csv（最少 3 条）
    ablations_content = """experiment_id,hypothesis_ref,ablation_type,metric,value,baseline_value,delta
exp_h1_ablation1,H1,remove_component_A,accuracy,0.83,0.85,-0.02
exp_h1_ablation2,H1,remove_component_B,accuracy,0.78,0.85,-0.07
exp_h1_ablation3,H1,replace_with_baseline,accuracy,0.81,0.85,-0.04
"""
    ablations_path = experiments_dir / "ablations.csv"
    ablations_path.write_text(ablations_content, encoding="utf-8")

    # 创建docker_digests.txt
    digest_content = """pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime@sha256:abc123def456
"""
    digest_path = experiments_dir / "docker_digests.txt"
    digest_path.write_text(digest_content, encoding="utf-8")

    # 创建seed_ensemble_summary.json
    ensemble_content = """{
  "headline_experiments": [
    {
      "experiment_id": "exp_baseline_20260419_120000",
      "seeds": [42, 43, 44],
      "metric_mean": 0.85,
      "metric_std": 0.01,
      "metric_values": [0.85, 0.86, 0.84]
    }
  ]
}
"""
    ensemble_path = experiments_dir / "seed_ensemble_summary.json"
    ensemble_path.write_text(ensemble_content, encoding="utf-8")

    # 创建iteration_diversity_check.md
    diversity_content = """# 迭代多样性检查

## Iteration 1
- 探索方向：baseline 对比
- 超参数：lr=1e-4, batch_size=32
- 结果：accuracy=0.85

## 判定
- 总迭代数：1
- 实质性改进：✓
"""
    diversity_path = experiments_dir / "iteration_diversity_check.md"
    diversity_path.write_text(diversity_content, encoding="utf-8")

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


def test_validate_outputs_accepts_legacy_seeds_field(experimenter_agent, mock_workspace):
    """兼容旧版 results_summary：使用 seeds 数组而不是 seed_runs。"""

    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T7",
        run_id="test_run_legacy_seeds",
        mode="full",
        extra={},
    )

    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    results_data = {
        "exp_plan_ref": "ideation/exp_plan.yaml",
        "total_experiments": 2,
        "completed": 2,
        "failed": 0,
        "experiments": [
            {
                "experiment_id": "full_exp1_synthetic",
                "name": "SyntheticDataValidation",
                "hypothesis_ref": "H2",
                "tier": "headline",
                "status": "DONE",
                "metrics": {"acc": 0.8},
                "seeds": [42, 43, 44],
                "quality_status": "ok",
            },
            {
                "experiment_id": "full_exp3_scale",
                "name": "MemoryScaleEffect",
                "hypothesis_ref": "H3",
                "tier": "final_method",
                "status": "DONE",
                "metrics": {"acc": 0.82},
                "seeds": [42, 43],
                "quality_status": "ok",
            },
        ],
    }
    (experiments_dir / "results_summary.json").write_text(
        json.dumps(results_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (experiments_dir / "ablations.csv").write_text(
        "experiment_id,hypothesis_ref,ablation_type,metric,value,baseline_value,delta\n"
        "a,H1,x,acc,0.8,0.82,-0.02\n"
        "b,H1,y,acc,0.79,0.82,-0.03\n"
        "c,H2,z,acc,0.78,0.82,-0.04\n",
        encoding="utf-8",
    )
    (experiments_dir / "docker_digests.txt").write_text(
        "pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime@sha256:abc123\n",
        encoding="utf-8",
    )
    (experiments_dir / "seed_ensemble_summary.json").write_text(
        json.dumps({"headline_experiments": [{"experiment_id": "full_exp1_synthetic"}]}),
        encoding="utf-8",
    )
    (experiments_dir / "iteration_diversity_check.md").write_text("# check\n\nok\n", encoding="utf-8")
    (experiments_dir / "iteration_log.md").write_text("# 实验日志\n\n" + ("x" * 200), encoding="utf-8")

    ok, err = experimenter_agent.validate_outputs(ctx)
    assert ok, f"校验失败: {err}"


def test_validate_outputs_missing_results(experimenter_agent, mock_workspace):
    """测试输出校验（缺少results_summary.json）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T7",
        run_id="test_run_001",
        mode="full",
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
        task_id="T7",
        run_id="test_run_001",
        mode="full",
        extra={},
    )

    # 创建experiments目录
    experiments_dir = mock_workspace / "experiments"
    experiments_dir.mkdir()

    # 创建格式错误的results_summary.json
    results_path = experiments_dir / "results_summary.json"
    results_path.write_text("invalid json content", encoding="utf-8")

    # 创建其他必需文件以避免 "缺少必需产出" 错误
    ablations_path = experiments_dir / "ablations.csv"
    ablations_path.write_text("experiment_id,hypothesis_ref,ablation_type,metric,value,baseline_value,delta\nexp1,H1,remove_A,accuracy,0.8,0.85,-0.05\nexp2,H1,remove_B,accuracy,0.78,0.85,-0.07\nexp3,H1,replace,accuracy,0.81,0.85,-0.04\n", encoding="utf-8")
    digest_path = experiments_dir / "docker_digests.txt"
    digest_path.write_text("test@sha256:abc\n", encoding="utf-8")
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
        task_id="T7",
        run_id="test_run_001",
        mode="full",
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

    # 创建其他必需文件
    ablations_path = experiments_dir / "ablations.csv"
    ablations_path.write_text("experiment_id,hypothesis_ref,ablation_type,metric,value,baseline_value,delta\nexp1,H1,remove_A,accuracy,0.8,0.85,-0.05\nexp2,H1,remove_B,accuracy,0.78,0.85,-0.07\nexp3,H1,replace,accuracy,0.81,0.85,-0.04\n", encoding="utf-8")
    digest_path = experiments_dir / "docker_digests.txt"
    digest_path.write_text("test@sha256:abc\n", encoding="utf-8")
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text("# 实验迭代日志\n\n测试内容", encoding="utf-8")

    # 校验应该失败
    ok, err = experimenter_agent.validate_outputs(ctx)
    assert not ok
    # full 模式下，验证先检查 required_files（iteration_diversity_check.md）
    # 再检查 ablations，再检查 results
    assert "实验结果" in err or "iteration_diversity_check" in err or "results_summary" in err


def test_validate_outputs_missing_required_fields(experimenter_agent, mock_workspace):
    """测试输出校验（实验结果缺少必需字段）。"""
    ctx = ExecutionContext(
        workspace_dir=mock_workspace,
        project_id="test_project",
        task_id="T7",
        run_id="test_run_001",
        mode="full",
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

    # 创建其他必需文件
    ablations_path = experiments_dir / "ablations.csv"
    ablations_path.write_text("experiment_id,hypothesis_ref,ablation_type,metric,value,baseline_value,delta\nexp1,H1,remove_A,accuracy,0.8,0.85,-0.05\nexp2,H1,remove_B,accuracy,0.78,0.85,-0.07\nexp3,H1,replace,accuracy,0.81,0.85,-0.04\n", encoding="utf-8")
    digest_path = experiments_dir / "docker_digests.txt"
    digest_path.write_text("test@sha256:abc\n", encoding="utf-8")
    log_path = experiments_dir / "iteration_log.md"
    log_path.write_text("# 实验迭代日志\n\n测试内容", encoding="utf-8")

    # 校验应该失败
    # full 模式下，验证先检查 required_files（包括 iteration_diversity_check.md）
    ok, err = experimenter_agent.validate_outputs(ctx)
    assert not ok
    # iteration_diversity_check.md 是必需文件之一
    assert "iteration_diversity_check" in err
