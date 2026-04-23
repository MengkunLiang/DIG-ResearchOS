"""Writer Agent Integration Tests.

测试论文写作 Agent（T8 outline/draft/revise 模式）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.writer import WriterAgent


class TestWriterAgent:
    """Writer Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = WriterAgent()
        assert agent is not None
        assert agent.spec.name == "writer"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = WriterAgent()
        # writer agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 writer agent 没有 docker_exec 工具。"""
        agent = WriterAgent()
        # writer agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt_outline_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式的 system prompt。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建必要的文件
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text("# Synthesis\n\nContent...", encoding="utf-8")

        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\nH1: Test\n", encoding="utf-8")

        novelty_report = standard_workspace / "novelty" / "novelty_report.md"
        novelty_report.write_text("# Novelty\n\nLevel 1\n", encoding="utf-8")

        results = standard_workspace / "experiments" / "results_summary.json"
        results.write_text('{"results": []}', encoding="utf-8")

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message_outline_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "outline" in msg.lower()

    def test_agent_initial_user_message_draft_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "draft" in msg.lower()

    def test_agent_initial_user_message_revise_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 revise 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="revise",
            extra={"phase": "revise"},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "修订" in msg


class TestWriterAgentValidateOutputs:
    """Writer Agent 输出验证测试。"""

    def test_validate_outline_no_file(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建必要的依赖文件
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text("# Synthesis\n\nContent...", encoding="utf-8")

        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\nH1: Test\n", encoding="utf-8")

        novelty_report = standard_workspace / "novelty" / "novelty_report.md"
        novelty_report.write_text("# Novelty\n\nLevel 1\n", encoding="utf-8")

        results = standard_workspace / "experiments" / "results_summary.json"
        results.write_text('{"results": []}', encoding="utf-8")

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "outline" in err.lower()

    def test_validate_outline_missing_sections(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 没有章节标记时的验证。

        注意：当前实现只检查 outline 是否包含 ## 标记，不检查特定章节。
        """
        from researchos.runtime.agent import ExecutionContext

        # 创建没有 ## 章节标记的 outline（但有足够的字符数）
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text(
            "# Outline\n\n"
            "This is a short document without proper heading markers. "
            "It has enough characters to pass the length check but is missing "
            "the required markers that indicate proper structure. "
            "Each chapter should be marked to denote sections. "
            "Without these markers the document will not pass validation checks.\n" * 5,
            encoding="utf-8",
        )

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "section" in err.lower() or "章节" in err

    def test_validate_outline_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建完整的 outline
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text(
            "# Outline\n\n"
            "## Abstract\n\n"
            "A brief abstract.\n\n"
            "## 1. Introduction\n\n"
            "Introduction content.\n\n"
            "## 2. Related Work\n\n"
            "Related work content.\n\n"
            "## 3. Method\n\n"
            "Method content.\n\n"
            "## 4. Experiment\n\n"
            "Experiment content.\n\n"
            "## 5. Conclusion\n\n"
            "Conclusion content.\n\n"
            "This is a complete outline with all required sections.\n" * 5,
            encoding="utf-8",
        )

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True

    def _create_draft_paper_tex(self, workspace: Path) -> None:
        """创建测试用的 paper.tex 文件。"""
        paper = workspace / "drafts" / "paper.tex"
        paper.write_text(
            r"\documentclass{article}" + "\n"
            + r"\begin{document}" + "\n"
            + r"\section{Introduction}" + "\n"
            + "This is a test paper with enough content to pass validation.\n"
            + r"\section{Method}" + "\n"
            + "Method description with sufficient text.\n"
            + r"\section{Experiment}" + "\n"
            + "Experimental results.\n"
            + r"\section{Conclusion}" + "\n"
            + "Conclusion text.\n"
            + r"\end{document}",
            encoding="utf-8",
        )

    def test_validate_draft_no_file(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 模式无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 outline
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text("# Outline\n\nComplete outline.\n" * 10, encoding="utf-8")

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "paper" in err.lower() or "draft" in err.lower()

    def test_validate_draft_too_short(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 过短时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建过短的 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nShort.", encoding="utf-8")

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "过短" in err or "too short" in err.lower()

    def test_validate_draft_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建完整的 outline
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text(
            "# Outline\n\n"
            "## Abstract\n\n"
            "A brief abstract.\n\n"
            "## 1. Introduction\n\n"
            "Introduction content.\n\n"
            "## 2. Related Work\n\n"
            "Related work content.\n\n"
            "## 3. Method\n\n"
            "Method content.\n\n"
            "## 4. Experiment\n\n"
            "Experiment content.\n\n"
            "## 5. Conclusion\n\n"
            "Conclusion content.\n\n"
            "This is a complete outline.\n",
            encoding="utf-8",
        )

        # 创建 paper.tex（使用辅助方法）
        self._create_draft_paper_tex(standard_workspace)

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, f"Expected ok=True, got ok={ok}, err={err}"

    def test_validate_revise_no_feedback(self, standard_workspace: Path, project_yaml: Path):
        """测试 revise 模式无反馈时的验证。

        注意：当前实现中 revise 模式不强制要求反馈文件，只验证 paper.tex 结构。
        如果 paper.tex 结构完整，验证应该通过。
        """
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.tex（有效的 LaTeX 格式）
        self._create_draft_paper_tex(standard_workspace)

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="revise",
            extra={"phase": "revise"},
        )

        ok, err = agent.validate_outputs(ctx)
        # revise 模式只验证 paper.tex 结构，不强制要求反馈文件
        assert ok is True, f"Expected ok=True, got ok={ok}, err={err}"

    def test_validate_revise_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 revise 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.tex（有效的 LaTeX 格式）
        self._create_draft_paper_tex(standard_workspace)

        # 创建反馈文件
        feedback = standard_workspace / "drafts" / "review_rounds" / "feedback_round1.md"
        feedback.write_text(
            "# Feedback Round 1\n\n"
            "## Comments\n\n"
            "Please improve the writing.\n\n"
            "## Suggestions\n\n"
            "1. Fix grammar.\n"
            "2. Add more details.\n\n"
            "This is sufficient feedback.\n" * 5,
            encoding="utf-8",
        )

        # 创建修订后的 paper.md
        revised_paper = standard_workspace / "drafts" / "paper_revised.md"
        revised_paper.write_text(
            "# Revised Paper\n\n"
            "Content with improvements.\n" * 100,
            encoding="utf-8",
        )

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="revise",
            extra={"phase": "revise"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True