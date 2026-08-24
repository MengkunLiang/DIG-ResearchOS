from __future__ import annotations

import json
from pathlib import Path

from researchos.ideation.formalization import reset_t45_artifacts_for_new_selection
from researchos.orchestration.state_machine import StateMachine
from researchos.schemas.state import StateYaml
from researchos.ideation.proposal_portfolio import (
    TRACK_ARTIFACTS,
    TRACK_REQUIRED_ARTIFACTS,
    activate_next_track,
    backfill_historical_track_artifacts,
    create_manifest,
    materialize_selected_track,
    overview,
    resolve_ready_track_selection,
    snapshot_active_track,
)
from researchos.tools.human_gate import CLIHumanInterface


_CANDIDATE_BOUND_T45_ARTIFACTS = {
    "ideation/novelty_audit_fingerprints.json",
    "ideation/research_dossier.json",
    "ideation/contribution_hypothesis_map.yaml",
    "ideation/validation_map.yaml",
    "ideation/kill_criteria.yaml",
}


def _write_artifact(workspace: Path, relative: str, content: str = "artifact") -> None:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_parallel_track_snapshot_contains_all_candidate_bound_t45_artifacts(tmp_path: Path) -> None:
    for relative in TRACK_ARTIFACTS:
        if relative.endswith(("/selected", "/_mechanism_tuples", "/_design_rationale_tuples", "/proposal")):
            (tmp_path / relative).mkdir(parents=True, exist_ok=True)
        else:
            _write_artifact(tmp_path, relative)
    _write_artifact(tmp_path, "ideation/proposal/research_proposal.md", "proposal")
    _write_artifact(tmp_path, "ideation/selected/selected_candidate.json", '{"candidate_id":"C1"}')
    _write_artifact(tmp_path, "ideation/proposal/proposal_manifest.json", '{"candidate_id":"C1"}')
    manifest = create_manifest(
        tmp_path,
        candidate_ids=["C1", "C2"],
        population_id="P1",
        directive_path="ideation/human_directives/parallel.json",
        source="copilot",
    )

    manifest = snapshot_active_track(tmp_path, manifest)
    first_track = tmp_path / "ideation/proposal_portfolio/tracks/C1/artifacts"
    for relative in _CANDIDATE_BOUND_T45_ARTIFACTS:
        assert (first_track / relative).is_file(), relative

    assert activate_next_track(tmp_path, manifest) == "C2"
    # The next track starts with a clean candidate-bound projection.  Nothing
    # from C1 may remain at the shared active path to be mistaken for C2.
    for relative in TRACK_ARTIFACTS:
        assert not (tmp_path / relative).exists(), relative


def test_new_gate1_selection_archives_novelty_fingerprint_with_t45_sources(tmp_path: Path) -> None:
    selected = tmp_path / "ideation/selected/selected_candidate.json"
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(
        json.dumps({"candidate_id": "C1", "selection_fingerprint": "fp-C1"}),
        encoding="utf-8",
    )
    _write_artifact(tmp_path, "ideation/novelty_audit_fingerprints.json", '{"candidate":"C1"}')
    _write_artifact(tmp_path, "ideation/research_blueprint.yaml", "candidate: C1")

    archive = reset_t45_artifacts_for_new_selection(
        tmp_path,
        candidate_id="C2",
        selection_fingerprint="fp-C2",
    )
    archived_root = tmp_path / str(archive["archive"]["root"])
    assert (archived_root / "ideation/novelty_audit_fingerprints.json").is_file()
    assert (archived_root / "ideation/research_blueprint.yaml").is_file()
    assert not (tmp_path / "ideation/novelty_audit_fingerprints.json").exists()


def test_materialization_backfills_legacy_track_and_never_uses_active_projection(tmp_path: Path) -> None:
    manifest = create_manifest(
        tmp_path,
        candidate_ids=["C1", "C2"],
        population_id="P1",
        directive_path="parallel.json",
        source="copilot",
    )
    first_root = tmp_path / "ideation/proposal_portfolio/tracks/C1/artifacts"
    for relative in TRACK_REQUIRED_ARTIFACTS:
        _write_artifact(first_root, relative, "C1")
    # Emulate a pre-fix track: the four derived files existed only in the
    # selection archive, while the active projection contains C2 content.
    archive_root = tmp_path / "ideation/t45_selection_history/legacy_C1"
    for relative in (
        "ideation/research_dossier.json",
        "ideation/contribution_hypothesis_map.yaml",
        "ideation/validation_map.yaml",
        "ideation/kill_criteria.yaml",
    ):
        _write_artifact(archive_root, relative, "C1 archive")
        (first_root / relative).unlink()
    isolation = first_root / "ideation/t45_selection_isolation.json"
    isolation.write_text(
        json.dumps({"candidate_id": "C1", "archive": {"root": "ideation/t45_selection_history/legacy_C1"}}),
        encoding="utf-8",
    )
    for relative in TRACK_ARTIFACTS:
        if relative in {
            "ideation/research_dossier.json",
            "ideation/contribution_hypothesis_map.yaml",
            "ideation/validation_map.yaml",
            "ideation/kill_criteria.yaml",
        }:
            continue
        if relative.endswith(("/selected", "/_mechanism_tuples", "/_design_rationale_tuples", "/proposal")):
            (tmp_path / relative).mkdir(parents=True, exist_ok=True)
        else:
            _write_artifact(tmp_path, relative, "C2 active")

    manifest["tracks"][0]["status"] = "ready_for_t5_selection"
    manifest["tracks"][1]["status"] = "ready_for_t5_selection"
    backfill_historical_track_artifacts(tmp_path, manifest)
    selection = materialize_selected_track(
        tmp_path,
        manifest,
        "C1",
        selection_reason="single_completed_track_auto_selected",
    )
    assert selection["selection_reason"] == "single_completed_track_auto_selected"
    for relative in TRACK_REQUIRED_ARTIFACTS:
            assert (tmp_path / relative).read_text(encoding="utf-8") == "C1" or relative in {
                "ideation/research_dossier.json",
                "ideation/contribution_hypothesis_map.yaml",
                "ideation/validation_map.yaml",
                "ideation/kill_criteria.yaml",
                "ideation/t45_selection_isolation.json",
            }
    for relative in (
        "ideation/research_dossier.json",
        "ideation/contribution_hypothesis_map.yaml",
        "ideation/validation_map.yaml",
        "ideation/kill_criteria.yaml",
    ):
        assert (tmp_path / relative).read_text(encoding="utf-8") == "C1 archive"


def test_ready_tracks_have_stable_display_aliases_and_resolve_to_independent_ids(tmp_path: Path) -> None:
    manifest = create_manifest(
        tmp_path,
        candidate_ids=["EVO-EP1-C1-001", "CSB-S1"],
        population_id="P1",
        directive_path="parallel.json",
        source="copilot",
    )
    for track, marker in zip(manifest["tracks"], ("first", "second")):
        track["status"] = "ready_for_t5_selection"
        track["copied_artifacts"] = list(TRACK_REQUIRED_ARTIFACTS)
        track["title"] = marker

    rendered = overview(manifest, {"EVO-EP1-C1-001": "First", "CSB-S1": "Second"})
    assert [item["display_id"] for item in rendered["tracks"]] == ["D1", "D2"]
    assert resolve_ready_track_selection(manifest, "D1") == "EVO-EP1-C1-001"
    assert resolve_ready_track_selection(manifest, "d2") == "CSB-S1"
    assert resolve_ready_track_selection(manifest, "CSB-S1") == "CSB-S1"


def test_snapshot_fails_before_deleting_active_projection_when_required_artifact_is_missing(tmp_path: Path) -> None:
    manifest = create_manifest(
        tmp_path,
        candidate_ids=["C1"],
        population_id="P1",
        directive_path="parallel.json",
        source="copilot",
    )
    _write_artifact(tmp_path, "ideation/proposal/research_proposal.md", "active proposal")
    try:
        snapshot_active_track(tmp_path, manifest)
    except ValueError as exc:
        assert "candidate-bound T4.5 artifacts" in str(exc)
    else:
        raise AssertionError("snapshot should fail closed when the T4.5 package is incomplete")
    assert (tmp_path / "ideation/proposal/research_proposal.md").read_text(encoding="utf-8") == "active proposal"
    assert not (tmp_path / "ideation/proposal_portfolio/tracks/C1/artifacts").exists()


def test_t45_gate_accepts_display_aliases_and_full_candidate_ids_but_not_menu_numbers() -> None:
    options = [{"id": "select_proposal"}, {"id": "pause_selection"}]
    assert CLIHumanInterface._parse_inline_gate_customization("t45_proposal_portfolio_gate", "D1", options)["captured"] == {"candidate_id": "D1"}
    assert CLIHumanInterface._parse_inline_gate_customization("t45_proposal_portfolio_gate", "CSB-S1", options)["captured"] == {"candidate_id": "CSB-S1"}
    assert CLIHumanInterface._parse_inline_gate_customization("t45_proposal_portfolio_gate", "2", options) is None


def test_state_machine_skips_portfolio_gate_for_one_completed_track(tmp_path: Path) -> None:
    for relative in TRACK_ARTIFACTS:
        path = tmp_path / relative
        if any(required.startswith(relative.rstrip("/") + "/") for required in TRACK_REQUIRED_ARTIFACTS):
            path.mkdir(parents=True, exist_ok=True)
        else:
            _write_artifact(tmp_path, relative)
    _write_artifact(tmp_path, "ideation/selected/selected_candidate.json", '{"candidate_id":"C1"}')
    _write_artifact(tmp_path, "ideation/proposal/proposal_manifest.json", '{"candidate_id":"C1"}')
    _write_artifact(tmp_path, "ideation/proposal/research_proposal.md", "C1 proposal")
    manifest = create_manifest(
        tmp_path,
        candidate_ids=["C1"],
        population_id="P1",
        directive_path="parallel.json",
        source="copilot",
    )
    machine = StateMachine(
        Path(__file__).parents[2] / "config/system_config/state_machine.yaml",
        Path(__file__).parents[2] / "config/system_config/gates.yaml",
    )
    state = StateYaml(project_id="one-track", current_task="T4.5-REVIEW")
    state = machine._advance_t45_proposal_portfolio(state, tmp_path)
    assert state is not None
    assert state.current_task == "T5-REBOOST-GATE"
    assert state.status == "RUNNING"
    selection = json.loads((tmp_path / "ideation/proposal_portfolio/selection.json").read_text(encoding="utf-8"))
    assert selection["selected_candidate_id"] == "C1"
    assert selection["selection_reason"] == "single_completed_track_auto_selected"
