from __future__ import annotations

"""独立运行 skill 的便捷入口。"""

from pathlib import Path
import uuid

from ..runtime.agent import AgentResult, ExecutionContext, LLMConfigOverride
from ..runtime.config import RuntimeSettings
from ..runtime.orchestrator import AgentRunner
from ..tools.human_gate import HumanInterface
from ..tools.registry import ToolRegistry
from .agent import SkillAgent
from .contracts import SkillInteraction
from .intake import SkillIntakeAgent
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
    runtime_settings: RuntimeSettings | None = None,
    skill_session_path: str | None = None,
    skill_session_id: str | None = None,
    selected_inputs: dict[str, Path] | None = None,
    workspace_mode: str = "standalone",
    intake_packet_path: str = "",
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
        inputs={key: path for key, path in (selected_inputs or {}).items()},
        outputs_expected=outputs_expected or {},
        extra={
            "user_request": user_request,
            "skill_dir": str(skill.skill_dir),
            "skill_session_path": skill_session_path or "",
            "skill_session_id": skill_session_id or "",
            "skill_workspace_mode": workspace_mode,
            "skill_intake_packet_path": intake_packet_path,
            "skill_selected_inputs": {
                key: str(path.relative_to(workspace)) if path.is_relative_to(workspace) else str(path)
                for key, path in (selected_inputs or {}).items()
            },
        },
    )
    runner = AgentRunner(
        agent,
        tool_registry,
        llm_client,
        human_interface,
        runtime_settings=runtime_settings,
    )
    return await runner.run(ctx)


async def run_skill_intake(
    *,
    skill_name: str,
    interaction: SkillInteraction,
    user_request: str,
    workspace: Path,
    tool_registry: ToolRegistry,
    llm_client,
    human_interface: HumanInterface,
    session_id: str,
    intake_packet_path: str,
    runtime_settings: RuntimeSettings | None = None,
    llm_profile: str | None = None,
) -> AgentResult:
    """Run a bounded material-collection phase before normal Skill execution."""

    agent = SkillIntakeAgent(skill_name=skill_name, interaction=interaction)
    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="skill-intake",
        task_id=f"SKILL_INTAKE_{skill_name}",
        run_id=f"{skill_name}_intake_{_short_id()}",
        llm_override=LLMConfigOverride(profile=llm_profile),
        extra={
            "user_request": user_request,
            "skill_session_id": session_id,
            "skill_intake_packet_path": intake_packet_path,
        },
    )
    runner = AgentRunner(
        agent,
        tool_registry,
        llm_client,
        human_interface,
        runtime_settings=runtime_settings,
    )
    return await runner.run(ctx)
