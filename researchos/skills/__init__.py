from .agent import SkillAgent
from .loader import (
    Skill,
    discover_skills,
    discover_skills_from_roots,
    load_skill,
    register_skill_tools,
    resolve_skill,
)
from .tool_aliases import CLAUDE_CODE_TOOL_ALIASES, translate_tool_names


def run_skill(*args, **kwargs):
    """Lazily import the skill runner to avoid the tool/agent import cycle.

    Tool modules import project-specialization helpers during registry setup.
    Importing ``runner`` eagerly here pulls the orchestrator and the full agent
    registry back into that path, including external_experiment itself.
    """

    from .runner import run_skill as _run_skill

    return _run_skill(*args, **kwargs)

__all__ = [
    "CLAUDE_CODE_TOOL_ALIASES",
    "Skill",
    "SkillAgent",
    "discover_skills",
    "discover_skills_from_roots",
    "load_skill",
    "register_skill_tools",
    "resolve_skill",
    "run_skill",
    "translate_tool_names",
]
