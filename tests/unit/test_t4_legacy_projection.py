from __future__ import annotations

import pytest

from researchos.agents.ideation import validate_t4_gate1_ready
from researchos.ideation.legacy_projection import project_gate1_population
from researchos.ideation.models import BridgeCoverageEntry, CandidatePresentation, PopulationSnapshot, RouteGenerationResult
from tests.unit.test_t4_evolution_controller import _candidate
from tests.unit.test_t4_population_evolution import FINGERPRINT, _score


def _presentation(index: int, origin: str) -> CandidatePresentation:
    text = f"Candidate {index} uses a bounded evidence-backed mechanism and an explicit discriminating validation design"
    return CandidatePresentation(
        title=text + " for the active research population.",
        display_title=f"Bounded candidate {index}",
        basis_summary=(text + ". The available paper notes motivate the mechanism boundary, while the proposed control tests whether the signal remains after the active mechanism is disabled."),
        practical_implication="The decision process can target the documented condition while preserving an explicit failure boundary.",
        counterfactual="If the disabling control preserves the effect, the stated mechanism must be rejected rather than presented as established.",
        gate1_card={
            "role_summary": "This candidate keeps one coherent mechanism and makes its boundary falsifiable before downstream novelty auditing.",
            "evidence_interpretation": "The cited observations support an opportunity and a bounded mechanism hypothesis, not an external novelty conclusion.",
            "selection_advice": "Select this option when the stated control can be implemented with the available project constraints and evidence boundary.",
            "risk_summary": "A non-specific control effect or missing source upgrade would require reframing before final hypothesis compilation.",
            "user_edit_hint": "Keep the mechanism and narrow the target condition if a stricter validation scope is needed.",
        },
        basis_sources=[
            {"ref": "paper-note-1", "claim": "The reading notes identify a recurring boundary and an unresolved mechanism explanation in the observed setting.", "implication": "The candidate turns that observation into a discriminating mechanism and validation design."},
            {"ref": "paper-note-2", "claim": "The available evidence records a limitation that the current baseline does not separate from the proposed mechanism.", "implication": "The candidate retains a disabling control so the explanation can be falsified."},
        ],
        innovation={
            "summary": "Convert a recurring limitation into a mechanism-bound validation design.",
            "type": "mechanism",
            "novelty_delta": "The candidate requires a discriminating control rather than reporting an undifferentiated improvement.",
            "non_incremental_reason": "Its contribution changes the evidence needed for the claim, not merely a module setting.",
        },
        minimum_validation={"dataset": "workspace-defined task", "baseline": "documented baseline", "metric": "predeclared primary metric", "expected_signal": "the effect weakens when the mechanism is disabled", "evidence_status": "proposed_not_verified", "source_refs": []},
        idea_origin=origin,
        constraint_status="mainline",
        mechanism_family="bounded-mechanism-validation",
    )


def _ready_projection_inputs():
    dossiers = []
    for index, origin in enumerate(["evidence_driven", "informed_brainstorm", "cross_domain_analogy", "problem_reframing"], start=1):
        dossier = _candidate(f"I{index}", "fixture_route", mechanism=f"Fixture mechanism {index}")
        dossiers.append(dossier.model_copy(update={"presentation": _presentation(index, origin)}))
    scores = []
    for index, dossier in enumerate(dossiers, start=1):
        score = _score(dossier.candidate_id, 4.0)
        keys = ["novelty", "feasibility", "impact", "evaluability", "differentiation", "cost", "contribution_strength"]
        scores.append(
            score.model_copy(
                update={
                    "compatibility_scores": {key: 4 for key in keys},
                    "compatibility_rationales": {
                        key: f"The {key} assessment for candidate {index} follows its distinct mechanism, evidence boundary, and validation design."
                        for key in keys
                    },
                }
            )
        )
    population = PopulationSnapshot(
        population_id="P1",
        generation=1,
        input_fingerprint=FINGERPRINT,
        run_config_fingerprint=FINGERPRINT,
        active_candidate_ids=[item.candidate_id for item in dossiers],
    )
    return dossiers, scores, population


def test_evolved_population_projects_to_existing_gate1_validator(tmp_path):
    dossiers, scores, population = _ready_projection_inputs()
    result = project_gate1_population(tmp_path, population=population, dossiers=dossiers, scores=scores)
    assert result["candidate_count"] == 4
    ok, error = validate_t4_gate1_ready(tmp_path)
    assert ok, error


def test_projection_fails_closed_without_llm_presentation(tmp_path):
    dossiers, scores, population = _ready_projection_inputs()
    dossiers[0] = dossiers[0].model_copy(update={"presentation": None})

    with pytest.raises(ValueError, match="LLM-authored Gate1 presentation"):
        project_gate1_population(tmp_path, population=population, dossiers=dossiers, scores=scores)


def test_projection_fails_closed_without_complete_compatibility_score(tmp_path):
    dossiers, scores, population = _ready_projection_inputs()
    score = scores[0]
    compatibility_scores = dict(score.compatibility_scores)
    compatibility_scores.pop("novelty")
    scores[0] = score.model_copy(update={"compatibility_scores": compatibility_scores})

    with pytest.raises(ValueError, match="compatibility score fields: novelty"):
        project_gate1_population(tmp_path, population=population, dossiers=dossiers, scores=scores)


def test_projection_fails_closed_with_only_one_hypothesis(tmp_path):
    dossiers, scores, population = _ready_projection_inputs()
    dossiers[0] = dossiers[0].model_copy(update={"hypotheses": dossiers[0].hypotheses[:1]})

    with pytest.raises(ValueError, match="requires 2-3 LLM-authored provisional hypotheses"):
        project_gate1_population(tmp_path, population=population, dossiers=dossiers, scores=scores)


def test_projection_preserves_bridge_visibility_and_escape_hatch(tmp_path):
    dossiers, scores, population = _ready_projection_inputs()
    bridge_presentation = dossiers[2].presentation.model_copy(
        update={
            "idea_origin": "bridge_synthesis",
            "constraint_status": "bridge",
            "cross_domain_sources": ["B1"],
            "cross_domain_relation": "mechanism_bridge",
        }
    )
    dossiers[2] = dossiers[2].model_copy(update={"presentation": bridge_presentation})
    (tmp_path / "literature").mkdir()
    (tmp_path / "literature" / "bridge_domain_plan.json").write_text(
        '{"source":"user_confirmed","bridge_domains":[{"bridge_id":"B1","priority":"must_explore"}]}\n',
        encoding="utf-8",
    )
    route_results = [
        RouteGenerationResult(
            route="cross_domain_bridge",
            status="supported",
            candidate_ids=["I3"],
            bridge_reviews=[
                BridgeCoverageEntry(
                    bridge_id="B1",
                    candidate_ids=["I3"],
                    visible_to_gate=True,
                    decision_summary="The bridge candidate is visible because it supplies a bounded transferable mechanism with an explicit validation path.",
                    escape_status="deferred",
                    escape_reason="The bridge remains conditional on the stated evidence boundary and must not be upgraded to a final mechanism claim.",
                    falsification_or_kill_criteria="Drop the bridge if the transfer control cannot distinguish the proposed mechanism from the source-domain artifact.",
                    can_revisit_if="Revisit when an additional bridge reading note or a compatible validation resource becomes available.",
                )
            ],
        )
    ]

    project_gate1_population(
        tmp_path,
        population=population,
        dossiers=dossiers,
        scores=scores,
        route_results=route_results,
    )

    ok, error = validate_t4_gate1_ready(tmp_path)
    assert ok, error
    bridge_review = (tmp_path / "ideation" / "bridge_coverage_review.json").read_text(encoding="utf-8")
    assert '"bridge_id": "B1"' in bridge_review
    assert '"candidate_ids": [' in bridge_review


def test_projection_fails_closed_when_confirmed_bridge_has_no_route_review(tmp_path):
    dossiers, scores, population = _ready_projection_inputs()
    (tmp_path / "literature").mkdir()
    (tmp_path / "literature" / "bridge_domain_plan.json").write_text(
        '{"source":"user_confirmed","bridge_domains":[{"bridge_id":"B1","priority":"must_explore"}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="did not provide an LLM-authored Bridge review for B1"):
        project_gate1_population(tmp_path, population=population, dossiers=dossiers, scores=scores, route_results=[])
