from pathlib import Path
import json
import textwrap

from researchos.orchestration.state_machine import StateMachine, build_literature_param_payload
from researchos.tools.human_gate import CLIHumanInterface
from researchos.runtime.agent import AgentResult
from researchos.schemas.state import StateYaml, TaskHistoryEntry
from tests.unit.test_runner_basic import _write_t4_stage_visibility_artifacts


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
    assert sm.should_pause_for_immediate_gate(state, workspace_dir=tmp_workspace) is True

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


def test_t4_ready_artifacts_route_paused_state_to_gate1_before_deadlock(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4
        states:
          T4:
            agent: ideation
          T4-GATE1:
            agent: ideation
            extra:
              immediate_gate: true
            gate: t4_gate1_selection_gate
            outputs:
              gate1_user_selection: ideation/_gate1_user_selection.json
            next_on_success: T4
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t4_gate1_selection_gate:
            presentation:
              brief:
                from_file: ideation/_gate1_selection_brief.md
            options:
              - id: merge
                label: Merge
                next: T4
        """,
    )
    (tmp_workspace / "ideation").mkdir(parents=True, exist_ok=True)
    _write_t4_stage_visibility_artifacts(tmp_workspace / "ideation")
    sm = StateMachine(config, gates)
    state = StateYaml(
        project_id="p1",
        current_task="T4",
        status="PAUSED",
        iteration_history={
            "T4": [
                {"param_hash": "same"},
                {"param_hash": "same"},
                {"param_hash": "same"},
            ]
        },
    )

    assert sm.should_pause_for_immediate_gate(state, workspace_dir=tmp_workspace) is True
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)

    assert state.current_task == "T4-GATE1"
    assert state.status == "WAITING_HUMAN"
    assert state.pending_gate is not None


def test_t4_gate1_presentation_prioritizes_readable_candidate_cards(tmp_workspace):
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
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t4_gate1_selection_gate:
            presentation:
              gate1_candidate_cards:
                from_file: ideation/_gate1_candidate_cards.md
                mode: path_summary
                summary_chars: 600
              gate1_selection_brief:
                from_file: ideation/_gate1_selection_brief.md
                mode: path_summary
                summary_chars: 300
              machine_readable_artifacts:
                literal: "机器可读附录路径：ideation/_candidate_directions.json"
            options:
              - id: select_or_reframe
                label: Select
                next: T4
        """,
    )
    (tmp_workspace / "ideation").mkdir(parents=True, exist_ok=True)
    _write_t4_stage_visibility_artifacts(tmp_workspace / "ideation")
    sm = StateMachine(config, gates)

    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)

    presentation = state.pending_gate.presentation
    assert "gate1_candidate_cards" in presentation
    assert presentation["gate1_candidate_cards"]["path"] == "ideation/_gate1_candidate_cards.md"
    assert "Technical mechanism" in presentation["gate1_candidate_cards"]["summary"]
    assert "Practical / managerial / business implication" in presentation["gate1_candidate_cards"]["summary"]
    assert presentation["gate1_selection_brief"]["path"] == "ideation/_gate1_selection_brief.md"
    assert "candidate_directions" not in presentation
    assert "ideation/_candidate_directions.json" in presentation["machine_readable_artifacts"]


def test_t4_gate1_reanalyze_does_not_write_user_selection(tmp_workspace):
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
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t4_gate1_selection_gate:
            presentation:
              brief:
                from_file: ideation/_gate1_selection_brief.md
            options:
              - id: reanalyze
                label: Reanalyze
                next: T4
        """,
    )
    (tmp_workspace / "ideation").mkdir(parents=True, exist_ok=True)
    _write_t4_stage_visibility_artifacts(tmp_workspace / "ideation")
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)

    state = sm.resolve_pending_gate(
        state,
        {"option_id": "reanalyze", "captured": {"feedback": "make more cross-domain"}},
        workspace_dir=tmp_workspace,
    )

    assert state.current_task == "T4"
    assert not (tmp_workspace / "ideation" / "_gate1_user_selection.json").exists()
    assert (tmp_workspace / "ideation" / "_gate1_reanalysis_request.json").exists()
    assert (tmp_workspace / "ideation" / "_gate1_reanalysis_archive").is_dir()


def test_t4_gate1_resolve_fast_forwards_when_valid_selection_already_exists(tmp_workspace):
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
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t4_gate1_selection_gate:
            presentation:
              brief:
                from_file: ideation/_gate1_selection_brief.md
            options:
              - id: select_or_reframe
                label: Select
                next: T4
        """,
    )
    (tmp_workspace / "ideation").mkdir(parents=True, exist_ok=True)
    _write_t4_stage_visibility_artifacts(tmp_workspace / "ideation")
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)
    stale_pending_gate = state.pending_gate
    state_after_selection = sm.resolve_pending_gate(
        state,
        {"option_id": "select_or_reframe", "captured": {"selection": "D1"}},
        workspace_dir=tmp_workspace,
    )

    state.current_task = "T4-GATE1"
    state.status = "WAITING_HUMAN"
    state.pending_gate = stale_pending_gate
    state = sm.resolve_pending_gate(
        state,
        {"option_id": "select_or_reframe", "captured": {"selection": "stale input should be ignored"}},
        workspace_dir=tmp_workspace,
    )

    assert state_after_selection.current_task == "T4"
    assert state.current_task == "T4"
    assert state.status == "RUNNING"
    decision = json.loads((tmp_workspace / "ideation" / "_gate1_user_selection.json").read_text(encoding="utf-8"))
    assert decision["captured"]["selection"] == "D1"


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
    (ideation / "_gate1_candidate_cards.md").write_text("Candidate card A\n", encoding="utf-8")
    (ideation / "_gate1_selection_brief.md").write_text("Candidate A\n", encoding="utf-8")
    (ideation / "_candidate_directions.json").write_text('{"directions":[{"id":"D1"}]}\n', encoding="utf-8")

    sm = StateMachine(config, gates)
    state = sm.create_initial_state("p1")
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)
    (ideation / "_gate1_candidate_cards.md").write_text("Candidate card B changed while waiting\n", encoding="utf-8")

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
            next_on_success: T2-PARAM-CONFIRM-GATE
          T2-PARAM-CONFIRM-GATE:
            agent: scout
            extra:
              immediate_gate: true
            gate: t2_literature_param_confirm_gate
            inputs:
              literature_params: literature/literature_params.json
            outputs:
              literature_params_confirmation: literature/literature_params_confirmation.json
            next_on_success: __parse_from_output__
          T2:
            agent: scout
            outputs:
              papers_raw: literature/papers_raw.jsonl
          done:
            terminal: true
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
                next: T2-PARAM-CONFIRM-GATE
          t2_literature_param_confirm_gate:
            presentation:
              selected_parameters:
                from_file: literature/literature_params.json
                mode: path_summary
            options:
              - id: confirm_start_t2
                label: Confirm
                next: T2
              - id: revise_params
                label: Revise
                next: T2-PARAM-GATE
              - id: stop_project
                label: Stop
                next: done
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

    assert state.current_task == "T2-PARAM-CONFIRM-GATE"
    payload = json.loads((tmp_workspace / "literature" / "literature_params.json").read_text(encoding="utf-8"))
    assert payload["semantics"] == "workspace_literature_coverage_parameters_for_t2_t3"
    assert payload["selected_option"] == "survey_balanced"
    assert payload["t2_finalize"]["active_pool_max"] == 180
    assert payload["reader"]["deep_read_target"] == 60
    assert payload["reader"]["require_deep_read_target"] is True
    assert "保留候选数" in payload["parameter_meanings"]["active_pool_max"]
    assert payload["selected_summary"]["active_pool_max"] == 180

    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_workspace)
    assert state.pending_gate.gate_id == "t2_literature_param_confirm_gate"
    state = sm.resolve_pending_gate(state, {"option_id": "confirm_start_t2"}, workspace_dir=tmp_workspace)

    assert state.current_task == "T2"
    confirmation = json.loads(
        (tmp_workspace / "literature" / "literature_params_confirmation.json").read_text(encoding="utf-8")
    )
    assert confirmation["semantics"] == "human_final_confirmed_t2_literature_parameters_before_scout"
    assert confirmation["confirmed_to_start_t2"] is True
    assert confirmation["next_task"] == "T2"
    assert confirmation["human_interaction_id"]


def test_t2_literature_param_confirm_gate_can_return_to_parameter_selection(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T2-PARAM-CONFIRM-GATE
        states:
          T2-PARAM-GATE:
            agent: scout
            extra:
              immediate_gate: true
            gate: t2_literature_param_gate
            outputs:
              literature_params: literature/literature_params.json
            next_on_success: T2-PARAM-CONFIRM-GATE
          T2-PARAM-CONFIRM-GATE:
            agent: scout
            extra:
              immediate_gate: true
            gate: t2_literature_param_confirm_gate
            inputs:
              literature_params: literature/literature_params.json
            outputs:
              literature_params_confirmation: literature/literature_params_confirmation.json
            next_on_success: __parse_from_output__
          T2:
            agent: scout
          done:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t2_literature_param_gate:
            presentation: {}
            options:
              - id: survey_balanced
                label: Survey
                next: T2-PARAM-CONFIRM-GATE
          t2_literature_param_confirm_gate:
            presentation:
              selected_parameters:
                from_file: literature/literature_params.json
                mode: path_summary
            options:
              - id: confirm_start_t2
                label: Confirm
                next: T2
              - id: revise_params
                label: Revise
                next: T2-PARAM-GATE
              - id: stop_project
                label: Stop
                next: done
        """,
    )
    literature = tmp_workspace / "literature"
    literature.mkdir(parents=True)
    (tmp_workspace / "project.yaml").write_text("project_id: p\n", encoding="utf-8")
    params = build_literature_param_payload(selected_option="survey_balanced", workspace_dir=tmp_workspace)
    (literature / "literature_params.json").write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")

    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(state, {"option_id": "revise_params"}, workspace_dir=tmp_workspace)

    assert state.current_task == "T2-PARAM-GATE"
    assert state.status == "RUNNING"
    confirmation = json.loads((literature / "literature_params_confirmation.json").read_text(encoding="utf-8"))
    assert confirmation["confirmed_to_start_t2"] is False
    assert confirmation["selected_option"] == "revise_params"
    assert confirmation["next_task"] == "T2-PARAM-GATE"


def test_t2_literature_param_gate_displays_actual_values_and_profile_default(tmp_workspace):
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
            title: T2 文献覆盖参数确认
            description: confirm coverage
            presentation:
              meaning:
                literal: choose coverage
            options:
              - id: standard_research
                label: 标准研究论文覆盖
                next: T2
              - id: survey_balanced
                label: 综述均衡覆盖
                next: T2
              - id: survey_exhaustive
                label: 综述强覆盖
                next: T2
              - id: custom
                label: 自定义关键数字
                next: T2
        """,
    )
    (tmp_workspace / "project.yaml").write_text(
        "metadata:\n  manuscript_type: survey\n",
        encoding="utf-8",
    )
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)

    preview = state.pending_gate.presentation["current_parameter_preview"]
    assert preview["detected_profile"] == "survey"
    assert preview["recommended_option"] == "survey_balanced"
    balanced = next(option for option in state.pending_gate.options if option["id"] == "survey_balanced")
    standard = next(option for option in state.pending_gate.options if option["id"] == "standard_research")
    custom = next(option for option in state.pending_gate.options if option["id"] == "custom")
    assert balanced["is_default"] is True
    assert "保留候选：180 篇（active_pool_max=180；可选：120/180/240 或自定义）" in balanced["parameter_preview"]
    assert "深入阅读：目标 60 篇（deep_read=50/60/70；格式：min/target/max）" in balanced["parameter_preview"]
    assert "保留候选：120 篇（active_pool_max=120；可选：120/180/240 或自定义）" in standard["parameter_preview"]
    assert "深入阅读：目标 35 篇（deep_read=35/35/45；格式：min/target/max）" in standard["parameter_preview"]
    assert "input_prompts" in custom
    assert "manuscript_language" in custom["collect_input"]
    assert "include_chinese_literature" in custom["collect_input"]
    assert CLIHumanInterface._default_option_id("t2_literature_param_gate", state.pending_gate.options) == "survey_balanced"


def test_t2_literature_param_gate_defaults_to_standard_for_research_article(tmp_workspace):
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
            presentation: {}
            options:
              - id: standard_research
                label: 标准研究论文覆盖
                next: T2
              - id: survey_balanced
                label: 综述均衡覆盖
                next: T2
        """,
    )
    (tmp_workspace / "project.yaml").write_text("metadata:\n  manuscript_type: research_article\n", encoding="utf-8")
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)

    assert state.pending_gate.presentation["current_parameter_preview"]["recommended_option"] == "standard_research"
    standard = next(option for option in state.pending_gate.options if option["id"] == "standard_research")
    assert standard["is_default"] is True
    assert CLIHumanInterface._default_option_id("t2_literature_param_gate", state.pending_gate.options) == "standard_research"


def test_t2_coverage_gate_persists_human_confirmation(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T2-COVERAGE-GATE
        states:
          T2-COVERAGE-GATE:
            agent: scout
            extra:
              immediate_gate: true
            gate: t2_coverage_gate
            inputs:
              papers_verified: literature/papers_verified.jsonl
              deep_read_queue: literature/deep_read_queue.jsonl
              missing_areas: literature/missing_areas.md
            outputs:
              coverage_decision: literature/coverage_decision.json
            next_on_success: T3
          T3:
            agent: reader
            outputs:
              notes: literature/paper_notes
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t2_coverage_gate:
            presentation:
              summary:
                literal: confirm coverage
            options:
              - id: continue_to_t3
                label: Continue
                next: T3
              - id: rerun_t2_expand
                label: Expand
                next: T2
        """,
    )
    literature = tmp_workspace / "literature"
    literature.mkdir(parents=True)
    (literature / "papers_verified.jsonl").write_text('{"paper_id":"p1"}\n{"paper_id":"p2"}\n', encoding="utf-8")
    (literature / "deep_read_queue.jsonl").write_text('{"paper_id":"p1"}\n', encoding="utf-8")
    (literature / "missing_areas.md").write_text("# Coverage\n需要补充相邻领域。\n", encoding="utf-8")
    (literature / "literature_params.json").write_text(
        json.dumps({"confirmation_summary": "保留候选 80 篇；精读 35/35/45。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)

    state = sm.resolve_pending_gate(
        state,
        {"option_id": "continue_to_t3", "captured": {"note": "confirmed"}},
        workspace_dir=tmp_workspace,
    )

    assert state.current_task == "T3"
    payload = json.loads((literature / "coverage_decision.json").read_text(encoding="utf-8"))
    assert payload["semantics"] == "human_confirmed_t2_retrieval_coverage_before_t3"
    assert payload["selected_option"] == "continue_to_t3"
    assert payload["next_task"] == "T3"
    assert payload["captured"]["note"] == "confirmed"
    assert payload["coverage_summary"]["papers_verified_count"] == 2
    assert payload["coverage_summary"]["deep_read_queue_count"] == 1
    assert payload["coverage_summary"]["missing_area_signal_present"] is True
    assert payload["coverage_summary"]["literature_params_summary"] == "保留候选 80 篇；精读 35/35/45。"
    assert payload["decision_summary"]
    assert payload["input_fingerprints"]["papers_verified"]["exists"] is True
    assert payload["input_fingerprints"]["deep_read_queue"]["exists"] is True


def test_custom_t2_literature_params_inherit_detected_recommended_profile(tmp_workspace):
    (tmp_workspace / "project.yaml").write_text("metadata:\n  manuscript_type: research_article\n", encoding="utf-8")

    payload = build_literature_param_payload(
        selected_option="custom",
        captured={"active_pool_max": "300", "base_option": "standard_research"},
        workspace_dir=tmp_workspace,
    )

    assert payload["t2_finalize"]["active_pool_max"] == 300
    assert payload["reader"]["deep_read_target"] == 35
    assert payload["reader"]["abstract_sweep"]["lite_paper_num"] == 120
    assert payload["selected_summary"]["active_pool_max"] == 300


def test_custom_t2_literature_params_can_override_multiple_numbers(tmp_workspace):
    (tmp_workspace / "project.yaml").write_text("metadata:\n  manuscript_type: survey\n", encoding="utf-8")

    payload = build_literature_param_payload(
        selected_option="custom",
        captured={
            "active_pool_max": "300",
            "deep_read_target": "80",
            "require_deep_read_target": "false",
            "base_option": "survey_balanced",
        },
        workspace_dir=tmp_workspace,
    )

    assert payload["t2_finalize"]["active_pool_max"] == 300
    assert payload["reader"]["deep_read_min"] == 50
    assert payload["reader"]["deep_read_target"] == 80
    assert payload["reader"]["deep_read_max"] >= 80
    assert payload["reader"]["require_deep_read_target"] is False
    assert payload["reader"]["abstract_sweep"]["lite_paper_num"] == 120


def test_custom_t2_literature_params_can_disable_chinese_for_english_manuscript(tmp_workspace):
    (tmp_workspace / "project.yaml").write_text("language: en\n", encoding="utf-8")

    payload = build_literature_param_payload(
        selected_option="custom",
        captured={
            "active_pool_max": "300",
            "manuscript_language": "英文",
            "include_chinese_literature": "false",
            "base_option": "standard_research",
        },
        workspace_dir=tmp_workspace,
    )

    assert payload["t2_finalize"]["active_pool_max"] == 300
    assert payload["literature_quality"]["manuscript_language"] == "en"
    assert payload["literature_quality"]["include_chinese_literature"] == "false"
    assert payload["literature_quality"]["chinese_literature_policy"] == "review_flag_only"
    assert "literature_quality.include_chinese_literature" in payload["parameter_meanings"]


def test_t36_template_gate_persists_runtime_confirmed_template(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T3.6-TEMPLATE-GATE
        states:
          T3.6-TEMPLATE-GATE:
            agent: survey_writer
            mode: template_gate
            extra:
              immediate_gate: true
            gate: t36_template_gate
            outputs:
              writing_template: drafts/survey/writing_template.json
            next_on_success: T3.6-PLAN
          T3.6-PLAN:
            agent: survey_writer
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t36_template_gate:
            options:
              - id: utd_informs
                label: UTD
                next: T3.6-PLAN
                captured_defaults:
                  template_family: utd
                  template_id: informs
                  writing_language: en
        """,
    )
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(state, {"option_id": "utd_informs", "captured": {}}, workspace_dir=tmp_workspace)

    assert state.current_task == "T3.6-PLAN"
    payload = json.loads((tmp_workspace / "drafts" / "survey" / "writing_template.json").read_text(encoding="utf-8"))
    assert payload["template_family"] == "utd"
    assert payload["template_id"] == "informs"
    assert payload["writing_language"] == "en"
    assert payload["human_interaction_id"]
    interactions = (tmp_workspace / "_runtime" / "human_interactions.jsonl").read_text(encoding="utf-8")
    assert payload["human_interaction_id"] in interactions


def test_t36_post_survey_gate_can_finish_or_continue(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T3.6-POST-SURVEY-GATE
        states:
          T3.6-POST-SURVEY-GATE:
            agent: survey_writer
            mode: post_survey_gate
            extra:
              immediate_gate: true
            gate: t36_post_survey_gate
            outputs:
              post_survey_decision: drafts/survey/post_survey_decision.json
            next_on_success: __parse_from_output__
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
          t36_post_survey_gate:
            options:
              - id: continue_to_t4
                label: 继续进入 T4
                next: T4
              - id: finish_after_survey
                label: 结束项目
                next: done
        """,
    )
    (tmp_workspace / "drafts" / "survey").mkdir(parents=True)
    (tmp_workspace / "ideation").mkdir()
    (tmp_workspace / "project.yaml").write_text("project_id: p\n", encoding="utf-8")
    (tmp_workspace / "drafts" / "survey" / "survey_summary.md").write_text("# Summary\n", encoding="utf-8")
    (tmp_workspace / "drafts" / "survey" / "survey_compile_report.json").write_text("{}\n", encoding="utf-8")
    (tmp_workspace / "ideation" / "survey_insights.json").write_text("{}\n", encoding="utf-8")

    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(state, {"option_id": "finish_after_survey"}, workspace_dir=tmp_workspace)

    assert state.current_task == "done"
    assert state.status == "COMPLETED"
    payload = json.loads((tmp_workspace / "drafts" / "survey" / "post_survey_decision.json").read_text(encoding="utf-8"))
    assert payload["semantics"] == "human_confirmed_post_survey_next_step"
    assert payload["continue_to_t4"] is False

    (tmp_workspace / "drafts" / "survey" / "post_survey_decision.json").unlink()
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p2"), workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(state, {"option_id": "continue_to_t4"}, workspace_dir=tmp_workspace)

    assert state.current_task == "T4"
    assert state.status == "RUNNING"


def test_t8_style_template_gate_persists_runtime_confirmed_style(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T8-STYLE-GATE
        states:
          T8-STYLE-GATE:
            agent: writer
            mode: style_gate
            extra:
              immediate_gate: true
            gate: t8_style_template_gate
            outputs:
              writing_style: drafts/writing_style.json
            next_on_success: T8-RESOURCE
          T8-RESOURCE:
            agent: writer
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t8_style_template_gate:
            options:
              - id: ccf_neurips
                label: CCF
                next: T8-RESOURCE
                captured_defaults:
                  venue_style: ccf_a
                  template_family: ccf
                  template_id: neurips
                  writing_language: en
        """,
    )
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(state, {"option_id": "ccf_neurips", "captured": {}}, workspace_dir=tmp_workspace)

    assert state.current_task == "T8-RESOURCE"
    payload = json.loads((tmp_workspace / "drafts" / "writing_style.json").read_text(encoding="utf-8"))
    assert payload["venue_style"] == "ccf_a"
    assert payload["template_family"] == "ccf"
    assert payload["template_id"] == "neurips"
    assert payload["human_interaction_id"]


def test_template_gate_custom_cds_normalizes_to_informs(tmp_workspace):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T8-STYLE-GATE
        states:
          T8-STYLE-GATE:
            agent: writer
            mode: style_gate
            extra:
              immediate_gate: true
            gate: t8_style_template_gate
            outputs:
              writing_style: drafts/writing_style.json
            next_on_success: T8-RESOURCE
          T8-RESOURCE:
            agent: writer
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t8_style_template_gate:
            options:
              - id: custom
                label: Custom
                next: T8-RESOURCE
        """,
    )
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("p1"), workspace_dir=tmp_workspace)
    state = sm.resolve_pending_gate(
        state,
        {
            "option_id": "custom",
            "captured": {
                "venue_style": "CDS",
                "template_family": "CDS",
                "template_id": "CDS",
                "writing_language": "en",
            },
        },
        workspace_dir=tmp_workspace,
    )

    assert state.current_task == "T8-RESOURCE"
    payload = json.loads((tmp_workspace / "drafts" / "writing_style.json").read_text(encoding="utf-8"))
    assert payload["venue_style"] == "is"
    assert payload["template_family"] == "utd"
    assert payload["template_id"] == "informs"
    assert payload["writing_language"] == "en"


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
