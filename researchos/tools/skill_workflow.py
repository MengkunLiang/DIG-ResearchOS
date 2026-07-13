from __future__ import annotations

"""Durable progress updates for integrated standalone Skills."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..runtime.errors import ConfigurationError
from ..skills.session import record_workflow_progress
from .base import Tool, ToolResult


class UpdateSkillWorkflowParams(BaseModel):
    phase_id: str = Field(description="Workflow phase id declared in the Skill frontmatter.")
    status: Literal["running", "completed", "waiting_input", "waiting_evidence", "skipped"]
    summary: str = Field(min_length=8, max_length=1200, description="Research-facing result or current action for this phase.")
    artifacts: list[str] = Field(default_factory=list, description="Workspace-relative artifacts created, reused, or reviewed in this phase.")
    evidence_boundary: str = Field(default="", max_length=1000, description="Known support limit, unsupported result, or empty string when none.")
    next_action: str = Field(default="", max_length=800, description="Concrete next action or human decision.")


class UpdateSkillWorkflowTool(Tool):
    name = "update_skill_workflow"
    description = (
        "Persist one integrated Skill workflow milestone in the active session. Use this at every phase start, "
        "phase completion, and evidence/input wait so the CLI can show the research process without exposing hidden reasoning."
    )
    parameters_schema = UpdateSkillWorkflowParams

    def __init__(self, *, workspace: Path, session_id: str | None, task_id: str | None):
        self.workspace = workspace
        self.session_id = session_id
        self.task_id = task_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = UpdateSkillWorkflowParams(**kwargs)
        if not self.session_id or not str(self.task_id or "").startswith("SKILL_"):
            return ToolResult(
                ok=False,
                content="update_skill_workflow is available only inside a guided standalone Skill session.",
                error="skill_session_required",
            )
        try:
            path, entry = record_workflow_progress(
                workspace=self.workspace,
                session_id=self.session_id,
                phase_id=params.phase_id,
                status=params.status,
                summary=params.summary,
                artifacts=params.artifacts,
                evidence_boundary=params.evidence_boundary,
                next_action=params.next_action,
            )
        except ConfigurationError as exc:
            return ToolResult(ok=False, content=str(exc), error="invalid_workflow_phase")
        return ToolResult(
            ok=True,
            content=f"Workflow phase '{params.phase_id}' recorded as {params.status}.",
            data={"session_path": str(path.relative_to(self.workspace)), "phase": entry},
        )
