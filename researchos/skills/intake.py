from __future__ import annotations

"""Constrained interactive material collection for guided Skills.

The intake agent can organize material supplied by a human into the declared
``user_inputs/<skill>/`` area. It cannot create a paper, experiment artifact,
or any other substantive Skill output, so a missing upload never becomes a
license to invent research material.
"""

from pathlib import Path
from typing import Iterable

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.errors import ConfigurationError
from .contracts import SkillInputRequirement, SkillInteraction


def _user_input_paths(skill_name: str, requirements: Iterable[SkillInputRequirement]) -> list[str]:
    prefix = f"user_inputs/{skill_name}/"
    paths: list[str] = []
    for requirement in requirements:
        for path in requirement.paths:
            if path.startswith(prefix) and path not in paths:
                paths.append(path)
    return paths


class SkillIntakeAgent(Agent):
    """Ask focused questions and stage only human-provided material."""

    def __init__(self, *, skill_name: str, interaction: SkillInteraction) -> None:
        self.skill_name = skill_name
        self.interaction = interaction
        self.intake_paths = _user_input_paths(
            skill_name,
            interaction.required_inputs + interaction.optional_inputs,
        )
        if not self.intake_paths:
            raise ConfigurationError(
                f"guided skill '{skill_name}' has no writable user_inputs path for interactive intake"
            )
        super().__init__(
            AgentSpec(
                name=f"skill_intake_{skill_name}",
                model_tier="medium",
                tool_names=["read_file", "write_file", "ask_human", "finish_task"],
                max_steps=12,
                max_tokens_total=50_000,
                max_wall_seconds=900,
                temperature=0.0,
                allowed_read_prefixes=[
                    f"user_inputs/{skill_name}/",
                    "_runtime/skill_sessions/",
                ],
                allowed_write_prefixes=[f"user_inputs/{skill_name}/"],
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        required_lines: list[str] = []
        for item in self.interaction.required_inputs:
            writable = [path for path in item.paths if path in self.intake_paths]
            preferred = writable[0] if writable else item.paths[0]
            extensions = ", ".join(item.extensions) if item.extensions else "any"
            required_lines.append(
                f"- {item.key} / {item.label}: {item.description}\n"
                f"  preferred staging path: `{preferred}`\n"
                f"  deterministic minimum: {item.min_bytes} bytes; extensions: {extensions}"
            )
        paths = "\n".join(f"- `{path}`" for path in self.intake_paths)
        intake_packet = str(ctx.extra.get("skill_intake_packet_path") or "")
        return f"""# Restricted Guided-Skill Intake

You are collecting initial human material for the Skill `{self.skill_name}`.
This is not the Skill itself and must not create final outputs.

## Allowed actions
- Read the deterministic checklist `{intake_packet}` and files below `user_inputs/{self.skill_name}/`.
- Ask one focused human question at a time with `ask_human`.
- When the human pastes prose, organize only that supplied material into one declared path under `user_inputs/{self.skill_name}/` using `write_file`.
- When the human says they uploaded a file, inspect the declared intake path. Do not copy an uninspected file or claim it is semantically sufficient.
- Repeat focused questions until all required material has a usable declared file, or the human explicitly pauses.
- Call `finish_task` only after you have staged what the human provided or recorded that the user paused.

## Strict boundaries
- You may write only these paths:
{paths}
- Do not write `drafts/`, `literature/`, `ideation/`, `experiments/`, `submission/`, or any final Skill output.
- Never invent a research problem, method, evidence, numerical result, citation, bibliography entry, venue rule, author detail, or file content.
- Preserve uncertainty. If pasted content lacks a necessary fact, ask for that fact rather than filling it in.
- For `.tex`, `.bib`, `.csv`, or `.json`, ask the human to upload a file at the declared path unless they explicitly paste complete valid content. Do not synthesize source-format content from a prose summary.

## Required material
{chr(10).join(required_lines)}

The CLI runs a deterministic readiness check after you finish. Passing that check only establishes initial material presence; the actual Skill still performs semantic evidence checks and may ask focused follow-up questions later.
"""

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        request = str(ctx.extra.get("user_request") or "").strip()
        return (
            "Start interactive intake. Read the checklist, then explain the first missing required item "
            "and ask whether the human will upload it or paste the content. "
            f"User's stated task: {request or 'not yet specified'}"
        )
