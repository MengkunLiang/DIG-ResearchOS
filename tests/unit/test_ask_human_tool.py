import pytest

from researchos.pydantic_compat import model_dump
from researchos.testing.mocks import MockHumanInterface
from researchos.tools.ask_human import AskHumanParams, AskHumanTool
from researchos.tools.human_gate import HumanInputUnavailable


def test_ask_human_params_accepts_json_string_suggestions():
    params = AskHumanParams(
        question="请选择",
        suggestions='["确认", "修改假设", "修改计划"]',
    )

    assert params.suggestions == ["确认", "修改假设", "修改计划"]


def test_ask_human_params_accepts_delimited_string_suggestions():
    params = AskHumanParams(
        question="请选择",
        suggestions="确认, 修改假设\n修改计划",
    )

    assert params.suggestions == ["确认", "修改假设", "修改计划"]


@pytest.mark.asyncio
async def test_ask_human_tool_forwards_coerced_suggestions():
    human = MockHumanInterface(clarification_answer="确认")
    tool = AskHumanTool(human)
    params = AskHumanParams(question="请选择", suggestions='["确认", "修改"]')

    result = await tool.execute(**model_dump(params))

    assert result.ok is True
    assert result.data["answer"] == "确认"
    assert result.data["interaction_id"].startswith("human_")
    assert "human_interaction_id" in result.content
    assert human.calls == [
        ("clarification", {"question": "请选择", "suggestions": ["确认", "修改"]})
    ]


@pytest.mark.asyncio
async def test_ask_human_tool_records_interaction(tmp_path):
    human = MockHumanInterface(clarification_answer="确认")
    tool = AskHumanTool(human, workspace_dir=tmp_path, task_id="T4", run_id="r1")

    result = await tool.execute(question="请选择", suggestions=["确认"])

    assert result.ok is True
    log_path = tmp_path / "_runtime" / "human_interactions.jsonl"
    record = __import__("json").loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["interaction_id"] == result.data["interaction_id"]
    assert record["task_id"] == "T4"
    assert record["run_id"] == "r1"
    assert record["answer"] == "确认"


@pytest.mark.asyncio
async def test_ask_human_tool_reports_input_unavailable():
    class _UnavailableHuman(MockHumanInterface):
        async def ask_clarification(self, *, question: str, suggestions: list[str] | None = None) -> str:
            raise HumanInputUnavailable("stdin closed")

    tool = AskHumanTool(_UnavailableHuman())

    result = await tool.execute(question="请选择", suggestions=["确认"])

    assert result.ok is False
    assert result.error == "human_input_unavailable"
    assert result.data["input_unavailable"] is True


@pytest.mark.asyncio
async def test_ask_human_tool_rejects_empty_answer():
    tool = AskHumanTool(MockHumanInterface(clarification_answer=""))

    result = await tool.execute(question="请选择")

    assert result.ok is False
    assert result.error == "human_input_unavailable"
