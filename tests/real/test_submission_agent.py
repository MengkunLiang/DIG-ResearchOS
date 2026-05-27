"""Submission Agent Integration Tests.

测试论文提交 Agent（T9）。
注意：submission 需要 Docker（LaTeX 编译）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.submission import SubmissionAgent


class TestSubmissionAgent:
    """Submission Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = SubmissionAgent()
        assert agent is not None
        assert agent.spec.name == "submission"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = SubmissionAgent()
        # submission agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_docker_exec(self):
        """测试 submission agent 有 docker_exec 工具（LaTeX 编译）。"""
        agent = SubmissionAgent()
        # submission agent 需要 docker_exec（因为编译 LaTeX）
        assert "docker_exec" in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent...", encoding="utf-8")

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path, project_yaml: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "submission" in msg.lower() or "提交" in msg


class TestSubmissionAgentValidateOutputs:
    """Submission Agent 输出验证测试。"""

    def test_validate_outputs_no_files(self, standard_workspace: Path, project_yaml: Path):
        """测试无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 100, encoding="utf-8")

        # 创建 project.yaml
        project = standard_workspace / "project.yaml"
        project.write_text(
            "name: test\n"
            "target_venue: neurips2026\n",
            encoding="utf-8",
        )

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False

    def test_validate_outputs_missing_bib(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少 bib 文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 100, encoding="utf-8")

        # 创建 project.yaml
        project = standard_workspace / "project.yaml"
        project.write_text(
            "name: test\n"
            "target_venue: neurips2026\n",
            encoding="utf-8",
        )

        # 创建 bundle 目录（缺少 main.tex）
        bundle = standard_workspace / "submission" / "bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "main.pdf").write_text("%PDF-1.4", encoding="utf-8")

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "main.tex" in err or "bib" in err.lower()

    def test_validate_outputs_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 100, encoding="utf-8")

        # 创建 project.yaml
        project = standard_workspace / "project.yaml"
        project.write_text(
            "name: test\n"
            "target_venue: neurips2026\n",
            encoding="utf-8",
        )

        # 创建完整的 bundle
        bundle = standard_workspace / "submission" / "bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "Test\n"
            "\\end{document}",
            encoding="utf-8",
        )
        (bundle / "references.bib").write_text(
            "@article{test,\n  title={Test}\n}",
            encoding="utf-8",
        )
        (bundle / "main.pdf").write_text("%PDF-1.4", encoding="utf-8")

        # 创建 migration_report.md（SubmissionAgent 必需）
        report = standard_workspace / "submission" / "migration_report.md"
        report.write_text(
            "# Migration Report\n\n"
            "## 迁移状态\n\n"
            "所有文件已成功迁移。\n\n"
            "## 编译状态\n\n"
            "编译状态: 成功\n\n"
            "## 匿名化检查\n\n"
            "已完成匿名化处理。\n\n"
            "This is sufficient content for the migration report to pass validation.",
            encoding="utf-8",
        )

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True


class TestSubmissionAgentDockerDependency:
    """Submission Agent Docker 依赖测试。"""

    def test_submission_docker_boundary(self):
        """测试 submission 在 Docker 边界内。"""
        agent = SubmissionAgent()
        # submission 需要 Docker 执行 LaTeX 编译
        assert "docker_exec" in agent.spec.tool_names

    def test_only_experimenter_and_submission_require_docker(self):
        """测试只有 experimenter 和 submission 需要 Docker。"""
        from researchos.agents.hello import HelloAgent
        from researchos.agents.pi import PIAgent
        from researchos.agents.scout import ScoutAgent
        from researchos.agents.reader import ReaderAgent
        from researchos.agents.ideation import IdeationAgent
        from researchos.agents.novelty import NoveltyAgent
        from researchos.agents.novelty_auditor import NoveltyAuditorAgent
        from researchos.agents.writer import WriterAgent
        from researchos.agents.reviewer import ReviewerAgent

        # 确认其他 agent 不需要 docker_exec
        non_docker_agents = [
            HelloAgent(),
            PIAgent(),
            ScoutAgent(),
            ReaderAgent(),
            IdeationAgent(),
            NoveltyAuditorAgent(),
            NoveltyAgent(),
            WriterAgent(),
            ReviewerAgent(),
        ]

        for agent in non_docker_agents:
            assert (
                "docker_exec" not in agent.spec.tool_names
            ), f"{agent.spec.name} should not require docker_exec"
