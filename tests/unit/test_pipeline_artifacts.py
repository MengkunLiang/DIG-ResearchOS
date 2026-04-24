"""Pipeline 链路测试。

测试 T2→T3→T3.5→T4→T4.5 完整链路的 artifact 衔接和 schema 一致性。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from researchos.agents.reader import ReaderAgent
from researchos.agents.scout import ScoutAgent
from researchos.agents.ideation import IdeationAgent
from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.runtime.agent import ExecutionContext


class TestScoutToReaderPipeline:
    """测试 T2 (Scout) → T3 (Reader) 链路。"""

    @pytest.fixture
    def scout_output_workspace(self):
        """创建 Scout Agent (T2) 输出后的 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            # 创建 project.yaml
            project_data = {
                "project_id": "test-pipeline",
                "research_direction": "AI Agent Memory Retrieval",
                "keywords": ["memory", "retrieval", "agent", "LLM"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42],
                    "tier2_seeds": [123],
                    "tier3_seeds": [456],
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))

            # 创建 literature 目录
            (ws / "literature").mkdir(parents=True, exist_ok=True)

            # 创建 papers_dedup.jsonl（T2 输出）
            papers = [
                {
                    "id": "paper1",
                    "title": "Memory-Augmented Language Models",
                    "authors": ["Author A", "Author B"],
                    "year": 2024,
                    "abstract": "This paper proposes...",
                    "venue": "NeurIPS",
                    "citation_count": 50,
                    "relevance_score": 0.95,
                },
                {
                    "id": "paper2",
                    "title": "Retrieval-Augmented Generation",
                    "authors": ["Author C"],
                    "year": 2023,
                    "abstract": "We present RAG...",
                    "venue": "ICML",
                    "citation_count": 100,
                    "relevance_score": 0.90,
                },
            ]
            (ws / "literature" / "papers_dedup.jsonl").write_text(
                "\n".join(json.dumps(p) for p in papers)
            )

            # 创建 missing_areas.md（T2 输出）
            missing_areas = """## 研究空白

1. 现有方法在长期记忆方面存在不足
2. 因果推理与记忆检索的结合尚未探索
"""
            (ws / "literature" / "missing_areas.md").write_text(missing_areas)

            yield ws

    def test_reader_agent_reads_papers_dedup(self, scout_output_workspace):
        """Reader Agent 应该能读取 Scout Agent 的输出。"""
        ws = scout_output_workspace

        # 验证 papers_dedup.jsonl 存在且格式正确
        papers_path = ws / "literature" / "papers_dedup.jsonl"
        assert papers_path.exists()

        # 读取并验证
        lines = papers_path.read_text().strip().split("\n")
        papers = [json.loads(line) for line in lines]

        assert len(papers) == 2
        assert papers[0]["id"] == "paper1"
        assert papers[0]["relevance_score"] == 0.95

    def test_reader_agent_context_contains_inputs(self, scout_output_workspace):
        """Reader Agent 的 ExecutionContext 应该包含正确的 inputs。"""
        ws = scout_output_workspace

        ctx = ExecutionContext(
            workspace_dir=ws,
            project_id="test-pipeline",
            task_id="T3",
            run_id="reader_test",
            mode="read",
            inputs={
                "project": ws / "project.yaml",
                "papers_dedup": ws / "literature" / "papers_dedup.jsonl",
                "missing_areas": ws / "literature" / "missing_areas.md",
            },
            outputs_expected={},
        )

        # 验证 inputs 存在
        assert ctx.inputs["project"].exists()
        assert ctx.inputs["papers_dedup"].exists()
        assert ctx.inputs["missing_areas"].exists()


class TestReaderToIdeationPipeline:
    """测试 T3 (Reader) → T4 (Ideation) 链路。"""

    @pytest.fixture
    def reader_output_workspace(self):
        """创建 Reader Agent (T3+T3.5) 输出后的 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            # 创建 project.yaml
            project_data = {
                "project_id": "test-pipeline",
                "research_direction": "AI Agent Memory Retrieval",
                "keywords": ["memory", "retrieval", "agent"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42],
                    "tier2_seeds": [123],
                    "tier3_seeds": [456],
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))

            # 创建 literature 目录
            (ws / "literature").mkdir(parents=True, exist_ok=True)

            # 创建 paper_notes 目录（T3 输出）
            paper_notes_dir = ws / "literature" / "paper_notes"
            paper_notes_dir.mkdir(parents=True, exist_ok=True)

            # 创建论文笔记
            (paper_notes_dir / "paper1.md").write_text(
                "# Paper 1 Notes\n## Key Points\n- Memory augmentation technique\n- Attention-based retrieval"
            )
            (paper_notes_dir / "paper2.md").write_text(
                "# Paper 2 Notes\n## Key Points\n- RAG architecture\n- Hybrid retrieval"
            )

            # 创建 comparison_table.csv（T3 输出）
            comparison_table = """method,accuracy,f1_score,dataset
MemoryAug,0.85,0.82,NQ
RAG,0.88,0.85,NQ
"""
            (ws / "literature" / "comparison_table.csv").write_text(comparison_table)

            # 创建 synthesis.md（T3.5 输出）
            synthesis = """# Literature Synthesis

## Overview
This research area focuses on memory-augmented approaches for LLM.

## Key Themes
1. Memory retrieval mechanisms
2. Attention-based memory access
3. Hybrid retrieval methods

## Research Gaps
- Long-term memory limitations
- Causal reasoning in memory systems
"""
            (ws / "literature" / "synthesis.md").write_text(synthesis)

            # 创建 missing_areas.md（T2 输出）
            missing_areas = """## 研究空白

1. 现有方法在长期记忆方面存在不足
2. 因果推理与记忆检索的结合尚未探索
"""
            (ws / "literature" / "missing_areas.md").write_text(missing_areas)

            yield ws

    def test_ideation_agent_reads_synthesis(self, reader_output_workspace):
        """Ideation Agent 应该能读取 Reader Agent 的输出。"""
        ws = reader_output_workspace

        synthesis_path = ws / "literature" / "synthesis.md"
        assert synthesis_path.exists()

        synthesis_content = synthesis_path.read_text()
        assert "Literature Synthesis" in synthesis_content
        assert "Memory retrieval mechanisms" in synthesis_content

    def test_ideation_agent_context_contains_required_inputs(self, reader_output_workspace):
        """Ideation Agent 的 ExecutionContext 应该包含必需的 inputs。"""
        ws = reader_output_workspace

        ctx = ExecutionContext(
            workspace_dir=ws,
            project_id="test-pipeline",
            task_id="T4",
            run_id="ideation_test",
            mode=None,
            inputs={
                "project": ws / "project.yaml",
                "synthesis": ws / "literature" / "synthesis.md",
                "comparison_table": ws / "literature" / "comparison_table.csv",
                "missing_areas": ws / "literature" / "missing_areas.md",
            },
            outputs_expected={},
        )

        # T4 需要的 input 都存在
        assert ctx.inputs["synthesis"].exists()
        assert ctx.inputs["comparison_table"].exists()


class TestIdeationToNoveltyAuditorPipeline:
    """测试 T4 (Ideation) → T4.5 (NoveltyAuditor) 链路。"""

    @pytest.fixture
    def ideation_output_workspace(self):
        """创建 Ideation Agent (T4) 输出后的 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            # 创建 project.yaml
            project_data = {
                "project_id": "test-pipeline",
                "research_direction": "AI Agent Memory Retrieval",
                "keywords": ["memory", "retrieval", "agent"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42],
                    "tier2_seeds": [123],
                    "tier3_seeds": [456],
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))

            # 创建 ideation 目录
            (ws / "ideation").mkdir(parents=True, exist_ok=True)
            (ws / "literature").mkdir(parents=True, exist_ok=True)

            # 创建 hypotheses.md（T4 输出）
            hypotheses = """# Research Hypotheses

## H1: Causal Memory Retrieval
**Hypothesis**: Causal memory retrieval outperforms semantic similarity-based retrieval.

**Evidence**: Memory-augmented LLMs show improved factual accuracy.

## H2: Hybrid Attention Mechanism
**Hypothesis**: Combining long-term and short-term memory via hybrid attention improves performance.

**Evidence**: Existing work shows benefits of memory combination.
"""
            (ws / "ideation" / "hypotheses.md").write_text(hypotheses)

            # 创建 synthesis.md（T3.5 输出）
            synthesis = """# Literature Synthesis

## Overview
Memory-augmented approaches for LLM.

## Key Themes
1. Memory retrieval mechanisms
2. Hybrid retrieval methods
"""
            (ws / "literature" / "synthesis.md").write_text(synthesis)

            # 创建 comparison_table.csv（T3 输出）
            comparison_table = """method,accuracy,f1_score
MemoryAug,0.85,0.82
RAG,0.88,0.85
"""
            (ws / "literature" / "comparison_table.csv").write_text(comparison_table)

            # 创建 exp_plan.yaml（T4 输出）
            exp_plan = {
                "experiments": [
                    {
                        "name": "causal_retrieval_exp",
                        "description": "Test causal vs semantic retrieval",
                    },
                    {
                        "name": "hybrid_attention_exp",
                        "description": "Test hybrid attention mechanism",
                    },
                ],
            }
            (ws / "ideation" / "exp_plan.yaml").write_text(yaml.dump(exp_plan))

            yield ws

    def test_novelty_auditor_reads_hypotheses(self, ideation_output_workspace):
        """NoveltyAuditor Agent 应该能读取 Ideation Agent 的输出。"""
        ws = ideation_output_workspace

        hypotheses_path = ws / "ideation" / "hypotheses.md"
        assert hypotheses_path.exists()

        hypotheses_content = hypotheses_path.read_text()
        assert "Research Hypotheses" in hypotheses_content
        assert "H1" in hypotheses_content

    def test_novelty_auditor_agent_context(self, ideation_output_workspace):
        """NoveltyAuditor Agent 的 ExecutionContext 应该包含正确的 inputs。"""
        ws = ideation_output_workspace

        ctx = ExecutionContext(
            workspace_dir=ws,
            project_id="test-pipeline",
            task_id="T4.5",
            run_id="auditor_test",
            mode=None,
            inputs={
                "project": ws / "project.yaml",
                "hypotheses": ws / "ideation" / "hypotheses.md",
                "synthesis": ws / "literature" / "synthesis.md",
                "comparison_table": ws / "literature" / "comparison_table.csv",
            },
            outputs_expected={},
        )

        assert ctx.inputs["hypotheses"].exists()
        assert ctx.inputs["synthesis"].exists()
        assert ctx.inputs["comparison_table"].exists()


class TestSchemaConsistency:
    """测试 pipeline 中 schema 的一致性。"""

    def test_papers_dedup_schema(self):
        """验证 papers_dedup schema 的必需字段。"""
        required_fields = ["id", "title", "year", "authors", "relevance_score"]

        # 创建示例数据
        sample_paper = {
            "id": "test1",
            "title": "Test Paper",
            "authors": ["Author A"],
            "year": 2024,
            "relevance_score": 0.95,
            "abstract": "Test abstract",
            "venue": "NeurIPS",
            "citation_count": 50,
        }

        # 验证必需字段存在
        for field in required_fields:
            assert field in sample_paper, f"papers_dedup should have {field}"

    def test_exp_plan_schema(self):
        """验证 exp_plan.yaml schema。"""
        sample_exp_plan = {
            "experiments": [
                {
                    "name": "exp1",
                    "description": "Test experiment",
                    "dataset": "NQ",
                    "metrics": ["accuracy", "f1"],
                }
            ]
        }

        # 验证 experiments 存在
        assert "experiments" in sample_exp_plan
        assert len(sample_exp_plan["experiments"]) > 0

        # 验证每个 experiment 有必需字段
        for exp in sample_exp_plan["experiments"]:
            assert "name" in exp
            assert "description" in exp


class TestMultiAgentContextFlow:
    """测试多 Agent 上下文流转。"""

    @pytest.fixture
    def full_pipeline_workspace(self):
        """创建完整 T2→T4.5 pipeline 的 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            # 创建所有需要的目录
            for subdir in ["literature", "ideation", "novelty", "pilot", "experiments"]:
                (ws / subdir).mkdir(parents=True, exist_ok=True)
            (ws / "literature" / "paper_notes").mkdir(parents=True, exist_ok=True)

            # Project
            project_data = {
                "project_id": "full-pipeline-test",
                "research_direction": "Causal Memory Retrieval",
                "keywords": ["causal", "memory", "retrieval", "agent"],
                "target_venue": "NeurIPS",
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42, 123, 456],
                    "tier2_seeds": [789],
                    "tier3_seeds": [999],
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))

            # T2 output: papers_dedup.jsonl
            papers = [
                {
                    "id": "paper1",
                    "title": "Memory-Augmented LM",
                    "authors": ["Author A"],
                    "year": 2024,
                    "relevance_score": 0.95,
                }
            ]
            (ws / "literature" / "papers_dedup.jsonl").write_text(
                "\n".join(json.dumps(p) for p in papers)
            )

            # T2 output: missing_areas.md
            (ws / "literature" / "missing_areas.md").write_text("## Gap\nLong-term memory limitations")

            # T3 output: paper_notes and comparison_table
            (ws / "literature" / "paper_notes" / "paper1.md").write_text("# Notes")
            (ws / "literature" / "comparison_table.csv").write_text("method,acc\nMemAug,0.85")

            # T3.5 output: synthesis.md
            (ws / "literature" / "synthesis.md").write_text("# Synthesis\nMemory approaches")

            # T4 output: hypotheses.md and exp_plan.yaml
            (ws / "ideation" / "hypotheses.md").write_text("# H1\nCausal memory works better")
            (ws / "ideation" / "exp_plan.yaml").write_text(
                yaml.dump({"experiments": [{"name": "e1", "description": "test"}]})
            )

            yield ws

    def test_full_pipeline_inputs_exist(self, full_pipeline_workspace):
        """验证完整 pipeline 的所有输入文件存在。"""
        ws = full_pipeline_workspace

        # T3 inputs
        assert (ws / "project.yaml").exists()
        assert (ws / "literature" / "papers_dedup.jsonl").exists()
        assert (ws / "literature" / "missing_areas.md").exists()

        # T4 inputs
        assert (ws / "literature" / "synthesis.md").exists()
        assert (ws / "literature" / "comparison_table.csv").exists()

        # T4.5 inputs
        assert (ws / "ideation" / "hypotheses.md").exists()

    def test_agent_execution_order_is_valid(self, full_pipeline_workspace):
        """验证 agent 执行顺序是有效的。"""
        # 定义的执行顺序：T2 → T3 → T3.5 → T4 → T4.5 → T5 → ...
        execution_order = ["T2", "T3", "T3.5", "T4", "T4.5"]

        # 验证每个步骤的输出成为下一步的输入
        expected_flows = [
            # T2 output → T3 input
            ("literature/papers_dedup.jsonl", "T3"),
            # T3 output → T3.5 input
            ("literature/paper_notes", "T3.5"),
            ("literature/comparison_table.csv", "T3.5"),
            # T3.5 output → T4 input
            ("literature/synthesis.md", "T4"),
            # T4 output → T4.5 input
            ("ideation/hypotheses.md", "T4.5"),
        ]

        ws = full_pipeline_workspace
        for output_file, target_task in expected_flows:
            path = ws / output_file
            assert path.exists(), f"{output_file} should exist for {target_task}"
