from pathlib import Path
import json
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


def test_mark_interrupted_records_stale_running_reason(tmp_workspace):
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
    state = sm.create_initial_state("p1")
    state = sm.start_task(state, "run_stale")

    state = sm.mark_interrupted(state, reason="resume_detected_stale_running_state")

    assert state.status == "PAUSED"
    assert state.history[-1].status == "INTERRUPTED"
    assert state.history[-1].error == "resume_detected_stale_running_state"


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


def test_build_execution_context_supports_unlimited_budget_override(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T3
        states:
          T3:
            agent: reader
            budget:
              max_steps: 1
              unlimited_budget: true
            outputs:
              notes: literature/paper_notes
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.budget_override.max_steps == 1
    assert ctx.budget_override.unlimited_budget is True


def test_build_execution_context_supports_unlimited_budget_tag(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T8-SEC-INTRODUCTION
        states:
          T8-SEC-INTRODUCTION:
            agent: writer
            tags:
              - unlimited_budget
            budget:
              max_steps: 1
            outputs:
              section: drafts/sections/01_introduction.tex
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.budget_override.max_steps == 1
    assert ctx.budget_override.unlimited_budget is True


def test_build_execution_context_can_explicitly_disable_unlimited_budget(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4
        states:
          T4:
            agent: ideation
            budget:
              unlimited_budget: "false"
            outputs:
              hypotheses: ideation/hypotheses.md
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.budget_override.unlimited_budget is False


def test_task_without_llm_profile_override_inherits_agent_params_profile(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4
        states:
          T4:
            agent: ideation
            outputs:
              hypotheses: ideation/hypotheses.md
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.llm_override.profile is None


def test_task_llm_profile_override_is_explicit_and_visible(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4
        states:
          T4:
            agent: ideation
            llm:
              profile: audit_safe
            outputs:
              hypotheses: ideation/hypotheses.md
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.llm_override.profile == "audit_safe"


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


def test_t4_gate1_completion_mode_routes_to_immediate_gate(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4
        states:
          T4:
            agent: ideation
            outputs:
              hypotheses: ideation/hypotheses.md
            next_on_success: T4.5
          T4-GATE1:
            agent: ideation
            extra:
              immediate_gate: true
            gate: t4_gate1_selection_gate
            outputs:
              gate1_user_selection: ideation/_gate1_user_selection.json
            next_on_success: T4
          T4.5:
            agent: novelty_auditor
            outputs:
              novelty_audit: ideation/novelty_audit.md
          done:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t4_gate1_selection_gate:
            presentation:
              brief:
                literal: choose candidate
            options:
              - id: merge
                label: Merge
                next: T4
        """,
    )
    sm = StateMachine(config, gates)
    state = sm.create_initial_state("p1")
    state = sm.start_task(state, "run_t4")
    result = AgentResult(
        ok=True,
        message="gate1 ready",
        outputs_produced={},
        steps_used=1,
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
        duration_seconds=0.1,
        stop_reason=AgentResult.STOP_FINISHED,
        metadata={"completion_mode": "t4_gate1_ready"},
    )

    state = sm.advance(state, result, workspace_dir=tmp_workspace)

    assert state.current_task == "T4-GATE1"
    assert state.status == "RUNNING"
    assert sm.should_pause_for_immediate_gate(state) is True

    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(
        state,
        {"option_id": "merge", "captured": {"merge_plan": "D1+D3"}},
        workspace_dir=tmp_workspace,
    )

    assert state.current_task == "T4"
    decision = json.loads((tmp_workspace / "ideation" / "_gate1_user_selection.json").read_text(encoding="utf-8"))
    assert decision["semantics"] == "t4_gate1_user_selection_for_candidate_pool"
    assert decision["selected_option"] == "merge"
    assert decision["captured"]["merge_plan"] == "D1+D3"


def test_t4_gate1_resolve_reprompts_when_candidate_pool_changes(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4-GATE1
        states:
          T4-GATE1:
            agent: ideation
            extra:
              immediate_gate: true
            gate: t4_gate1_selection_gate
            outputs:
              gate1_user_selection: ideation/_gate1_user_selection.json
            next_on_success: T4
          T4:
            agent: ideation
          done:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t4_gate1_selection_gate:
            presentation:
              brief:
                path: ideation/_gate1_selection_brief.md
            options:
              - id: merge
                label: Merge
                next: T4
        """,
    )
    ideation = tmp_workspace / "ideation"
    ideation.mkdir()
    (ideation / "_gate1_selection_brief.md").write_text("Candidate A\n", encoding="utf-8")
    (ideation / "_candidate_directions.json").write_text('{"directions":[{"id":"D1"}]}\n', encoding="utf-8")

    sm = StateMachine(config, gates)
    state = sm.create_initial_state("p1")
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)
    (ideation / "_gate1_selection_brief.md").write_text("Candidate B changed while waiting\n", encoding="utf-8")

    state = sm.resolve_pending_gate(
        state,
        {"option_id": "merge", "captured": {"merge_plan": "D1+D3"}},
        workspace_dir=tmp_workspace,
    )

    assert state.status == "WAITING_HUMAN"
    assert state.current_task == "T4-GATE1"
    assert "candidate pool changed" in (state.last_error or "")
    assert not (ideation / "_gate1_user_selection.json").exists()


def test_t2_literature_param_gate_persists_clear_coverage_parameters(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T2-PARAM-GATE
        states:
          T2-PARAM-GATE:
            agent: scout
            extra:
              immediate_gate: true
            gate: t2_literature_param_gate
            inputs:
              project: project.yaml
              seed_outline_profile: user_seeds/seed_outline_profile.json
              bridge_domain_plan: literature/bridge_domain_plan.json
            outputs:
              literature_params: literature/literature_params.json
            next_on_success: T2
          T2:
            agent: scout
            outputs:
              papers_raw: literature/papers_raw.jsonl
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t2_literature_param_gate:
            presentation:
              meaning:
                literal: choose coverage
            options:
              - id: survey_balanced
                label: Survey balanced
                next: T2
        """,
    )
    (tmp_workspace / "project.yaml").write_text("project_id: p\n", encoding="utf-8")
    (tmp_workspace / "user_seeds").mkdir(parents=True)
    (tmp_workspace / "user_seeds" / "seed_outline_profile.json").write_text("{}", encoding="utf-8")
    (tmp_workspace / "literature").mkdir(parents=True)
    (tmp_workspace / "literature" / "bridge_domain_plan.json").write_text("{}", encoding="utf-8")
    sm = StateMachine(config, gates)
    state = sm.create_initial_state("p1")

    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(state, {"option_id": "survey_balanced"}, workspace_dir=tmp_workspace)

    assert state.current_task == "T2"
    payload = json.loads((tmp_workspace / "literature" / "literature_params.json").read_text(encoding="utf-8"))
    assert payload["semantics"] == "workspace_literature_coverage_parameters_for_t2_t3"
    assert payload["selected_option"] == "survey_balanced"
    assert payload["t2_finalize"]["active_pool_max"] == 180
    assert payload["reader"]["deep_read_target"] == 60
    assert payload["reader"]["require_deep_read_target"] is True
    assert "保留候选数" in payload["parameter_meanings"]["active_pool_max"]


def test_t5_executor_gate_persists_selection_and_patches_executor_files(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T5-EXECUTOR-GATE
        states:
          T5-EXECUTOR-GATE:
            agent: experimenter
            mode: executor_gate
            extra:
              immediate_gate: true
            outputs:
              executor_selection: external_executor/executor_selection.json
            gate: t5_executor_gate
          T5-DRY-RUN:
            agent: experimenter
          T5-EXTERNAL-WAIT:
            agent: experimenter
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t5_executor_gate:
            options:
              - id: mock_dry_run
                label: Mock
                next: T5-DRY-RUN
              - id: claude_code_window
                label: Claude
                next: T5-EXTERNAL-WAIT
        """,
    )
    ext = tmp_workspace / "external_executor"
    ext.mkdir()
    for name in ["AGENTS.md", "CLAUDE.md", "executor_prompt.md", "codex_prompt.md", "claude_code_prompt.md", "manual_instructions.md"]:
        (ext / name).write_text("dry_run: UNSET\nmock_only: UNSET\nreal_experiment_allowed: UNSET\n", encoding="utf-8")

    sm = StateMachine(config, gates)
    state = sm.create_initial_state("p1")
    state.pending_gate = None
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(
        state,
        {"option_id": "mock_dry_run", "captured": {}},
        workspace_dir=tmp_workspace,
    )

    assert state.current_task == "T5-DRY-RUN"
    selection = json.loads((ext / "executor_selection.json").read_text(encoding="utf-8"))
    assert selection["selected_executor"] == "mock_dry_run"
    assert selection["next_state"] == "T5-DRY-RUN"
    assert "UNSET" not in (ext / "AGENTS.md").read_text(encoding="utf-8")


def test_t5_external_wait_ready_option_requires_result_pack(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T5-EXTERNAL-WAIT
        states:
          T5-EXTERNAL-WAIT:
            agent: experimenter
            extra:
              immediate_gate: true
            outputs:
              wait_acceptance_report: external_executor/wait_acceptance_report.json
            gate: external_wait_gate
          T7-INGEST:
            agent: experimenter
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          external_wait_gate:
            options:
              - id: results_ready
                label: Ready
                next: T7-INGEST
        """,
    )

    sm = StateMachine(config, gates)
    state = sm.create_initial_state("p1")
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(
        state,
        {"option_id": "results_ready", "captured": {}},
        workspace_dir=tmp_workspace,
    )

    assert state.status == "WAITING_HUMAN"
    assert state.current_task == "T5-EXTERNAL-WAIT"
    assert "WAITING_EXTERNAL" in (state.last_error or "")


def test_t75_gate_can_follow_recommended_next_task_from_output(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T7.5
        states:
          T7.5:
            agent: pi
            mode: evaluate
            inputs:
              results_summary: experiments/results_summary.json
              iteration_log: experiments/iteration_log.md
              exp_plan: ideation/exp_plan.yaml
            outputs:
              evaluation_decision: evaluation/evaluation_decision.md
            gate: t75_gate
            next_on_success: __parse_from_output__
          T7:
            agent: experimenter
          T8-RESOURCE:
            agent: writer
          T8-WRITE:
            agent: writer
          done:
            terminal: true
          failed:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t75_gate:
            options:
              - id: follow_recommendation
                label: Follow
                next: __parse_from_output__
        """,
    )
    (tmp_workspace / "evaluation").mkdir()
    (tmp_workspace / "evaluation" / "evaluation_decision.md").write_text(
        "# T7.5\n\n## Option 1\nnext_task: T8-WRITE\n",
        encoding="utf-8",
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
    assert state.status == "WAITING_HUMAN"

    state = sm.resolve_pending_gate(
        state,
        {"option_id": "follow_recommendation", "captured": {}},
        workspace_dir=tmp_workspace,
    )
    assert state.current_task == "T8-RESOURCE"
    assert state.status == "RUNNING"


def test_t75_gate_direct_write_enters_resource_stage(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T7.5
        states:
          T7.5:
            agent: pi
            mode: evaluate
            outputs:
              evaluation_decision: evaluation/evaluation_decision.md
            gate: t75_gate
            next_on_success: __parse_from_output__
          T8-RESOURCE:
            agent: writer
          T8-WRITE:
            agent: writer
          done:
            terminal: true
          failed:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t75_gate:
            options:
              - id: go_write
                label: Write
                next: T8-RESOURCE
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
    state = sm.resolve_pending_gate(
        state,
        {"option_id": "go_write", "captured": {}},
        workspace_dir=tmp_workspace,
    )

    assert state.current_task == "T8-RESOURCE"


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


def test_advance_pauses_on_recoverable_runtime_limits(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: T3
        states:
          T3:
            agent: reader
            outputs:
              notes: literature/paper_notes
            next_on_success: T4
            next_on_failure: failed
          T4:
            agent: ideation
          failed:
            terminal: true
        """,
    )
    sm = StateMachine(config)
    state = sm.create_initial_state("p1")
    state = sm.start_task(state, "run_1")
    result = AgentResult(
        ok=False,
        message="max steps",
        outputs_produced={},
        steps_used=100,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        duration_seconds=1.0,
        stop_reason=AgentResult.STOP_MAX_STEPS,
        error="Reached maximum allowed steps; paused so you can resume.",
    )

    state = sm.advance(state, result, workspace_dir=tmp_workspace)

    assert state.status == "PAUSED"
    assert state.current_task == "T3"
    assert state.history[-1].status == "INTERRUPTED"
    assert state.history[-1].stop_reason == AgentResult.STOP_MAX_STEPS

    ctx = sm.build_execution_context(tmp_workspace, state)

    assert ctx.extra["resume_mode"] is True
    assert ctx.extra["resume_reason"] == "interrupted"
    assert ctx.extra["resumed_from_run_id"] == "run_1"


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
