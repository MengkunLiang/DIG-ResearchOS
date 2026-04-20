"""Unit tests for T5 Experimenter Agent (Pilot Mode).

测试 ExperimenterAgent 的 pilot 模式功能，包括：
- Pilot 模式的 AgentSpec 配置
- Pilot 模式的 system prompt 生成
- Pilot 模式的输出校验（smoke test、固定 seed、motivation validation）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext


@pytest.fixture
def experimenter_agent():
    """创建 ExperimenterAgent 实例。"""
    return ExperimenterAgent()


@pytest.fixture
def pilot_workspace(tmp_path: Path):
    """创建 pilot 模式的模拟 workspace。"""
    # 创建 project.yaml
    project_data = {
        "research_direction": "测试研究方向",
        "domain": "NLP",
        "constraints": {"max_budget_usd": 100.0},
    }
    project_path = tmp_path / "project.yaml"
    project_path.write_text(yaml.dump(project_data, allow_unicode=True), encoding="utf-8")

    # 创建 ideation 目录和文件
    ideation_dir = tmp_path / "ideation"
    ideation_dir.mkdir()

    # 创建 exp_plan.yaml
    exp_plan_data = {
        "experiments": [
            {
                "name": "pilot_experiment",
                "hypothesis_ref": "H1",
                "description": "Pilot 实验",
                "dataset": "test_dataset",
                "data_fraction": 0.1,  # 10% 数据
                "baseline_methods": ["baseline"],
                "our_method": {"name": "our_method", "description": "我们的方法"},
                "metrics": ["accuracy"],
                "compute_estimate": {"gpu_hours": 0.5, "gpu_type": "V100"},
                "success_criteria": [{"metric": "accuracy", "threshold": 0.7}],
            }
        ]
    }
    exp_plan_path = ideation_dir / "exp_plan.yaml"
    exp_plan_path.write_text(yaml.dump(exp_plan_data, allow_unicode=True), encoding="utf-8")

    # 创建 hypotheses.md（Integrity Gate 要求至少 50 字符）
    hypotheses_path = ideation_dir / "hypotheses.md"
    hypotheses_path.write_text(
        "## H1: 测试假设\n\n我们假设方法 X 可以提升性能。方法 X 通过改进模型架构来增强性能。\n\n实验将验证方法 X 在测试数据集上的效果，预期性能提升 5-10%。",
        encoding="utf-8"
    )

    # Integrity Gate 要求 novelty_audit.md
    novelty_audit_path = ideation_dir / "novelty_audit.md"
    novelty_audit_path.write_text(
        "# Novelty Audit\n\n## Level 2\n- H1: 新颖性高\n- H2: 新颖性中\n",
        encoding="utf-8"
    )

    return tmp_path


def test_pilot_mode_spec(experimenter_agent):
    """测试 pilot 模式的 AgentSpec 配置。

    验证：
    - 支持 pilot 和 full 两种模式
    - 工具列表包含必要的工具
    - 权限配置正确（包含 pilot/ 前缀）
    """
    spec = experimenter_agent.spec

    assert spec.name == "experimenter"
    assert spec.model_tier == "medium"

    # Pilot 模式应该使用相同的 spec，但在 system_prompt 中说明更严格的限制
    assert spec.max_steps == 150  # full 模式上限
    assert spec.max_tokens_total == 600_000
    assert spec.max_wall_seconds == 14400

    # 检查工具（pilot 模式需要的工具）
    expected_tools = [
        "read_file",
        "write_file",
        "list_files",
        "append_file",
        "bash_run",
        "docker_exec",
        "finish_task",
    ]
    for tool in expected_tools:
        assert tool in spec.tool_names, f"缺少工具: {tool}"

    # 检查权限（pilot 模式需要读写 pilot/ 目录）
    assert "pilot/" in spec.allowed_read_prefixes, "缺少 pilot/ 读权限"
    assert "pilot/" in spec.allowed_write_prefixes, "缺少 pilot/ 写权限"


def test_pilot_system_prompt(experimenter_agent, pilot_workspace):
    """测试 pilot 模式的 system prompt 生成。

    验证：
    - mode="pilot" 时生成 pilot 特定的指令
    - 包含 smoke test 要求
    - 包含固定 seed 要求
    - 包含 motivation validation 要求
    """
    ctx = ExecutionContext(
        workspace_dir=pilot_workspace,
        project_id="test_project",
        task_id="T5",
        run_id="test_run_001",
        mode="pilot",  # 指定 pilot 模式
    )

    prompt = experimenter_agent.system_prompt(ctx)

    # 验证 prompt 包含 pilot 模式的关键指令
    assert "pilot" in prompt.lower() or "T5" in prompt, "Prompt 应该提到 pilot 模式"
    assert "smoke" in prompt.lower() or "烟测" in prompt, "Prompt 应该包含 smoke test 要求"
    assert "seed" in prompt.lower() or "种子" in prompt, "Prompt 应该包含固定 seed 要求"
    assert "motivation" in prompt.lower() or "动机" in prompt, "Prompt 应该包含 motivation validation 要求"

    # 验证 prompt 包含小规模数据要求
    assert "5-10%" in prompt or "小规模" in prompt or "部分数据" in prompt, \
        "Prompt 应该包含小规模数据要求"


def test_pilot_validate_outputs_success(experimenter_agent, pilot_workspace):
    """测试 pilot 模式输出校验 - 成功场景。

    验证：
    - 所有必需文件存在
    - smoke_test_passed.marker 存在
    - pilot_results.json 包含 seed=42
    - motivation_validation.md 包含判定
    """
    # 创建 pilot 输出目录
    pilot_dir = pilot_workspace / "pilot"
    pilot_dir.mkdir()

    pilot_code_dir = pilot_dir / "pilot_code"
    pilot_code_dir.mkdir()

    # 创建必需的输出文件

    # 1. pilot_results.json（包含 seed=42）
    pilot_results = {
        "seed": 42,
        "experiments": [
            {
                "experiment_id": "pilot_h1",
                "status": "DONE",
                "metrics": {"accuracy": 0.75},
                "duration_seconds": 300,
            }
        ],
    }
    (pilot_dir / "pilot_results.json").write_text(
        json.dumps(pilot_results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 2. motivation_validation.md（包含判定）
    motivation_validation = """## 判定：PASS

### 理由
- H1: 初步结果显示 accuracy=0.75，超过阈值 0.7，方向正确

### 建议
- H1: 继续 full 实验，预期可以达到更高性能
"""
    (pilot_dir / "motivation_validation.md").write_text(
        motivation_validation,
        encoding="utf-8"
    )

    # 3. pilot_code/run_pilot.py
    (pilot_code_dir / "run_pilot.py").write_text(
        "# Pilot experiment code\nprint('Hello')",
        encoding="utf-8"
    )

    # 4. smoke_test_passed.marker
    (pilot_dir / "smoke_test_passed.marker").write_text(
        "smoke_test: PASS",
        encoding="utf-8"
    )

    # 5. docker_digests.txt
    (pilot_dir / "docker_digests.txt").write_text(
        "researchos/system@sha256:abc123",
        encoding="utf-8"
    )

    # 执行校验
    ctx = ExecutionContext(
        workspace_dir=pilot_workspace,
        project_id="test_project",
        task_id="T5",
        run_id="test_run_001",
        mode="pilot",
    )

    ok, err = experimenter_agent.validate_outputs(ctx)

    assert ok, f"校验应该成功，但失败了: {err}"
    assert err is None


def test_pilot_validate_outputs_missing_smoke_test(experimenter_agent, pilot_workspace):
    """测试 pilot 模式输出校验 - 缺少 smoke test marker。

    验证：
    - 缺少 smoke_test_passed.marker 时校验失败
    - 错误消息清晰
    """
    # 创建 pilot 输出目录
    pilot_dir = pilot_workspace / "pilot"
    pilot_dir.mkdir()

    pilot_code_dir = pilot_dir / "pilot_code"
    pilot_code_dir.mkdir()

    # 创建其他必需文件，但不创建 smoke_test_passed.marker
    pilot_results = {
        "seed": 42,
        "experiments": [{"experiment_id": "pilot_h1", "status": "DONE"}],
    }
    (pilot_dir / "pilot_results.json").write_text(
        json.dumps(pilot_results),
        encoding="utf-8"
    )

    (pilot_dir / "motivation_validation.md").write_text(
        "## 判定：PASS\n\n理由：测试通过",
        encoding="utf-8"
    )

    (pilot_code_dir / "run_pilot.py").write_text(
        "# Code",
        encoding="utf-8"
    )

    (pilot_dir / "docker_digests.txt").write_text(
        "digest",
        encoding="utf-8"
    )

    # 执行校验
    ctx = ExecutionContext(
        workspace_dir=pilot_workspace,
        project_id="test_project",
        task_id="T5",
        run_id="test_run_001",
        mode="pilot",
    )

    ok, err = experimenter_agent.validate_outputs(ctx)

    assert not ok, "缺少 smoke_test_passed.marker 时校验应该失败"
    assert err is not None
    assert "smoke_test" in err.lower() or "烟测" in err, \
        f"错误消息应该提到 smoke test，实际消息: {err}"


def test_pilot_validate_outputs_wrong_seed(experimenter_agent, pilot_workspace):
    """测试 pilot 模式输出校验 - 错误的 seed。

    验证：
    - seed 不是 42 时校验失败
    - 错误消息清晰
    """
    # 创建 pilot 输出目录
    pilot_dir = pilot_workspace / "pilot"
    pilot_dir.mkdir()

    pilot_code_dir = pilot_dir / "pilot_code"
    pilot_code_dir.mkdir()

    # 创建必需文件，但 seed 不是 42
    pilot_results = {
        "seed": 123,  # 错误的 seed
        "experiments": [{"experiment_id": "pilot_h1", "status": "DONE"}],
    }
    (pilot_dir / "pilot_results.json").write_text(
        json.dumps(pilot_results),
        encoding="utf-8"
    )

    (pilot_dir / "motivation_validation.md").write_text(
        "## 判定：PASS\n\n理由：测试通过",
        encoding="utf-8"
    )

    (pilot_code_dir / "run_pilot.py").write_text("# Code", encoding="utf-8")
    (pilot_dir / "smoke_test_passed.marker").write_text("PASS", encoding="utf-8")
    (pilot_dir / "docker_digests.txt").write_text("digest", encoding="utf-8")

    # 执行校验
    ctx = ExecutionContext(
        workspace_dir=pilot_workspace,
        project_id="test_project",
        task_id="T5",
        run_id="test_run_001",
        mode="pilot",
    )

    ok, err = experimenter_agent.validate_outputs(ctx)

    assert not ok, "seed 不是 42 时校验应该失败"
    assert err is not None
    assert "seed" in err.lower() or "种子" in err, \
        f"错误消息应该提到 seed，实际消息: {err}"
    assert "42" in err, f"错误消息应该提到 seed=42，实际消息: {err}"


def test_pilot_validate_outputs_missing_verdict(experimenter_agent, pilot_workspace):
    """测试 pilot 模式输出校验 - 缺少判定。

    验证：
    - motivation_validation.md 不包含判定时校验失败
    - 错误消息清晰
    """
    # 创建 pilot 输出目录
    pilot_dir = pilot_workspace / "pilot"
    pilot_dir.mkdir()

    pilot_code_dir = pilot_dir / "pilot_code"
    pilot_code_dir.mkdir()

    # 创建必需文件
    pilot_results = {
        "seed": 42,
        "experiments": [{"experiment_id": "pilot_h1", "status": "DONE"}],
    }
    (pilot_dir / "pilot_results.json").write_text(
        json.dumps(pilot_results),
        encoding="utf-8"
    )

    # motivation_validation.md 不包含判定
    (pilot_dir / "motivation_validation.md").write_text(
        "## 实验结果\n\n结果还不错，但没有明确判定。",
        encoding="utf-8"
    )

    (pilot_code_dir / "run_pilot.py").write_text("# Code", encoding="utf-8")
    (pilot_dir / "smoke_test_passed.marker").write_text("PASS", encoding="utf-8")
    (pilot_dir / "docker_digests.txt").write_text("digest", encoding="utf-8")

    # 执行校验
    ctx = ExecutionContext(
        workspace_dir=pilot_workspace,
        project_id="test_project",
        task_id="T5",
        run_id="test_run_001",
        mode="pilot",
    )

    ok, err = experimenter_agent.validate_outputs(ctx)

    assert not ok, "缺少判定时校验应该失败"
    assert err is not None
    assert "判定" in err or "PASS" in err or "REVISE" in err or "FAIL" in err, \
        f"错误消息应该提到判定，实际消息: {err}"
