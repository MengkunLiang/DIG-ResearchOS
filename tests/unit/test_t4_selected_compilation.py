from __future__ import annotations

import yaml

from researchos.ideation.legacy_projection import project_gate1_population
from researchos.ideation.selected_compilation import (
    compile_pre_novelty_hypothesis_brief,
    selected_candidate_id_from_gate_input,
)
from tests.unit.test_t4_legacy_projection import _ready_projection_inputs


def test_selected_candidate_compiles_pre_novelty_artifacts_without_finalizing_claims(tmp_path):
    dossiers, scores, population = _ready_projection_inputs()
    project_gate1_population(tmp_path, population=population, dossiers=dossiers, scores=scores)

    selected = selected_candidate_id_from_gate_input(tmp_path, {"selection": "Use I1 for the novelty review"})
    assert selected == "I1"
    outputs = compile_pre_novelty_hypothesis_brief(
        tmp_path,
        selection_fingerprint="f" * 64,
        selected_candidate_id=selected,
    )

    assert set(outputs) == {"selected_candidate", "hypothesis_brief", "hypothesis_lineage", "search_targets", "brief"}
    brief = yaml.safe_load((tmp_path / "ideation/hypothesis_brief.yaml").read_text(encoding="utf-8"))
    assert brief["status"] == "draft_for_novelty_review"
    assert brief["candidate_id"] == "I1"
    assert len(brief["draft_hypotheses"]) == 2
    assert not (tmp_path / "ideation/exp_plan.yaml").exists()
