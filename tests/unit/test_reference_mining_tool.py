from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.skills.loader import discover_skills
from researchos.tools.reference_mining import MineReferenceProjectsTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@pytest.mark.asyncio
async def test_mine_reference_projects_generates_pattern_artifacts(tmp_path: Path):
    reference_repo = tmp_path / "reference" / "AutoResearchClaw-main"
    reference_repo.mkdir(parents=True)
    (reference_repo / "README.md").write_text(
        "StageContract checkpoint benchmark dataset baseline repo result-to-claim paper-claim-audit",
        encoding="utf-8",
    )
    (reference_repo / "skills").mkdir()
    (reference_repo / "skills" / "result-to-claim.md").write_text(
        "result-to-claim maps experiment evidence to claims",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(
        workspace,
        [""],
        ["researchos_reference/", "docs/"],
    )

    result = await MineReferenceProjectsTool(policy).execute(
        reference_roots=[str(reference_repo)],
        output_dir="researchos_reference",
        review_output_path="docs/reference_project_review.md",
    )

    assert result.ok
    pattern_path = workspace / "researchos_reference" / "pattern_cards.jsonl"
    cards = [json.loads(line) for line in pattern_path.read_text(encoding="utf-8").splitlines()]
    assert cards
    assert any("RESULT_TO_CLAIM" in card["pattern_id"] for card in cards)
    assert (workspace / "researchos_reference" / "transfer_matrix.csv").exists()
    review = (workspace / "docs" / "reference_project_review.md").read_text(encoding="utf-8")
    assert "Reference Project Review" in review
    assert "reference_missing: `False`" in review


@pytest.mark.asyncio
async def test_mine_reference_projects_records_missing_reference(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(workspace, [""], ["researchos_reference/", "docs/"])

    result = await MineReferenceProjectsTool(policy).execute(
        reference_roots=[str(tmp_path / "missing-reference")],
        output_dir="researchos_reference",
        review_output_path="docs/reference_project_review.md",
    )

    assert result.ok
    card = json.loads((workspace / "researchos_reference" / "pattern_cards.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "REFERENCE_MISSING" in card["pattern_id"]
    review = (workspace / "docs" / "reference_project_review.md").read_text(encoding="utf-8")
    assert "reference_missing: `True`" in review


def test_new_researchos_skills_are_discoverable():
    skills = discover_skills(Path("skills"))

    for name in [
        "reference-project-miner",
        "external-executor-bridge",
        "experiment-integrity-audit",
        "result-to-claim",
        "paper-claim-audit",
        "experiment-to-writing-handoff",
    ]:
        assert name in skills

