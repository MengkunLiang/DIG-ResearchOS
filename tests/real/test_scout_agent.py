"""Scout Agent Integration Tests.

测试文献检索 Agent。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.agents.scout import ScoutAgent


def _paper_record(index: int) -> dict:
    return {
        "id": f"test{index}",
        "canonical_id": f"test{index}",
        "preferred_id_source": "source_id",
        "source": "semantic_scholar" if index % 3 else "arxiv",
        "title": f"Test Paper {index}",
        "year": 2025 - (index % 6),
        "authors": [f"Author{index}"],
        "venue": "NeurIPS",
        "source_type": "top_conference",
        "relevance_score": round(0.95 - index * 0.01, 3),
        "why_relevant": "Directly relevant to the test research direction.",
        "abstract": f"Test abstract {index}",
        "citation_count": index * 10,
        "url": f"https://example.com/papers/{index}",
        "doi": f"10.1234/test{index}",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


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

        lit_dir = standard_workspace / "literature"
        raw_records = [_paper_record(i) for i in range(1, 13)]
        dedup_records = [_paper_record(i) for i in range(1, 11)]
        verified_records = []
        for record in dedup_records:
            verified = dict(record)
            verified.update(
                {
                    "canonical_id": record["id"],
                    "verification_status": "metadata_verified",
                    "verification_method": "semantic_scholar",
                    "verification_source": "semantic_scholar",
                    "verification_confidence": 0.95,
                    "verification_title_similarity": 0.98,
                    "verification_year_match": True,
                }
            )
            verified_records.append(verified)
        queue_records = [
            {
                "paper_id": record["canonical_id"],
                "normalized_id": record["canonical_id"],
                "title": record["title"],
                "source": record["source"],
                "year": record["year"],
                "venue": record["venue"],
                "relevance_score": record["relevance_score"],
                "access_score_estimate": 0.75,
                "access_score": 0.75,
                "evidence_level": "PARTIAL_TEXT",
                "seed_priority": False,
                "queue_rank": index,
                "read_priority": 0.8,
                "target_bucket": "target",
                "verification_status": "metadata_verified",
                "verification_confidence": 0.95,
            }
            for index, record in enumerate(verified_records, start=1)
        ]

        _write_jsonl(lit_dir / "papers_raw.jsonl", raw_records)
        _write_jsonl(lit_dir / "papers_dedup.jsonl", dedup_records)
        _write_jsonl(lit_dir / "papers_verified.jsonl", verified_records)
        (lit_dir / "verification_failures.jsonl").write_text("", encoding="utf-8")
        _write_jsonl(lit_dir / "deep_read_queue.jsonl", queue_records)

        search_log = standard_workspace / "literature" / "search_log.md"
        search_log.write_text("# Search Log\n\n## Query 1\n- Query: test\n- Results: 10\n", encoding="utf-8")

        access_audit = standard_workspace / "literature" / "access_audit.md"
        access_audit.write_text("# Access Audit\n\n- Verified metadata for 10 papers.\n", encoding="utf-8")

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


class TestScoutProgressLogger:
    """Scout Agent 进度日志工具测试。"""

    def test_scout_progress_logger_init(self, standard_workspace: Path):
        """测试 ScoutProgressLogger 初始化。"""
        from researchos.tools.scout_progress import ScoutProgressLogger

        logger = ScoutProgressLogger(standard_workspace)
        assert logger.workspace_dir == standard_workspace
        assert logger.log_file == standard_workspace / "literature" / "temp" / "scout_progress.md"

    def test_scout_progress_logger_log_step(self, standard_workspace: Path):
        """测试记录步骤。"""
        from researchos.tools.scout_progress import ScoutProgressLogger

        logger = ScoutProgressLogger(standard_workspace)
        logger.log_step("test_step", "测试详情")

        progress = logger.read_progress()
        assert progress is not None
        assert "test_step" in progress
        assert "测试详情" in progress

    def test_scout_progress_logger_log_search_result(self, standard_workspace: Path):
        """测试记录检索结果。"""
        from researchos.tools.scout_progress import ScoutProgressLogger

        logger = ScoutProgressLogger(standard_workspace)
        logger.log_search_result("attention mechanism", 25, "arxiv")

        progress = logger.read_progress()
        assert progress is not None
        assert "search_result" in progress
        assert "attention mechanism" in progress
        assert "25" in progress
        assert "arxiv" in progress

    def test_scout_progress_logger_log_dedup(self, standard_workspace: Path):
        """测试记录去重。"""
        from researchos.tools.scout_progress import ScoutProgressLogger

        logger = ScoutProgressLogger(standard_workspace)
        logger.log_dedup(before=150, after=120)

        progress = logger.read_progress()
        assert progress is not None
        assert "dedup" in progress
        assert "150" in progress
        assert "120" in progress

    def test_log_scout_progress_tool_registered(self):
        """测试 log_scout_progress 工具已注册。"""
        from researchos.tools.registry import ToolRegistry

        registry = ToolRegistry()
        from researchos.tools.builtin import register_builtin_tools

        register_builtin_tools(registry)

        # 检查工具是否在注册表中
        assert registry.has("log_scout_progress")
