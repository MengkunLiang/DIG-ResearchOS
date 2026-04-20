"""ExperimenterAgent 的 pilot 和 full 模式单元测试"""

import json
from pathlib import Path

import pytest

from researchos.agents.experimenter import ExperimenterAgent


class MockExecutionContext:
    """模拟 ExecutionContext"""

    def __init__(self, mode: str, workspace_dir: Path):
        self.mode = mode
        self.workspace_dir = workspace_dir
        self.agent_name = "experimenter"
        self.task_id = "T5" if mode == "pilot" else "T7"


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时 workspace"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "ideation").mkdir()
    (ws / "pilot").mkdir()
    (ws / "experiments").mkdir()
    (ws / "project.yaml").write_text("name: test_project\n")
    (ws / "ideation" / "hypotheses.md").write_text(
        "# Test Hypotheses\n\nH1: Test hypothesis - Method X improves performance through better architecture.\n\nH2: Additional hypothesis about data augmentation effectiveness.",
        encoding="utf-8"
    )
    (ws / "ideation" / "exp_plan.yaml").write_text("experiments:\n  - name: test_exp\n")
    # Integrity Gate 要求 novelty_audit.md
    (ws / "ideation" / "novelty_audit.md").write_text(
        "# Novelty Audit\n\n## Level 2\n- H1: 新颖性高\n- H2: 新颖性中\n", encoding="utf-8"
    )
    return ws


def test_experimenter_agent_initialization():
    """测试 ExperimenterAgent 初始化"""
    agent = ExperimenterAgent()
    assert agent.spec.name == "experimenter"
    assert agent.spec.max_steps == 150
    assert agent.spec.max_tokens_total == 600_000
    assert "append_file" in agent.spec.tool_names
    assert "pilot/" in agent.spec.allowed_write_prefixes
    assert "experiments/" in agent.spec.allowed_write_prefixes


def test_pilot_mode_initial_message(temp_workspace):
    """测试 pilot 模式的初始消息"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("pilot", temp_workspace)
    msg = agent.initial_user_message(ctx)

    assert "T5 Pilot" in msg
    assert "smoke test" in msg
    assert "seed=42" in msg
    assert "motivation_validation.md" in msg
    assert "PASS/REVISE/FAIL" in msg


def test_full_mode_initial_message(temp_workspace):
    """测试 full 模式的初始消息"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("full", temp_workspace)
    msg = agent.initial_user_message(ctx)

    assert "T7 Full" in msg
    assert "ablation" in msg
    assert "seed ensemble" in msg
    assert "results_summary.json" in msg


def test_pilot_mode_validate_outputs_missing_files(temp_workspace):
    """测试 pilot 模式缺少必需文件"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("pilot", temp_workspace)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "pilot_results.json" in err or "pilot" in err


def test_pilot_mode_validate_outputs_success(temp_workspace):
    """测试 pilot 模式输出验证成功"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("pilot", temp_workspace)

    # 创建所有必需文件
    (temp_workspace / "pilot" / "pilot_code").mkdir()
    (temp_workspace / "pilot" / "pilot_results.json").write_text(
        json.dumps({"seed": 42, "accuracy": 0.85})
    )
    (temp_workspace / "pilot" / "motivation_validation.md").write_text(
        "## 判定\n\nPASS - 实验验证了假设"
    )
    (temp_workspace / "pilot" / "pilot_code" / "run_pilot.py").write_text(
        "import argparse\nparser.add_argument('--smoke_test')\nparser.add_argument('--seed')"
    )
    (temp_workspace / "pilot" / "smoke_test_passed.marker").write_text("passed")
    (temp_workspace / "pilot" / "docker_digests.txt").write_text("sha256:abc123")

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_pilot_mode_validate_outputs_wrong_seed(temp_workspace):
    """测试 pilot 模式错误的 seed"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("pilot", temp_workspace)

    # 创建文件但使用错误的 seed
    (temp_workspace / "pilot" / "pilot_code").mkdir()
    (temp_workspace / "pilot" / "pilot_results.json").write_text(
        json.dumps({"seed": 123, "accuracy": 0.85})  # 错误的 seed
    )
    (temp_workspace / "pilot" / "motivation_validation.md").write_text("PASS")
    (temp_workspace / "pilot" / "pilot_code" / "run_pilot.py").write_text("test")
    (temp_workspace / "pilot" / "smoke_test_passed.marker").write_text("passed")
    (temp_workspace / "pilot" / "docker_digests.txt").write_text("sha256:abc123")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "seed=42" in err


def test_pilot_mode_validate_outputs_missing_smoke_test(temp_workspace):
    """测试 pilot 模式缺少 smoke test marker"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("pilot", temp_workspace)

    # 创建文件但缺少 smoke_test_passed.marker
    (temp_workspace / "pilot" / "pilot_code").mkdir()
    (temp_workspace / "pilot" / "pilot_results.json").write_text(
        json.dumps({"seed": 42, "accuracy": 0.85})
    )
    (temp_workspace / "pilot" / "motivation_validation.md").write_text("PASS")
    (temp_workspace / "pilot" / "pilot_code" / "run_pilot.py").write_text("test")
    (temp_workspace / "pilot" / "docker_digests.txt").write_text("sha256:abc123")
    # 缺少 smoke_test_passed.marker

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "smoke_test_passed.marker" in err


def test_full_mode_validate_outputs_missing_ablations(temp_workspace):
    """测试 full 模式缺少 ablations.csv"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("full", temp_workspace)

    # 创建部分文件但缺少 ablations.csv
    (temp_workspace / "experiments" / "results_summary.json").write_text(
        json.dumps({"experiments": []})
    )
    (temp_workspace / "experiments" / "iteration_log.md").write_text("# Log\n" + "test " * 50)
    (temp_workspace / "experiments" / "docker_digests.txt").write_text("sha256:def456")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "ablations.csv" in err


def test_full_mode_validate_outputs_insufficient_ablations(temp_workspace):
    """测试 full 模式 ablation 数量不足"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("full", temp_workspace)

    # 创建文件但 ablations 少于 3 条
    (temp_workspace / "experiments" / "ablations.csv").write_text(
        "experiment_id,accuracy\nexp1,0.85\nexp2,0.86"  # 只有 2 条
    )
    (temp_workspace / "experiments" / "results_summary.json").write_text(
        json.dumps({"experiments": []})
    )
    (temp_workspace / "experiments" / "iteration_log.md").write_text("# Log\n" + "test " * 50)
    (temp_workspace / "experiments" / "docker_digests.txt").write_text("sha256:def456")
    (temp_workspace / "experiments" / "iteration_diversity_check.md").write_text("test")
    (temp_workspace / "experiments" / "seed_ensemble_summary.json").write_text("{}")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "至少 3 条" in err


def test_full_mode_validate_outputs_insufficient_seeds(temp_workspace):
    """测试 full 模式 seed ensemble 不足"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("full", temp_workspace)

    # 创建文件但 headline 实验的 seed 少于 3 个
    (temp_workspace / "experiments" / "ablations.csv").write_text(
        "experiment_id,accuracy\nexp1,0.85\nexp2,0.86\nexp3,0.87"
    )
    results = {
        "experiments": [
            {
                "experiment_id": "headline_exp1",
                "tier": "headline",
                "seed_runs": [{"seed": 42}, {"seed": 43}],  # 只有 2 个
                "status": "success",
                "quality_status": "ok",
            }
        ]
    }
    (temp_workspace / "experiments" / "results_summary.json").write_text(json.dumps(results))
    (temp_workspace / "experiments" / "iteration_log.md").write_text("# Log\n" + "test " * 50)
    (temp_workspace / "experiments" / "docker_digests.txt").write_text("sha256:def456")
    (temp_workspace / "experiments" / "iteration_diversity_check.md").write_text("test")
    (temp_workspace / "experiments" / "seed_ensemble_summary.json").write_text("{}")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "至少 3 个 seed" in err


def test_full_mode_validate_outputs_success(temp_workspace):
    """测试 full 模式输出验证成功"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("full", temp_workspace)

    # 创建所有必需文件且符合要求
    (temp_workspace / "experiments" / "ablations.csv").write_text(
        "experiment_id,accuracy\nexp1,0.85\nexp2,0.86\nexp3,0.87\nexp4,0.88"
    )
    results = {
        "experiments": [
            {
                "experiment_id": "headline_exp1",
                "tier": "headline",
                "seed_runs": [{"seed": 42}, {"seed": 43}, {"seed": 44}],
                "status": "success",
                "quality_status": "ok",
            },
            {
                "experiment_id": "final_exp1",
                "tier": "final_method",
                "seed_runs": [{"seed": 42}, {"seed": 43}],
                "status": "success",
                "quality_status": "ok",
            },
        ]
    }
    (temp_workspace / "experiments" / "results_summary.json").write_text(json.dumps(results))
    (temp_workspace / "experiments" / "iteration_log.md").write_text("# Log\n" + "test " * 50)
    (temp_workspace / "experiments" / "docker_digests.txt").write_text("sha256:def456")
    (temp_workspace / "experiments" / "iteration_diversity_check.md").write_text("test")
    (temp_workspace / "experiments" / "seed_ensemble_summary.json").write_text("{}")

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None
