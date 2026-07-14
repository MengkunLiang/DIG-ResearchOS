from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.agents.ideation import IdeationAgent, validate_t4_gate1_ready
from researchos.ideation.config import load_t4_evolution_settings
from researchos.ideation.legacy_projection import project_gate1_population
from researchos.ideation.prerun import default_run_config, inspect_t4_inputs
from researchos.ideation.state import T4ArtifactStore, run_config_fingerprint
from researchos.ideation.models import TargetProfile
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.agent import Agent, AgentSpec, ExecutionContext
from researchos.runtime.config import AgentBehaviorSettings, RuntimeSettings
from researchos.runtime.errors import RecoverableRuntimePause
from researchos.runtime.orchestrator import AgentRunner
from researchos.testing.mocks import MockHumanInterface, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry
from tests.unit.test_t4_legacy_projection import _ready_projection_inputs


class _GenericT4ProbeAgent(Agent):
    """A non-production Agent that reuses the T4 label for runtime testing."""

    def __init__(self) -> None:
        super().__init__(
            AgentSpec(
                name="generic-t4-probe",
                model_tier="standard",
                tool_names=[],
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return "generic runtime probe"

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "exercise generic runtime behavior"


def _write_workspace(workspace):
    (workspace / "literature" / "deep_read_notes").mkdir(parents=True)
    (workspace / "user_seeds").mkdir()
    (workspace / "project.yaml").write_text("project_id: runtime-test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("A research synthesis with bounded evidence.\n", encoding="utf-8")
    (workspace / "literature" / "synthesis_workbench.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "domain_map.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "comparison_table.csv").write_text("id,title\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_ideas.md").write_text("\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_constraints.md").write_text("\n", encoding="utf-8")
    (workspace / "literature" / "deep_read_notes" / "p1.md").write_text(
        "# Paper note\n\n## Mechanism\n\nA bounded observation supports a testable mechanism question.\n",
        encoding="utf-8",
    )
    store = T4ArtifactStore(workspace)
    config = default_run_config(
        load_t4_evolution_settings(),
        target_profile=TargetProfile(confirmed_by_user=True),
    )
    store.write_run_config(config)
    inspection = inspect_t4_inputs(workspace)
    store.write_json(
        "ideation/evolution/pre_run_confirmation.json",
        {
            "schema_version": "1.0.0",
            "semantics": "t4_pre_run_confirmation",
            "input_fingerprint": inspection.input_fingerprint,
            "run_config_fingerprint": run_config_fingerprint(config),
            "selected_option": "start_standard",
        },
    )


def _candidate(candidate_id: str, route: str, *, parent_ids=None):
    origin, status = {
        "evidence_routed_literature": ("evidence_driven", "mainline"),
        "informed_brainstorm": ("informed_brainstorm", "mainline"),
        "mechanism_challenge": ("mechanism_challenge", "supplement"),
        "reverse_operation": ("reverse_operation", "supplement"),
        "subgroup_failure": ("subgroup_failure", "supplement"),
        "gap_exploration": ("gap_exploration", "supplement"),
        "cross_domain_bridge": ("cross_domain_analogy", "mainline"),
    }.get(route, ("evidence_driven", "mainline"))
    gene = lambda value: {"value": value}
    presentation = {
        "title": f"{candidate_id} tests a bounded mechanism under a project-defined condition.",
        "display_title": f"Bounded {candidate_id}",
        "basis_summary": f"{candidate_id} connects the available reading notes to a bounded mechanism question and a discriminating validation design. The proposal keeps the evidence boundary explicit and tests whether the mechanism survives an active disabling control.",
        "practical_implication": "The project can target the documented condition while retaining a decision rule for when the mechanism should be rejected.",
        "counterfactual": "If a disabling control preserves the effect, the proposed mechanism must be rejected rather than treated as established.",
        "gate1_card": {
            "role_summary": "This candidate preserves one coherent mechanism and an explicit falsification path for comparison.",
            "evidence_interpretation": "The notes motivate an opportunity and bounded hypothesis, not an external novelty conclusion.",
            "selection_advice": "Select this option when the stated control fits the available project constraints and evidence boundary.",
            "risk_summary": "A non-specific control effect or a failed reading upgrade requires reframing before final hypothesis compilation.",
            "user_edit_hint": "Keep the mechanism intact and narrow the target condition when a stricter scope is required.",
        },
        "basis_sources": [
            {"ref": f"{candidate_id}-note-1", "claim": "A reading note identifies a recurring boundary that current methods do not explain with a discriminating mechanism.", "implication": "The candidate retains a mechanism control rather than reporting an undifferentiated improvement."},
            {"ref": f"{candidate_id}-note-2", "claim": "A second reading observation records a limitation in the available baseline explanation.", "implication": "The validation design tests the alternative explanation directly."},
        ],
        "innovation": {
            "summary": "Convert an observed limitation into a mechanism-bound validation design.",
            "type": "mechanism",
            "novelty_delta": "Require a discriminating control rather than only an average improvement.",
            "non_incremental_reason": "The candidate changes the evidence required for a claim rather than adding a routine module.",
        },
        "minimum_validation": {"dataset": "workspace-defined task", "baseline": "documented baseline", "metric": "predeclared primary metric", "expected_signal": "the effect weakens when the mechanism is disabled", "evidence_status": "proposed_not_verified", "source_refs": []},
        "idea_origin": origin,
        "constraint_status": status,
        "mechanism_family": f"family-{candidate_id}",
    }
    return {
        "candidate_id": candidate_id,
        "version": 1,
        "status": "active",
        "maturity": "evolved",
        "genome": {
            "candidate_id": candidate_id,
            "route": route,
            "parents": parent_ids or [],
            "problem": gene("A bounded problem needs a falsifiable explanation."),
            "opportunity": gene("A documented opportunity can be tested under a clear evidence boundary."),
            "challenged_assumption": gene("The baseline explanation may not hold under the target condition."),
            "core_thesis": gene("A coherent mechanism changes the expected outcome under its stated boundary."),
            "mechanism": gene(f"A distinct bounded mechanism for {candidate_id}."),
            "design_or_artifact": gene("A bounded artifact with an active and disabling control."),
            "contribution_package": gene("A mechanism contribution and a validation contribution."),
            "hypothesis_bundle": gene("Two falsifiable hypotheses with discriminating tests."),
            "validation_logic": gene("Compare active and disabling controls under the same project condition."),
            "boundary_conditions": gene("The effect should weaken outside the target condition."),
            "risks": gene("A non-specific control effect invalidates the claimed mechanism."),
        },
        "contributions": [
            {"contribution_id": f"{candidate_id}-C1", "statement": "Make the bounded mechanism testable.", "contribution_type": "mechanism", "what_changes_if_true": "Future work must evaluate the documented boundary instead of only an average outcome."},
            {"contribution_id": f"{candidate_id}-C2", "statement": "Add a discriminating validation control.", "contribution_type": "design", "what_changes_if_true": "The validation can reject a non-specific alternative explanation."},
        ],
        "hypotheses": [
            {"hypothesis_id": f"{candidate_id}-H1", "statement": "The target condition changes the expected outcome.", "mechanism": "The stated mechanism reacts to the target condition.", "observable_prediction": "The target group differs from the matched control.", "discriminating_test": "Disable the mechanism while holding the condition fixed."},
            {"hypothesis_id": f"{candidate_id}-H2", "statement": "The effect weakens outside the target condition.", "mechanism": "The mechanism has a boundary.", "observable_prediction": "The non-target group has a smaller effect.", "discriminating_test": "Compare matched target and non-target groups."},
        ],
        "lineage": {"candidate_id": candidate_id, "parent_ids": parent_ids or [], "route": route, "created_by": "evolver" if parent_ids else "generator"},
        "presentation": presentation,
    }


def _score(candidate_id: str, batch_id: str):
    score_keys = ("novelty", "feasibility", "impact", "evaluability", "differentiation", "cost", "contribution_strength")
    return {
        "candidate_id": candidate_id,
        "scoring_batch_id": batch_id,
        "blind": True,
        "scores": {"research_value": 4.0, "mechanism_integrity": 4.0, "contribution_distinctiveness": 4.0, "evidence_calibration": 4.0, "validation_tractability": 4.0},
        "overall_readiness": 4.0,
        "score_uncertainty": 0.2,
        "rationales": {key: f"The {key} assessment follows the candidate's bounded mechanism and explicit validation design." for key in ("research_value", "mechanism_integrity", "contribution_distinctiveness", "evidence_calibration", "validation_tractability")},
        "dominant_strength": "A coherent mechanism and discriminating control.",
        "dominant_bottleneck": "The validation must keep the boundary operational.",
        "preserve_genes": ["problem"],
        "modify_genes": ["validation_logic"],
        "recommended_operators": ["repair_validation"],
        "compatibility_scores": {key: 4 for key in score_keys},
        "compatibility_rationales": {key: f"The {key} score for {candidate_id} follows a distinct mechanism, evidence boundary, and validation design." for key in score_keys},
        "profile_fit": {
            "profile_type": "hybrid",
            "overall_fit": 4.0,
            "dimensions": {"dual_contribution_coherence": 4.0},
            "rationale": "The candidate connects a bounded technical mechanism to a clearly stated research consequence.",
            "cautions": [],
        },
    }


def _payload_from_prompt(prompt: str) -> dict:
    marker = '{\n  "prompt_version"'
    start = prompt.find(marker)
    assert start >= 0, prompt
    return json.loads(prompt[start:])


def test_t4_controller_controls_are_scoped_to_the_registered_ideation_agent(tmp_workspace):
    """Generic AgentRunner uses must not trigger native T4 artifact work."""

    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        _GenericT4ProbeAgent(),
        registry,
        MockLLMClient([]),
        MockHumanInterface(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_workspace,
        project_id="runtime-test",
        task_id="T4",
        run_id="generic-t4-probe",
    )

    runner._prepare_t4_execution_mode_before_prompt(ctx)

    assert "t4_execution_mode" not in ctx.extra
    assert runner._maybe_prepare_t4_context_pack_before_prompt(ctx) is False
    assert not (tmp_workspace / "ideation" / "t4_context_pack.json").exists()


@pytest.mark.asyncio
async def test_confirmed_standard_t4_runs_p0_to_p1_and_preserves_gate1_transition(tmp_workspace, capsys, monkeypatch):
    _write_workspace(tmp_workspace)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(IdeationAgent(), registry, MockLLMClient([]), MockHumanInterface())
    generated = 0

    async def fake_role_call(*, ctx, eff, budget, system_contract, user_prompt):
        nonlocal generated
        if "opportunity-planning" in system_contract:
            return json.dumps({"opportunities": [
                {"opportunity_id": "O1", "type": "mechanism_gap", "one_line_summary": "A bounded mechanism opportunity.", "question": "Which mechanism can be tested?", "why_it_matters": "A discriminating test is required.", "compatible_routes": ["evidence_routed_literature", "informed_brainstorm"]},
                {"opportunity_id": "O2", "type": "failure_boundary", "one_line_summary": "A bounded failure opportunity.", "question": "Where does the mechanism fail?", "why_it_matters": "A boundary prevents overclaiming.", "compatible_routes": ["mechanism_challenge", "subgroup_failure"]},
                {"opportunity_id": "O3", "type": "bridge_transfer_opportunity", "one_line_summary": "A cross-domain opportunity.", "question": "Which transferable mechanism is worth testing?", "why_it_matters": "Bridge reasoning should remain falsifiable.", "compatible_routes": ["cross_domain_bridge", "reverse_operation", "gap_exploration"]},
            ]})
        payload = _payload_from_prompt(user_prompt)
        if "IdeaGeneratorAgent." in system_contract:
            route = payload["route"]
            quota = payload["quota"]
            candidates = []
            for _ in range(quota):
                generated += 1
                candidates.append(_candidate(f"I{generated}", route))
            return json.dumps({"candidates": candidates})
        if "crossover-review" in system_contract:
            return json.dumps({"decisions": []})
        if "IdeaScoringAgent" in system_contract:
            return json.dumps({"scores": [_score(item["candidate_id"], payload["scoring_batch_id"]) for item in payload["candidates"]]})
        if "IdeaEvolverAgent" in system_contract:
            children = []
            for index, plan in enumerate(payload["plans"], start=1):
                children.append(_candidate(f"M{index}", "evidence_routed_literature", parent_ids=plan["parent_ids"]))
            return json.dumps({"children": children})
        if "Final Idea Card Compiler" in system_contract:
            cards = []
            for candidate in payload["candidates"]:
                cards.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "profile_type": "hybrid",
                        "core_thesis": candidate["genome"]["core_thesis"]["value"],
                        "contribution_ids": [item["contribution_id"] for item in candidate["contributions"]],
                        "hypothesis_ids": [item["hypothesis_id"] for item in candidate["hypotheses"]],
                        "plain_language_summary": "A bounded candidate tests its stated mechanism before any strong conclusion is made.",
                        "why_it_matters": "The design separates the proposed mechanism from an alternative explanation.",
                        "affected_stakeholders_or_processes": ["research workflow"],
                        "representative_scenario": "A researcher must decide whether a mechanism is genuine before adopting the design.",
                        "current_failure": "Existing comparison does not distinguish the mechanism from a broad alternative.",
                        "scientific_technical_core": "A discriminating control tests the bounded mechanism.",
                        "implications": [
                            {
                                "implication_type": "scientific",
                                "statement": "A passing validation would narrow the explanation that later work may use.",
                                "evidence_status": "llm_inference",
                                "conditions": ["The proposed validation passes."],
                            }
                        ],
                        "conditions_for_impact": ["The planned control must rule out the competing explanation."],
                        "claims_not_to_make": ["Do not claim an established effect before the proposed validation."],
                        "risks_and_boundaries": ["The candidate remains bounded by its stated condition."],
                        "evidence_status_summary": "This is a proposed candidate rather than a verified result.",
                    }
                )
            return json.dumps({"cards": cards})
        raise AssertionError(system_contract)

    monkeypatch.setattr(runner, "_call_t4_evolution_role", fake_role_call)
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="runtime-test", task_id="T4", run_id="t4-runtime")
    result = await runner.run(ctx)

    assert result.ok
    assert result.metadata["completion_mode"] == "t4_gate1_ready"
    assert (tmp_workspace / "ideation/populations/P0.json").exists()
    assert (tmp_workspace / "ideation/populations/P1.json").exists()
    assert (tmp_workspace / "ideation/portfolio.json").exists()
    assert (tmp_workspace / "ideation/final_cards/portfolio_cards.json").exists()
    execution_mode = json.loads(
        (tmp_workspace / "ideation/evolution/execution_mode.json").read_text(encoding="utf-8")
    )
    assert execution_mode["mode"] == "evolutionary"
    assert execution_mode["reason"] == "current_pre_run_confirmation"
    assert runner.llm.call_count == 0
    assert validate_t4_gate1_ready(tmp_workspace)[0]
    rendered = capsys.readouterr().out
    assert "Evidence Routing" in rendered
    assert '"candidate_id"' not in rendered

    machine = StateMachine(
        Path("/mnt/data/DIG-ResearchOS/config/system_config/state_machine.yaml"),
        Path("/mnt/data/DIG-ResearchOS/config/system_config/gates.yaml"),
    )
    state = machine.create_initial_state("runtime-test")
    state.current_task = "T4"
    state = machine.start_task(state, "t4-runtime", workspace_dir=tmp_workspace)
    advanced = machine.advance(state, result, workspace_dir=tmp_workspace)
    assert advanced.current_task == "T4-GATE1"


@pytest.mark.asyncio
async def test_selected_candidate_advances_to_t45_from_pre_novelty_artifacts_without_legacy_t4_rewrite(tmp_workspace):
    (tmp_workspace / "project.yaml").write_text("project_id: selected-runtime\n", encoding="utf-8")
    dossiers, scores, population = _ready_projection_inputs()
    project_gate1_population(tmp_workspace, population=population, dossiers=dossiers, scores=scores)
    machine = StateMachine(
        Path("/mnt/data/DIG-ResearchOS/config/system_config/state_machine.yaml"),
        Path("/mnt/data/DIG-ResearchOS/config/system_config/gates.yaml"),
    )
    gate_node = machine.nodes["T4-GATE1"]
    machine._persist_immediate_gate_result(
        gate_node,
        {"option_id": "select_or_reframe", "captured": {"selection": "Use I1"}},
        "T4",
        tmp_workspace,
    )
    assert (tmp_workspace / "ideation" / "hypothesis_brief.yaml").exists()
    assert not (tmp_workspace / "ideation" / "hypotheses.md").exists()

    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(IdeationAgent(), registry, MockLLMClient([]), MockHumanInterface())
    result = await runner.run(
        ExecutionContext(workspace_dir=tmp_workspace, project_id="selected-runtime", task_id="T4", run_id="selected-t4")
    )

    assert result.ok
    assert result.metadata["completion_mode"] == "t4_pre_novelty_ready"
    assert not (tmp_workspace / "ideation" / "hypotheses.md").exists()
    state = machine.create_initial_state("selected-runtime")
    state.current_task = "T4"
    state = machine.start_task(state, "selected-t4", workspace_dir=tmp_workspace)
    advanced = machine.advance(state, result, workspace_dir=tmp_workspace)
    assert advanced.current_task == "T4.5"


@pytest.mark.asyncio
async def test_t4_without_confirmation_fails_closed_before_legacy_prompt_or_llm(tmp_workspace):
    """Direct invocation must not silently use the historical prompt path."""

    _write_workspace(tmp_workspace)
    (tmp_workspace / "ideation/evolution/pre_run_confirmation.json").unlink()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    llm = MockLLMClient([])
    runner = AgentRunner(IdeationAgent(), registry, llm, MockHumanInterface())
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="runtime-test", task_id="T4", run_id="native-required")

    result = await runner.run(ctx)

    assert not result.ok
    assert result.stop_reason == "interrupted"
    assert "pre-run confirmation" in str(result.error)
    assert llm.call_count == 0
    assert not (tmp_workspace / "ideation/evolution/execution_mode.json").exists()
    assert "T4 Ideation Agent recovery assistant" not in runner.agent.system_prompt(ctx)


def test_explicit_legacy_setting_is_the_only_path_that_renders_legacy_prompt_and_records_reason(tmp_workspace):
    """A migration operator can opt in, and the receipt remains auditable."""

    _write_workspace(tmp_workspace)
    (tmp_workspace / "ideation/evolution/pre_run_confirmation.json").unlink()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    settings = RuntimeSettings(
        agent_behavior=AgentBehaviorSettings(allow_legacy_t4_fallback=True)
    )
    runner = AgentRunner(
        IdeationAgent(), registry, MockLLMClient([]), MockHumanInterface(), runtime_settings=settings
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="runtime-test", task_id="T4", run_id="legacy-opt-in")

    runner._prepare_t4_execution_mode_before_prompt(ctx)

    assert ctx.extra["t4_execution_mode"] == "legacy_fallback"
    assert "T4 Ideation Agent recovery assistant" in runner.agent.system_prompt(ctx)
    receipt = json.loads(
        (tmp_workspace / "ideation/evolution/execution_mode.json").read_text(encoding="utf-8")
    )
    assert receipt["mode"] == "legacy_fallback"
    assert receipt["reason"] == "explicit_runtime_setting_without_current_evolution_confirmation"


def test_legacy_fallback_cannot_select_or_overwrite_native_t4_artifacts(tmp_workspace):
    """Native work remains authoritative even when a migration switch is on."""

    _write_workspace(tmp_workspace)
    (tmp_workspace / "ideation/evolution/pre_run_confirmation.json").unlink()
    protected = tmp_workspace / "ideation/populations/P0.json"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text('{"native": true}\n', encoding="utf-8")
    settings = RuntimeSettings(
        agent_behavior=AgentBehaviorSettings(allow_legacy_t4_fallback=True)
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        IdeationAgent(), registry, MockLLMClient([]), MockHumanInterface(), runtime_settings=settings
    )
    ctx = ExecutionContext(workspace_dir=tmp_workspace, project_id="runtime-test", task_id="T4", run_id="protected-native")

    runner._prepare_t4_execution_mode_before_prompt(ctx)

    assert ctx.extra["t4_execution_mode"] == "evolutionary"
    assert "T4 Ideation Agent recovery assistant" not in runner.agent.system_prompt(ctx)
    with pytest.raises(RecoverableRuntimePause, match="native Evolution artifacts"):
        runner._record_t4_execution_mode(
            ctx,
            mode="legacy_fallback",
            reason="test_attempt_after_native_artifact",
        )
    assert protected.read_text(encoding="utf-8") == '{"native": true}\n'
