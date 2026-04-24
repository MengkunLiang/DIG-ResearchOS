"""Agent 间通信机制测试。

测试 ResearchOS 中 agent 之间如何通过 ExecutionContext、TaskContext 和 State 传递信息。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from dataclasses import dataclass

import pytest
import yaml

from researchos.agents.scout import ScoutAgent
from researchos.agents.reader import ReaderAgent
from researchos.agents.ideation import IdeationAgent
from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext, AgentResult
from researchos.runtime.llm_client import LLMClient
from researchos.tools.registry import ToolRegistry
from researchos.tools.human_gate import HumanInterface


class MockLLMResponse:
    """模拟 LLM 响应。"""
    model_used: str
    endpoint_used: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    raw: MagicMock

    def __init__(self, model_used="mock-model", endpoint_used="mock-endpoint",
                 tokens_in=100, tokens_out=50, cost_usd=0.01):
        self.model_used = model_used
        self.endpoint_used = endpoint_used
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost_usd = cost_usd
        self.raw = MagicMock()
        choice = MagicMock()
        choice.message = MagicMock()
        choice.message.content = "Mock response"
        choice.message.tool_calls = []
        self.raw.choices = [choice]


class MockLLMClient:
    """模拟 LLM 客户端。"""

    def __init__(self, responses: list[MockLLMResponse] | None = None):
        self.responses = responses or [MockLLMResponse()]
        self.call_count = 0
        self.calls: list[dict] = []

    async def chat(self, messages, tools=None, temperature=None, tier=None,
                   profile=None, model_override=None, endpoint_override=None,
                   max_context_override=None, timeout=120,
                   max_retries_per_model=2, retry_base_delay=2.0):
        response = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "tier": tier,
            "timeout": timeout,
            "max_retries_per_model": max_retries_per_model,
            "retry_base_delay": retry_base_delay,
        })
        return response

    def resolve(self, profile=None, tier=None, model_override=None,
                endpoint_override=None, max_context_override=None):
        return [(MagicMock(model="mock-model", max_context=128000)), None]

    def get_context_window(self, binding):
        return 128000

    def count_tokens(self, messages, binding):
        return 100

    def get_truncation_config(self):
        return {"trigger_ratio": 0.8, "target_ratio": 0.6}


class MockHumanInterface:
    """模拟人机接口。"""

    async def ask_approval(self, tool_name, arguments):
        return True

    async def ask_question(self, question, options=None, allow_free_text=False):
        return {"option_id": "default"}


class TestExecutionContextCommunication:
    """测试 ExecutionContext 如何在 agent 间传递信息。"""

    @pytest.fixture
    def workspace(self):
        """创建测试 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            project_data = {
                "project_id": "test-agent-comm",
                "research_direction": "AI Agent Memory",
                "keywords": ["memory", "agent", "LLM"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {"tier1_seeds": [42], "tier2_seeds": [123]},
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))
            for subdir in ["literature", "ideation", "novelty", "pilot", "experiments"]:
                (ws / subdir).mkdir(parents=True, exist_ok=True)
            yield ws

    def test_context_contains_inputs_and_outputs(self, workspace):
        """验证 ExecutionContext 包含正确的输入输出映射。"""
        ctx = ExecutionContext(
            workspace_dir=workspace,
            project_id="test-project",
            task_id="T2",
            run_id="test-run-1",
            inputs={"project": workspace / "project.yaml"},
            outputs_expected={"papers_dedup": workspace / "literature" / "papers_dedup.jsonl"},
        )

        assert ctx.inputs["project"].exists()
        assert ctx.outputs_expected["papers_dedup"] == workspace / "literature" / "papers_dedup.jsonl"

    def test_context_passes_mode_between_agents(self, workspace):
        """验证 mode 在 agent 间正确传递。"""
        scout_ctx = ExecutionContext(
            workspace_dir=workspace,
            project_id="test-project",
            task_id="T2",
            run_id="scout-run-1",
            mode="scout",
            inputs={},
            outputs_expected={},
        )

        experimenter_ctx = ExecutionContext(
            workspace_dir=workspace,
            project_id="test-project",
            task_id="T5",
            run_id="exp-run-1",
            mode="pilot",
            inputs={},
            outputs_expected={},
        )

        assert scout_ctx.mode == "scout"
        assert experimenter_ctx.mode == "pilot"
        assert scout_ctx.mode != experimenter_ctx.mode

    def test_context_passes_round_between_iterations(self, workspace):
        """验证 round 在迭代间正确传递。"""
        ctx_round1 = ExecutionContext(
            workspace_dir=workspace,
            project_id="test-project",
            task_id="T4.5",
            run_id="novelty-round1",
            extra={"round": 1},
            inputs={},
            outputs_expected={},
        )

        ctx_round2 = ExecutionContext(
            workspace_dir=workspace,
            project_id="test-project",
            task_id="T4.5",
            run_id="novelty-round2",
            extra={"round": 2},
            inputs={},
            outputs_expected={},
        )

        assert ctx_round1.extra["round"] == 1
        assert ctx_round2.extra["round"] == 2


class TestArtifactPassingBetweenAgents:
    """测试 Agent 间通过文件传递 artifact。"""

    @pytest.fixture
    def workspace(self):
        """创建测试 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            project_data = {
                "project_id": "test-pipeline",
                "research_direction": "AI Memory",
                "keywords": ["memory"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {"tier1_seeds": [42]},
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))
            for subdir in ["literature", "ideation", "novelty"]:
                (ws / subdir).mkdir(parents=True, exist_ok=True)
            yield ws

    def test_scout_writes_papers_for_reader(self, workspace):
        """模拟 Scout (T2) 写入 papers_dedup.jsonl，Reader (T3) 读取。"""
        papers = [
            {"id": "paper1", "title": "Memory-Augmented LM", "authors": ["Author A"],
             "year": 2024, "relevance_score": 0.95},
            {"id": "paper2", "title": "RAG Methods", "authors": ["Author B"],
             "year": 2023, "relevance_score": 0.90},
        ]
        papers_file = workspace / "literature" / "papers_dedup.jsonl"
        papers_file.write_text("\n".join(json.dumps(p) for p in papers))

        assert papers_file.exists()
        content = papers_file.read_text()
        loaded_papers = [json.loads(line) for line in content.strip().split("\n")]
        assert len(loaded_papers) == 2
        assert loaded_papers[0]["id"] == "paper1"
        assert loaded_papers[1]["title"] == "RAG Methods"

    def test_reader_writes_notes_for_ideation(self, workspace):
        """模拟 Reader (T3) 写入 synthesis.md，Ideation (T4) 读取。"""
        synthesis_content = """# Literature Synthesis

## Key Themes
1. Memory retrieval mechanisms
2. Attention-based approaches

## Research Gaps
- Long-term memory limitations
"""
        synthesis_file = workspace / "literature" / "synthesis.md"
        synthesis_file.write_text(synthesis_content)

        assert synthesis_file.exists()
        content = synthesis_file.read_text()
        assert "Memory retrieval mechanisms" in content
        assert "Research Gaps" in content

    def test_ideation_writes_hypotheses_for_experimenter(self, workspace):
        """模拟 Ideation (T4) 写入 hypotheses.md 和 exp_plan.yaml，Experimenter (T5) 读取。"""
        hypotheses_content = """# Research Hypotheses

## H1: Causal Memory Retrieval
Causal memory retrieval outperforms semantic similarity methods.

## H2: Hybrid Attention
Combining long-term and short-term memory improves performance.
"""
        (workspace / "ideation" / "hypotheses.md").write_text(hypotheses_content)

        exp_plan = {
            "experiments": [
                {"name": "causal_vs_semantic", "description": "Compare causal vs semantic retrieval", "dataset": "NQ"},
                {"name": "hybrid_attention", "description": "Test hybrid attention mechanism", "dataset": "NQ"},
            ]
        }
        (workspace / "ideation" / "exp_plan.yaml").write_text(yaml.dump(exp_plan))

        hypotheses_file = workspace / "ideation" / "hypotheses.md"
        exp_plan_file = workspace / "ideation" / "exp_plan.yaml"

        assert hypotheses_file.exists()
        assert exp_plan_file.exists()
        hypotheses = hypotheses_file.read_text()
        assert "H1: Causal Memory Retrieval" in hypotheses
        assert "H2: Hybrid Attention" in hypotheses
        loaded_plan = yaml.safe_load(exp_plan_file.read_text())
        assert len(loaded_plan["experiments"]) == 2
        assert loaded_plan["experiments"][0]["name"] == "causal_vs_semantic"


class TestTaskContextPassing:
    """测试 TaskContext 在 agent 间的传递。"""

    def test_gate_decision_updates_task_context(self):
        """验证 gate 决策后更新 task_context。"""
        from researchos.schemas.state import StateYaml

        state = StateYaml(project_id="test-project", current_task="T2")
        gate_result = {
            "option_id": "expand_scope",
            "extra": {
                "search_keywords": ["memory", "retrieval", "agent"],
                "max_papers": 100,
            }
        }

        state.task_context.update(gate_result.get("extra", {}))

        assert "search_keywords" in state.task_context
        assert state.task_context["search_keywords"] == ["memory", "retrieval", "agent"]
        assert state.task_context["max_papers"] == 100

    def test_task_context_propagates_to_next_agent(self):
        """验证 task_context 正确传递给下一个 agent。"""
        from researchos.orchestration.state_machine import StateMachine
        from researchos.schemas.state import StateYaml

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            sm_config = {
                "initial_state": "T2",
                "states": {
                    "T2": {"agent": "scout", "next_on_success": "T3"},
                    "T3": {"agent": "reader", "next_on_success": "T3.5"},
                    "T3.5": {"agent": "reader", "next_on_success": "T4"},
                }
            }
            sm_path = ws / "state_machine.yaml"
            sm_path.write_text(yaml.dump(sm_config))

            sm = StateMachine(sm_path)

            state = StateYaml(project_id="test-project", current_task="T2")
            state.task_context = {
                "papers_found": 50,
                "top_sources": ["semantic_scholar", "arxiv"],
                "search_keywords": ["memory", "retrieval"],
            }

            state.current_task = "T3"
            ctx = sm.build_execution_context(ws, state)

            assert "papers_found" in ctx.extra
            assert ctx.extra["papers_found"] == 50
            assert ctx.extra["top_sources"] == ["semantic_scholar", "arxiv"]


class TestHistoryBasedCommunication:
    """测试基于 History 的 agent 间通信。"""

    def test_agent_reads_previous_agent_outputs(self):
        """验证 agent 可以读取前一个 agent 的输出。"""
        from researchos.schemas.state import StateYaml, TaskHistoryEntry

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)

            state = StateYaml(project_id="test-project", current_task="T3")
            state.history.append(TaskHistoryEntry(
                task="T2",
                run_id="t2-run-001",
                status="DONE",
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:10:00Z",
            ))

            (ws / "literature").mkdir(parents=True, exist_ok=True)
            papers_raw = [{"id": "paper1", "title": "Memory LM", "source": "semantic_scholar"}]
            (ws / "literature" / "papers_raw.jsonl").write_text(
                "\n".join(json.dumps(p) for p in papers_raw)
            )

            t2_run_id = state.history[-1].run_id
            trace_file = ws / "_runtime" / "traces" / f"{t2_run_id}.jsonl"
            trace_file.parent.mkdir(parents=True, exist_ok=True)
            trace_file.write_text(json.dumps({
                "run_id": t2_run_id,
                "task_id": "T2",
                "outputs_produced": {"papers_raw": str(ws / "literature" / "papers_raw.jsonl")},
            }))

            assert trace_file.exists()
            trace_data = json.loads(trace_file.read_text())
            assert "outputs_produced" in trace_data
            assert "papers_raw" in trace_data["outputs_produced"]

    def test_resume_restores_previous_context(self):
        """验证中断恢复时恢复之前的上下文。"""
        from researchos.schemas.state import StateYaml, TaskHistoryEntry

        state = StateYaml(project_id="test-project", current_task="T3")
        state.history.append(TaskHistoryEntry(
            task="T3",
            run_id="t3-run-001",
            status="INTERRUPTED",
            started_at="2026-01-01T00:00:00Z",
        ))

        assert state.history[-1].status == "INTERRUPTED"

        extra = {
            "is_resume": True,
            "resumed_from_run_id": "t3-run-001",
            "resume_reason": "interrupted",
        }

        assert extra["is_resume"] is True
        assert extra["resumed_from_run_id"] == "t3-run-001"


class TestAgentChainIntegration:
    """集成测试：完整的 agent 通信链。"""

    @pytest.fixture
    def full_pipeline_workspace(self):
        """创建完整 pipeline 的 workspace。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            project_data = {
                "project_id": "full-chain-test",
                "research_direction": "Causal Memory Retrieval",
                "keywords": ["causal", "memory", "retrieval"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {"tier1_seeds": [42, 123]},
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data))
            for subdir in ["literature", "ideation", "novelty", "pilot"]:
                (ws / subdir).mkdir(parents=True, exist_ok=True)
            yield ws

    def test_t2_to_t3_to_t4_communication(self, full_pipeline_workspace):
        """测试 T2 → T3 → T4 的完整通信链。"""
        ws = full_pipeline_workspace

        # ===== T2: Scout Agent =====
        t2_papers = [
            {"id": "paper1", "title": "Memory-Augmented LM", "relevance_score": 0.95},
            {"id": "paper2", "title": "RAG Survey", "relevance_score": 0.90},
        ]
        (ws / "literature" / "papers_dedup.jsonl").write_text(
            "\n".join(json.dumps(p) for p in t2_papers)
        )
        (ws / "literature" / "missing_areas.md").write_text(
            "## Gap\nLong-term memory mechanisms are underexplored."
        )
        (ws / "literature" / "search_log.md").write_text(
            "## Search Log\nSearched 5 queries, found 50 papers."
        )

        assert (ws / "literature" / "papers_dedup.jsonl").exists()
        assert (ws / "literature" / "missing_areas.md").exists()
        assert (ws / "literature" / "search_log.md").exists()

        # ===== T3: Reader Agent =====
        papers = [json.loads(line) for line in
                  (ws / "literature" / "papers_dedup.jsonl").read_text().split("\n")
                  if line.strip()]

        synthesis = f"""# Literature Synthesis

## Overview
Found {len(papers)} relevant papers on memory retrieval.

## Key Themes
1. Memory augmentation techniques
2. Retrieval-augmented generation

## Gaps
- Long-term memory mechanisms
"""
        (ws / "literature" / "synthesis.md").write_text(synthesis)
        (ws / "literature" / "comparison_table.csv").write_text(
            "method,accuracy,f1\nMemAug,0.85,0.82\nRAG,0.88,0.85"
        )

        assert (ws / "literature" / "synthesis.md").exists()
        assert (ws / "literature" / "comparison_table.csv").exists()
        assert "Memory augmentation techniques" in (ws / "literature" / "synthesis.md").read_text()

        # ===== T4: Ideation Agent =====
        synthesis_content = (ws / "literature" / "synthesis.md").read_text()
        assert "Memory augmentation" in synthesis_content

        hypotheses = """# Research Hypotheses

## H1: Causal Memory
Causal memory retrieval outperforms semantic similarity.

## H2: Hybrid Attention
Combining long-term and short-term memory via attention mechanism.
"""
        (ws / "ideation" / "hypotheses.md").write_text(hypotheses)

        exp_plan = {
            "experiments": [
                {"name": "causal_exp", "description": "Test causal memory retrieval"},
                {"name": "hybrid_exp", "description": "Test hybrid attention"},
            ]
        }
        (ws / "ideation" / "exp_plan.yaml").write_text(yaml.dump(exp_plan))

        assert (ws / "ideation" / "hypotheses.md").exists()
        assert (ws / "ideation" / "exp_plan.yaml").exists()
        assert "H1: Causal Memory" in (ws / "ideation" / "hypotheses.md").read_text()

    def test_t4_to_t5_handoff_with_mode(self, full_pipeline_workspace):
        """测试 T4 → T5 的交接，包含 mode 传递。"""
        ws = full_pipeline_workspace

        (ws / "ideation" / "hypotheses.md").write_text("# H1\nCausal memory works.")
        (ws / "ideation" / "exp_plan.yaml").write_text(
            yaml.dump({"experiments": [{"name": "exp1"}]})
        )

        hypotheses_file = ws / "ideation" / "hypotheses.md"
        exp_plan_file = ws / "ideation" / "exp_plan.yaml"

        assert hypotheses_file.exists()
        assert exp_plan_file.exists()

        ctx_t5 = ExecutionContext(
            workspace_dir=ws,
            project_id="test",
            task_id="T5",
            run_id="t5-run-1",
            mode="pilot",
            inputs={"hypotheses": hypotheses_file, "exp_plan": exp_plan_file},
            outputs_expected={},
        )

        assert ctx_t5.mode == "pilot"
        assert ctx_t5.inputs["hypotheses"].exists()
        assert ctx_t5.inputs["exp_plan"].exists()
