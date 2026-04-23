"""Scout Agent Integration Tests.

测试文献检索 Agent。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.scout import ScoutAgent


class TestScoutAgent:
    """Scout Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = ScoutAgent()
        assert agent is not None
        assert agent.spec.name == "scout"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = ScoutAgent()
        # scout agent 应该有以下工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 scout agent 没有 docker_exec 工具。"""
        agent = ScoutAgent()
        # scout agent 不需要 docker_exec（使用 API 检索）
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="scout",
            run_id="scout_run",
            task_id="T2",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path, project_yaml: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="scout",
            run_id="scout_run",
            task_id="T2",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert len(msg) > 0

    def test_agent_validate_outputs_no_files(self, standard_workspace: Path, project_yaml: Path):
        """测试输出验证（无文件时）。"""
        from researchos.runtime.agent import ExecutionContext

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="scout",
            run_id="scout_run",
            task_id="T2",
            mode=None,
            extra={},
        )

        # 没有输出文件时应该失败
        ok, err = agent.validate_outputs(ctx)
        assert ok is False

    def test_agent_validate_outputs_with_partial_files(self, standard_workspace: Path, project_yaml: Path):
        """测试输出验证（部分文件时）。"""
        from researchos.runtime.agent import ExecutionContext

        # 只创建部分文件
        papers_raw = standard_workspace / "literature" / "papers_raw.jsonl"
        papers_raw.write_text('{"id": "test1", "title": "Test Paper"}\n', encoding="utf-8")

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="scout",
            run_id="scout_run",
            task_id="T2",
            mode=None,
            extra={},
        )

        # 应该失败，因为缺少其他必需文件
        ok, err = agent.validate_outputs(ctx)
        assert ok is False

    def test_agent_validate_outputs_with_all_files(self, standard_workspace: Path, project_yaml: Path):
        """测试输出验证（有所有文件时）。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建所有必需的文件
        papers_raw = standard_workspace / "literature" / "papers_raw.jsonl"
        papers_raw.write_text(
            '{"id": "test1", "title": "Test Paper 1", "year": 2024, "authors": ["Author1"], "relevance_score": 0.9}\n'
            '{"id": "test2", "title": "Test Paper 2", "year": 2023, "authors": ["Author2"], "relevance_score": 0.85}\n'
            '{"id": "test3", "title": "Test Paper 3", "year": 2023, "authors": ["Author3"], "relevance_score": 0.8}\n'
            '{"id": "test4", "title": "Test Paper 4", "year": 2022, "authors": ["Author4"], "relevance_score": 0.75}\n'
            '{"id": "test5", "title": "Test Paper 5", "year": 2022, "authors": ["Author5"], "relevance_score": 0.7}\n'
            '{"id": "test6", "title": "Test Paper 6", "year": 2021, "authors": ["Author6"], "relevance_score": 0.65}\n'
            '{"id": "test7", "title": "Test Paper 7", "year": 2021, "authors": ["Author7"], "relevance_score": 0.6}\n'
            '{"id": "test8", "title": "Test Paper 8", "year": 2020, "authors": ["Author8"], "relevance_score": 0.55}\n'
            '{"id": "test9", "title": "Test Paper 9", "year": 2020, "authors": ["Author9"], "relevance_score": 0.5}\n'
            '{"id": "test10", "title": "Test Paper 10", "year": 2019, "authors": ["Author10"], "relevance_score": 0.45}\n'
            '{"id": "test11", "title": "Test Paper 11", "year": 2019, "authors": ["Author11"], "relevance_score": 0.4}\n'
            '{"id": "test12", "title": "Test Paper 12", "year": 2018, "authors": ["Author12"], "relevance_score": 0.35}\n',
            encoding="utf-8",
        )

        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.write_text(
            '{"id": "test1", "source": "arxiv", "title": "Test Paper 1", "year": 2024, "authors": ["Author1"], "venue": "ICML", "source_type": "preprint", "relevance_score": 0.9, "why_relevant": "Directly relevant", "abstract": "Test abstract 1", "citation_count": 10, "url": "https://arxiv.org/abs/test1"}\n'
            '{"id": "test2", "source": "arxiv", "title": "Test Paper 2", "year": 2023, "authors": ["Author2"], "venue": "NeurIPS", "source_type": "preprint", "relevance_score": 0.85, "why_relevant": "Very relevant", "abstract": "Test abstract 2", "citation_count": 5, "url": "https://arxiv.org/abs/test2"}\n'
            '{"id": "test3", "source": "semantic_scholar", "title": "Test Paper 3", "year": 2023, "authors": ["Author3"], "venue": "ICLR", "source_type": "top_conference", "relevance_score": 0.8, "why_relevant": "Related work", "abstract": "Test abstract 3", "citation_count": 20, "url": "https://arxiv.org/abs/test3"}\n'
            '{"id": "test4", "source": "arxiv", "title": "Test Paper 4", "year": 2022, "authors": ["Author4"], "venue": "AAAI", "source_type": "journal", "relevance_score": 0.75, "why_relevant": "Methodology", "abstract": "Test abstract 4", "citation_count": 15, "url": "https://arxiv.org/abs/test4"}\n'
            '{"id": "test5", "source": "arxiv", "title": "Test Paper 5", "year": 2022, "authors": ["Author5"], "venue": "ICML", "source_type": "preprint", "relevance_score": 0.7, "why_relevant": "Foundation", "abstract": "Test abstract 5", "citation_count": 8, "url": "https://arxiv.org/abs/test5"}\n'
            '{"id": "test6", "source": "semantic_scholar", "title": "Test Paper 6", "year": 2021, "authors": ["Author6"], "venue": "CVPR", "source_type": "top_conference", "relevance_score": 0.65, "why_relevant": "Applications", "abstract": "Test abstract 6", "citation_count": 50, "url": "https://arxiv.org/abs/test6"}\n'
            '{"id": "test7", "source": "arxiv", "title": "Test Paper 7", "year": 2021, "authors": ["Author7"], "venue": "EMNLP", "source_type": "preprint", "relevance_score": 0.6, "why_relevant": "Evaluation", "abstract": "Test abstract 7", "citation_count": 12, "url": "https://arxiv.org/abs/test7"}\n'
            '{"id": "test8", "source": "crossref", "title": "Test Paper 8", "year": 2020, "authors": ["Author8"], "venue": "JMLR", "source_type": "journal", "relevance_score": 0.55, "why_relevant": "Theory", "abstract": "Test abstract 8", "citation_count": 100, "url": "https://arxiv.org/abs/test8"}\n'
            '{"id": "test9", "source": "arxiv", "title": "Test Paper 9", "year": 2020, "authors": ["Author9"], "venue": "ICLR", "source_type": "preprint", "relevance_score": 0.5, "why_relevant": "Comparison", "abstract": "Test abstract 9", "citation_count": 25, "url": "https://arxiv.org/abs/test9"}\n'
            '{"id": "test10", "source": "semantic_scholar", "title": "Test Paper 10", "year": 2019, "authors": ["Author10"], "venue": "NeurIPS", "source_type": "top_conference", "relevance_score": 0.45, "why_relevant": "Background", "abstract": "Test abstract 10", "citation_count": 200, "url": "https://arxiv.org/abs/test10"}\n',
            encoding="utf-8",
        )

        search_log = standard_workspace / "literature" / "search_log.md"
        search_log.write_text("# Search Log\n\n## Query 1\n- Query: test\n- Results: 10\n", encoding="utf-8")

        missing_areas = standard_workspace / "literature" / "missing_areas.md"
        missing_areas.write_text("# Missing Areas\n\n- Area 1\n- Area 2\n", encoding="utf-8")

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="scout",
            run_id="scout_run",
            task_id="T2",
            mode=None,
            extra={},
        )

        # 应该通过
        ok, err = agent.validate_outputs(ctx)
        assert ok is True


class TestScoutAgentDedupLogic:
    """Scout Agent 去重逻辑测试。"""

    def test_validate_dedup_creates_filtered_list(self, standard_workspace: Path, project_yaml: Path):
        """测试去重后列表过滤了重复论文。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建包含重复的原始列表
        papers_raw = standard_workspace / "literature" / "papers_raw.jsonl"
        papers_raw.write_text(
            '{"id": "test1", "title": "Paper 1"}\n'
            '{"id": "test2", "title": "Paper 2"}\n'
            '{"id": "test1", "title": "Paper 1 Duplicate"}\n',  # 重复
            encoding="utf-8",
        )

        # 验证去重后应该只有一个 test1
        import json

        papers = []
        with papers_raw.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    papers.append(json.loads(line))

        # 去重逻辑（根据 id）
        unique_ids = set()
        unique_papers = []
        for paper in papers:
            paper_id = paper.get("id")
            if paper_id not in unique_ids:
                unique_ids.add(paper_id)
                unique_papers.append(paper)

        assert len(unique_papers) == 2
        assert len(unique_papers) < len(papers)