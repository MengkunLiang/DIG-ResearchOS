"""Reader Agent Integration Tests.

测试文献阅读 Agent（T3 read 模式和 T3.5 synthesize 模式）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.reader import ReaderAgent


class TestReaderAgent:
    """Reader Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = ReaderAgent()
        assert agent is not None
        assert agent.spec.name == "reader"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = ReaderAgent()
        # reader agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 reader agent 没有 docker_exec 工具。"""
        agent = ReaderAgent()
        # reader agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt_read_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 read 模式的 system prompt。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.write_text(
            '{"id": "p1", "title": "Paper 1"}\n'
            '{"id": "p2", "title": "Paper 2"}\n',
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_system_prompt_synthesize_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 synthesize 模式的 system prompt。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper_notes
        notes_dir = standard_workspace / "literature" / "paper_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "p1.md").write_text("# Paper 1\n\nNotes...", encoding="utf-8")
        (notes_dir / "p2.md").write_text("# Paper 2\n\nNotes...", encoding="utf-8")

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message_read_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 read 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "read" in msg.lower() or "paper" in msg.lower()

    def test_agent_initial_user_message_synthesize_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 synthesize 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "synthesize" in msg.lower() or "synthesis" in msg.lower()


class TestReaderAgentValidateReadOutputs:
    """Reader Agent T3 (read) 输出验证测试。"""

    def test_validate_read_outputs_no_notes(self, standard_workspace: Path, project_yaml: Path):
        """测试无笔记时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.write_text(
            '{"id": "p1", "title": "Paper 1"}\n'
            '{"id": "p2", "title": "Paper 2"}\n'
            '{"id": "p3", "title": "Paper 3"}\n',
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "paper_notes" in err

    def test_validate_read_outputs_insufficient_notes(self, standard_workspace: Path, project_yaml: Path):
        """测试笔记数量不足时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl（10 篇论文）
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.parent.mkdir(parents=True, exist_ok=True)
        papers_dedup.write_text(
            "\n".join(f'{{"id": "p{i}", "title": "Paper {i}"}}' for i in range(10)) + "\n",
            encoding="utf-8",
        )

        # 创建 paper_notes（只有 3 篇笔记，不足 80%）
        notes_dir = standard_workspace / "literature" / "paper_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (notes_dir / f"p{i}.md").write_text("# Paper\n\nNotes...", encoding="utf-8")

        # 创建 comparison_table.csv 和 related_work.bib
        ct = standard_workspace / "literature" / "comparison_table.csv"
        ct.write_text("Method,Accuracy\nMethod1,0.9\n", encoding="utf-8")

        bib = standard_workspace / "literature" / "related_work.bib"
        bib.write_text("@article{key1,\n  title={Title}\n}", encoding="utf-8")

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "笔记" in err or "note" in err.lower()

    def test_validate_read_outputs_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 papers_dedup.jsonl（5 篇论文）
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        for i in range(5):
            papers_dedup.write_text(
                f'{{"id": "p{i}", "title": "Paper {i}"}}\n',
                encoding="utf-8",
            )

        # 创建 paper_notes（5 篇笔记，满足 80%）
        notes_dir = standard_workspace / "literature" / "paper_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (notes_dir / f"p{i}.md").write_text("# Paper\n\nNotes...", encoding="utf-8")

        # 创建 comparison_table.csv
        ct = standard_workspace / "literature" / "comparison_table.csv"
        ct.write_text("Method,Accuracy\nMethod1,0.9\n", encoding="utf-8")

        # 创建 related_work.bib
        bib = standard_workspace / "literature" / "related_work.bib"
        bib.write_text("@article{key1,\n  title={Title}\n}", encoding="utf-8")

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3",
            mode="read",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True


class TestReaderAgentValidateSynthesizeOutputs:
    """Reader Agent T3.5 (synthesize) 输出验证测试。"""

    def test_validate_synthesize_no_file(self, standard_workspace: Path, project_yaml: Path):
        """测试无 synthesis.md 时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "synthesis.md" in err

    def test_validate_synthesize_missing_sections(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少必需章节时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建不完整的 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Synthesis\n\n"
            "Only a brief intro.\n",
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "章节" in err or "section" in err.lower()

    def test_validate_synthesize_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建完整的 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Literature Synthesis\n\n"
            "## Method Families\n\n"
            "Several method families have been proposed in recent years. "
            "These include family A based on approach X, family B using technique Y, "
            "and family C employing method Z. Each family has distinct characteristics "
            "and trade-offs that are important for practitioners to understand. "
            "Family A tends to excel in scenarios requiring high accuracy but demands "
            "significant computational resources. Family B offers a balance between "
            "performance and efficiency, making it suitable for practical applications. "
            "Family C represents the latest advances in the field, combining elements "
            "from both previous approaches while introducing novel techniques.\n\n"
            "## Shared Assumptions\n\n"
            "All methods assume X as their fundamental premise. "
            "This includes the availability of training data, the assumption that "
            "patterns in the data are generalizable, and that evaluation metrics "
            "appropriately capture the desired outcomes. These assumptions are critical "
            "for understanding the limitations and potential failure modes of each approach.\n\n"
            "## Performance-Efficiency Frontier\n\n"
            "There is a trade-off between performance and efficiency. "
            "High-performance methods typically require more computational resources, "
            "while efficient methods may sacrifice some accuracy. "
            "This frontier represents the current state of the art. "
            "The research community continues to push the boundaries of both dimensions.\n\n"
            "## Technology Trends\n\n"
            "Trends include A and B. Emerging approaches are focusing on reducing "
            "computational requirements while maintaining accuracy. "
            "There is also growing interest in interpretability and fairness. "
            "These trends reflect the maturation of the field and its increasing "
            "practical relevance across various application domains.\n\n"
            "## Research Questions\n\n"
            "[p1] How to improve X? This question remains open and requires further investigation.\n\n"
            "[p2] What about Y? Addressing this could lead to significant improvements.\n\n"
            "[p3] What is the relationship between Z and W? Understanding this could unlock new approaches.\n\n"
            "[p4] How do methods perform under distribution shift? This is crucial for real-world deployment.\n\n"
            "[p5] Can we achieve better efficiency without sacrificing accuracy? This is an ongoing challenge.\n\n"
            "This is a long enough synthesis document with multiple sections that references many papers.\n"
            "It references [p1], [p2], [p3], [p4], and [p5] from the paper notes.\n",
            encoding="utf-8",
        )

        agent = ReaderAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="reader",
            run_id="reader_run",
            task_id="T3.5",
            mode="synthesize",
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True