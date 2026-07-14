from __future__ import annotations

import pytest

from researchos.ideation.models import EvolutionPhase, PopulationSnapshot, T4RunConfig
from researchos.ideation.state import T4ArtifactStore, run_config_fingerprint, t4_input_fingerprint


def _write_inputs(workspace):
    (workspace / "literature").mkdir(parents=True)
    (workspace / "user_seeds").mkdir()
    (workspace / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("synthesis\n", encoding="utf-8")
    (workspace / "literature" / "synthesis_workbench.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "domain_map.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "comparison_table.csv").write_text("id,title\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_ideas.md").write_text("\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_constraints.md").write_text("\n", encoding="utf-8")


def _population(workspace, config, population_id="P0", generation=0):
    fingerprint = t4_input_fingerprint(workspace)
    return PopulationSnapshot(
        population_id=population_id,
        generation=generation,
        input_fingerprint=fingerprint,
        run_config_fingerprint=run_config_fingerprint(config),
        active_candidate_ids=["I1"],
    )


def test_store_initializes_idempotently_and_reuses_equal_artifacts(tmp_path):
    _write_inputs(tmp_path)
    config = T4RunConfig()
    store = T4ArtifactStore(tmp_path)
    state = store.initialize_state(config=config, population=_population(tmp_path, config))
    assert state.current_population_id == "P0"
    first = store.write_population(_population(tmp_path, config))
    second = store.write_population(_population(tmp_path, config))
    assert first.changed is False
    assert second.changed is False
    assert store.read_state().phase == EvolutionPhase.FORMATION


def test_phase_marker_reuses_only_matching_fingerprints(tmp_path):
    _write_inputs(tmp_path)
    config = T4RunConfig()
    store = T4ArtifactStore(tmp_path)
    population = _population(tmp_path, config)
    store.initialize_state(config=config, population=population)
    input_fp = t4_input_fingerprint(tmp_path)
    config_fp = run_config_fingerprint(config)
    store.write_phase_marker(
        phase=EvolutionPhase.FORMATION,
        generation=0,
        input_fingerprint=input_fp,
        run_config_fingerprint=config_fp,
        artifact_paths=["ideation/populations/P0.json"],
    )
    assert store.phase_is_complete(
        phase=EvolutionPhase.FORMATION,
        generation=0,
        input_fingerprint=input_fp,
        run_config_fingerprint=config_fp,
    )
    (tmp_path / "literature" / "synthesis.md").write_text("changed\n", encoding="utf-8")
    assert not store.phase_is_complete(
        phase=EvolutionPhase.FORMATION,
        generation=0,
        input_fingerprint=t4_input_fingerprint(tmp_path),
        run_config_fingerprint=config_fp,
    )


def test_rollback_switches_population_without_deleting_later_generation(tmp_path):
    _write_inputs(tmp_path)
    config = T4RunConfig()
    store = T4ArtifactStore(tmp_path)
    p0 = _population(tmp_path, config, "P0", 0)
    store.initialize_state(config=config, population=p0)
    p1 = _population(tmp_path, config, "P1", 1)
    store.write_population(p1)
    store.activate_population("P1")
    rolled_back = store.activate_population("P0")
    assert rolled_back.current_population_id == "P0"
    assert (tmp_path / "ideation" / "populations" / "P1.json").exists()
    assert "P1" in rolled_back.archived_population_ids


def test_state_input_validation_detects_changed_scientific_input(tmp_path):
    _write_inputs(tmp_path)
    config = T4RunConfig()
    store = T4ArtifactStore(tmp_path)
    state = store.initialize_state(config=config, population=_population(tmp_path, config))
    assert store.validate_state_inputs(state) == (True, None)
    (tmp_path / "project.yaml").write_text("project_id: changed\n", encoding="utf-8")
    ok, error = store.validate_state_inputs(state)
    assert not ok
    assert error is not None


def test_atomic_write_failure_preserves_previous_artifact_and_cleans_temp(tmp_path, monkeypatch):
    store = T4ArtifactStore(tmp_path)
    store.write_json("ideation/evolution/example.json", {"state": "before"})
    destination = tmp_path / "ideation" / "evolution" / "example.json"

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr("researchos.ideation.state.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        store.write_json("ideation/evolution/example.json", {"state": "after"})

    assert destination.read_text(encoding="utf-8") == '{\n  "state": "before"\n}\n'
    assert not list(destination.parent.glob(".example.json.*.tmp"))
