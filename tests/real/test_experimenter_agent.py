"""Experimenter Agent Integration Tests.

测试实验执行 Agent（T5 pilot 模式和 T7 full 模式）。
注意：这些测试验证 Docker 依赖边界 - experimenter 需要 Docker。
"""

from __future__ import annotations

from pathlib import Path
import json
import yaml

import pytest

from researchos.agents.experimenter import ExperimenterAgent


class TestExperimenterAgent:
    """Experimenter Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = ExperimenterAgent()
        assert agent is not None
        assert agent.spec.name == "experimenter"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = ExperimenterAgent()
        # experimenter agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_does_need_docker_exec(self):
        """测试 experimenter agent 需要 docker_exec 工具。"""
        agent = ExperimenterAgent()
        # experimenter agent 需要 docker_exec（因为执行实验代码）
        assert "docker_exec" in agent.spec.tool_names

    def test_agent_system_prompt_pilot_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 pilot 模式的 system prompt。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1\n\n"
            "This is a test hypothesis.\n",
            encoding="utf-8",
        )

        # 创建 exp_plan.yaml
        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Test\n",
            encoding="utf-8",
        )

        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="experimenter",
            run_id="experimenter_run",
            task_id="T5",
            mode="pilot",
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message_pilot_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 pilot 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="experimenter",
            run_id="experimenter_run",
            task_id="T5",
            mode="pilot",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "pilot" in msg.lower()

    def test_agent_initial_user_message_full_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 full 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="experimenter",
            run_id="experimenter_run",
            task_id="T7",
            mode="full",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "full" in msg.lower()


class TestExperimenterAgentValidateOutputs:
    """Experimenter Agent 输出验证测试。"""

    def test_validate_pilot_no_results(self, standard_workspace: Path, project_yaml: Path):
        """测试 pilot 模式无结果时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md（至少 50 字符以通过 Integrity Gate）
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\nH1: Test hypothesis with sufficient content for integrity gate.\n"
            "This hypothesis needs more than 50 characters to pass the basic check.\n",
            encoding="utf-8",
        )

        # 创建 exp_plan.yaml (必须是正确的格式)
        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Test\n"
            "experiments:\n"
            "  - id: E1\n"
            "    title: Test Experiment\n",
            encoding="utf-8",
        )

        # 创建 novelty_audit.md (必须包含 Level)
        novelty_audit = standard_workspace / "ideation" / "novelty_audit.md"
        novelty_audit.write_text("# Novelty Audit\n\nLevel 2: 中等新颖性\n", encoding="utf-8")

        # 创建 pilot_plan.yaml（T5 当前契约必需，且需要满足 schema）
        pilot_plan = standard_workspace / "pilot" / "pilot_plan.yaml"
        pilot_plan.write_text(
            yaml.dump(
                {
                    "goal": "Pilot validation",
                    "experiments": [
                        {
                            "name": "pilot_e1",
                            "hypothesis_ref": "H1",
                            "dataset": "test_dataset",
                            "data_fraction": 0.1,
                            "seed": 42,
                            "smoke_test_required": True,
                            "success_criteria": ["accuracy > 0.8"],
                        }
                    ],
                    "success_criteria": ["smoke test passes", "seed is fixed"],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        # 不创建 pilot_results.json，验证应该失败
        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="experimenter",
            run_id="experimenter_run",
            task_id="T5",
            mode="pilot",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "pilot" in err.lower() or "results" in err.lower() or "smoke" in err.lower()

    def test_validate_pilot_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 pilot 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md（至少 50 字符）
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\nH1: Test hypothesis with sufficient content.\n"
            "This hypothesis needs more than 50 characters to pass the basic check.\n",
            encoding="utf-8",
        )

        # 创建 exp_plan.yaml (正确的格式)
        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Test\n"
            "experiments:\n"
            "  - id: E1\n"
            "    title: Test Experiment\n",
            encoding="utf-8",
        )

        # 创建 novelty_audit.md
        novelty_audit = standard_workspace / "ideation" / "novelty_audit.md"
        novelty_audit.write_text("# Novelty Audit\n\nLevel 2: 中等新颖性\n", encoding="utf-8")

        # 创建 pilot_plan.yaml（T5 当前契约必需，且需要满足 schema）
        pilot_plan = standard_workspace / "pilot" / "pilot_plan.yaml"
        pilot_plan.write_text(
            yaml.dump(
                {
                    "goal": "Pilot validation",
                    "experiments": [
                        {
                            "name": "pilot_e1",
                            "hypothesis_ref": "H1",
                            "dataset": "test_dataset",
                            "data_fraction": 0.1,
                            "seed": 42,
                            "smoke_test_required": True,
                            "success_criteria": ["accuracy > 0.8"],
                        }
                    ],
                    "success_criteria": ["smoke test passes", "seed is fixed"],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        # 创建 pilot/pilot_code/run_pilot.py（必须存在）
        pilot_code_dir = standard_workspace / "pilot" / "pilot_code"
        pilot_code_dir.mkdir(parents=True, exist_ok=True)
        (pilot_code_dir / "run_pilot.py").write_text(
            "#!/usr/bin/env python3\n"
            '"""Pilot experiment runner."""\n'
            "import argparse\n"
            "def main():\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument('--smoke_test', action='store_true')\n"
            "    parser.add_argument('--seed', type=int, default=42)\n"
            "    # ... main logic\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )

        # 创建 pilot_results.json（必须包含 seed: 42）
        pilot_results = standard_workspace / "pilot" / "pilot_results.json"
        pilot_results.write_text(
            json.dumps(
                {
                    "seed": 42,
                    "total_experiments": 1,
                    "successful": 1,
                    "experiments": [
                        {
                            "experiment_id": "pilot_e1",
                            "hypothesis_ref": "H1",
                            "status": "DONE",
                            "seed": 42,
                            "metrics": {"accuracy": 0.85},
                            "duration_seconds": 12.5,
                            "smoke_test_passed": True,
                            "error": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 创建 smoke_test_passed.marker
        smoke_marker = standard_workspace / "pilot" / "smoke_test_passed.marker"
        smoke_marker.write_text("smoke test passed", encoding="utf-8")

        # 创建 motivation_validation.md (必须包含 PASS/REVISE/FAIL)
        motivation = standard_workspace / "pilot" / "motivation_validation.md"
        motivation.write_text("# Motivation Validation\n\n## 判定\n\nPASS\n", encoding="utf-8")

        # 创建 docker_digests.txt（§8.2 复现保证）
        digest_file = standard_workspace / "pilot" / "docker_digests.txt"
        digest_file.write_text("sha256:abc123def456\n", encoding="utf-8")

        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="experimenter",
            run_id="experimenter_run",
            task_id="T5",
            mode="pilot",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, f"Expected validation to pass, but got error: {err}"

    def test_validate_full_no_results(self, standard_workspace: Path, project_yaml: Path):
        """测试 full 模式无结果时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\nH1: Test hypothesis with enough content.\n", encoding="utf-8")

        # 创建 novelty_audit.md
        novelty_audit = standard_workspace / "ideation" / "novelty_audit.md"
        novelty_audit.write_text("# Novelty Audit\n\nLevel 2: 中等新颖性\n", encoding="utf-8")

        # 创建 exp_plan.yaml
        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "experiments:\n"
            "  - id: E1\n"
            "    title: Test Experiment\n",
            encoding="utf-8",
        )

        # 创建 pilot_results.json (full 模式需要)
        pilot_results = standard_workspace / "pilot" / "pilot_results.json"
        pilot_results.write_text('{"status": "success"}', encoding="utf-8")

        # 不创建 results_summary.json，验证应该失败
        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="experimenter",
            run_id="experimenter_run",
            task_id="T7",
            mode="full",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "results_summary" in err.lower() or "full" in err.lower()

    def test_validate_full_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 full 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md（至少 50 字符）
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\nH1: Test hypothesis with sufficient content for integrity gate.\n"
            "This hypothesis needs more than 50 characters to pass the basic check.\n",
            encoding="utf-8",
        )

        # 创建 novelty_audit.md
        novelty_audit = standard_workspace / "ideation" / "novelty_audit.md"
        novelty_audit.write_text("# Novelty Audit\n\nLevel 2: 中等新颖性\n", encoding="utf-8")

        # 创建 exp_plan.yaml
        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Test\n"
            "experiments:\n"
            "  - id: E1\n"
            "    title: Test Experiment\n",
            encoding="utf-8",
        )

        # 创建 pilot_results.json
        pilot_results = standard_workspace / "pilot" / "pilot_results.json"
        pilot_results.write_text('{"status": "success", "seed": 42}', encoding="utf-8")

        # 创建 experiments/runs/exp_H1/main.py（代码目录）
        exp_code_dir = standard_workspace / "experiments" / "runs" / "exp_H1"
        exp_code_dir.mkdir(parents=True, exist_ok=True)
        (exp_code_dir / "main.py").write_text("# Main\nprint('test')", encoding="utf-8")

        # 创建 experiments/code/run_exp.py（必须存在）
        exp_code = standard_workspace / "experiments" / "code"
        exp_code.mkdir(parents=True, exist_ok=True)
        (exp_code / "run_exp.py").write_text(
            "#!/usr/bin/env python3\n"
            '"""Full experiment runner."""\n'
            "def main():\n"
            "    print('experiment')\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )

        # 创建 results_summary.json（包含 headline 和 final_method 实验，每个有足够的 seed）
        results_summary = standard_workspace / "experiments" / "results_summary.json"
        results_summary.write_text(
            '{"experiments": ['
            '{"experiment_id": "exp_headline", "tier": "headline", '
            '"seed_runs": [{"seed": 42}, {"seed": 123}, {"seed": 456}], '
            '"metrics": {"accuracy": 0.85}, "quality_status": "ok"}, '
            '{"experiment_id": "exp_final", "tier": "final_method", '
            '"seed_runs": [{"seed": 42}, {"seed": 123}], '
            '"metrics": {"accuracy": 0.88}, "quality_status": "ok"}'
            ']}',
            encoding="utf-8",
        )

        # 创建 ablations.csv（至少 3 条记录）
        ablations = standard_workspace / "experiments" / "ablations.csv"
        ablations.write_text(
            "ablation_id,component,result\na1,encoder,0.82\na2,decoder,0.80\na3,attention,0.78\na4,bottleneck,0.75\n",
            encoding="utf-8",
        )

        # 创建 iteration_log.md（至少 100 字符）
        iteration_log = standard_workspace / "experiments" / "iteration_log.md"
        iteration_log.write_text(
            "# Iteration Log\n\n## Iteration 1\n\nCompleted baseline experiment with seed=42.\n"
            "Results show reasonable performance with accuracy=0.85.\n\n"
            "## Iteration 2\n\nRan ablation studies to verify component contributions.\n"
            "Found that encoder component has highest impact.\n\n"
            "## Summary\n\nMultiple perspectives analyzed: efficiency, accuracy, and robustness.\n",
            encoding="utf-8",
        )

        # 创建 docker_digests.txt（§8.2 复现保证）
        digest_file = standard_workspace / "experiments" / "docker_digests.txt"
        digest_file.write_text("sha256:abc123def456\n", encoding="utf-8")

        # 创建 iteration_diversity_check.md（§5.1 迭代多样性检查）
        diversity_check = standard_workspace / "experiments" / "iteration_diversity_check.md"
        diversity_check.write_text(
            "# Iteration Diversity Check\n\n## Analysis\n\nEach iteration explored different hyperparameters.\n"
            "Diversity score: 0.85 (acceptable)\n",
            encoding="utf-8",
        )

        # 创建 seed_ensemble_summary.json
        ensemble_summary = standard_workspace / "experiments" / "seed_ensemble_summary.json"
        ensemble_summary.write_text(
            '{"headline": {"mean": 0.85, "std": 0.02, "seeds": [42, 123, 456]}, '
            '"final_method": {"mean": 0.88, "std": 0.01, "seeds": [42, 123]}}',
            encoding="utf-8",
        )

        agent = ExperimenterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="experimenter",
            run_id="experimenter_run",
            task_id="T7",
            mode="full",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, f"Expected validation to pass, but got error: {err}"


class TestExperimenterAgentDockerDependency:
    """Experimenter Agent Docker 依赖测试。"""

    def test_experimenter_docker_boundary(self):
        """测试 experimenter 在 Docker 边界内。"""
        agent = ExperimenterAgent()
        # experimenter 需要 Docker 执行实验代码
        assert "docker_exec" in agent.spec.tool_names

    def test_experimenter_only_agent_requiring_docker(self):
        """测试只有 experimenter 需要 Docker（验证边界）。"""
        from researchos.agents.hello import HelloAgent
        from researchos.agents.pi import PIAgent
        from researchos.agents.scout import ScoutAgent
        from researchos.agents.reader import ReaderAgent
        from researchos.agents.ideation import IdeationAgent
        from researchos.agents.novelty import NoveltyAgent
        from researchos.agents.writer import WriterAgent
        from researchos.agents.reviewer import ReviewerAgent

        # 确认其他 agent 不需要 docker_exec
        non_docker_agents = [
            HelloAgent(),
            PIAgent(),
            ScoutAgent(),
            ReaderAgent(),
            IdeationAgent(),
            NoveltyAgent(),
            WriterAgent(),
            ReviewerAgent(),
        ]

        for agent in non_docker_agents:
            assert (
                "docker_exec" not in agent.spec.tool_names
            ), f"{agent.spec.name} should not require docker_exec"
