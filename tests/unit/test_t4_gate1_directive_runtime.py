from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.ideation.evolution_controller import IdeaEvolutionController
from researchos.ideation.config import OffspringDefaults, PopulationDefaults, RouteQuota
from researchos.ideation.legacy_projection import project_gate1_population
from researchos.ideation.models import CandidateLineage, GeneDonorMap, HumanCompositionCompatibility, T4RunConfig
from researchos.ideation.state import T4ArtifactStore
from researchos.orchestration.state_machine import StateMachine, _t4_gate1_candidate_pool_fingerprints
from researchos.runtime.agent import AgentResult, ExecutionContext
from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.system_config import system_config_path
from researchos.schemas.state import GateState
from researchos.testing.mocks import MockHumanInterface, MockLLMClient
from researchos.tools.registry import ToolRegistry
from researchos.agents.ideation import IdeationAgent
from tests.unit.test_t4_evolution_controller import FakeEvolver, FakeGenerator, FakeScorer, _candidate, _settings, _write_inputs
from tests.unit.test_t4_legacy_projection import _presentation


def _config() -> T4RunConfig:
    return T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )


async def _native_population(workspace: Path):
    _write_inputs(workspace)
    controller = IdeaEvolutionController(
        workspace_dir=workspace,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    return await controller.run(_config())


def _machine() -> StateMachine:
    return StateMachine(system_config_path("state_machine.yaml"), system_config_path("gates.yaml"))


def _waiting_gate(machine: StateMachine, workspace: Path):
    state = machine.create_initial_state("directive-runtime")
    state.current_task = "T4-GATE1"
    state.pending_gate = GateState(
        gate_id="t4_gate1_selection_gate",
        presented_at="2026-01-01T00:00:00+00:00",
        presentation={"candidate_pool_fingerprints": _t4_gate1_candidate_pool_fingerprints(workspace)},
        options=list(machine.gates["t4_gate1_selection_gate"]["options"]),
    )
    state.status = "WAITING_HUMAN"
    return state


@pytest.mark.asyncio
async def test_native_gate_another_generation_is_confirmed_then_queues_a_real_t4_operation(tmp_path):
    await _native_population(tmp_path)
    machine = _machine()
    state = _waiting_gate(machine, tmp_path)

    confirmation = machine.resolve_pending_gate(
        state,
        {"option_id": "another_generation", "captured": {}},
        workspace_dir=tmp_path,
    )

    assert confirmation.status == "WAITING_HUMAN"
    assert confirmation.pending_gate is not None
    assert confirmation.pending_gate.options[0]["id"] == "confirm"
    assert "t4_pending_directive" in confirmation.task_context

    queued = machine.resolve_pending_gate(
        confirmation,
        {"option_id": "confirm", "captured": {}},
        workspace_dir=tmp_path,
    )

    assert queued.current_task == "T4"
    assert queued.status == "RUNNING"
    assert queued.task_context["t4_operation_request"]["action"] == "continue_evolution"
    operation_path = tmp_path / queued.task_context["t4_operation_request"]["path"]
    assert operation_path.exists()
    assert json.loads(operation_path.read_text(encoding="utf-8"))["action"] == "continue_evolution"
    confirmations = list((tmp_path / "ideation" / "human_directives").glob("*_confirmation.json"))
    assert confirmations
    assert json.loads(confirmations[0].read_text(encoding="utf-8"))["accepted"] is True
    assert machine.should_pause_for_immediate_gate(queued, workspace_dir=tmp_path) is False


@pytest.mark.asyncio
async def test_native_gate_profile_revision_is_confirmed_and_stays_inside_t4(tmp_path):
    population_result = await _native_population(tmp_path)
    machine = _machine()
    state = _waiting_gate(machine, tmp_path)

    confirmation = machine.resolve_pending_gate(
        state,
        {
            "option_id": "change_orientation",
            "captured": {"directive": "Change Publication Orientation to CCF A with a technical emphasis."},
        },
        workspace_dir=tmp_path,
    )

    assert confirmation.current_task == "T4-GATE1"
    assert confirmation.status == "WAITING_HUMAN"
    assert confirmation.pending_gate is not None
    assert confirmation.pending_gate.options[0]["id"] == "confirm"
    assert confirmation.pending_gate.presentation["t4_directive_confirmation"]["action"] == "Change Publication Orientation"

    queued = machine.resolve_pending_gate(
        confirmation,
        {"option_id": "confirm", "captured": {}},
        workspace_dir=tmp_path,
    )

    operation = queued.task_context["t4_operation_request"]
    persisted_config = T4ArtifactStore(tmp_path).read_run_config()
    assert queued.current_task == "T4"
    assert queued.status == "RUNNING"
    assert operation["action"] == "change_target_profile"
    assert operation["requested_from_population"] == population_result.population.population_id
    assert persisted_config.target_profile.profile_type == "technical_cs"
    assert (tmp_path / "ideation" / "populations" / population_result.population.population_id).with_suffix(".json").exists()
    assert not queued.current_task.startswith(("T2", "T3"))


@pytest.mark.asyncio
async def test_native_gate_read_only_population_view_does_not_queue_a_model_operation(tmp_path):
    await _native_population(tmp_path)
    machine = _machine()
    state = _waiting_gate(machine, tmp_path)

    reopened = machine.resolve_pending_gate(
        state,
        {"option_id": "show_population", "captured": {}},
        workspace_dir=tmp_path,
    )

    assert reopened.current_task == "T4-GATE1"
    assert reopened.status == "WAITING_HUMAN"
    assert "t4_operation_request" not in reopened.task_context
    result = reopened.pending_gate.presentation["t4_directive_result"]
    assert result["kind"] == "remaining_population"


@pytest.mark.asyncio
async def test_component_composition_requires_a_second_confirmed_gate_before_generation_is_queued(tmp_path):
    result = await _native_population(tmp_path)
    population = result.population
    store = T4ArtifactStore(tmp_path)
    plan_path = "ideation/human_compositions/HC-TEST/composition_plan.json"
    store.write_json(
        plan_path,
        {
            "semantics": "t4_human_composition_plan",
            "composition_id": "HC-TEST",
            "status": "awaiting_human_confirmation",
            "population_id": population.population_id,
            "input_fingerprint": population.input_fingerprint,
            "run_config_fingerprint": population.run_config_fingerprint,
            "compatibility_report": "ideation/human_compositions/HC-TEST/compatibility_report.json",
            "compatibility": {
                "composition_id": "HC-TEST",
                "source_candidate_ids": population.active_candidate_ids[:2],
                "source_components": [f"{population.active_candidate_ids[0]}-H1", f"{population.active_candidate_ids[1]}-H1"],
                "problem_compatibility": "high",
                "assumption_conflict": "none",
                "mechanism_compatibility": "high",
                "joint_testability": "high",
                "contribution_coherence": "high",
                "evidence_compatibility": "high",
                "complexity_risk": "low",
                "composition_type": "complementary",
                "recommended_action": "compose",
                "explanation_for_user": "A coherent thesis remains possible after the documented compatibility review.",
                "required_repairs": [],
                "gene_donor_map": {"donors": {"problem": population.active_candidate_ids[0], "mechanism": population.active_candidate_ids[1]}},
            },
        },
    )
    machine = _machine()
    state = _waiting_gate(machine, tmp_path)

    queued = machine.resolve_pending_gate(
        state,
        {"option_id": "confirm_composition", "captured": {}},
        workspace_dir=tmp_path,
    )

    assert queued.current_task == "T4"
    assert queued.task_context["t4_operation_request"]["action"] == "execute_human_composition"
    assert not list((tmp_path / "ideation" / "candidates").glob("HC*.json"))


class _CompositionReviewScorer:
    async def review_human_composition(
        self,
        *,
        composition_id,
        candidates,
        component_refs,
        preserve_genes,
        donor_genes,
        constraints,
    ):
        return HumanCompositionCompatibility(
            composition_id=composition_id,
            source_candidate_ids=[candidate.candidate_id for candidate in candidates],
            source_components=component_refs,
            problem_compatibility="high",
            assumption_conflict="none",
            mechanism_compatibility="high",
            joint_testability="high",
            contribution_coherence="high",
            evidence_compatibility="high",
            complexity_risk="low",
            composition_type="complementary",
            recommended_action="compose",
            explanation_for_user="The selected components can be reconciled into one bounded thesis with a shared discriminating test.",
            required_repairs=[],
            gene_donor_map=GeneDonorMap(
                donors={"problem": candidates[0].candidate_id, "mechanism": candidates[1].candidate_id},
                synthesized_genes=["core_thesis", "validation_logic"],
            ),
        )


class _CompositionEvolver:
    async def generate_human_composition(self, *, composition_id, target_candidate_id, compatibility, parents):
        candidate = _candidate(
            target_candidate_id,
            "human_composition",
            parent_ids=list(compatibility.source_candidate_ids),
            mechanism="A reconciled bounded mechanism with one discriminating validation path.",
        )
        return candidate.model_copy(
            update={
                "lineage": CandidateLineage(
                    candidate_id=target_candidate_id,
                    parent_ids=list(compatibility.source_candidate_ids),
                    route="human_composition",
                    created_by="human_composition",
                ),
                "presentation": _presentation(99, "human_composition"),
            }
        )


class _ProjectionScorer(FakeScorer):
    async def score_population(self, *, candidates, scoring_batch_id, blind):
        scores = await super().score_population(
            candidates=candidates,
            scoring_batch_id=scoring_batch_id,
            blind=blind,
        )
        keys = ("novelty", "feasibility", "impact", "evaluability", "differentiation", "cost", "contribution_strength")
        return [
            score.model_copy(
                update={
                    "compatibility_scores": {key: 4 for key in keys},
                    "compatibility_rationales": {
                        key: f"The {key} comparison follows the candidate's documented mechanism, evidence boundary, and validation design."
                        for key in keys
                    },
                }
            )
            for score in scores
        ]


def _t4_gate_completion() -> AgentResult:
    return AgentResult(
        ok=True,
        message="T4 operation completed",
        outputs_produced={},
        steps_used=0,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        duration_seconds=0.0,
        stop_reason=AgentResult.STOP_FINISHED,
        metadata={"completion_mode": "t4_gate1_ready"},
    )


@pytest.mark.asyncio
async def test_component_composition_runtime_runs_check_confirmation_scoring_and_gate_reentry(tmp_path):
    _write_inputs(tmp_path)
    settings = _settings().model_copy(
        update={
            "route_quotas": [
            RouteQuota(route="evidence_routed_literature", minimum=1, maximum=2, required=True),
            RouteQuota(route="informed_brainstorm", minimum=1, maximum=2, required=True),
            ],
            "population": PopulationDefaults(
            max_initial_population=8,
            active_population_target=4,
            active_population_minimum=4,
            active_population_maximum=4,
            ),
            "offspring": OffspringDefaults(
            mutation_minimum=1,
            mutation_maximum=1,
            crossover_minimum=0,
            crossover_maximum=0,
            max_total=1,
            ),
        }
    )
    initial_controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=settings,
        generator=FakeGenerator(),
        scorer=_ProjectionScorer(),
        evolver=FakeEvolver(),
    )
    initial = await initial_controller.run(
        T4RunConfig(
            mode="standard",
            rounds=1,
            allow_crossover=False,
            final_top_k=2,
            max_initial_population=8,
            active_population_size=4,
            max_offspring_per_round=1,
            max_crossover_children=0,
            route_quotas={"evidence_routed_literature": 2, "informed_brainstorm": 2},
        )
    )
    store = T4ArtifactStore(tmp_path)
    for index, candidate_id in enumerate(initial.population.active_candidate_ids, start=1):
        dossier = store.read_model(f"ideation/candidates/{candidate_id}.v1.json", type(initial.active_dossiers[0]))
        store.write_candidate(dossier.model_copy(update={"presentation": _presentation(index, "fixture")}))

    machine = _machine()
    selection_gate = _waiting_gate(machine, tmp_path)
    source_ids = initial.population.active_candidate_ids[:2]
    requested = machine.resolve_pending_gate(
        selection_gate,
        {
            "option_id": "compose",
            "captured": {"directive": f"Compose {source_ids[0]}-H1 and {source_ids[1]}-H1 as a new candidate"},
        },
        workspace_dir=tmp_path,
    )
    assert requested.status == "WAITING_HUMAN"
    queued_check = machine.resolve_pending_gate(
        requested,
        {"option_id": "confirm", "captured": {}},
        workspace_dir=tmp_path,
    )
    assert queued_check.current_task == "T4"
    assert queued_check.task_context["t4_operation_request"]["action"] == "compose_from_components"
    queued_check = machine.start_task(queued_check, "composition-check", workspace_dir=tmp_path)

    runner = AgentRunner(IdeationAgent(), ToolRegistry(), MockLLMClient([]), MockHumanInterface())
    ctx = ExecutionContext(workspace_dir=tmp_path, project_id="directive-runtime", task_id="T4", run_id="composition-check")
    await runner._run_t4_human_composition_check(
        ctx=ctx,
        scorer=_CompositionReviewScorer(),
        operation=queued_check.task_context["t4_operation_request"],
    )
    check_result = json.loads((tmp_path / "ideation" / "evolution" / "latest_operation_result.json").read_text(encoding="utf-8"))
    assert check_result["status"] == "awaiting_composition_confirmation"

    gate_after_check = machine.advance(queued_check, _t4_gate_completion(), workspace_dir=tmp_path)
    gate_after_check = machine.pause_for_immediate_gate(gate_after_check, workspace_dir=tmp_path)
    queued_generation = machine.resolve_pending_gate(
        gate_after_check,
        {"option_id": "confirm_composition", "captured": {}},
        workspace_dir=tmp_path,
    )
    assert queued_generation.current_task == "T4"
    assert queued_generation.task_context["t4_operation_request"]["action"] == "execute_human_composition"
    queued_generation = machine.start_task(queued_generation, "composition-generate", workspace_dir=tmp_path)

    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=settings,
        generator=FakeGenerator(),
        scorer=_ProjectionScorer(),
        evolver=FakeEvolver(),
    )
    result = await runner._run_t4_human_composition_generation(
        ctx=ExecutionContext(workspace_dir=tmp_path, project_id="directive-runtime", task_id="T4", run_id="composition-generate"),
        run_config=store.read_run_config(),
        controller=controller,
        evolver=_CompositionEvolver(),
        operation=queued_generation.task_context["t4_operation_request"],
    )
    project_gate1_population(
        tmp_path,
        population=result.population,
        dossiers=result.active_dossiers,
        scores=result.active_scores,
        route_results=result.route_results,
    )
    final_gate = machine.advance(queued_generation, _t4_gate_completion(), workspace_dir=tmp_path)
    final_gate = machine.pause_for_immediate_gate(final_gate, workspace_dir=tmp_path)

    assert result.population.population_id == "P2"
    assert final_gate.current_task == "T4-GATE1"
    assert final_gate.status == "WAITING_HUMAN"
    composition_plan = next((tmp_path / "ideation" / "human_compositions").rglob("composition_plan.json"))
    plan = json.loads(composition_plan.read_text(encoding="utf-8"))
    assert plan["status"] == "generated_and_independently_scored"
    assert set(plan["compatibility"]["source_candidate_ids"]) == set(source_ids)
    assert any(path.name.startswith("HC2-") for path in (tmp_path / "ideation" / "candidates").glob("*.json"))
