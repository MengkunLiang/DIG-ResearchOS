"""Novelty Agent Integration Tests.

测试新颖性验证 Agent（T6）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.novelty import NoveltyAgent


class TestNoveltyAgent:
    """Novelty Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = NoveltyAgent()
        assert agent is not None
        assert agent.spec.name == "novelty"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = NoveltyAgent()
        # novelty agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 novelty agent 没有 docker_exec 工具。"""
        agent = NoveltyAgent()
        # novelty agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建假设文件
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1\n\n"
            "This is a test hypothesis.\n",
            encoding="utf-8",
        )

        agent = NoveltyAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="novelty",
            run_id="novelty_run",
            task_id="T6",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = NoveltyAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="novelty",
            run_id="novelty_run",
            task_id="T6",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "新颖性" in msg or "novelty" in msg.lower()


class TestNoveltyAgentValidateOutputs:
    """Novelty Agent 输出验证测试。"""

    def test_validate_outputs_no_files(self, standard_workspace: Path):
        """测试无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1\n\n"
            "Hypothesis content.\n",
            encoding="utf-8",
        )

        agent = NoveltyAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="novelty",
            run_id="novelty_run",
            task_id="T6",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False

    def test_validate_outputs_report_too_short(self, standard_workspace: Path):
        """测试报告过短时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建过短的 novelty_report.md
        report = standard_workspace / "novelty" / "novelty_report.md"
        report.write_text("# Report\n\nShort.", encoding="utf-8")

        # 创建 baselines
        baselines = standard_workspace / "novelty" / "must_add_baselines.md"
        baselines.write_text("# Baselines\n\nBaseline 1", encoding="utf-8")

        agent = NoveltyAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="novelty",
            run_id="novelty_run",
            task_id="T6",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "过短" in err

    def test_validate_outputs_missing_level_markers(self, standard_workspace: Path):
        """测试缺少新颖性等级标记时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建没有等级标记的报告
        report = standard_workspace / "novelty" / "novelty_report.md"
        report.write_text(
            "# Novelty Report\n\n"
            "This is a report without Level markers.\n" * 20,
            encoding="utf-8",
        )

        baselines = standard_workspace / "novelty" / "must_add_baselines.md"
        baselines.write_text("# Baselines\n\n" + "Baseline content.\n" * 5, encoding="utf-8")

        agent = NoveltyAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="novelty",
            run_id="novelty_run",
            task_id="T6",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "Level" in err

    def test_validate_outputs_baselines_too_short(self, standard_workspace: Path):
        """测试 baselines 过短时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建有等级标记的报告
        report = standard_workspace / "novelty" / "novelty_report.md"
        report.write_text(
            "# Novelty Report\n\n"
            "## H1\n\n"
            "Level 1: Incremental contribution.\n\n"
            "Analysis of novelty.\n" * 20,
            encoding="utf-8",
        )

        # 创建过短的 baselines
        baselines = standard_workspace / "novelty" / "must_add_baselines.md"
        baselines.write_text("# Baselines\n\nX", encoding="utf-8")

        agent = NoveltyAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="novelty",
            run_id="novelty_run",
            task_id="T6",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "过短" in err

    def test_validate_outputs_success(self, standard_workspace: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1\n\n"
            "Hypothesis content.\n",
            encoding="utf-8",
        )

        # 创建有等级标记的报告
        report = standard_workspace / "novelty" / "novelty_report.md"
        report.write_text(
            "# Novelty Report\n\n"
            "## H1\n\n"
            "### Novelty Level\n"
            "Level 1: Incremental contribution.\n\n"
            "### Analysis\n"
            "This is a novel contribution.\n" * 20,
            encoding="utf-8",
        )

        # 创建 baselines
        baselines = standard_workspace / "novelty" / "must_add_baselines.md"
        baselines.write_text(
            "# Must Add Baselines\n\n"
            "## Baseline 1\n"
            "Method A: Standard approach.\n"
            "### Why Required\n"
            "Standard baseline.\n\n"
            "This is a sufficient baselines document.\n" * 5,
            encoding="utf-8",
        )

        agent = NoveltyAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="novelty",
            run_id="novelty_run",
            task_id="T6",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True