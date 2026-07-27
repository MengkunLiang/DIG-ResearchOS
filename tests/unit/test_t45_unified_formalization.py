from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml

from researchos.ideation.formalization import (
    legacy_t45_upgrade_reason,
    validate_blueprint_and_claim_registry,
    validate_t45_formalization_core,
)
from researchos.ideation.proposal import validate_t45_research_proposal
from researchos.orchestration.state_machine import StateMachine
from researchos.orchestration.task_io_contract import task_import_paths
from researchos.runtime.agent import Agent, AgentSpec, ExecutionContext
from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.config import RuntimeSettings
from researchos.schemas.state import GateState, StateYaml
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.external_experiment import _build_reboost_pack
from researchos.tools.registry import ToolRegistry
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace, write, write_yaml


def _proposal_path(workspace: Path) -> Path:
    return workspace / "ideation" / "proposal" / "research_proposal.md"


class _RepeatedT45RepairAgent(Agent):
    """A scripted Formalizer used to exercise the runtime recovery loop."""

    def __init__(self) -> None:
        super().__init__(
            AgentSpec(
                name="research_formalizer",
                model_tier="heavy",
                tool_names=["write_file", "finish_task"],
                allowed_read_prefixes=[""],
                allowed_write_prefixes=["ideation/"],
                # Deliberately low: the T4.5-specific loop must not use it.
                max_validation_retries=1,
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return "Test only: repair the declared T4.5 source artifact."

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "Run the scripted T4.5 quality-gate repair test."

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        proposal = ctx.workspace_dir / "ideation" / "proposal" / "research_proposal.md"
        if "repair iteration 6" in proposal.read_text(encoding="utf-8"):
            return True, None
        return False, "research_proposal.md is too short for hybrid proposal (120/1900 words)"


class _EditCompatibilityAgent(Agent):
    """Agent fixture that deliberately uses the common legacy edit name."""

    def __init__(self) -> None:
        super().__init__(
            AgentSpec(
                name="edit-compatibility-test",
                model_tier="light",
                tool_names=["write_file", "finish_task"],
                allowed_read_prefixes=["ideation/"],
                allowed_write_prefixes=["ideation/"],
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return "Test the policy-bound edit_file compatibility tool."

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "Replace exactly one known text fragment, then finish."

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        path = ctx.workspace_dir / "ideation" / "compatibility.md"
        if path.read_text(encoding="utf-8") == "after\n":
            return True, None
        return False, "edit_file compatibility replacement was not applied"


def _finish_response(summary: str) -> FakeRawCompletion:
    return FakeRawCompletion(
        message=FakeLLMMessage(
            tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": summary})]
        )
    )


def _proposal_write_response(iteration: int) -> FakeRawCompletion:
    return FakeRawCompletion(
        message=FakeLLMMessage(
            tool_calls=[
                FakeToolCall(
                    name="write_file",
                    arguments={
                        "path": "ideation/proposal/research_proposal.md",
                        "content": f"repair iteration {iteration}",
                    },
                )
            ]
        )
    )


def test_write_capable_agent_can_use_safe_edit_file_compatibility(tmp_path: Path) -> None:
    """A model's familiar edit call must not become an unknown-tool retry."""

    target = tmp_path / "ideation" / "compatibility.md"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    llm = MockLLMClient(
        [
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="edit_file",
                            arguments={
                                "path": "ideation/compatibility.md",
                                "old_string": "before",
                                "new_string": "after",
                            },
                        )
                    ]
                )
            ),
            _finish_response("exact replacement complete"),
        ]
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        _EditCompatibilityAgent(),
        registry,
        llm,
        MockHumanInterface(),
        RuntimeSettings(),
    )

    result = asyncio.run(
        runner.run(
            ExecutionContext(
                workspace_dir=tmp_path,
                project_id="edit-compatibility",
                task_id="T1",
                run_id="edit-compatibility-run",
            )
        )
    )

    assert result.ok is True, result.error or result.message
    assert target.read_text(encoding="utf-8") == "after\n"


def test_one_unified_template_accepts_all_three_orientations(tmp_path: Path) -> None:
    for orientation in ("ccf_a", "utd", "hybrid"):
        workspace = tmp_path / orientation
        populate_valid_t45_workspace(workspace, orientation=orientation)
        ok, error = validate_t45_research_proposal(workspace, workspace / "ideation" / "novelty_audit.md")
        assert ok is True, f"{orientation}: {error}"
        proposal = _proposal_path(workspace).read_text(encoding="utf-8")
        assert proposal.count("## ") == 7


def test_short_heading_complete_proposal_fails_with_substantive_reason(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    write(
        _proposal_path(tmp_path),
        "# Research Proposal\n\n"
        "## Research Motivation and Core Problem\nshort\n\n"
        "## Prior Research, Gap and Key Challenges\nshort\n\n"
        "## Proposed Approach and Design Rationale\nshort\n\n"
        "## Research Questions, Claims and Hypotheses\nshort\n\n"
        "## Research Design and Evaluation\nshort\n\n"
        "## Expected Contributions and Implications\nshort\n\n"
        "## Risks, Limitations and Execution Plan\nshort\n",
    )
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "too short" in (error or "") or "fragments" in (error or "")


def test_repetitive_or_audit_dominated_proposal_fails(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    path = _proposal_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\nT4.5 Level 0 true_collision. T4.5 Level 0 true_collision.\n", encoding="utf-8")
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "audit-dominated" in (error or "")


def test_removed_h1_cannot_satisfy_active_claim_contract(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    write(
        tmp_path / "ideation" / "hypotheses.md",
        "# Research Claims and Hypotheses\n\n### H1 [removed]\nThis claim was dropped after an internal audit and is not active.\n",
    )
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "TC1" in (error or "") or "too short" in (error or "")


def test_claim_without_experiment_mapping_and_component_test_fails(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    plan_path = tmp_path / "ideation" / "exp_plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["experiments"] = [plan["experiments"][0]]
    write_yaml(plan_path, plan)
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "MC1" in (error or "")

    populate_valid_t45_workspace(tmp_path)
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["evaluation"]["ablations"] = [{"component_id": "COMP1", "planned_test": "Remove COMP1 only."}]
    blueprint["evaluation"]["mechanism_tests"] = [{"component_id": "COMP1", "planned_test": "Test COMP1 only."}]
    write_yaml(blueprint_path, blueprint)
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "COMP2" in (error or "")


def test_orientation_specific_failure_modes_are_blocked(tmp_path: Path) -> None:
    ccf = tmp_path / "ccf"
    populate_valid_t45_workspace(ccf, orientation="ccf_a")
    proposal = _proposal_path(ccf)
    proposal.write_text(proposal.read_text(encoding="utf-8").replace("algorithmic model", "narrative description").replace("algorithm and diagnostic system artifact", "application paragraph"), encoding="utf-8")
    # The validator must still reject lack of explicit method rationale when
    # the component/alternative language is removed.
    proposal.write_text(proposal.read_text(encoding="utf-8").replace("A simpler alternative", "A conventional option").replace("cannot distinguish", "is not described"), encoding="utf-8")
    ok, error = validate_t45_research_proposal(ccf, ccf / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "alternative" in (error or "") or "computational" in (error or "")

    utd = tmp_path / "utd"
    populate_valid_t45_workspace(utd, orientation="utd")
    text = _proposal_path(utd).read_text(encoding="utf-8")
    before, tail = text.split("## Proposed Approach and Design Rationale\n", 1)
    _old_approach, after = tail.split("## Research Questions, Claims and Hypotheses\n", 1)
    text = (
        before
        + "## Proposed Approach and Design Rationale\n"
        + "Central Insight: call an existing LLM API for the task. COMP1 and COMP2 are labels for the same API call. "
        + "A simpler alternative is not discussed beyond reusing the API. "
        + " ".join(
            f"API-only approach statement {index} deliberately omits learned structure, a training objective, optimization, inference design, and a new technical artifact."
            for index in range(1, 9)
        )
        + "\n\n## Research Questions, Claims and Hypotheses\n"
        + after
    )
    _proposal_path(utd).write_text(text, encoding="utf-8")
    ok, error = validate_t45_research_proposal(utd, utd / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "LLM/API" in (error or "") or "technical artifact" in (error or "")

    hybrid = tmp_path / "hybrid"
    populate_valid_t45_workspace(hybrid, orientation="hybrid")
    blueprint_path = hybrid / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["research_claims"]["cross_level_links"] = []
    write_yaml(blueprint_path, blueprint)
    ok, error = validate_blueprint_and_claim_registry(hybrid)
    assert ok is False
    assert "cross-level" in (error or "")


def test_t45_quality_repair_feedback_targets_the_failing_source_artifact(tmp_path: Path) -> None:
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-feedback",
    )

    proposal_feedback = AgentRunner._validation_repair_feedback(
        ctx=ctx,
        error="research_proposal.md is too short for hybrid proposal (120/1900 words)",
    )
    plan_feedback = AgentRunner._validation_repair_feedback(
        ctx=ctx,
        error="Experiment plan has no experiment mapped to active claims: TC1",
    )
    review_feedback = AgentRunner._validation_repair_feedback(
        ctx=ctx,
        error="Orientation-aware review scores remain below threshold: evaluation_rigor",
    )

    assert "research_proposal.md" in proposal_feedback
    assert "不要直接写 `proposal_manifest.json`" in proposal_feedback
    assert "exp_plan.yaml" in plan_feedback
    assert "orientation_review.json" in review_feedback
    assert "不得只把 status 改为 accepted" in review_feedback


def test_t45_quality_repair_has_no_fixed_retry_limit_but_stops_without_source_progress(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-fingerprint",
    )
    error = "research_proposal.md is too short for hybrid proposal (120/1900 words)"

    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is False
    # A repeated finish_task with neither a source write nor a new diagnosis
    # cannot make progress, irrespective of any numeric retry setting.
    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is True

    proposal = _proposal_path(tmp_path)
    proposal.write_text(proposal.read_text(encoding="utf-8") + "\nA source repair.\n", encoding="utf-8")
    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is False


def test_t45_quality_gate_repairs_past_legacy_limit_without_human_extension(tmp_path: Path) -> None:
    """Exercise finish -> validation failure -> repair -> revalidate with mock LLM calls."""

    populate_valid_t45_workspace(tmp_path)
    responses = [_finish_response("initial validation")]
    for iteration in range(1, 7):
        responses.append(_proposal_write_response(iteration))
        responses.append(_finish_response(f"repair {iteration}"))
    llm = MockLLMClient(responses)
    human = MockHumanInterface()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        _RepeatedT45RepairAgent(),
        registry,
        llm,
        human,
        RuntimeSettings(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-run",
        mode="review",
    )

    result = asyncio.run(runner.run(ctx))

    assert result.ok is True, result.error or result.message
    assert llm.call_count == len(responses)
    assert not any(
        call[0] == "gate" and call[1]["gate_id"] == "runtime_validation_retry_extension"
        for call in human.calls
    )
    user_messages = [
        str(message.get("content") or "")
        for call_messages in llm.last_messages
        for message in call_messages
        if message.get("role") == "user"
    ]
    assert any("research_proposal.md is too short" in message for message in user_messages)


def test_t45_quality_gate_pauses_if_the_model_retries_without_changing_a_source(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    llm = MockLLMClient([_finish_response("initial validation"), _finish_response("ignored repair")])
    human = MockHumanInterface()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        _RepeatedT45RepairAgent(),
        registry,
        llm,
        human,
        RuntimeSettings(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-no-progress",
        mode="review",
    )

    result = asyncio.run(runner.run(ctx))

    assert result.ok is False
    assert result.stop_reason == "interrupted"
    assert "源产物没有变化" in (result.error or "")
    assert llm.call_count == 2
    assert not any(call[0] == "gate" for call in human.calls)


def test_t5_is_blocked_when_final_quality_gate_is_not_passed(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    review_path = tmp_path / "ideation" / "orientation_review.json"
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    review["status"] = "requires_repair"
    write(review_path, __import__("json").dumps(review, ensure_ascii=False, indent=2))
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "requires targeted repair" in (error or "")
    pack, _report = _build_reboost_pack(tmp_path)
    assert pack["generation_status"] == "blocked"
    assert any("T4.5 formalization" in item["question"] for item in pack["unresolved_items"])


def test_legacy_passed_audit_requires_formalization_upgrade_not_silent_t5_use(tmp_path: Path) -> None:
    write(
        tmp_path / "ideation" / "novelty_audit.md",
        "# Old attempt\nFinal Gate Verdict: drop_due_to_collision\n\n# Final attempt\nFinal Gate Verdict: pass_to_experiment\n",
    )
    write_yaml(tmp_path / "ideation" / "hypothesis_brief.yaml", {"draft_hypotheses": [{"id": "H1", "statement": "draft"}]})
    write(tmp_path / "ideation" / "selected" / "selected_candidate.json", "{}\n")
    write(tmp_path / "literature" / "synthesis.md", "# Synthesis\n")
    reason = legacy_t45_upgrade_reason(tmp_path)
    assert reason is not None
    assert "统一研究正式化质量 gate" in reason


def test_t4_gate1_workspace_import_includes_the_complete_reselection_closure() -> None:
    """A Gate1 import must not leave a cards-only workspace without project/T4 state."""

    paths = task_import_paths("T4-GATE1")

    assert "project.yaml" in paths
    assert "literature" in paths
    assert "ideation" in paths
    assert not any(path.startswith("ideation/") for path in paths)


def _state_machine() -> StateMachine:
    repo_root = Path(__file__).resolve().parents[2]
    return StateMachine(
        repo_root / "config/system_config/state_machine.yaml",
        repo_root / "config/system_config/gates.yaml",
    )


def test_incomplete_t5_contract_redirects_to_t45_formalization(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    (tmp_path / "ideation" / "orientation_review.json").unlink()
    state = StateYaml(project_id="test", current_task="T5-REBOOST-GATE", status="PAUSED")

    redirected = _state_machine()._redirect_incomplete_t5_to_t45_formalization(
        state,
        tmp_path,
        source="test",
    )

    assert redirected is state
    assert state.current_task == "T4.5-FORMALIZE"
    assert state.status == "RUNNING"
    assert state.pending_gate is None
    repair = state.task_context["t45_t5_handoff_repair"]
    assert repair["target_task"] == "T4.5-FORMALIZE"
    assert "orientation_review" in repair["error_summary"]


def test_t45_human_continue_cannot_bypass_missing_formalization(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    (tmp_path / "ideation" / "exp_plan.yaml").unlink()
    state = StateYaml(
        project_id="test",
        current_task="T4.5-HUMAN-REVIEW",
        status="WAITING_HUMAN",
        pending_gate=GateState(
            gate_id="t45_human_review_gate",
            presented_at="2026-07-27T00:00:00+00:00",
            presentation={},
            options=[],
        ),
    )

    resolved = _state_machine().resolve_pending_gate(
        state,
        {"option_id": "continue_to_t5", "captured": {}},
        workspace_dir=tmp_path,
    )

    assert resolved.current_task == "T4.5-FORMALIZE"
    assert resolved.status == "RUNNING"
    assert resolved.pending_gate is None
    receipt = json.loads((tmp_path / "ideation" / "novelty_human_review.json").read_text(encoding="utf-8"))
    assert receipt["selected_option"] == "continue_to_t5"
    assert receipt["next_task"] == "T4.5-FORMALIZE"


def test_pending_t5_recovery_is_upgraded_to_t45_formalization(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    (tmp_path / "ideation" / "research_blueprint.yaml").unlink()
    state = StateYaml(
        project_id="test",
        current_task="T5-REBOOST-GATE",
        status="WAITING_HUMAN",
        pending_gate=GateState(
            gate_id="runtime_recovery_gate",
            presented_at="2026-07-27T00:00:00+00:00",
            presentation={},
            options=[],
        ),
    )

    refreshed = _state_machine().refresh_pending_gate_presentation(state, workspace_dir=tmp_path)

    assert refreshed.current_task == "T4.5-FORMALIZE"
    assert refreshed.status == "RUNNING"
    assert refreshed.pending_gate is None
