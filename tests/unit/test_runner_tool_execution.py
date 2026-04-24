import asyncio

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
