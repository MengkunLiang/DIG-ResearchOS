from __future__ import annotations

"""Public stage aliases accepted by the CLI.

The state machine deliberately uses explicit node identifiers such as
``T3.6-GATE-SURVEY``.  Users, documentation, and older workspaces naturally
refer to the public stage name (``T3.6``), so command entry points must resolve
that name consistently before they validate prerequisites or create state.
"""


_PUBLIC_STAGE_ALIASES: dict[str, str] = {
    "T3.6": "T3.6-GATE-SURVEY",
    "T3.6-SURVEY": "T3.6-GATE-SURVEY",
    "SURVEY": "T3.6-GATE-SURVEY",
    "T5": "T5-REBOOST-GATE",
    "T5-REBOOST": "T5-REBOOST-GATE",
    "T5-SPECIALIZE": "T5-SPECIALIZE-EXECUTOR-SKILLS",
    "T5-SPECIALIZE-EXECUTOR-SKILLS": "T5-SPECIALIZE-EXECUTOR-SKILLS",
    # The top-level public T8 entry intentionally starts from the mandatory
    # writing-style Gate. Real downstream node names must remain runnable for
    # targeted recovery once their declared inputs validate.
    "T8": "T8-STYLE-GATE",
    "T8-SECTIONS": "T8-SECTION-PLAN",
    "T8-SEC-LIMITATIONS": "T8-SEC-CONCLUSION",
}


def resolve_public_stage_alias(task_id: str) -> str:
    """Return the canonical state-machine identifier for a public stage name."""

    normalized = str(task_id or "").strip()
    return _PUBLIC_STAGE_ALIASES.get(normalized.upper(), normalized)
