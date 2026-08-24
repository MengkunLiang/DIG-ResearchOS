from __future__ import annotations

import json
from pathlib import Path

from researchos.ideation.formalization import reset_t45_artifacts_for_new_selection
from researchos.ideation.proposal_portfolio import (
    TRACK_ARTIFACTS,
    TRACK_REQUIRED_ARTIFACTS,
    activate_next_track,
    backfill_historical_track_artifacts,
    create_manifest,
    materialize_selected_track,
    snapshot_active_track,
)


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
    materialize_selected_track(tmp_path, manifest, "C1")
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
