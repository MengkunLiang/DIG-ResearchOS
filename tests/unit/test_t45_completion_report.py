from __future__ import annotations

from pathlib import Path

from researchos.runtime.observability.reporter import StageReporter
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace


def _reporter(workspace: Path, output: list[str]) -> StageReporter:
    return StageReporter(
        workspace=workspace,
        no_color=True,
        emit_fn=output.append,
    )


def test_t45_completion_table_appears_only_after_final_review(tmp_path: Path) -> None:
    """Audit output is intermediate; the final table requires accepted review."""

    populate_valid_t45_workspace(tmp_path)
    output: list[str] = []
    reporter = _reporter(tmp_path, output)

    reporter.stage_completed(
        task_id="T4.5",
        run_id="novelty-audit",
        outputs={"novelty_audit": tmp_path / "ideation" / "novelty_audit.md"},
        ok=True,
        summary="Novelty audit completed.",
        error=None,
    )

    assert "研究方案审计与正式化已完成" not in "\n".join(output)

    reporter.stage_completed(
        task_id="T4.5-REVIEW",
        run_id="final-review",
        outputs={"orientation_review": tmp_path / "ideation" / "orientation_review.json"},
        ok=True,
        summary="Research-plan quality review accepted.",
        error=None,
    )

    rendered = "\n".join(output)
    assert "研究方案审计与正式化已完成" in rendered
    assert "质量门已通过" in rendered
    assert "ideation/research_blueprint.yaml" in rendered
    assert "ideation/proposal/proposal_manifest.json" in rendered
    assert "ideation/post_novelty_formalization.json" in rendered
