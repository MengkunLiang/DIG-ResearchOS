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


def test_build_execution_context_propagates_mode_phase_and_round(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T8-REVISE-2
        states:
          T8-REVISE-2:
            agent: writer
            mode: revise
            round: 2
            outputs:
              paper: drafts/paper.tex
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.mode == "revise"
    assert ctx.extra["phase"] == "revise"
    assert ctx.extra["round"] == 2


def test_build_execution_context_injects_generic_recovery_snapshot(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T6
        states:
          T6:
            agent: novelty
            outputs:
              novelty_report: novelty/novelty_report.md
              collision_cases: novelty/collision_cases.md
              must_add_baselines: novelty/must_add_baselines.md
        """,
    )
    (tmp_workspace / "novelty").mkdir(parents=True)
    (tmp_workspace / "novelty" / "novelty_report.md").write_text("# report\n", encoding="utf-8")
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.extra["resume_mode"] is True
    assert "novelty_report" in ctx.extra["resume_existing_outputs"]
    assert "must_add_baselines" in ctx.extra["resume_missing_outputs"]


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


def test_gate_option_extra_flows_into_task_context(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T1
        states:
          T1:
            agent: hello
            gate: review_gate
          T2:
            agent: hello
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          review_gate:
            options:
              - id: go
                label: Go
                next: T2
                extra:
                  chosen_direction: 2
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
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
        duration_seconds=0.1,
        stop_reason=AgentResult.STOP_FINISHED,
    )

    state = sm.advance(state, result, workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(state, {"option_id": "go", "captured": {}})

    assert state.current_task == "T2"
    assert state.task_context["chosen_direction"] == 2


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


def test_validate_definition_reports_unknown_branch_and_contract_mismatch(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4
        states:
          T4:
            agent: hello
            inputs:
              project: project.yaml
            outputs:
              wrong_output: ideation/wrong.md
            gate:
              id: review_gate
              branches:
                retry: T4
                accept: T_DOES_NOT_EXIST
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          review_gate:
            options:
              - id: retry
                label: Retry
              - id: accept
                label: Accept
        """,
    )

    sm = StateMachine(config, gates)
    errors = sm.validate_definition()

    assert any("unknown node 'T_DOES_NOT_EXIST'" in item for item in errors)
    assert any("node.outputs does not match task_io_contract" in item for item in errors)
