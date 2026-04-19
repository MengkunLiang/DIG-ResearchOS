from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..runtime.errors import ConfigurationError


@dataclass
class Skill:
    name: str
    description: str
    body: str
    allowed_tools: list[str]
    skill_dir: Path
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise ConfigurationError("SKILL.md frontmatter is not closed with '---'")
    raw_meta, body = parts
    meta = yaml.safe_load(raw_meta.removeprefix("---\n")) or {}
    if not isinstance(meta, dict):
        raise ConfigurationError("SKILL.md frontmatter must be a YAML object")
    return meta, body


def load_skill(skill_dir: Path) -> Skill:
    if not skill_dir.is_dir():
        raise ConfigurationError(f"Skill dir not found: {skill_dir}")
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ConfigurationError(f"SKILL.md missing in {skill_dir}")
    meta, body = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = meta.get("name") or skill_dir.name
    tools = meta.get("tools")
    if tools is None:
        tools = meta.get("allowed-tools", [])
    if not isinstance(tools, list):
        raise ConfigurationError(f"Skill tools must be a list: {skill_md}")
    return Skill(
        name=name,
        description=meta.get("description", ""),
        body=body.strip(),
        allowed_tools=[str(item) for item in tools],
        skill_dir=skill_dir,
        metadata=meta,
    )


def discover_skills(skills_root: Path) -> dict[str, Skill]:
    if not skills_root.exists():
        return {}
    discovered: dict[str, Skill] = {}
    for child in sorted(skills_root.iterdir()):
        if not child.is_dir() or not (child / "SKILL.md").exists():
            continue
        skill = load_skill(child)
        if skill.name in discovered:
            raise ConfigurationError(f"Duplicate skill name '{skill.name}' in {skills_root}")
        discovered[skill.name] = skill
    return discovered
