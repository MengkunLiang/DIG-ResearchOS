"""End-to-end deterministic smoke coverage for the 13-Skill specializer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from researchos.skills.project_specialization.compiler import specialize_project_skills
from researchos.tools.external_experiment import CompileResearchReboostHandoffTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace


def _compile_current_t5_handoff(workspace: Path) -> None:
    """Publish a real current-format T5 handoff from the shared T4.5 fixture."""

    policy = WorkspaceAccessPolicy(
        workspace_dir=workspace,
        allowed_read_prefixes=["external_executor/"],
        allowed_write_prefixes=["external_executor/"],
        task_id="T5-REBOOST-GATE",
    )
    result = asyncio.run(CompileResearchReboostHandoffTool(policy).execute())
    assert result.ok, result.content


def test_project_skill_specializer_dry_run_renders_all_templates_without_publishing(tmp_path: Path) -> None:
    """A valid handoff must render all executor templates and retain real uncertainty."""

    populate_valid_t45_workspace(tmp_path)
    _compile_current_t5_handoff(tmp_path)

    result = specialize_project_skills(
        workspace=tmp_path,
        repo_root=Path(__file__).resolve().parents[2],
        dry_run=True,
    )

    assert result.status in {"ready", "incomplete"}
    assert result.errors == []
    assert result.report["skills_total"] == 13
    assert result.report["skills_specialized"] == 13
    assert len(result.report["skills"]) == 13
    assert all(skill["template_integrity"] == "pass" for skill in result.report["skills"])
    # The shared research fixture intentionally has unresolved execution
    # details. A dry run must preserve that fact and must not publish a suite.
    assert result.status == "incomplete"
    assert result.required_uncertain_fields
    assert not (tmp_path / "external_executor" / "skills").exists()
