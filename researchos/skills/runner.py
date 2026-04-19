from __future__ import annotations

"""独立运行 skill 的便捷入口。"""

from pathlib import Path
import uuid

from ..runtime.agent import AgentResult, ExecutionContext
from ..runtime.orchestrator import AgentRunner
from ..tools.human_gate import HumanInterface
from ..tools.registry import ToolRegistry
from .agent import SkillAgent
from .loader import Skill


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


async def run_skill(
    *,
    skill: Skill,
    user_request: str,
    workspace: Path,
    tool_registry: ToolRegistry,
    llm_client,
    human_interface: HumanInterface,
    outputs_expected: dict[str, Path] | None = None,
    llm_profile: str | None = None,
) -> AgentResult:
    """在当前 workspace 中执行一个 skill。"""
    agent = SkillAgent(
        skill=skill,
        available_tools=set(tool_registry.available_names()),
        llm_profile=llm_profile,
    )
    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="skill-run",
        task_id=f"SKILL_{skill.name}",
        run_id=f"{skill.name}_{_short_id()}",
        outputs_expected=outputs_expected or {},
        extra={
            "user_request": user_request,
            "skill_dir": str(skill.skill_dir),
        },
    )
    runner = AgentRunner(agent, tool_registry, llm_client, human_interface)
    return await runner.run(ctx)
