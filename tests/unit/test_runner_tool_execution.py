import asyncio
import json

import pytest
from pydantic import BaseModel

from researchos.runtime.agent import Agent, AgentSpec, ExecutionContext
from researchos.runtime.orchestrator import AgentRunner
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.base import Tool, ToolResult
from researchos.tools.registry import ToolRegistry


class Params(BaseModel):
    value: str


class SlowTool(Tool):
    name = "slow"
    description = "slow"
    parameters_schema = Params

    async def execute(self, **kwargs):
        await asyncio.sleep(0.01)
        return ToolResult(ok=True, content=kwargs["value"])


class MinimalAgent(Agent):
    def __init__(self):
        super().__init__(AgentSpec(name="parallel", model_tier="medium", tool_names=["slow", "finish_task"]))

    def system_prompt(self, ctx):
        return "parallel"

    def initial_user_message(self, ctx):
        return "run tools"


class SearchParams(BaseModel):
    query: str


class SearchTool(Tool):
    name = "arxiv_search"
    description = "search"
    parameters_schema = SearchParams

    async def execute(self, **kwargs):
        return ToolResult(
            ok=True,
            content="found 1 paper",
            data={
                "papers": [
                    {
                        "id": "2501.00001",
                        "source": "arxiv",
                        "title": "Runtime Auto Save for T2",
                        "authors": ["Test Author"],
                        "year": 2025,
                        "abstract": "Test abstract",
                        "venue": "arXiv",
                        "url": "https://arxiv.org/abs/2501.00001",
                        "citation_count": 0,
                        "doi": "",
                    }
                ]
            },
        )


class T2SearchAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(
                name="t2-search",
                model_tier="medium",
                tool_names=["arxiv_search", "finish_task"],
                allowed_write_prefixes=["literature/"],
            )
        )

    def system_prompt(self, ctx):
        return "t2"

    def initial_user_message(self, ctx):
        return "search then finish"


class RecoverySearchTool(Tool):
    name = "arxiv_search"
    description = "search with enough papers to recover T2 outputs"
    parameters_schema = SearchParams

    async def execute(self, **kwargs):
        topic_variants = [
            "causal modeling for ai agent memory retrieval",
            "episodic memory for llm agents",
            "pytorch memory retrieval for autonomous agents",
            "causal inference in agent retrieval systems",
            "reinforcement learning with episodic memory",
            "memory routing for ai agents",
            "retrieval benchmarks for agent memory",
            "tool-using agents with long-term memory",
            "auditable memory retrieval in llm agents",
            "causal memory tracing for ai agents",
            "agent memory evaluation with pytorch",
            "retrieval-augmented episodic memory agents",
        ]
        papers = []
        for i, topic in enumerate(topic_variants):
            papers.append(
                {
                    "id": f"2501.{i:05d}",
                    "source": "arxiv",
                    "title": topic.title(),
                    "authors": ["Test Author"],
                    "year": 2025,
                    "abstract": f"{topic} with causal modeling, episodic memory, and PyTorch.",
                    "venue": "arXiv",
                    "url": f"https://arxiv.org/abs/2501.{i:05d}",
                    "citation_count": i,
                    "doi": "",
                }
            )
        return ToolResult(ok=True, content="found 12 papers", data={"papers": papers})


class LargeRecoverySearchTool(Tool):
    name = "arxiv_search"
    description = "search with enough papers for normal T2 deterministic finalization"
    parameters_schema = SearchParams

    async def execute(self, **kwargs):
        papers = []
        for i in range(120):
            papers.append(
                {
                    "id": f"2502.{i:05d}",
                    "source": "arxiv",
                    "title": f"LLM Agent Long Term Memory Retrieval Framework {i}",
                    "authors": ["Test Author"],
                    "year": 2025,
                    "abstract": (
                        "LLM agent long-term memory retrieval, hierarchical memory, "
                        "compression, consistency, and evaluation for task-oriented agents."
                    ),
                    "venue": "arXiv",
                    "url": f"https://arxiv.org/abs/2502.{i:05d}",
                    "citation_count": i,
                    "doi": "",
                }
            )
        return ToolResult(ok=True, content="found 120 papers", data={"papers": papers})


class T2RecoveryAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(
                name="t2-recovery",
                model_tier="medium",
                tool_names=["arxiv_search"],
                allowed_write_prefixes=["literature/"],
            )
        )

    def system_prompt(self, ctx):
        return "t2 recover"

    def initial_user_message(self, ctx):
        return "search"

    def validate_outputs(self, ctx):
        for path in ctx.outputs_expected.values():
            if not path.exists():
                return False, f"missing {path.name}"
        dedup_lines = (ctx.workspace_dir / "literature" / "papers_dedup.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if len([line for line in dedup_lines if line.strip()]) < 10:
            return False, "too few dedup papers"
        return True, None


@pytest.mark.asyncio
async def test_multiple_tool_calls_keep_order(tmp_workspace):
    registry = ToolRegistry()
    registry.register("slow", lambda ctx: SlowTool())
    from researchos.tools.finish_task import FinishTaskTool

    registry.register("finish_task", lambda ctx: FinishTaskTool())
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(name="slow", arguments={"value": "a"}, id="tc1"),
                        FakeToolCall(name="slow", arguments={"value": "b"}, id="tc2"),
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "done"}, id="tc3")]
                )
            ),
        ]
    )
    runner = AgentRunner(
        MinimalAgent(),
        registry,
        llm,
        MockHumanInterface(),
    )
    result = await runner.run(
        ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T0", run_id="r4")
    )
    assert result.ok
    trace = (tmp_workspace / "_runtime" / "traces" / "r4.jsonl").read_text(encoding="utf-8")
    assert "tc1" in trace
    assert "tc2" in trace


@pytest.mark.asyncio
async def test_t2_search_results_are_auto_persisted_to_papers_raw(tmp_workspace):
    registry = ToolRegistry()
    registry.register("arxiv_search", lambda ctx: SearchTool())
    from researchos.tools.finish_task import FinishTaskTool

    registry.register("finish_task", lambda ctx: FinishTaskTool())
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(name="arxiv_search", arguments={"query": "test query"}, id="tc1")
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "done"}, id="tc2")]
                )
            ),
        ]
    )
    runner = AgentRunner(
        T2SearchAgent(),
        registry,
        llm,
        MockHumanInterface(),
    )
    result = await runner.run(
        ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T2", run_id="r5")
    )
    assert result.ok

    raw_path = tmp_workspace / "literature" / "papers_raw.jsonl"
    assert raw_path.exists()
    content = raw_path.read_text(encoding="utf-8")
    assert "Runtime Auto Save for T2" in content


@pytest.mark.asyncio
async def test_t2_failed_run_can_finalize_outputs_from_raw(tmp_workspace):
    (tmp_workspace / "project.yaml").write_text(
        json.dumps(
            {
                "project_id": "p1",
                "research_direction": "Causal modeling for AI agent memory retrieval",
                "keywords": [
                    "AI agent",
                    "memory retrieval",
                    "causal modeling",
                    "episodic memory",
                    "PyTorch",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.register("arxiv_search", lambda ctx: RecoverySearchTool())

    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="arxiv_search",
                            arguments={"query": "causal agent memory retrieval"},
                            id="tc1",
                        )
                    ]
                )
            )
        ]
    )

    runner = AgentRunner(
        T2RecoveryAgent(),
        registry,
        llm,
        MockHumanInterface(),
    )
    result = await runner.run(
        ExecutionContext(
            workspace_dir=tmp_workspace,
            project_id="p1",
            task_id="T2",
            run_id="T2_single_recover",
            outputs_expected={
                "papers_raw": tmp_workspace / "literature" / "papers_raw.jsonl",
                "papers_dedup": tmp_workspace / "literature" / "papers_dedup.jsonl",
                "search_log": tmp_workspace / "literature" / "search_log.md",
                "missing_areas": tmp_workspace / "literature" / "missing_areas.md",
            },
        )
    )

    assert result.ok
    assert result.stop_reason == result.STOP_FINISHED
    assert (tmp_workspace / "literature" / "papers_dedup.jsonl").exists()
    assert (tmp_workspace / "literature" / "search_log.md").exists()
    assert (tmp_workspace / "literature" / "missing_areas.md").exists()


@pytest.mark.asyncio
async def test_t2_auto_finalizes_from_raw_without_second_llm_turn(tmp_workspace):
    (tmp_workspace / "project.yaml").write_text(
        json.dumps(
            {
                "project_id": "p1",
                "research_direction": "LLM Agent long-term memory retrieval framework",
                "keywords": [
                    "LLM Agent",
                    "long-term memory",
                    "memory retrieval",
                    "hierarchical memory",
                    "memory compression",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.register("arxiv_search", lambda ctx: LargeRecoverySearchTool())

    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="arxiv_search",
                            arguments={"query": "LLM agent memory retrieval"},
                            id="tc1",
                        )
                    ]
                )
            )
        ]
    )

    runner = AgentRunner(
        T2RecoveryAgent(),
        registry,
        llm,
        MockHumanInterface(),
    )
    result = await runner.run(
        ExecutionContext(
            workspace_dir=tmp_workspace,
            project_id="p1",
            task_id="T2",
            run_id="T2_single_auto_finalize",
            outputs_expected={
                "papers_raw": tmp_workspace / "literature" / "papers_raw.jsonl",
                "papers_dedup": tmp_workspace / "literature" / "papers_dedup.jsonl",
                "papers_verified": tmp_workspace / "literature" / "papers_verified.jsonl",
                "verification_failures": tmp_workspace / "literature" / "verification_failures.jsonl",
                "deep_read_queue": tmp_workspace / "literature" / "deep_read_queue.jsonl",
                "access_audit": tmp_workspace / "literature" / "access_audit.md",
                "search_log": tmp_workspace / "literature" / "search_log.md",
                "missing_areas": tmp_workspace / "literature" / "missing_areas.md",
            },
        )
    )

    assert result.ok
    assert result.metadata["completion_mode"] == "t2_deterministic"
    assert result.steps_used == 1
    assert llm.call_count == 1
    assert (tmp_workspace / "literature" / "papers_verified.jsonl").exists()
    assert (tmp_workspace / "literature" / "deep_read_queue.jsonl").exists()
