"""Project validated evolutionary artifacts into the retained Gate1 contract.

Projection is structural only: every display sentence originates in an
LLM-authored CandidatePresentation or ScoreReport compatibility rationale.
Missing compatibility prose fails closed instead of being template-generated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..pydantic_compat import model_dump
from .models import CandidateDossier, PopulationSnapshot, ScoreReport
from .state import T4ArtifactStore


_LEGACY_SCORE_KEYS = ("novelty", "feasibility", "impact", "evaluability", "differentiation", "cost", "contribution_strength")


def project_gate1_population(
    workspace_dir: Path,
    *,
    population: PopulationSnapshot,
    dossiers: list[CandidateDossier],
    scores: list[ScoreReport],
) -> dict[str, Any]:
    """Write Pass1/Pass2/Gate1 projections for an evolved active population."""

    store = T4ArtifactStore(workspace_dir)
    score_by_id = {item.candidate_id: item for item in scores}
    dossier_by_id = {item.candidate_id: item for item in dossiers}
    active = [dossier_by_id[candidate_id] for candidate_id in population.active_candidate_ids if candidate_id in dossier_by_id]
    if len(active) < 4:
        raise ValueError("Gate1 projection requires at least four active candidates")
    candidates = [_legacy_candidate(item, score_by_id[item.candidate_id]) for item in active if item.candidate_id in score_by_id]
    if len(candidates) != len(active):
        raise ValueError("Gate1 projection requires an independent score for every active candidate")
    pass2 = [_pass2_review(candidate) for candidate in candidates]
    store.write_json("ideation/_pass1_forward_candidates.json", {"version": "4.0", "candidates": candidates})
    store.write_json("ideation/_pass2_grounding_review.json", {"version": "4.0", "reviews": pass2})
    store.write_json("ideation/_candidate_directions.json", {"version": "4.0", "candidates": candidates})
    _write_family_summary(store, candidates)
    _write_gate_cards(store, candidates)
    _write_gate_brief(store, candidates, population)
    return {"candidate_count": len(candidates), "candidate_ids": [item["id"] for item in candidates]}


def _legacy_candidate(dossier: CandidateDossier, score: ScoreReport) -> dict[str, Any]:
    presentation = dossier.presentation
    if presentation is None:
        raise ValueError(f"Candidate {dossier.candidate_id} lacks LLM-authored Gate1 presentation")
    if not 2 <= len(dossier.hypotheses) <= 3:
        raise ValueError(
            f"Candidate {dossier.candidate_id} requires 2-3 LLM-authored provisional hypotheses for Gate1 projection"
        )
    minimum_sources = 2 if presentation.constraint_status in {"mainline", "bridge"} else 1
    if len(presentation.basis_sources) < minimum_sources:
        raise ValueError(
            f"Candidate {dossier.candidate_id} requires {minimum_sources} LLM-authored basis sources for its constraint status"
        )
    missing_scores = [key for key in _LEGACY_SCORE_KEYS if key not in score.compatibility_scores or key not in score.compatibility_rationales]
    if missing_scores:
        raise ValueError(f"Candidate {dossier.candidate_id} lacks compatibility score fields: {', '.join(missing_scores)}")
    invalid_scores = [
        key
        for key in _LEGACY_SCORE_KEYS
        if not isinstance(score.compatibility_scores[key], int) or not 1 <= score.compatibility_scores[key] <= 5
    ]
    if invalid_scores:
        raise ValueError(
            f"Candidate {dossier.candidate_id} has invalid compatibility scores: {', '.join(invalid_scores)}"
        )
    missing_rationales = [key for key in _LEGACY_SCORE_KEYS if len(" ".join(score.compatibility_rationales[key].split())) < 18]
    if missing_rationales:
        raise ValueError(
            f"Candidate {dossier.candidate_id} lacks substantive compatibility rationales: {', '.join(missing_rationales)}"
        )
    supporting = []
    for gene in (dossier.genome.problem, dossier.genome.mechanism, dossier.genome.validation_logic):
        for ref in gene.provenance.source_refs:
            supporting.append({"title": ref.paper_id or ref.source_path, "ref": ref.citation_key, "source_file": ref.source_path, "claim_used": ref.note, "evidence_level": ",".join(level.value for level in gene.provenance.reading_levels)})
    first_hypothesis = dossier.hypotheses[0] if dossier.hypotheses else None
    minimum = dict(presentation.minimum_validation)
    minimum.setdefault("evidence_status", "proposed_not_verified")
    minimum.setdefault("source_refs", [])
    cdr = {
        "problem_frame": str(dossier.genome.problem.value),
        "design_rationale": str(dossier.genome.design_or_artifact.value),
        "artifact": str(dossier.genome.design_or_artifact.value),
        "design_principles": [str(dossier.genome.design_or_artifact.value)],
        "data_view": str(dossier.genome.validation_logic.value),
        "evaluation_mode": str(dossier.genome.validation_logic.value),
        "contribution_type": str(dossier.contributions[0].contribution_type) if dossier.contributions else "improvement",
        "boundary_conditions": [str(dossier.genome.boundary_conditions.value)],
        "cross_paper_tension": [str(dossier.genome.opportunity.value)],
    }
    return {
        "id": dossier.candidate_id,
        "title": presentation.title,
        "display_title": presentation.display_title,
        "idea_origin": presentation.idea_origin,
        "constraint_status": presentation.constraint_status,
        "pitch": str(dossier.genome.core_thesis.value),
        "core_claim": str(dossier.genome.core_thesis.value),
        "target_problem": str(dossier.genome.problem.value),
        "mechanism": str(dossier.genome.mechanism.value),
        "prediction": first_hypothesis.observable_prediction if first_hypothesis else str(dossier.genome.hypothesis_bundle.value),
        "counterfactual": presentation.counterfactual,
        "practical_implication": presentation.practical_implication,
        "basis_summary": presentation.basis_summary,
        "basis_sources": presentation.basis_sources,
        "supporting_papers": supporting,
        "mechanism_family": presentation.mechanism_family,
        "cdr_tuple": cdr,
        "contribution_character": dossier.contributions[0].what_changes_if_true if dossier.contributions else "",
        "contribution_strength": score.compatibility_scores["contribution_strength"],
        "innovation": presentation.innovation,
        "candidate_hypotheses": [
            {"id": item.hypothesis_id, "statement": item.statement, "mechanism": item.mechanism, "observable_prediction": item.observable_prediction, "discriminating_test": item.discriminating_test, "evidence_status": item.evidence_status}
            for item in dossier.hypotheses[:3]
        ],
        "minimum_experiment": minimum,
        "scores": score.compatibility_scores,
        "score_rationale": score.compatibility_rationales,
        "gate1_card": presentation.gate1_card,
        "pass2_screening": {"visible_to_gate": True, "screening_recommendation": "proceed", "selection_warning": ""},
    }


def _pass2_review(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"idea_id": candidate["id"], "visible_to_gate": True, "screening_recommendation": "proceed", "counterfactual_check": "independent", "counterfactual_note": candidate["counterfactual"], "nearest_prior_work": {"work": "not_computed", "distance": "not_computed"}, "novelty_signal": "not_computed", "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "requires_t45_audit"}, "feasibility_check": {"feasible_under_budget": True, "blocking_risks": []}, "contribution_check": {"contribution_type": candidate["cdr_tuple"]["contribution_type"], "routine_risk": False, "reframe_needed": False, "why": candidate["innovation"]["non_incremental_reason"]}, "grounding_notes": [candidate["basis_summary"]], "selection_warning": ""}


def _write_family_summary(store: T4ArtifactStore, candidates: list[dict[str, Any]]) -> None:
    """Render an inventory of LLM-authored families without inventing science."""

    lines = [
        "# Idea Family Summary",
        "",
        "This view keeps every active candidate visible for comparison. Family labels, titles, and evidence summaries below were authored with the candidate; this file only groups the retained population.",
        "",
    ]
    for candidate in candidates:
        lines.extend(
            [
                f"## {candidate['id']} · {candidate['mechanism_family']}",
                candidate["display_title"],
                "",
                candidate["basis_summary"],
                "",
                f"- Origin: {candidate['idea_origin']}",
                f"- Evidence status: {candidate['minimum_experiment'].get('evidence_status', 'unknown')}",
                "",
            ]
        )
    store.path("ideation/_family_distribution.md").parent.mkdir(parents=True, exist_ok=True)
    store.path("ideation/_family_distribution.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_gate_cards(store: T4ArtifactStore, candidates: list[dict[str, Any]]) -> None:
    lines = ["<!-- ResearchOS Gate1 candidate-card schema: v3 -->", "# Research Idea Candidates", ""]
    for candidate in candidates:
        card = candidate["gate1_card"]
        lines.extend(
            [
                f"## {candidate['id']} · {candidate['display_title']}",
                candidate["title"],
                "",
                f"**Why it is here:** {card['role_summary']}",
                f"**Technical mechanism:** {candidate['mechanism']}",
                f"**Evidence interpretation:** {card['evidence_interpretation']}",
                f"**Practical implication:** {candidate['practical_implication']}",
                f"**Recommended action:** {card['selection_advice']}",
                f"**Risk / kill criteria:** {card['risk_summary']} {candidate['counterfactual']}",
                f"**Editable scope:** {card['user_edit_hint']}",
                "",
                "### Score rationale",
            ]
        )
        for key, value in candidate["scores"].items():
            lines.append(f"- **{key} ({value}/5):** {candidate['score_rationale'][key]}")
        lines.extend(["", "### Core paper dependencies"])
        if candidate["basis_sources"]:
            for source in candidate["basis_sources"]:
                lines.append(f"- **{source['ref']}:** {source['claim']} Implication: {source['implication']}")
        else:
            lines.append("- No source reference was retained in this candidate; treat its evidence boundary as unresolved.")
        lines.extend(
            [
                "",
                "### Candidate hypotheses",
            ]
        )
        for hypothesis in candidate["candidate_hypotheses"]:
            lines.append(f"- **{hypothesis['id']}:** {hypothesis['statement']}")
        lines.extend(
            [
                "",
                "### Related files",
                "- Machine-readable candidate details: `ideation/_candidate_directions.json`",
                "- Pass2 review and warnings: `ideation/_pass2_grounding_review.json`",
                "",
            ]
        )
    store.path("ideation/_gate1_candidate_cards.md").write_text("\n".join(lines), encoding="utf-8")


def _write_gate_brief(store: T4ArtifactStore, candidates: list[dict[str, Any]], population: PopulationSnapshot) -> None:
    origin_counts: dict[str, int] = {}
    for candidate in candidates:
        origin = candidate["idea_origin"]
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    lines = [
        "# T4 Gate1 Selection Brief",
        "",
        f"Generation: {population.generation}",
        f"Active candidates: {len(candidates)}",
        "",
        "Select one complete candidate to prepare a pre-novelty hypothesis brief. A selection preserves the other candidates and can be rolled back. Keep candidates parallel when their mechanisms differ; request a composition only when you intentionally want a new compatibility-checked candidate.",
        "",
        "## Population concentration",
        "Candidates remain visible even when they belong to a similar Idea Family, so the comparison is not silently narrowed before your decision.",
        "",
        "## Origin distribution",
    ]
    for origin, count in sorted(origin_counts.items()):
        lines.append(f"- {origin}: {count}")
    lines.extend(["", "## Novelty-Utility layout", "Use the score rationale and each candidate's stated risk to compare higher-upside alternatives with candidates that have a clearer validation path. These are decision aids, not novelty conclusions.", "", "## Candidates"])
    for candidate in candidates:
        lines.append(f"- **{candidate['id']}**: {candidate['display_title']} - {candidate['gate1_card']['selection_advice']}")
    lines.extend(
        [
            "",
            "## Available actions",
            "- Select a full candidate when its mechanism, evidence boundary, and validation scope fit the project.",
            "- Continue one evolution round to retain this version and produce additional Mutation Child or compatibility-gated Crossover Child candidates.",
            "- Keep multiple candidates parallel when they answer distinct questions.",
            "- Combine D1+D2 only as an explicit request. ResearchOS will create a new Human-composed Candidate, run a Compatibility Check and independent scoring, then ask for confirmation; it never concatenates candidates directly.",
            "",
            "Detailed candidate material is in `ideation/_gate1_candidate_cards.md`; structured population data is in `ideation/_candidate_directions.json` and Pass2 warnings are in `ideation/_pass2_grounding_review.json`.",
        ]
    )
    store.path("ideation/_gate1_selection_brief.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
