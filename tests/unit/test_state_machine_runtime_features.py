from pathlib import Path
import textwrap

from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.agent import AgentResult
from researchos.schemas.state import StateYaml, TaskHistoryEntry


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_build_execution_context_sets_resume_and_iteration(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T1
        states:
          T1:
            agent: hello
            outputs:
              hello_file: hello.txt
        """,
    )
    sm = StateMachine(config)
    state = StateYaml(
        project_id="p1",
        current_task="T1",
        history=[
            TaskHistoryEntry(
                task="T1",
                run_id="prev_run",
                status="INTERRUPTED",
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:10:00+00:00",
            )
        ],
        iteration_count={"T1": 2},
        status="PAUSED",
    )

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.extra["is_resume"] is True
    assert ctx.extra["resumed_from"] == "prev_run"
    assert ctx.extra["iteration_count"] == 2


def test_advance_enters_gate_and_resolve_branch_increments_iteration(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T1
        states:
          T1:
            agent: hello
            gate:
              id: review_gate
              branches:
                retry: T1
                accept: done
          done:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          review_gate:
            presentation:
              summary:
                literal: choose
            options:
              - id: retry
                label: Retry
              - id: accept
                label: Accept
        """,
    )
    sm = StateMachine(config, gates)
    state = sm.create_initial_state("p1")
    state = sm.start_task(state, "run_1")
    result = AgentResult(
        ok=True,
        message="done",
        outputs_produced={},
        steps_used=1,
        tokens_in=5,
        tokens_out=7,
        cost_usd=0.1,
        duration_seconds=1.0,
        stop_reason=AgentResult.STOP_FINISHED,
    )

    state = sm.advance(state, result, workspace_dir=tmp_workspace)
    assert state.status == "WAITING_HUMAN"
    assert state.pending_gate is not None
    assert state.pending_gate.gate_id == "review_gate"

    state = sm.resolve_pending_gate(state, {"option_id": "retry", "captured": {}})
    assert state.status == "RUNNING"
    assert state.current_task == "T1"
    assert state.iteration_count["T1"] == 1


def test_mark_interrupted_updates_state(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T1
        states:
          T1:
            agent: hello
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")
    state = sm.start_task(state, "run_1")

    state = sm.mark_interrupted(state)

    assert state.status == "PAUSED"
    assert state.history[-1].status == "INTERRUPTED"
    assert state.history[-1].stop_reason == AgentResult.STOP_INTERRUPTED
