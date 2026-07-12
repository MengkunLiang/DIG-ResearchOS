from __future__ import annotations

from researchos.agents.ideation import T4_GATE1_ARTIFACTS, refresh_t4_gate1_progress
from researchos.runtime.agent import ExecutionContext
from researchos.runtime.message import ToolCall
from researchos.runtime.orchestrator import AgentRunner


def test_t4_progress_uses_actual_artifacts_and_marks_post_gate_work_waiting(tmp_path):
    (tmp_path / "ideation").mkdir()
    (tmp_path / "literature").mkdir()
    result = refresh_t4_gate1_progress(tmp_path)
    progress = (tmp_path / "ideation" / "t4_progress.md").read_text(encoding="utf-8")
    assert result["completed_count"] == 0
    assert "[running] compact context pack" in progress
    assert "[queued] 1/6 Pass1 候选发散" in progress
    assert "[waiting_human]" in progress
    assert "idea_scorecard.yaml" in progress

    (tmp_path / "ideation" / "t4_context_pack.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ideation" / "t4_context_pack.md").write_text("# compact pack\n", encoding="utf-8")
    result = refresh_t4_gate1_progress(tmp_path)
    progress = (tmp_path / "ideation" / "t4_progress.md").read_text(encoding="utf-8")
    assert result["completed_count"] == 0
    assert "[done] compact context pack" in progress
    assert "[running] 1/6 Pass1 候选发散" in progress

    for relative, _label in T4_GATE1_ARTIFACTS[:2]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    result = refresh_t4_gate1_progress(tmp_path)
    assert result["completed_count"] == 2
    assert "[done] 2/6 Pass2 接地复核" in (tmp_path / "ideation" / "t4_progress.md").read_text(encoding="utf-8")


def test_t4_gate1_write_order_rejects_skipping_persisted_predecessors(tmp_path):
    context = ExecutionContext(workspace_dir=tmp_path, project_id="p", task_id="T4", run_id="r")
    skipped = ToolCall.create("write_file", {"path": "ideation/_candidate_directions.json", "content": "{}"})
    error = AgentRunner._t4_artifact_write_order_error(context, skipped)
    assert error is not None
    assert "_pass1_forward_candidates.json" in error

    first = tmp_path / T4_GATE1_ARTIFACTS[0][0]
    second = tmp_path / T4_GATE1_ARTIFACTS[1][0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    assert AgentRunner._t4_artifact_write_order_error(context, skipped) is None
