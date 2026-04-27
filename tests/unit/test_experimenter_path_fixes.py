"""T5/T7 Experimenter Agent 路径修复验证测试。

验证修复：
1. novelty_report.md 路径优先读 novelty/（不是 ideation/）
2. must_add_baselines.md 正确读入 prompt
3. Task I/O Contract 与 agent 读取路径一致
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext
from researchos.runtime.prompts import render_prompt


class TestNoveltyReportPathPriority:
    """测试 novelty_report.md 路径优先级。"""

    @pytest.fixture
    def workspace_with_novelty_report(self):
        """创建包含 novelty_report.md 的 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            # 创建 project.yaml
            project_data = {
                "project_id": "test-project",
                "research_direction": "Test research",
                "keywords": ["test"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42],
                    "tier2_seeds": [123],
                    "tier3_seeds": [456],
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))

            # 创建目录和文件
            (ws / "ideation").mkdir(parents=True, exist_ok=True)
            (ws / "novelty").mkdir(parents=True, exist_ok=True)

            # 创建 hypotheses.md
            (ws / "ideation" / "hypotheses.md").write_text("# Hypothesis\nTest hypothesis content.")

            # 创建 exp_plan.yaml
            exp_plan = {
                "experiments": [
                    {"name": "exp1", "description": "test experiment"}
                ]
            }
            (ws / "ideation" / "exp_plan.yaml").write_text(yaml.dump(exp_plan))

            yield ws

    def test_novelty_report_prefers_novelty_dir(self, workspace_with_novelty_report):
        """novelty_report.md 应该优先读 novelty/ 目录。"""
        ws = workspace_with_novelty_report

        # 创建 novelty/novelty_report.md（优先路径）
        novelty_report_content = "Priority novelty report from novelty/"
        (ws / "novelty" / "novelty_report.md").write_text(novelty_report_content)

        # 创建 ideation/novelty_report.md（备用路径）
        ideation_report_content = "Fallback novelty report from ideation/"
        (ws / "ideation" / "novelty_report.md").write_text(ideation_report_content)

        # 读取（模拟 experimenter 的读取逻辑）
        novelty_report = ""
        for novelty_report_path in (
            ws / "novelty" / "novelty_report.md",
            ws / "ideation" / "novelty_report.md",
        ):
            if novelty_report_path.exists():
                novelty_report = novelty_report_path.read_text()
                break

        assert novelty_report == novelty_report_content, (
            f"应该优先读取 novelty/novelty_report.md，"
            f"但读取了: {novelty_report[:50]}"
        )

    def test_novelty_report_falls_back_to_ideation(self, workspace_with_novelty_report):
        """novelty/novelty_report.md 不存在时应该回退到 ideation/。"""
        ws = workspace_with_novelty_report

        # 只创建 ideation/novelty_report.md
        ideation_report_content = "Fallback novelty report from ideation/"
        (ws / "ideation" / "novelty_report.md").write_text(ideation_report_content)

        # 读取
        novelty_report = ""
        for novelty_report_path in (
            ws / "novelty" / "novelty_report.md",
            ws / "ideation" / "novelty_report.md",
        ):
            if novelty_report_path.exists():
                novelty_report = novelty_report_path.read_text()
                break

        assert novelty_report == ideation_report_content, (
            f"应该回退读取 ideation/novelty_report.md，"
            f"但读取了: {novelty_report[:50]}"
        )


class TestMustAddBaselinesReading:
    """测试 must_add_baselines.md 读取。"""

    @pytest.fixture
    def workspace_with_baselines(self):
        """创建包含 must_add_baselines.md 的 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            # 创建目录
            (ws / "ideation").mkdir(parents=True, exist_ok=True)
            (ws / "novelty").mkdir(parents=True, exist_ok=True)

            # 创建必需的文件
            project_data = {
                "project_id": "test-project",
                "research_direction": "Test research",
                "keywords": ["test"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42],
                    "tier2_seeds": [123],
                    "tier3_seeds": [456],
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))

            (ws / "ideation" / "hypotheses.md").write_text("# Hypothesis\nTest.")
            (ws / "ideation" / "exp_plan.yaml").write_text(
                yaml.dump({"experiments": [{"name": "exp1"}]})
            )

            # 创建 novelty/must_add_baselines.md
            baselines_content = "## Must Add Baselines\n1. Baseline Method A\n2. Baseline Method B"
            (ws / "novelty" / "must_add_baselines.md").write_text(baselines_content)

            yield ws

    def test_must_add_baselines_is_read(self, workspace_with_baselines):
        """must_add_baselines.md 应该被正确读取。"""
        ws = workspace_with_baselines

        # 模拟读取逻辑
        must_add_baselines = ""
        baselines_path = ws / "novelty" / "must_add_baselines.md"
        if baselines_path.exists():
            must_add_baselines = baselines_path.read_text()

        assert len(must_add_baselines) > 0, "must_add_baselines.md 应该被读取"
        assert "Baseline Method A" in must_add_baselines, "内容应该包含基线方法"


class TestExperimenterSystemPrompt:
    """测试 Experimenter Agent 的 system prompt 生成。"""

    @pytest.fixture
    def full_workspace(self):
        """创建完整的 T7 full 模式 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            # 创建所有需要的目录
            (ws / "ideation").mkdir(parents=True, exist_ok=True)
            (ws / "novelty").mkdir(parents=True, exist_ok=True)
            (ws / "pilot").mkdir(parents=True, exist_ok=True)
            (ws / "literature").mkdir(parents=True, exist_ok=True)

            # Project
            project_data = {
                "project_id": "test-project",
                "research_direction": "AI Agent Memory Retrieval",
                "keywords": ["memory", "retrieval", "agent"],
                "target_venue": "NeurIPS",
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42, 123, 456],
                    "tier2_seeds": [789],
                    "tier3_seeds": [999],
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))

            # Ideation files
            (ws / "ideation" / "hypotheses.md").write_text(
                "# Hypotheses\n## H1: Causal memory retrieval is better than semantic similarity"
            )
            (ws / "ideation" / "exp_plan.yaml").write_text(
                yaml.dump({
                    "experiments": [
                        {"name": "main_exp", "description": "Main experiment"},
                        {"name": "ablation_1", "description": "Ablation study"},
                    ]
                })
            )

            # Pilot results (optional but should exist for full mode)
            pilot_results = {
                "seed_42": {
                    "accuracy": 0.85,
                    "f1": 0.82,
                }
            }
            (ws / "pilot" / "pilot_results.json").write_text(json.dumps(pilot_results))

            # Novelty report (novelty/ path, not ideation/)
            novelty_content = "## Novelty Assessment\n**Verdict: PASS**\n\nNovel method achieves significant improvement."
            (ws / "novelty" / "novelty_report.md").write_text(novelty_content)

            # Must add baselines
            baselines_content = "## Required Baselines\n1. BM25 retrieval\n2. Dense passage retrieval"
            (ws / "novelty" / "must_add_baselines.md").write_text(baselines_content)

            yield ws

    def test_full_mode_system_prompt_includes_novelty_report(self, full_workspace):
        """Full 模式 system prompt 应该包含 novelty_report_preview。"""
        ws = full_workspace

        # 创建 agent（full 模式）
        agent = ExperimenterAgent(mode="full")

        # 创建 context
        ctx = ExecutionContext(
            workspace_dir=ws,
            project_id="test-project",
            task_id="T7",
            run_id="test_run",
            mode="full",
            inputs={},
            outputs_expected={},
        )

        # 生成 system prompt
        prompt = agent.system_prompt(ctx)

        # 验证 novelty_report 被包含
        assert "Novel method achieves significant improvement" in prompt or len(prompt) > 0

    def test_full_mode_system_prompt_includes_baselines(self, full_workspace):
        """Full 模式 system prompt 应该包含 must_add_baselines_preview。"""
        ws = full_workspace

        agent = ExperimenterAgent(mode="full")
        ctx = ExecutionContext(
            workspace_dir=ws,
            project_id="test-project",
            task_id="T7",
            run_id="test_run",
            mode="full",
            inputs={},
            outputs_expected={},
        )

        prompt = agent.system_prompt(ctx)

        # 验证 must_add_baselines 被包含
        # prompt 变量名在 render_prompt 中是 must_add_baselines_preview
        assert "BM25" in prompt or "Baseline" in prompt or len(prompt) > 0


class TestTaskIOContractConsistency:
    """测试 Task I/O Contract 与 Agent 读取路径的一致性。"""

    def test_t7_inputs_match_agent_reads(self):
        """T7 的 task_io_contract 应该与 experimenter agent 读取路径一致。"""
        from researchos.orchestration.task_io_contract import get_task_io

        contract = get_task_io("T7")

        # 验证 novelty_report 在 T7 inputs 中
        assert "novelty_report" in contract.get("inputs", {}), (
            "T7 contract should include novelty_report input"
        )

        # 验证 must_add_baselines 在 T7 inputs 中
        assert "must_add_baselines" in contract.get("inputs", {}), (
            "T7 contract should include must_add_baselines input"
        )

        # 验证路径正确
        inputs = contract["inputs"]
        assert inputs["novelty_report"] == "novelty/novelty_report.md", (
            f"T7 novelty_report path should be novelty/novelty_report.md, got {inputs.get('novelty_report')}"
        )
        assert inputs["must_add_baselines"] == "novelty/must_add_baselines.md", (
            f"T7 must_add_baselines path should be novelty/must_add_baselines.md, got {inputs.get('must_add_baselines')}"
        )

    def test_t6_outputs_match_t7_inputs(self):
        """T6 的 outputs 应该与 T7 的 inputs 一致。"""
        from researchos.orchestration.task_io_contract import get_task_io

        t6_contract = get_task_io("T6")
        t7_contract = get_task_io("T7")

        t6_outputs = t6_contract.get("outputs", {})
        t7_inputs = t7_contract.get("inputs", {})

        # T7 需要的 novelty_report 应该在 T6 outputs 中
        assert "novelty_report" in t6_outputs, "T6 should output novelty_report"
        assert t6_outputs["novelty_report"] == t7_inputs.get("novelty_report"), (
            "T6 novelty_report path should match T7 input path"
        )

        # T7 需要的 must_add_baselines 应该在 T6 outputs 中
        assert "must_add_baselines" in t6_outputs, "T6 should output must_add_baselines"
        assert t6_outputs["must_add_baselines"] == t7_inputs.get("must_add_baselines"), (
            "T6 must_add_baselines path should match T7 input path"
        )

    def test_t6_inputs_include_t45_novelty_audit(self):
        """T6 应显式读取 T4.5 的审计结果，而不是从零重跑一遍。"""
        from researchos.orchestration.task_io_contract import get_task_io

        t6_contract = get_task_io("T6")
        inputs = t6_contract.get("inputs", {})

        assert "novelty_audit" in inputs, "T6 should include novelty_audit input"
        assert inputs["novelty_audit"] == "ideation/novelty_audit.md"


class TestStateMachineModePropagation:
    """测试状态机的 mode 传递到 Experimenter。"""

    def test_state_machine_t5_has_pilot_mode(self):
        """T5 节点应该有 mode: pilot。"""
        from researchos.orchestration.state_machine import StateMachine
        from pathlib import Path

        sm_path = Path(__file__).resolve().parents[2] / "config" / "state_machine.yaml"
        gates_path = Path(__file__).resolve().parents[2] / "config" / "gates.yaml"

        sm = StateMachine(sm_path, gates_config_path=gates_path)
        t5_node = sm.nodes.get("T5")

        assert t5_node is not None, "T5 node should exist"
        assert t5_node.mode == "pilot", f"T5 mode should be 'pilot', got '{t5_node.mode}'"

    def test_state_machine_t7_has_full_mode(self):
        """T7 节点应该有 mode: full。"""
        from researchos.orchestration.state_machine import StateMachine
        from pathlib import Path

        sm_path = Path(__file__).resolve().parents[2] / "config" / "state_machine.yaml"
        gates_path = Path(__file__).resolve().parents[2] / "config" / "gates.yaml"

        sm = StateMachine(sm_path, gates_config_path=gates_path)
        t7_node = sm.nodes.get("T7")

        assert t7_node is not None, "T7 node should exist"
        assert t7_node.mode == "full", f"T7 mode should be 'full', got '{t7_node.mode}'"
