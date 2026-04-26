from pathlib import Path

from researchos.runtime.task_recovery import prepare_task_resume_artifacts


def test_prepare_task_resume_artifacts_detects_existing_and_missing_outputs(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "novelty").mkdir(parents=True)
    (workspace / "novelty" / "novelty_report.md").write_text("# report\n", encoding="utf-8")
    (workspace / "novelty" / "collision_cases.md").write_text("# collisions\n", encoding="utf-8")

    outputs_expected = {
        "novelty_report": workspace / "novelty" / "novelty_report.md",
        "collision_cases": workspace / "novelty" / "collision_cases.md",
        "must_add_baselines": workspace / "novelty" / "must_add_baselines.md",
    }

    info = prepare_task_resume_artifacts(
        workspace,
        task_id="T6",
        outputs_expected=outputs_expected,
        base_extra={},
    )

    assert info["resume_mode"] is True
    assert info["resume_existing_outputs"] == ["novelty_report", "collision_cases"]
    assert info["resume_missing_outputs"] == ["must_add_baselines"]
    assert "novelty/novelty_report.md" in info["resume_existing_artifacts"]
    assert (workspace / "_runtime" / "resume" / "t6_resume_state.json").exists()


def test_prepare_task_resume_artifacts_ignores_runtime_managed_state_file(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "state.yaml").write_text("status: RUNNING\n", encoding="utf-8")

    outputs_expected = {
        "state": workspace / "state.yaml",
    }

    info = prepare_task_resume_artifacts(
        workspace,
        task_id="T1",
        outputs_expected=outputs_expected,
        base_extra={},
    )

    assert info["resume_mode"] is False
    assert info["resume_existing_outputs"] == []
    assert info["resume_missing_outputs"] == ["state"]
