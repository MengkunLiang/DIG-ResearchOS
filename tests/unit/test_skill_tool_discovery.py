import asyncio
from pathlib import Path

import pytest

from researchos.skills.loader import register_skill_tools, resolve_skill
from researchos.tools.base import ToolResult
from researchos.tools.registry import ToolBuildContext, ToolRegistry
from researchos.tools.workspace_policy import WorkspaceAccessPolicy
from researchos.testing.mocks import MockHumanInterface


def test_register_skill_tools_and_resolve_skill(tmp_path: Path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo-skill"
    tools_dir = skill_dir / "tools"
    tools_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo-skill
tools:
  - skill_demo_echo
---
demo body
""",
        encoding="utf-8",
    )
    (tools_dir / "echo_tool.py").write_text(
        """
from pydantic import BaseModel
from researchos.tools.base import Tool, ToolResult

class Params(BaseModel):
    text: str

class DemoTool(Tool):
    name = "skill_demo_echo"
    description = "demo"
    parameters_schema = Params

    async def execute(self, **kwargs):
        return ToolResult(ok=True, content=kwargs["text"])

TOOL = DemoTool()
""".strip(),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    register_skill_tools(registry, [skills_root])

    policy = WorkspaceAccessPolicy(tmp_path, [""], [""])
    built = registry.build(
        ["skill_demo_echo"],
        ToolBuildContext(policy=policy, human=MockHumanInterface()),
    )
    result = asyncio.run(built["skill_demo_echo"].execute(text="hello"))
    assert result.ok

    skill = resolve_skill("demo-skill", [skills_root])
    assert skill.skill_dir == skill_dir


@pytest.mark.asyncio
async def test_registered_skill_tool_executes(tmp_path: Path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo-skill"
    tools_dir = skill_dir / "tools"
    tools_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\nbody\n", encoding="utf-8")
    (tools_dir / "echo_tool.py").write_text(
        """
from pydantic import BaseModel
from researchos.tools.base import Tool, ToolResult

class Params(BaseModel):
    text: str

class DemoTool(Tool):
    name = "skill_demo_echo"
    description = "demo"
    parameters_schema = Params

    async def execute(self, **kwargs):
        return ToolResult(ok=True, content=kwargs["text"], data={"text": kwargs["text"]})

TOOL = DemoTool()
""".strip(),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    register_skill_tools(registry, [skills_root])
    policy = WorkspaceAccessPolicy(tmp_path, [""], [""])
    built = registry.build(
        ["skill_demo_echo"],
        ToolBuildContext(policy=policy, human=MockHumanInterface()),
    )

    result = await built["skill_demo_echo"].execute(text="hello")

    assert result == ToolResult(ok=True, content="hello", data={"text": "hello"})
