import asyncio
import json

import pytest
from pydantic import BaseModel

from researchos.runtime.agent import Agent, AgentSpec, ExecutionContext
from researchos.runtime.message import ToolCall
from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.run_logger import RunLogger
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.base import Tool, ToolResult
from researchos.tools.registry import ToolRegistry
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


class Params(BaseModel):
    value: str


class SlowTool(Tool):
    name = "slow"
    description = "slow"
    parameters_schema = Params

    async def execute(self, **kwargs):
        await asyncio.sleep(0.01)
        return ToolResult(ok=True, content=kwargs["value"])


class DockerParams(BaseModel):
    command: str


class SuccessfulDockerTool(Tool):
    name = "docker_exec"
    description = "fake docker"
    parameters_schema = DockerParams

    async def execute(self, **kwargs):
        return ToolResult(ok=True, content=f"ran {kwargs['command']}")


class WriteParams(BaseModel):
    path: str
    content: str


class SuccessfulWriteTool(Tool):
    name = "write_file"
    description = "fake write"
    parameters_schema = WriteParams

    async def execute(self, **kwargs):
        return ToolResult(ok=True, content=f"wrote {kwargs['path']}")


class MinimalAgent(Agent):
    def __init__(self):
        super().__init__(AgentSpec(name="parallel", model_tier="medium", tool_names=["slow", "finish_task"]))

    def system_prompt(self, ctx):
        return "parallel"

    def initial_user_message(self, ctx):
        return "run tools"


class SearchParams(BaseModel):
    query: str


class FetchParams(BaseModel):
    paper_id: str
    save_path: str


class FailingFetchTool(Tool):
    name = "fetch_paper_pdf"
    description = "always fails"
    parameters_schema = FetchParams
    calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        return ToolResult(ok=False, content=f"download failed for {kwargs['paper_id']}", error="download_failed")


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


class FailingSearchTool(Tool):
    name = "arxiv_search"
    description = "failed search"
    parameters_schema = SearchParams

    async def execute(self, **kwargs):
        return ToolResult(
            ok=False,
            content="query 不能为空",
            error="empty_query",
            data={"query": kwargs.get("query", "")},
        )


class CitationParams(BaseModel):
    openalex_id_or_doi: str


class CitationSnowballTool(Tool):
    name = "fetch_outgoing_citations"
    description = "citation snowball"
    parameters_schema = CitationParams

    async def execute(self, **kwargs):
        return ToolResult(
            ok=True,
            content="resolved citation snowball candidates",
            data={
                "source_id": "W_seed",
                "referenced_works": ["W_ref"],
                "related_works": ["W_rel"],
                "query_bucket": "snowball",
                "papers": [
                    {
                        "id": "W_ref",
                        "source": "openalex_snowball",
                        "title": "Referenced Snowball Paper",
                        "authors": ["Ref Author"],
                        "year": 2024,
                        "abstract": "Referenced work with a reusable design rationale.",
                        "venue": "Journal",
                        "url": "https://openalex.org/W_ref",
                        "citation_count": 7,
                        "doi": "",
                        "referenced_works": [],
                        "related_works": [],
                        "source_bucket": "snowball",
                        "search_bucket": "snowball",
                    },
                    {
                        "id": "W_rel",
                        "source": "openalex_snowball",
                        "title": "Adjacent Related Paper",
                        "authors": ["Rel Author"],
                        "year": 2025,
                        "abstract": "Adjacent field mechanism.",
                        "venue": "AdjacentConf",
                        "url": "https://openalex.org/W_rel",
                        "citation_count": 3,
                        "doi": "",
                        "referenced_works": [],
                        "related_works": [],
                        "source_bucket": "adjacent",
                        "search_bucket": "snowball",
                        "adjacent_field": True,
                    },
                ],
            },
        )


class T2SearchAgent(Agent):
    def __init__(self):
        super().__init__(
            AgentSpec(
                name="t2-search",
                model_tier="medium",
                tool_names=["arxiv_search", "fetch_outgoing_citations", "finish_task"],
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
        methods = [
            "hierarchical indexing",
            "causal routing",
            "episodic replay",
            "memory compression",
            "retrieval auditing",
            "long-context distillation",
            "graph memory",
            "tool trace grounding",
            "preference-aware recall",
            "stateful planning",
        ]
        settings = [
            "software engineering agents",
            "scientific discovery agents",
            "personal assistant workflows",
            "multi-step reasoning tasks",
            "interactive benchmark suites",
            "knowledge-intensive planning",
            "autonomous web navigation",
            "long-horizon task solving",
            "human feedback loops",
            "policy constrained tools",
            "document analysis systems",
            "collaborative research assistants",
        ]
        papers = []
        for i in range(120):
            method = methods[i % len(methods)]
            setting = settings[i % len(settings)]
            papers.append(
                {
                    "id": f"2502.{i:05d}",
                    "source": "arxiv",
                    "title": (
                        "LLM Agent Long Term Memory Retrieval with "
                        f"{method.title()} for {setting.title()}"
                    ),
                    "authors": ["Test Author"],
                    "year": 2025,
                    "abstract": (
                        f"LLM agent long-term memory retrieval using {method} in {setting}, "
                        "with hierarchical memory, compression, consistency, and evaluation."
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
async def test_fetch_pdf_failures_are_cached_within_run(tmp_workspace):
    tool = FailingFetchTool()
    runner = AgentRunner(
        MinimalAgent(),
        ToolRegistry(),
        MockLLMClient([]),
        MockHumanInterface(),
    )
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    cache = {}
    args = {"paper_id": "arxiv:2604.08224", "save_path": "literature/pdfs/arxiv_2604.08224.pdf"}

    first = await runner._execute_one_tool_call(
        ToolCall.create("fetch_paper_pdf", args),
        {"fetch_paper_pdf": tool},
        ctx=ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T3", run_id="r-cache"),
        policy=policy,
        step=1,
        tool_failure_cache=cache,
    )
    second = await runner._execute_one_tool_call(
        ToolCall.create("fetch_paper_pdf", args),
        {"fetch_paper_pdf": tool},
        ctx=ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T3", run_id="r-cache"),
        policy=policy,
        step=2,
        tool_failure_cache=cache,
    )

    assert first.metadata["is_error"] is True
    assert second.metadata["error"] == "cached_failure"
    assert "already failed in this run" in (second.content or "")
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_runner_records_t5_reproducibility_metadata(tmp_workspace):
    runner = AgentRunner(
        MinimalAgent(),
        ToolRegistry(),
        MockLLMClient([]),
        MockHumanInterface(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="p1",
        task_id="T5",
        run_id="r-meta",
        mode="pilot",
        extra={},
    )
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tools = {
        "docker_exec": SuccessfulDockerTool(),
        "write_file": SuccessfulWriteTool(),
    }

    await runner._execute_one_tool_call(
        ToolCall.create("docker_exec", {"command": "python run_pilot.py --smoke_test"}),
        tools,
        ctx=ctx,
        policy=policy,
        step=1,
        tool_failure_cache={},
    )
    await runner._execute_one_tool_call(
        ToolCall.create(
            "write_file",
            {"path": "pilot/pilot_code/run_pilot.py", "content": "print('v1')"},
        ),
        tools,
        ctx=ctx,
        policy=policy,
        step=2,
        tool_failure_cache={},
    )
    await runner._execute_one_tool_call(
        ToolCall.create(
            "write_file",
            {"path": "pilot/pilot_code/run_pilot.py", "content": "print('v2')"},
        ),
        tools,
        ctx=ctx,
        policy=policy,
        step=3,
        tool_failure_cache={},
    )

    assert ctx.extra["docker_exec_call_count"] == 1
    assert ctx.extra["docker_exec_success_count"] == 1
    assert ctx.extra["pilot_code_write_count"] == 2
    assert ctx.extra["artifact_write_counts"]["pilot/pilot_code/run_pilot.py"] == 2


@pytest.mark.asyncio
async def test_t2_search_results_are_auto_persisted_to_papers_raw(tmp_workspace):
    registry = ToolRegistry()
    registry.register("arxiv_search", lambda ctx: SearchTool())
    registry.register("fetch_outgoing_citations", lambda ctx: CitationSnowballTool())
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


def test_run_logger_marks_failed_search_error_as_append_status(tmp_workspace):
    logger = RunLogger(tmp_workspace)

    logger.tool_result(
        "arxiv_search",
        {"query": "   "},
        ok=False,
        content="query 不能为空",
        data={"query": "   "},
        error="empty_query",
        duration_ms=1,
        metadata={},
        step=3,
    )

    run_log = (tmp_workspace / "_runtime" / "logs" / "researchos.log").read_text(encoding="utf-8")
    assert "TOOL_RESULT" in run_log
    assert "tool=arxiv_search" in run_log
    assert "append_status=empty_query" in run_log
    assert "append_status=no_papers" not in run_log


@pytest.mark.asyncio
async def test_t2_failed_search_updates_scout_progress(tmp_workspace):
    from researchos.tools.finish_task import FinishTaskTool

    registry = ToolRegistry()
    registry.register("arxiv_search", lambda ctx: FailingSearchTool())
    registry.register("fetch_outgoing_citations", lambda ctx: CitationSnowballTool())
    registry.register("finish_task", lambda ctx: FinishTaskTool())
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(name="arxiv_search", arguments={"query": "   "}, id="tc1")
                    ]
                )
            )
        ]
    )
    runner = AgentRunner(T2SearchAgent(), registry, llm, MockHumanInterface())

    await runner.run(
        ExecutionContext(
            workspace_dir=tmp_workspace,
            project_id="p1",
            task_id="T2",
            run_id="T2_failed_search_progress",
        )
    )

    progress = (tmp_workspace / "literature" / "temp" / "scout_progress.md").read_text(encoding="utf-8")
    assert "runtime_search_result" in progress
    assert "append_status=empty_query" in progress


@pytest.mark.asyncio
async def test_t2_citation_snowball_candidates_are_auto_persisted(tmp_workspace):
    registry = ToolRegistry()
    registry.register("arxiv_search", lambda ctx: SearchTool())
    registry.register("fetch_outgoing_citations", lambda ctx: CitationSnowballTool())
    from researchos.tools.finish_task import FinishTaskTool

    registry.register("finish_task", lambda ctx: FinishTaskTool())
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="fetch_outgoing_citations",
                            arguments={"openalex_id_or_doi": "W_seed"},
                            id="tc1",
                        )
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
    runner = AgentRunner(T2SearchAgent(), registry, llm, MockHumanInterface())
    result = await runner.run(
        ExecutionContext(workspace_dir=tmp_workspace, project_id="p1", task_id="T2", run_id="r_snowball")
    )

    assert result.ok
    raw_records = [
        json.loads(line)
        for line in (tmp_workspace / "literature" / "papers_raw.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {record["id"] for record in raw_records} == {"W_ref", "W_rel"}
    assert any(record.get("source_bucket") == "adjacent" for record in raw_records)
    assert any(record.get("search_bucket") == "snowball" for record in raw_records)
    citation_edges = json.loads((tmp_workspace / "literature" / "citation_edges.json").read_text(encoding="utf-8"))
    assert ["W_seed", "W_ref"] in citation_edges
    assert ["W_seed", "W_rel"] in citation_edges


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
            extra={"allow_t2_failure_recovery": True, "t2_finish_finalize_min_raw": 10},
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
async def test_t2_large_raw_search_does_not_auto_finalize_without_finish_task(tmp_workspace):
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
            run_id="T2_large_raw_no_finish",
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

    assert not result.ok
    assert result.stop_reason == result.STOP_ERROR
    assert "No mock responses left" in (result.error or "")
    assert result.steps_used == 2
    assert llm.call_count == 2
    assert (tmp_workspace / "literature" / "papers_raw.jsonl").exists()
    assert not (tmp_workspace / "literature" / "papers_verified.jsonl").exists()
    assert not (tmp_workspace / "literature" / "deep_read_queue.jsonl").exists()
    assert "completion_mode" not in result.metadata


@pytest.mark.asyncio
async def test_t2_finish_task_finalizes_from_raw(tmp_workspace):
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
    registry.register("fetch_outgoing_citations", lambda ctx: CitationSnowballTool())
    from researchos.tools.finish_task import FinishTaskTool

    registry.register("finish_task", lambda ctx: FinishTaskTool())

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
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="finish_task",
                            arguments={"summary": "coverage is sufficient"},
                            id="tc2",
                        )
                    ]
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
        ExecutionContext(
            workspace_dir=tmp_workspace,
            project_id="p1",
            task_id="T2",
            run_id="T2_finish_finalize",
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
    assert result.metadata["completion_mode"] == "t2_finish_finalize"
    assert result.steps_used == 2
    assert llm.call_count == 2
    assert (tmp_workspace / "literature" / "papers_verified.jsonl").exists()
    assert (tmp_workspace / "literature" / "deep_read_queue.jsonl").exists()
    run_log = (tmp_workspace / "_runtime" / "logs" / "researchos.log").read_text(encoding="utf-8")
    assert "TOOL_RESULT" in run_log
    assert "tool=arxiv_search" in run_log
    assert "reported_paper_count=120" in run_log
    assert "persisted_raw_delta=120" in run_log
    assert "raw_count_after=120" in run_log
    assert "append_status=ok" in run_log
    assert "FINISH_REQUESTED" in run_log
    assert "VALIDATION_PASS" in run_log
