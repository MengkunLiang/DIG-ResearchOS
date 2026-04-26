from __future__ import annotations

import json
from pathlib import Path

from researchos.runtime.experiment_recovery import prepare_experiment_resume_artifacts


def test_prepare_experiment_resume_artifacts_for_pilot(tmp_path: Path):
    workspace = tmp_path / "ws"
    code_dir = workspace / "pilot" / "pilot_code"
    code_dir.mkdir(parents=True)
    (workspace / "pilot" / "pilot_plan.yaml").write_text("experiments: []\n", encoding="utf-8")
    (code_dir / "run_pilot.py").write_text("print('ok')\n", encoding="utf-8")

    info = prepare_experiment_resume_artifacts(workspace, mode="pilot")

    assert info["resume_mode"] is True
    assert "pilot_plan" in info["resume_existing_outputs"]
    assert "pilot_results" in info["resume_missing_outputs"]
    assert info["resume_has_existing_code"] is True
    state_path = workspace / "pilot" / "pilot_resume_state.json"
    assert state_path.exists()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["has_existing_code"] is True


def test_prepare_experiment_resume_artifacts_for_full(tmp_path: Path):
    workspace = tmp_path / "ws"
    code_dir = workspace / "experiments" / "code"
    code_dir.mkdir(parents=True)
    (workspace / "experiments" / "results_summary.json").write_text("{}", encoding="utf-8")
    (code_dir / "run_exp.py").write_text("print('ok')\n", encoding="utf-8")

    info = prepare_experiment_resume_artifacts(workspace, mode="full")

    assert info["resume_mode"] is True
    assert "results_summary" in info["resume_existing_outputs"]
    assert info["resume_has_existing_code"] is True
