"""Ideation Agent Integration Tests.

测试头脑风暴 Agent（T4）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.ideation import IdeationAgent


def _write_valid_idea_rationales(workspace: Path, refs: list[str] | None = None) -> None:
    refs = refs or ["H1"]
    (workspace / "ideation" / "idea_scorecard.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea": {
                            "id": "D1",
                            "title": "Test rationale",
                            "pitch": "A traceable idea generated from synthesis gaps.",
                            "core_claim": "The proposed mechanism improves a measurable metric.",
                            "target_problem": "Prior methods leave a measurable gap.",
                        },
                        "hypothesis_refs": refs,
                        "source": {
                            "from_synthesis_section": "synthesis.md: Q1",
                            "from_missing_area": "missing_areas.md: mechanism gap",
                            "from_seed_idea": False,
                            "supporting_papers": [
                                {
                                    "title": "Prior Paper",
                                    "claim_used": "Prior methods leave a measurable gap.",
                                }
                            ],
                            "trigger_observation": "The synthesis gap points to a pilotable mechanism.",
                        },
                        "selection_rationale": {
                            "novelty_reason": "The mechanism is underexplored.",
                            "feasibility_reason": "A small pilot is enough.",
                            "impact_reason": "The problem matters.",
                            "evaluability_reason": "Metrics are clear.",
                            "paper_story": "Problem, method, and experiment align.",
                        },
                        "closest_baselines": [
                            {
                                "name": "Baseline",
                                "similarity": "Same target problem.",
                                "difference": "Different mechanism.",
                            }
                        ],
                        "scores": {
                            "novelty": 4,
                            "feasibility": 4,
                            "impact": 4,
                            "evaluability": 5,
                            "differentiation": 3,
                            "cost": 5,
                            "contribution_strength": 4,
                        },
                        "decision": {
                            "status": "selected",
                            "selected_reason": ["clear story", "cheap pilot"],
                            "selected_by": "user",
                            "user_feedback": "select D1",
                        },
                        "risks": [
                            {
                                "risk": "No gain",
                                "early_signal": "Pilot fails",
                                "mitigation": "Run ablation",
                                "kill_criteria": "No improvement",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "small data",
                            "baseline": "Baseline",
                            "metric": ["accuracy"],
                            "expected_signal": "improvement",
                            "estimated_cost_usd": 5,
                        },
                    },
                    {
                        "idea": {
                            "id": "D2",
                            "title": "Rejected idea",
                            "pitch": "Too close to prior work.",
                            "core_claim": "A weak transfer may work.",
                            "target_problem": "Weak gap.",
                        },
                        "hypothesis_refs": [],
                        "source": {
                            "from_synthesis_section": "synthesis.md: Q2",
                            "from_missing_area": "missing_areas.md: weak gap",
                            "from_seed_idea": False,
                            "supporting_papers": [
                                {
                                    "title": "Nearby Paper",
                                    "claim_used": "Prior work already covers it.",
                                }
                            ],
                            "trigger_observation": "Direct transfer idea.",
                        },
                        "selection_rationale": {
                            "novelty_reason": "Weak novelty.",
                            "feasibility_reason": "Feasible.",
                            "impact_reason": "Limited impact.",
                            "evaluability_reason": "Metrics unclear.",
                            "paper_story": "Story too thin.",
                        },
                        "closest_baselines": [
                            {
                                "name": "Nearby Paper",
                                "similarity": "Very similar.",
                                "difference": "Mostly scenario change.",
                            }
                        ],
                        "scores": {
                            "novelty": 2,
                            "feasibility": 4,
                            "impact": 2,
                            "evaluability": 2,
                            "differentiation": 2,
                            "cost": 4,
                            "contribution_strength": 2,
                        },
                        "decision": {
                            "status": "rejected",
                            "rejection_reason": ["too close to prior work"],
                            "can_revisit_if": "Find a stronger mechanism.",
                        },
                        "risks": [
                            {
                                "risk": "Low novelty",
                                "early_signal": "High overlap",
                                "mitigation": "Find differentiation",
                                "kill_criteria": "Only scenario change",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "small data",
                            "baseline": "Nearby Paper",
                            "metric": ["accuracy"],
                            "expected_signal": "large improvement",
                            "estimated_cost_usd": 5,
                        },
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "rejected_ideas.md").write_text(
        "# Rejected / Deferred Ideas\n\n"
        "## D2: Rejected idea\n\n"
        "- **Status**: rejected\n"
        "- **Why rejected**:\n"
        "  - too close to prior work\n"
        "- **Closest existing work**: Nearby Paper.\n"
        "- **Missing evidence / metric**: stronger mechanism.\n"
        "- **Can revisit if**: Find a stronger mechanism.\n"
        "- **Cheap pilot that was not chosen**: small data is not enough.\n",
        encoding="utf-8",
    )
    (workspace / "ideation" / "gate_decisions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "decisions": [
                    {
                        "gate_id": "T4-DECIDE-1",
                        "action": "select_direction",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": ["D2"],
                        "selected_by": "user",
                        "rationale": ["D1 clearer", "D2 too close"],
                    },
                    {
                        "gate_id": "T4-DECIDE-2",
                        "action": "confirm_plan",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": [],
                        "selected_by": "user",
                        "rationale": ["plan ok"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "idea_rationales.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea_id": "D1",
                        "hypothesis_refs": refs,
                        "title": "Test rationale",
                        "idea_summary": "A traceable idea generated from synthesis gaps.",
                        "basis": {
                            "source_questions": ["Q1"],
                            "literature_observations": [
                                {
                                    "claim": "Prior methods leave a measurable gap.",
                                    "source": "synthesis.md: Q1 / [p1]",
                                    "strength": "direct",
                                }
                            ],
                            "missing_area_links": ["missing_areas.md: mechanism gap"],
                            "comparison_table_signals": [],
                            "seed_idea_links": [],
                            "lens_insights": ["causal: the mechanism is experimentally separable"],
                        },
                        "reasoning": "The synthesis gap points to a measurable mechanism hypothesis.",
                        "confidence": "medium",
                        "limitations": ["Needs novelty audit."],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


class TestIdeationAgent:
    """Ideation Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = IdeationAgent()
        assert agent is not None
        assert agent.spec.name == "ideation"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = IdeationAgent()
        # ideation agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 ideation agent 没有 docker_exec 工具。"""
        agent = IdeationAgent()
        # ideation agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Synthesis\n\n"
            "## Method Families\n\n"
            "Family 1\n\n"
            "## Research Questions\n\n"
            "[p1] Question 1?\n",
            encoding="utf-8",
        )

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path, project_yaml: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert len(msg) > 0


class TestIdeationAgentValidateOutputs:
    """Ideation Agent 输出验证测试。"""

    def test_validate_outputs_no_hypotheses(self, standard_workspace: Path, project_yaml: Path):
        """测试无假设文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text("# Synthesis\n\nContent...", encoding="utf-8")

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "hypotheses.md" in err

    def test_validate_outputs_hypotheses_too_short(self, standard_workspace: Path, project_yaml: Path):
        """测试假设文件过短时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建过短的 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\nH1", encoding="utf-8")

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "过短" in err

    def test_validate_outputs_missing_exp_plan(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少实验计划时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1: Title\n\n"
            "### Hypothesis\n"
            "This is a test hypothesis with sufficient content.\n\n"
            "### Evidence\n"
            "Evidence supporting this hypothesis.\n\n"
            "This is a longer hypothesis document.\n" * 10,
            encoding="utf-8",
        )

        # 缺少 exp_plan.yaml
        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "exp_plan.yaml" in err

    def test_validate_outputs_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1: Test Hypothesis\n\n"
            "### Hypothesis\n"
            "This is a test hypothesis with sufficient content.\n\n"
            "### Evidence\n"
            "Evidence supporting this hypothesis.\n\n"
            "This is a longer hypothesis document.\n" * 10,
            encoding="utf-8",
        )

        # 创建 exp_plan.yaml
        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Test Hypothesis\n"
            "    priority: high\n"
            "datasets:\n"
            "  - name: dataset1\n"
            "experiments:\n"
            "  - name: exp1\n"
            "    hypothesis_ref: H1\n"
            "    compute_estimate:\n"
            "      gpu_hours: 10\n",
            encoding="utf-8",
        )

        # 创建 risks.md（至少需要3条风险）
        risks = standard_workspace / "ideation" / "risks.md"
        risks.write_text(
            "# Risks\n\n"
            "## 风险 1: 数据质量风险\n\n"
            "数据可能存在噪声。\n\n"
            "## 风险 2: 计算资源风险\n\n"
            "可能超出预算。\n\n"
            "## 风险 3: 时间风险\n\n"
            "进度可能延迟。\n",
            encoding="utf-8",
        )
        _write_valid_idea_rationales(standard_workspace)

        agent = IdeationAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="ideation",
            run_id="ideation_run",
            task_id="T4",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True


class TestIdeationAgentHypothesisStructure:
    """Ideation Agent 假设结构测试。"""

    def test_hypothesis_has_required_fields(self, standard_workspace: Path, project_yaml: Path):
        """测试假设是否包含必需字段。"""
        from researchos.runtime.agent import ExecutionContext

        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1: Efficient Attention\n\n"
            "### Hypothesis\n"
            "We propose a new attention mechanism that reduces complexity.\n\n"
            "### Mechanism\n"
            "The mechanism uses X to achieve O(n) complexity.\n\n"
            "### Evidence\n"
            "Prior work shows X is effective.\n\n"
            "### Risk Level\n"
            "Medium\n\n"
            "This is a test hypothesis.\n" * 20,
            encoding="utf-8",
        )

        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Efficient Attention\n"
            "    priority: high\n",
            encoding="utf-8",
        )

        # 验证假设内容
        content = hypotheses.read_text(encoding="utf-8")
        assert "Hypothesis" in content
        assert "Mechanism" in content
        assert "Evidence" in content
        assert "H1" in content
