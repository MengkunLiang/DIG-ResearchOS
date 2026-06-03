"""Reviewer Agent Integration Tests.

测试论文审阅 Agent（T8 审阅模式）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.reviewer import ReviewerAgent


class TestReviewerAgent:
    """Reviewer Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = ReviewerAgent()
        assert agent is not None
        assert agent.spec.name == "reviewer"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = ReviewerAgent()
        # reviewer agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 reviewer agent 没有 docker_exec 工具。"""
        agent = ReviewerAgent()
        # reviewer agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent...", encoding="utf-8")

        agent = ReviewerAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reviewer",
            run_id="reviewer_run",
            task_id="T8-REVIEW-1",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path, project_yaml: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ReviewerAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reviewer",
            run_id="reviewer_run",
            task_id="T8-REVIEW-1",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "review" in msg.lower() or "审阅" in msg


class TestReviewerAgentValidateOutputs:
    """Reviewer Agent 输出验证测试。"""

    def test_validate_outputs_no_file(self, standard_workspace: Path, project_yaml: Path):
        """测试无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 50, encoding="utf-8")

        agent = ReviewerAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reviewer",
            run_id="reviewer_run",
            task_id="T8-REVIEW-1",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        # 当文件不存在时，read_text_file 返回空字符串，长度为 0
        assert "过短" in err or "short" in err.lower()

    def test_validate_outputs_too_short(self, standard_workspace: Path, project_yaml: Path):
        """测试反馈过短时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 50, encoding="utf-8")

        # 创建过短的 feedback.md
        review_rounds = standard_workspace / "drafts" / "review_rounds"
        review_rounds.mkdir(parents=True, exist_ok=True)
        # ReviewerAgent 期望文件名格式: round_{round_num}.md
        feedback = review_rounds / "round_1.md"
        feedback.write_text("# Feedback\n\nShort.", encoding="utf-8")

        agent = ReviewerAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reviewer",
            run_id="reviewer_run",
            task_id="T8-REVIEW-1",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "过短" in err or "too short" in err.lower()

    def test_validate_outputs_missing_sections(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少必需章节时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 50, encoding="utf-8")

        # 创建缺少必需章节的 feedback
        review_rounds = standard_workspace / "drafts" / "review_rounds"
        review_rounds.mkdir(parents=True, exist_ok=True)
        # ReviewerAgent 期望文件名格式: round_{round_num}.md
        feedback = review_rounds / "round_1.md"
        feedback.write_text(
            "# Feedback Round 1\n\n"
            "## Comments\n\n"
            "Some comments on the paper.\n\n"
            "Additional feedback text to exceed 50 characters minimum.\n",
            encoding="utf-8",
        )

        agent = ReviewerAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reviewer",
            run_id="reviewer_run",
            task_id="T8-REVIEW-1",
            mode=None,
            extra={"round": 1},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "章节" in err or "section" in err.lower()

    def test_validate_outputs_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 50, encoding="utf-8")

        # 创建完整的 feedback
        review_rounds = standard_workspace / "drafts" / "review_rounds"
        review_rounds.mkdir(parents=True, exist_ok=True)
        # ReviewerAgent 期望文件名格式: round_{round_num}.md
        feedback = review_rounds / "round_1.md"
        feedback.write_text(
            "# Feedback Round 1\n\n"
            "## 总体评价\n\n"
            "The paper has good structure.\n\n"
            "## 主要问题\n\n"
            "1. Introduction needs more context.\n"
            "2. Method section is unclear.\n\n"
            "## 次要问题\n\n"
            "1. Grammar errors.\n"
            "2. Figure labels.\n\n"
            "## 写作范式与对齐核查\n\n"
            "- The review checks writing craft and alignment with the contribution plan.\n\n"
            "## CDR Contribution Verdict\n\n"
            "- Problem frame clarity: acceptable.\n"
            "- Design rationale support: acceptable.\n"
            "- Contribution type credibility: improvement.\n"
            "- Evidence alignment: mostly aligned.\n"
            "- Boundary condition honesty: limitations present.\n"
            "- Verdict: revise.\n\n"
            "This is a complete feedback with all required sections.\n" * 5,
            encoding="utf-8",
        )
        section_dir = review_rounds / "round_1_sections"
        section_dir.mkdir(parents=True, exist_ok=True)
        for section_id in [
            "methodology",
            "experiments",
            "related_work",
            "analysis",
            "introduction",
            "conclusion",
            "abstract",
        ]:
            (section_dir / f"{section_id}.md").write_text(
                f"# Review for {section_id}\n\n"
                "## Summary\nThis section is readable but needs targeted revision.\n\n"
                "## CDR Alignment Check\nThe section connects to the contribution map.\n\n"
                "## Alignment Matrix Check\nThe section references the planned contribution rows.\n\n"
                "## Writing Craft Check\nThe prose is acceptable for this test fixture.\n",
                encoding="utf-8",
            )

        agent = ReviewerAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reviewer",
            run_id="reviewer_run",
            task_id="T8-REVIEW-1",
            mode=None,
            extra={"round": 1},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, err


class TestReviewerAgentFeedbackQuality:
    """Reviewer Agent 反馈质量测试。"""

    def test_feedback_has_actionable_suggestions(self, standard_workspace: Path, project_yaml: Path):
        """测试反馈包含可操作的建议。"""
        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 50, encoding="utf-8")

        # 创建包含可操作建议的 feedback
        review_rounds = standard_workspace / "drafts" / "review_rounds"
        review_rounds.mkdir(parents=True, exist_ok=True)
        feedback = review_rounds / "feedback_round1.md"
        feedback.write_text(
            "# Feedback Round 1\n\n"
            "## Overall Assessment\n\n"
            "Good paper structure.\n\n"
            "## Major Issues\n\n"
            "1. [M1] Introduction: Add motivation paragraph.\n"
            "2. [M2] Method: Clarify notation.\n\n"
            "## Minor Issues\n\n"
            "1. [m1] Fix typos in Section 2.\n"
            "2. [m2] Improve figure resolution.\n\n"
            "## Suggestions\n\n"
            "1. Consider adding comparison with method X.\n"
            "2. Cite recent work on this topic.\n\n"
            "This is sufficient feedback content.\n" * 5,
            encoding="utf-8",
        )

        content = feedback.read_text(encoding="utf-8")
        assert "Overall Assessment" in content
        assert "Major Issues" in content
        assert "Suggestions" in content
        # 检查是否有具体的建议标记
        assert "[" in content or "1." in content
