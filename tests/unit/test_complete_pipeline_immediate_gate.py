from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from researchos.cli_runners.complete_pipeline import CompletePipelineRunner
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.config import RuntimeSettings
from researchos.schemas.state import GateState, StateYaml


class _GateHuman:
    def __init__(self) -> None:
        self.presented: list[str] = []

    async def present_gate(self, *, gate_id: str, presentation: dict, options: list[dict]) -> dict:
        self.presented.append(gate_id)
        return {"option_id": "continue", "captured": {}}


class _ImmediateGateStateMachine:
    def __init__(self) -> None:
        self.nodes = {"T2-PARAM-GATE": SimpleNamespace(terminal=False)}
        self.should_pause_calls = 0

    def should_pause_for_immediate_gate(self, state: StateYaml, *, workspace_dir: Path) -> bool:
        self.should_pause_calls += 1
        return True

    def pause_for_immediate_gate(self, state: StateYaml, *, workspace_dir: Path) -> StateYaml:
        state.status = "WAITING_HUMAN"
        state.pending_gate = GateState(
            gate_id="t2_literature_param_gate",
            presented_at="2026-01-01T00:00:00Z",
            presentation={"_title": "T2", "_description": "Choose coverage."},
            options=[{"id": "continue", "label": "Continue"}],
        )
        return state

    def refresh_pending_gate_presentation(self, state: StateYaml, *, workspace_dir: Path) -> StateYaml:
        return state

    def resolve_pending_gate(self, state: StateYaml, gate_result: dict, *, workspace_dir: Path) -> StateYaml:
        state.pending_gate = None
        state.status = "RUNNING"
        state.current_task = "T2"
        return state


class _ConsumedGateStateMachine:
    def __init__(self) -> None:
        self.nodes = {"T4-GATE1": SimpleNamespace(terminal=False)}

    def refresh_pending_gate_presentation(self, state: StateYaml, *, workspace_dir: Path) -> StateYaml:
        # T4 can consume a persisted Gate when it reconstructs an already
        # confirmed operation.  The runner must return this new state instead
        # of dereferencing the now-cleared Gate.
        state.pending_gate = None
        state.status = "RUNNING"
        state.current_task = "T4"
        return state


def _runner(tmp_path: Path, *, state_machine: object, human: object) -> CompletePipelineRunner:
    return CompletePipelineRunner(
        workspace=tmp_path,
        state_machine=state_machine,  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        tool_registry=object(),  # type: ignore[arg-type]
        human_interface=human,  # type: ignore[arg-type]
        runtime_settings=RuntimeSettings(),
    )


def test_immediate_gate_is_presented_once_instead_of_recreated(tmp_path: Path) -> None:
    state_machine = _ImmediateGateStateMachine()
    human = _GateHuman()
    state = StateYaml(project_id="test", current_task="T2-PARAM-GATE", status="RUNNING")
    state_path = tmp_path / "state.yaml"

    result = asyncio.run(_runner(tmp_path, state_machine=state_machine, human=human)._run_one_step(state, state_path))

    assert human.presented == ["t2_literature_param_gate"]
    assert state_machine.should_pause_calls == 1
    assert result.current_task == "T2"
    assert result.status == "RUNNING"
    assert result.pending_gate is None


def test_consumed_gate_refresh_returns_new_t4_state_without_rendering_stale_gate(tmp_path: Path) -> None:
    human = _GateHuman()
    state = StateYaml(
        project_id="test",
        current_task="T4-GATE1",
        status="WAITING_HUMAN",
        pending_gate=GateState(
            gate_id="t4_gate1_selection_gate",
            presented_at="2026-01-01T00:00:00Z",
            presentation={},
            options=[],
        ),
    )

    result = asyncio.run(
        _runner(tmp_path, state_machine=_ConsumedGateStateMachine(), human=human)._present_pending_gate(
            state,
            tmp_path / "state.yaml",
        )
    )

    assert human.presented == []
    assert result.current_task == "T4"
    assert result.status == "RUNNING"
    assert result.pending_gate is None
    persisted = StateYaml.load_yaml(tmp_path / "state.yaml")
    assert persisted.current_task == "T4"
    assert persisted.status == "RUNNING"


def test_selection_score_recovery_is_not_mistaken_for_legacy_evolution() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    state_machine = StateMachine(
        repo_root / "config/system_config/state_machine.yaml",
        repo_root / "config/system_config/gates.yaml",
    )
    state = StateYaml(
        project_id="test",
        current_task="T4",
        status="RUNNING",
        task_context={
            "t4_operation_request": {
                "action": "recover_selection_score",
                "directive": {
                    "action": "select_candidate",
                    "raw_user_input": "推进 D2",
                    "target_candidate_ids": ["candidate-2"],
                },
            }
        },
    )

    assert state_machine.should_pause_for_immediate_gate(state, workspace_dir=repo_root) is False
