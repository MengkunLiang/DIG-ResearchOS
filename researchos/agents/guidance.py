from __future__ import annotations

"""Reusable LLM guidance blocks for built-in agents.

These files follow a lightweight SKILL.md style: optional YAML frontmatter plus
Markdown instructions. They are prompt guidance, not deterministic tools.
"""

from pathlib import Path


_GUIDANCE_ROOT = Path(__file__).resolve().parent.parent / "agent_guidance"


def load_agent_guidance(*names: str) -> str:
    """Load named guidance blocks from ``researchos/agent_guidance``.

    Missing blocks are ignored so incomplete installs do not break the runtime.
    """

    blocks: list[str] = []
    for name in names:
        path = _GUIDANCE_ROOT / name / "SKILL.md"
        if not path.exists():
            continue
        body = _strip_frontmatter(path.read_text(encoding="utf-8"))
        if body.strip():
            blocks.append(f"## Guidance: {name}\n\n{body.strip()}")
    return "\n\n".join(blocks)


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return text
    return parts[1]
