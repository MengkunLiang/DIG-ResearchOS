import pytest

from researchos.pydantic_compat import model_dump
from researchos.testing.mocks import MockHumanInterface
from researchos.tools.ask_human import AskHumanParams, AskHumanTool


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
    assert human.calls == [
        ("clarification", {"question": "请选择", "suggestions": ["确认", "修改"]})
    ]
