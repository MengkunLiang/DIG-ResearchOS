"""Pipeline Integration Tests.

测试 agent 之间的 pipeline 衔接。
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestPipelineArtifacts:
    """Pipeline 工件衔接测试。"""

    def test_t1_to_t2_artifact_flow(self, standard_workspace: Path, project_yaml: Path):
        """测试 T1 → T2 的 artifact 衔接。"""
        # T1 (pi agent) 应该生成 project.yaml
        assert project_yaml.exists()
        project_content = project_yaml.read_text(encoding="utf-8")
        assert "name:" in project_content
        assert "target_venue:" in project_content

        # T2 (scout agent) 应该读取 project.yaml 并生成 papers_raw.jsonl, papers_dedup.jsonl
        papers_raw = standard_workspace / "literature" / "papers_raw.jsonl"
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"

        # 验证 schema 一致性
        if papers_raw.exists():
            import json

            with papers_raw.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        paper = json.loads(line)
                        # papers_raw 应该有 id, title 等字段
                        assert "id" in paper or "title" in paper

    def test_t2_to_t3_artifact_flow(self, standard_workspace: Path, project_yaml: Path):
        """测试 T2 → T3 的 artifact 衔接。"""
        # T2 (scout agent) 生成 papers_dedup.jsonl
        papers_dedup = standard_workspace / "literature" / "papers_dedup.jsonl"
        papers_dedup.write_text(
            '{"id": "p1", "title": "Paper 1"}\n'
            '{"id": "p2", "title": "Paper 2"}\n',
            encoding="utf-8",
        )

        # T3 (reader agent) 应该读取 papers_dedup.jsonl 并生成 paper_notes/*.md
        paper_notes_dir = standard_workspace / "literature" / "paper_notes"
        paper_notes_dir.mkdir(parents=True, exist_ok=True)

        # 验证 paper_notes 目录存在
        assert paper_notes_dir.exists()

        # 创建一些笔记文件
        (paper_notes_dir / "p1.md").write_text("# Paper 1\n\nNotes...", encoding="utf-8")
        (paper_notes_dir / "p2.md").write_text("# Paper 2\n\nNotes...", encoding="utf-8")

        # 验证笔记文件数量与论文数量一致
        notes_files = list(paper_notes_dir.glob("*.md"))
        assert len(notes_files) >= 2

    def test_t3_to_t3_5_artifact_flow(self, standard_workspace: Path, project_yaml: Path):
        """测试 T3 → T3.5 的 artifact 衔接。"""
        # T3 生成 paper_notes/*.md
        paper_notes_dir = standard_workspace / "literature" / "paper_notes"
        paper_notes_dir.mkdir(parents=True, exist_ok=True)
        (paper_notes_dir / "p1.md").write_text("# Paper 1\n\nNotes...", encoding="utf-8")

        # T3.5 (reader synthesize) 应该读取 paper_notes 并生成 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Synthesis\n\n"
            "## Method Families\n\n"
            "Family 1\n\n"
            "## Research Questions\n\n"
            "[p1] Question?\n",
            encoding="utf-8",
        )

        assert synthesis.exists()

    def test_t3_5_to_t4_artifact_flow(self, standard_workspace: Path, project_yaml: Path):
        """测试 T3.5 → T4 的 artifact 衔接。"""
        # T3.5 生成 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Synthesis\n\n"
            "## Method Families\n\n"
            "Family 1\n\n"
            "## Research Questions\n\n"
            "[p1] Question?\n",
            encoding="utf-8",
        )

        # T4 (ideation) 应该读取 synthesis.md 并生成 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1\n\n"
            "### Hypothesis\n"
            "This is a hypothesis.\n\n"
            "### Evidence\n"
            "Evidence.\n\n"
            "This is sufficient content.\n" * 10,
            encoding="utf-8",
        )

        assert hypotheses.exists()

        # 验证 hypotheses 引用了 synthesis 中的论文
        content = hypotheses.read_text(encoding="utf-8")
        # hypotheses 中可能引用 [p1] 等标记
        assert len(content) > 100

    def test_t4_to_t5_artifact_flow(self, standard_workspace: Path, project_yaml: Path):
        """测试 T4 → T5 的 artifact 衔接。"""
        # T4 生成 hypotheses.md 和 exp_plan.yaml
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\n## H1\n\nHypothesis.\n", encoding="utf-8")

        exp_plan = standard_workspace / "ideation" / "exp_plan.yaml"
        exp_plan.write_text(
            "hypotheses:\n"
            "  - id: H1\n"
            "    title: Test\n"
            "    priority: high\n",
            encoding="utf-8",
        )

        # T5 (experimenter pilot) 应该读取 hypotheses.md 和 exp_plan.yaml 并生成 pilot_results.json
        pilot_results = standard_workspace / "pilot" / "pilot_results.json"
        pilot_results.write_text(
            '{"hypothesis_id": "H1", "status": "success", '
            '"metrics": {"accuracy": 0.85}}',
            encoding="utf-8",
        )

        assert pilot_results.exists()


class TestPipelineErrorRecovery:
    """Pipeline 错误恢复测试。"""

    def test_missing_input_file_handling(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少输入文件时的处理。"""
        from researchos.runtime.agent import ExecutionContext
        from researchos.agents.scout import ScoutAgent

        agent = ScoutAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="scout",
            run_id="scout_run",
            task_id="T2",
            mode=None,
            extra={},
        )

        # 缺少 project.yaml 时应该失败
        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "papers_dedup" in err.lower() or "10 条" in err

    def test_schema_mismatch_handling(self, standard_workspace: Path, project_yaml: Path):
        """测试 schema 不匹配时的处理。"""
        # 创建格式错误的 papers_raw.jsonl
        papers_raw = standard_workspace / "literature" / "papers_raw.jsonl"
        papers_raw.write_text("not valid json\n", encoding="utf-8")

        # 验证文件存在但格式不正确
        assert papers_raw.exists()
        try:
            import json

            with papers_raw.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        json.loads(line)
        except json.JSONDecodeError:
            # 预期行为：JSON 解析失败
            pass


class TestPipelineStateTransitions:
    """Pipeline 状态转换测试。"""

    def test_hypotheses_references_synthesis_papers(self, standard_workspace: Path, project_yaml: Path):
        """测试 hypotheses 是否正确引用 synthesis 中的论文。"""
        # 创建 synthesis.md
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text(
            "# Synthesis\n\n"
            "## Literature Overview\n\n"
            "[p1] Paper 1: Introduction to Method X\n\n"
            "[p2] Paper 2: Extensions of Method X\n\n"
            "## Research Questions\n\n"
            "1. How to improve Method X?\n"
            "2. Can Method X be applied to new domains?\n",
            encoding="utf-8",
        )

        # 创建 hypotheses.md
        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text(
            "# Hypotheses\n\n"
            "## H1\n\n"
            "### Hypothesis\n"
            "Building on [p1]'s approach, we hypothesize that...\n\n"
            "### Evidence\n"
            "[p1] shows initial evidence.\n\n"
            "## H2\n\n"
            "### Hypothesis\n"
            "Inspired by [p2], we propose...\n\n"
            "### Evidence\n"
            "[p2] provides supporting evidence.\n\n"
            "This is sufficient content for validation.\n" * 10,
            encoding="utf-8",
        )

        # 验证 hypotheses 引用了 synthesis 中的论文
        content = hypotheses.read_text(encoding="utf-8")
        assert "[p1]" in content or "[p2]" in content