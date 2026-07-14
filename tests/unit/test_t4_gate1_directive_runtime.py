from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.ideation.evolution_controller import IdeaEvolutionController
from researchos.ideation.models import T4RunConfig
from researchos.ideation.state import T4ArtifactStore
from researchos.orchestration.state_machine import StateMachine, _t4_gate1_candidate_pool_fingerprints
from researchos.schemas.state import GateState
from tests.unit.test_t4_evolution_controller import FakeEvolver, FakeGenerator, FakeScorer, _settings, _write_inputs


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
    root = Path("/mnt/data/DIG-ResearchOS")
    return StateMachine(root / "config/system_config/state_machine.yaml", root / "config/system_config/gates.yaml")


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
