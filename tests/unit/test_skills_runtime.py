from pathlib import Path

import pytest

from researchos.skills.agent import SkillAgent
from researchos.skills.loader import discover_skills, load_skill
from researchos.skills.runner import run_skill
from researchos.skills.tool_aliases import translate_tool_names
from researchos.testing.mocks import (
    FakeLLMMessage,
    FakeRawCompletion,
    FakeToolCall,
    MockHumanInterface,
    MockLLMClient,
)
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def test_translate_tool_names_supports_alias_runtime_and_mcp():
    translated, warnings = translate_tool_names(
        ["Read", "finish_task", "Task", "mcp__arxiv__search"],
        available_tools={"read_file", "finish_task", "mcp_arxiv_search"},
    )

    assert translated == ["read_file", "finish_task", "mcp_arxiv_search"]
    assert any("Task" in warning for warning in warnings)


def test_discover_skill_and_build_agent(tmp_path):
    skill_dir = tmp_path / "hello-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: hello-skill
description: demo
tools:
  - Write
  - finish_task
  - Task
---
Write hello.txt and finish.
""",
        encoding="utf-8",
    )

    discovered = discover_skills(tmp_path)
    skill = discovered["hello-skill"]
    agent = SkillAgent(
        skill=skill,
        available_tools={"write_file", "finish_task"},
    )

    prompt = agent.system_prompt(
        type(
            "Ctx",
            (),
            {
                "workspace_dir": Path("/tmp/workspace"),
                "task_id": "SKILL_hello-skill",
                "project_id": "skill-run",
                "run_id": "run_1",
                "inputs": {},
                "outputs_expected": {},
                "mode": None,
                "extra": {},
            },
        )()
    )
    assert "workspace_dir" in prompt
    assert "Translation Warnings" in prompt
    assert agent.spec.tool_names == ["write_file", "finish_task"]


@pytest.mark.asyncio
async def test_run_skill_end_to_end(tmp_workspace):
    skill_dir = tmp_workspace / "skills" / "hello-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: hello-skill
tools:
  - Write
  - finish_task
---
Write hello.txt with the required content and then finish.
""",
        encoding="utf-8",
    )
    skill = load_skill(skill_dir)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={"path": "hello.txt", "content": "hello from skill"},
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

    result = await run_skill(
        skill=skill,
        user_request="请执行 skill。",
        workspace=tmp_workspace,
        tool_registry=registry,
        llm_client=llm,
        human_interface=MockHumanInterface(),
        outputs_expected={"hello_file": tmp_workspace / "hello.txt"},
    )

    assert result.ok
    assert (tmp_workspace / "hello.txt").read_text(encoding="utf-8") == "hello from skill"
