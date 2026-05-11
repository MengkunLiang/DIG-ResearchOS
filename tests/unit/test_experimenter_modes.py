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
        self.project_id = "test-project"
        self.run_id = "run-test"
        self.inputs = {}
        self.outputs_expected = {}
        self.extra = {}


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
    assert agent.spec.max_steps == 2000
    assert agent.spec.max_tokens_total == 60_000_000
    assert "append_file" in agent.spec.tool_names
    assert "pilot/" in agent.spec.allowed_write_prefixes
    assert "experiments/" in agent.spec.allowed_write_prefixes


def test_experimenter_agent_mode_specific_spec():
    """测试 mode 会触发 YAML 中的分模式配置。"""
    pilot_agent = ExperimenterAgent(mode="pilot")
    full_agent = ExperimenterAgent(mode="full")

    assert pilot_agent.spec.max_steps == 1000
    assert full_agent.spec.max_steps == 5000


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


def test_full_mode_system_prompt_reads_novelty_outputs_from_novelty_dir(temp_workspace):
    """T7 应该读取 T6 产出的 novelty 目录。"""
    agent = ExperimenterAgent(mode="full")
    ctx = MockExecutionContext("full", temp_workspace)
    (temp_workspace / "novelty").mkdir(exist_ok=True)
    (temp_workspace / "novelty" / "novelty_report.md").write_text(
        "# Novelty Report\n\nUse baseline A and B.\n",
        encoding="utf-8",
    )
    (temp_workspace / "novelty" / "must_add_baselines.md").write_text(
        "- Baseline A\n- Baseline B\n",
        encoding="utf-8",
    )

    prompt = agent.system_prompt(ctx)

    assert "Use baseline A and B." in prompt
    assert "Baseline A" in prompt


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
    (temp_workspace / "pilot" / "pilot_plan.yaml").write_text(
        """
experiments:
  - name: pilot_h1
    hypothesis_ref: H1
    data_fraction: 0.1
    seed: 42
    smoke_test_required: true
"""
    )
    (temp_workspace / "pilot" / "pilot_results.json").write_text(
        json.dumps(
            {
                "experiments": [
                    {
                        "experiment_id": "pilot_h1",
                        "status": "DONE",
                        "seed": 42,
                        "metrics": {"accuracy": 0.85},
                        "duration_seconds": 1,
                        "smoke_test_passed": True,
                    }
                ]
            }
        )
    )
    (temp_workspace / "pilot" / "motivation_validation.md").write_text(
        "## 判定\n\nPASS - 实验验证了假设"
    )
    (temp_workspace / "pilot" / "pilot_code" / "run_pilot.py").write_text(
        "import argparse\nparser.add_argument('--smoke_test')\nparser.add_argument('--seed')"
    )
    (temp_workspace / "pilot" / "smoke_test_passed.marker").write_text("passed")
    (temp_workspace / "pilot" / "docker_digests.txt").write_text("researchos/system@sha256:abc123")

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_pilot_mode_validate_outputs_wrong_seed(temp_workspace):
    """测试 pilot 模式错误的 seed"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("pilot", temp_workspace)

    # 创建文件但使用错误的 seed
    (temp_workspace / "pilot" / "pilot_code").mkdir()
    (temp_workspace / "pilot" / "pilot_plan.yaml").write_text(
        """
experiments:
  - name: pilot_h1
    hypothesis_ref: H1
    data_fraction: 0.1
    seed: 42
    smoke_test_required: true
"""
    )
    (temp_workspace / "pilot" / "pilot_results.json").write_text(
        json.dumps(
            {
                "experiments": [
                    {
                        "experiment_id": "pilot_h1",
                        "status": "DONE",
                        "seed": 123,
                        "metrics": {"accuracy": 0.85},
                        "duration_seconds": 1,
                        "smoke_test_passed": True,
                    }
                ]
            }
        )
    )
    (temp_workspace / "pilot" / "motivation_validation.md").write_text("PASS")
    (temp_workspace / "pilot" / "pilot_code" / "run_pilot.py").write_text("test")
    (temp_workspace / "pilot" / "smoke_test_passed.marker").write_text("passed")
    (temp_workspace / "pilot" / "docker_digests.txt").write_text("researchos/system@sha256:abc123")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "seed=42" in err


def test_pilot_mode_validate_outputs_missing_smoke_test(temp_workspace):
    """测试 pilot 模式缺少 smoke test marker"""
    agent = ExperimenterAgent()
    ctx = MockExecutionContext("pilot", temp_workspace)

    # 创建文件但缺少 smoke_test_passed.marker
    (temp_workspace / "pilot" / "pilot_code").mkdir()
    (temp_workspace / "pilot" / "pilot_plan.yaml").write_text(
        """
experiments:
  - name: pilot_h1
    hypothesis_ref: H1
    data_fraction: 0.1
    seed: 42
    smoke_test_required: true
"""
    )
    (temp_workspace / "pilot" / "pilot_results.json").write_text(
        json.dumps(
            {
                "experiments": [
                    {
                        "experiment_id": "pilot_h1",
                        "status": "DONE",
                        "seed": 42,
                        "metrics": {"accuracy": 0.85},
                        "duration_seconds": 1,
                        "smoke_test_passed": True,
                    }
                ]
            }
        )
    )
    (temp_workspace / "pilot" / "motivation_validation.md").write_text("PASS")
    (temp_workspace / "pilot" / "pilot_code" / "run_pilot.py").write_text("test")
    (temp_workspace / "pilot" / "docker_digests.txt").write_text("researchos/system@sha256:abc123")
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
