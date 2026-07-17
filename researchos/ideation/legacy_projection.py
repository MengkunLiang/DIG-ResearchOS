"""Project validated evolutionary artifacts into the retained Gate1 contract.

Projection is structural only: every research-bearing display sentence
originates in an LLM-authored CandidatePresentation or ScoreReport
compatibility rationale.  Operational recovery receipts can record that an
optional enrichment review was unavailable, but never manufacture its
scientific prose.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..pydantic_compat import model_dump
from .models import CandidateMaturity, BridgeCoverageEntry, CandidateDossier, FinalIdeaCardTranslation, PopulationSnapshot, ScoreReport
from .selected_compilation import validate_candidate_selection_ready
from .state import T4ArtifactStore


_LEGACY_SCORE_KEYS = ("novelty", "feasibility", "impact", "evaluability", "differentiation", "cost", "contribution_strength")
_CROSS_DOMAIN_RELATIONS = frozenset(
    {
        "mechanism_bridge",
        "method_transfer",
        "evaluation_or_metric_bridge",
        "baseline_or_dataset_relevance",
        "adjacent_application",
    }
)
_EXPLANATION_PLACEHOLDER_RE = re.compile(
    r"^(?:\.{2,}|…+|unknown|n/?a|none|tbd|todo|待(?:补充|确定|核验)|未提供|未标注)$",
    flags=re.IGNORECASE,
)


def _validated_final_card_payload(value: Any) -> dict[str, Any] | None:
    """Return a complete LLM card payload or no presentation at all.

    The compatibility projection keeps its old ``gate1_card`` field for
    machine consumers, but no Markdown artifact may treat that field as a
    substitute for the Portfolio Final Card.  Validating here also protects a
    manually edited or stale ``_candidate_directions.json`` from producing a
    plausible-looking partial card outside the normal Gate1 readiness check.
    """

    if not isinstance(value, dict):
        return None
    try:
        return model_dump(FinalIdeaCardTranslation.model_validate(value), mode="json")
    except (TypeError, ValueError):
        return None


def _current_portfolio_candidate_ids(store: T4ArtifactStore) -> list[str] | None:
    """Read current Portfolio membership without deriving a replacement deck.

    ``None`` means this is an older structural projection without a Portfolio
    artifact.  The caller may still record an operational recovery receipt,
    but it must not guess which active Candidate deserves a human-facing card.
    """

    try:
        raw = json.loads(store.path("ideation/portfolio.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    ids = [
        str(candidate_id).strip()
        for candidate_id in (
            raw.get("lead_id"),
            *(raw.get("alternative_ids") if isinstance(raw.get("alternative_ids"), list) else []),
            *(raw.get("high_upside_ids") if isinstance(raw.get("high_upside_ids"), list) else []),
        )
        if str(candidate_id or "").strip()
    ]
    return list(dict.fromkeys(ids))


def project_gate1_population(
    workspace_dir: Path,
    *,
    population: PopulationSnapshot,
    dossiers: list[CandidateDossier],
    scores: list[ScoreReport],
    route_results: list[object] | None = None,
) -> dict[str, Any]:
    """Write Pass1/Pass2/Gate1 projections for an evolved active population."""

    store = T4ArtifactStore(workspace_dir)
    score_by_id = {item.candidate_id: item for item in scores}
    dossier_by_id = {item.candidate_id: item for item in dossiers}
    final_cards, final_card_diagnostics = _load_final_cards(store)
    active = [dossier_by_id[candidate_id] for candidate_id in population.active_candidate_ids if candidate_id in dossier_by_id]
    if not active:
        raise ValueError("Gate1 projection requires at least one active candidate")
    missing_active_ids = [
        candidate_id
        for candidate_id in population.active_candidate_ids
        if candidate_id not in dossier_by_id
    ]
    candidates: list[dict[str, Any]] = []
    candidate_degradations: list[dict[str, str]] = []
    for dossier in active:
        candidate_id = dossier.candidate_id
        try:
            candidate = _legacy_candidate(
                dossier,
                score_by_id.get(candidate_id),
                final_cards.get(candidate_id),
            )
        except (TypeError, ValueError, IndexError, KeyError) as exc:
            # Card prose, a legacy compatibility score, or an under-enriched
            # Candidate is not allowed to erase an otherwise traceable active
            # research object.  The degraded projection copies only durable
            # Candidate fields and surfaces the missing presentation work to
            # the researcher; it does not synthesize a hypothesis, source,
            # score, or recommendation.
            diagnostic = _projection_diagnostic(str(exc))
            candidate = _degraded_legacy_candidate(
                dossier,
                score_by_id.get(candidate_id),
                final_cards.get(candidate_id),
                diagnostic=diagnostic,
            )
            candidate_degradations.append(
                {
                    "candidate_id": candidate_id,
                    "scope": "candidate_projection",
                    "reason": diagnostic,
                }
            )
        final_card_diagnostic = final_card_diagnostics.get(candidate_id)
        if final_card_diagnostic:
            candidate["final_card_status"] = "degraded"
            candidate["final_card_diagnostic"] = final_card_diagnostic
            candidate_degradations.append(
                {
                    "candidate_id": candidate_id,
                    "scope": "final_card",
                    "reason": final_card_diagnostic,
                }
            )
        else:
            candidate["final_card_status"] = "completed" if candidate.get("final_idea_card") else "unavailable"
        # These are controller-owned traceability facts, not card prose.  They
        # make the Rich view self-contained without asking a researcher to
        # reverse-engineer an internal ID or search for the current snapshot.
        candidate["lineage"] = model_dump(dossier.lineage, mode="json")
        candidate["artifact_index"] = _candidate_artifact_index(
            dossier,
            population=population,
            scored=score_by_id.get(candidate_id) is not None,
        )
        # ``degraded`` describes Candidate maturity or score availability.  It
        # is not synonymous with a failed Gate1 projection: an exploratory
        # IdeaSeed can be projected faithfully from its typed, LLM-authored
        # fields and should remain visible at Gate1.  Reserve
        # ``projection_status=degraded`` for the exception path above, where a
        # presentation field truly could not be projected and a diagnostic is
        # recorded.  Conflating the two made normal Seeds fail the Gate1
        # validator merely because their optional final-card enrichment was
        # deferred.
        candidate.setdefault("projection_status", "complete")
        candidate.setdefault("projection_diagnostics", [])
        candidates.append(candidate)
    # Keep the embedded Candidate view and the standalone Pass2 artifact in
    # sync.  Earlier versions computed the review separately, leaving a
    # stale ``pass2_screening=proceed`` on a candidate whose projected
    # constraint status had already become ``not_supported_by_current_evidence``.
    # That inconsistency then looked like a hard integrity error during Gate1
    # validation and paused the entire evolution run.
    pass2: list[dict[str, Any]] = []
    for candidate in candidates:
        review = _pass2_review(candidate)
        candidate["pass2_screening"] = {
            "visible_to_gate": review["visible_to_gate"],
            "screening_recommendation": review["screening_recommendation"],
            "selection_warning": review["selection_warning"],
        }
        pass2.append(review)
    store.write_json("ideation/_pass1_forward_candidates.json", {"version": "4.0", "candidates": candidates})
    store.write_json("ideation/_pass2_grounding_review.json", {"version": "4.0", "reviews": pass2})
    store.write_json("ideation/_candidate_directions.json", {"version": "4.0", "candidates": candidates})
    _write_family_summary(store, candidates)
    _write_gate_cards(store, candidates)
    _write_gate_brief(store, candidates, population)
    bridge_coverage = _write_bridge_coverage_review(store, candidates, route_results or [])
    return {
        "candidate_count": len(candidates),
        "candidate_ids": [item["id"] for item in candidates],
        # This is operational metadata, not a scientific judgement.  It lets
        # the runtime distinguish a completed Candidate Population from an
        # optional Cross-domain review that needs another model pass.
        "degradations": bridge_coverage["degradations"],
        "candidate_degradations": candidate_degradations,
        "missing_active_candidate_ids": missing_active_ids,
    }


def _legacy_candidate(
    dossier: CandidateDossier,
    score: ScoreReport | None,
    final_card: FinalIdeaCardTranslation | None = None,
) -> dict[str, Any]:
    presentation = dossier.presentation
    is_seed = dossier.maturity == CandidateMaturity.SEED
    # Legacy projection is a best-effort compatibility export. New native T4
    # scores intentionally contain only three formal scientific dimensions,
    # and an evolved Candidate may still be awaiting optional card prose.
    # Neither condition can invalidate the canonical Candidate or prevent
    # Gate1. Reuse the typed Seed view when an LLM presentation is absent; it
    # only copies canonical Candidate fields and leaves missing interpretation
    # visible for later enrichment.
    supporting = []
    for gene in (dossier.genome.problem, dossier.genome.mechanism, dossier.genome.validation_logic):
        for ref in gene.provenance.source_refs:
            supporting.append({"title": ref.paper_id or ref.source_path, "ref": ref.citation_key, "source_file": ref.source_path, "claim_used": ref.note, "evidence_level": ",".join(level.value for level in gene.provenance.reading_levels)})
    first_hypothesis = dossier.hypotheses[0] if dossier.hypotheses else None
    seed_view = _seed_gate_view(dossier, supporting) if presentation is None else None
    minimum = dict(seed_view["minimum_validation"] if seed_view is not None else presentation.minimum_validation)
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
        "internal_id": dossier.candidate_id,
        "version": dossier.version,
        "maturity": dossier.maturity.value,
        "candidate_stage": (
            "idea_seed" if dossier.maturity == CandidateMaturity.SEED else "evolved_candidate"
        ),
        "contribution_type": str(dossier.contributions[0].contribution_type) if dossier.contributions else "",
        "candidate_status": dossier.status.value,
        "parent_ids": list(dossier.genome.parents),
        "title": seed_view["title"] if seed_view is not None else presentation.title,
        "display_title": seed_view["display_title"] if seed_view is not None else presentation.display_title,
        "idea_origin": seed_view["idea_origin"] if seed_view is not None else presentation.idea_origin,
        "constraint_status": seed_view["constraint_status"] if seed_view is not None else presentation.constraint_status,
        "pitch": str(dossier.genome.core_thesis.value),
        "core_claim": str(dossier.genome.core_thesis.value),
        "target_problem": str(dossier.genome.problem.value),
        "mechanism": str(dossier.genome.mechanism.value),
        "prediction": first_hypothesis.observable_prediction if first_hypothesis else str(dossier.genome.hypothesis_bundle.value),
        "counterfactual": seed_view["counterfactual"] if seed_view is not None else presentation.counterfactual,
        "practical_implication": seed_view["practical_implication"] if seed_view is not None else presentation.practical_implication,
        "basis_summary": seed_view["basis_summary"] if seed_view is not None else presentation.basis_summary,
        "basis_sources": seed_view["basis_sources"] if seed_view is not None else presentation.basis_sources,
        "supporting_papers": supporting,
        "mechanism_family": seed_view["mechanism_family"] if seed_view is not None else presentation.mechanism_family,
        "cross_domain_sources": seed_view["cross_domain_sources"] if seed_view is not None else presentation.cross_domain_sources,
        "cross_domain_relation": _project_cross_domain_relation(seed_view["cross_domain_relation"] if seed_view is not None else presentation.cross_domain_relation),
        "cross_domain_relation_detail": seed_view["cross_domain_relation"] if seed_view is not None else presentation.cross_domain_relation,
        "cdr_tuple": cdr,
        "contribution_character": dossier.contributions[0].what_changes_if_true if dossier.contributions else "",
        "contributions": [
            {
                # Native Candidates may use local IDs such as C1 or IDs from a
                # previous authoring system. Gate1's compatibility artifacts
                # require IDs scoped to the Candidate, so preserve the source
                # value separately and expose a deterministic public ID.
                "id": f"{dossier.candidate_id}-C{index}",
                "source_contribution_id": item.contribution_id,
                "statement": item.statement,
                "type": str(item.contribution_type),
                "what_changes_if_true": item.what_changes_if_true,
            }
            for index, item in enumerate(dossier.contributions, start=1)
        ],
        "contribution_strength": score.compatibility_scores.get("contribution_strength") if score is not None else None,
        "creative_context": model_dump(dossier.creative_context, mode="json"),
        "innovation": seed_view["innovation"] if seed_view is not None else presentation.innovation,
        "candidate_hypotheses": [
            {
                # Keep the model-authored/local identifier for lineage while
                # giving the legacy Gate1 contract a globally traceable ID.
                "id": f"{dossier.candidate_id}-H{index}",
                "source_hypothesis_id": item.hypothesis_id,
                "statement": item.statement,
                "mechanism": item.mechanism,
                "observable_prediction": item.observable_prediction,
                "discriminating_test": item.discriminating_test,
                "evidence_status": item.evidence_status,
            }
            for index, item in enumerate(dossier.hypotheses[:4], start=1)
        ],
        "minimum_experiment": minimum,
        "scoring_status": "scored" if score is not None else "unscored",
        "unscored_reason": "" if score is not None else "独立评分在有界重试后仍不可用；该候选保持可见，但当前不参与自动排序。",
        # Historical compatibility grids are optional display metadata. The
        # native three-dimensional `evolution_score` below is authoritative.
        "scores": score.compatibility_scores if score is not None else {},
        "score_rationale": score.compatibility_rationales if score is not None else {},
        "evolution_score": (
            {
                "overall_readiness": score.overall_readiness,
                "uncertainty": score.score_uncertainty,
                "dimensions": model_dump(score.scores, mode="json"),
                "rationales": score.rationales,
                "dominant_strength": score.dominant_strength,
                "dominant_bottleneck": score.dominant_bottleneck,
                "scientific_upside": score.scientific_upside,
                "scientific_upside_rationale": score.scientific_upside_rationale,
                "evolution_potential": score.evolution_potential,
                "recommended_crossover_role": score.recommended_crossover_role,
                "wildcard_recommended": score.wildcard_recommended,
                "wildcard_rationale": score.wildcard_rationale,
                "diagnostics": model_dump(score.diagnostics, mode="json"),
                "rationale_missing": list(score.rationale_missing),
                "diagnostic_warnings": list(score.diagnostic_warnings),
                "profile_fit": model_dump(score.profile_fit, mode="json"),
            }
            if score is not None
            else {"status": "unscored"}
        ),
        "final_idea_card": model_dump(final_card, mode="json") if final_card is not None else None,
        "evidence_composition": dossier.evidence_composition,
        "artifact_paths": dossier.artifact_paths,
        "gate1_card": seed_view["gate1_card"] if seed_view is not None else presentation.gate1_card,
        "degraded": is_seed or score is None,
        "warnings": list(dossier.warnings),
        "pass2_screening": {
            "visible_to_gate": True,
            # A Candidate without a current supporting-note anchor may still
            # be a valuable conjectural direction at Gate1, but it is not
            # selection-ready.  Keep it visible and route it to evidence/
            # mechanism enrichment rather than emitting the internally
            # contradictory pair ``not_supported_by_current_evidence`` +
            # ``proceed``.  This is a state label, not a deterministic
            # scientific judgement or a replacement for LLM-authored advice.
            "screening_recommendation": (
                "revise_before_selection"
                if is_seed
                or score is None
                or (seed_view is not None and seed_view["constraint_status"] == "not_supported_by_current_evidence")
                else "proceed"
            ),
            "selection_warning": (
                "该 Idea Seed 尚需补充机制、证据或验证说明后才能进入最终选择。"
                if is_seed
                else "独立评分当前不可用；请先检查并补充后再作最终选择。"
                if score is None
                else "该候选当前属于探索性提议，尚无可追溯阅读笔记锚点；请补充证据或机制说明后再作最终选择。"
                if seed_view is not None and seed_view["constraint_status"] == "not_supported_by_current_evidence"
                else ""
            ),
        },
    }


def _degraded_legacy_candidate(
    dossier: CandidateDossier,
    score: ScoreReport | None,
    final_card: FinalIdeaCardTranslation | None,
    *,
    diagnostic: str,
) -> dict[str, Any]:
    """Expose a locally incomplete Candidate without manufacturing card prose.

    This path is deliberately narrower than normal projection.  It is used
    only after a typed Candidate Dossier already exists but optional Gate1
    enrichment, legacy score compatibility, or a card section is incomplete.
    Every research-bearing string below is copied from the Dossier; blanks are
    kept blank and accompanied by a machine-readable diagnostic so the UI can
    ask for focused LLM enrichment instead of filling a generic template.
    """

    supporting = _supporting_papers(dossier)
    hypotheses = _candidate_hypotheses(dossier)
    contributions = _candidate_contributions(dossier)
    first_hypothesis = dossier.hypotheses[0] if dossier.hypotheses else None
    first_contribution = dossier.contributions[0] if dossier.contributions else None
    creative = dossier.creative_context
    score_is_display_safe = _score_has_compatibility_view(score)
    evidence_status = (
        "proposed_not_verified"
        if first_hypothesis is not None
        else "unknown"
    )
    return {
        "id": dossier.candidate_id,
        "internal_id": dossier.candidate_id,
        "version": dossier.version,
        "maturity": dossier.maturity.value,
        "candidate_stage": "idea_seed" if dossier.maturity == CandidateMaturity.SEED else "evolved_candidate",
        "contribution_type": str(first_contribution.contribution_type) if first_contribution is not None else "",
        "candidate_status": dossier.status.value,
        "parent_ids": list(dossier.lineage.parent_ids),
        # The thesis is LLM-authored Candidate content.  It may be long, but
        # the renderer can abbreviate a heading without claiming it authored a
        # new scientific title.
        "title": str(dossier.genome.core_thesis.value),
        "display_title": str(dossier.genome.core_thesis.value),
        "idea_origin": dossier.lineage.route,
        "constraint_status": "presentation_degraded",
        "pitch": str(dossier.genome.core_thesis.value),
        "core_claim": str(dossier.genome.core_thesis.value),
        "target_problem": str(dossier.genome.problem.value),
        "mechanism": str(dossier.genome.mechanism.value),
        "prediction": first_hypothesis.observable_prediction if first_hypothesis is not None else "",
        "counterfactual": first_hypothesis.discriminating_test if first_hypothesis is not None else "",
        "practical_implication": first_contribution.what_changes_if_true if first_contribution is not None else "",
        "basis_summary": str(dossier.genome.opportunity.value),
        "basis_sources": [
            {
                "ref": str(item.get("ref") or item.get("title") or item.get("source_file") or ""),
                "claim": str(item.get("claim_used") or ""),
                # No deterministic prose is used as an implication.  A blank
                # value means the LLM needs to explain the source-to-design
                # connection in a later targeted enrichment pass.
                "implication": "",
            }
            for item in supporting
            if str(item.get("ref") or item.get("title") or item.get("source_file") or "").strip()
        ],
        "supporting_papers": supporting,
        "mechanism_family": "",
        "cross_domain_sources": [],
        "cross_domain_relation": "",
        "cross_domain_relation_detail": "",
        "cdr_tuple": {
            "problem_frame": str(dossier.genome.problem.value),
            "design_rationale": str(dossier.genome.design_or_artifact.value),
            "artifact": str(dossier.genome.design_or_artifact.value),
            "design_principles": [str(dossier.genome.design_or_artifact.value)],
            "data_view": str(dossier.genome.validation_logic.value),
            "evaluation_mode": str(dossier.genome.validation_logic.value),
            "contribution_type": str(first_contribution.contribution_type) if first_contribution is not None else "",
            "boundary_conditions": [str(dossier.genome.boundary_conditions.value)],
            "cross_paper_tension": [str(dossier.genome.opportunity.value)],
        },
        "contribution_character": first_contribution.what_changes_if_true if first_contribution is not None else "",
        "contributions": contributions,
        "contribution_strength": score.compatibility_scores.get("contribution_strength") if score_is_display_safe and score is not None else None,
        "creative_context": model_dump(creative, mode="json"),
        "innovation": {
            "summary": first_contribution.statement if first_contribution is not None else "",
            "type": str(first_contribution.contribution_type) if first_contribution is not None else "",
            "novelty_delta": creative.conceptual_leap,
            "non_incremental_reason": creative.surprising_prediction or creative.research_program_potential,
        },
        "candidate_hypotheses": hypotheses,
        "minimum_experiment": {
            "expected_signal": first_hypothesis.observable_prediction if first_hypothesis is not None else "",
            "evidence_status": evidence_status,
            "source_refs": [],
        },
        "scoring_status": "scored" if score_is_display_safe else "unscored",
        "unscored_reason": "" if score_is_display_safe else diagnostic,
        "scores": score.compatibility_scores if score_is_display_safe and score is not None else {},
        "score_rationale": score.compatibility_rationales if score_is_display_safe and score is not None else {},
        "evolution_score": (
            {
                "overall_readiness": score.overall_readiness,
                "uncertainty": score.score_uncertainty,
                "dimensions": model_dump(score.scores, mode="json"),
                "rationales": score.rationales,
                "dominant_strength": score.dominant_strength,
                "dominant_bottleneck": score.dominant_bottleneck,
                "scientific_upside": score.scientific_upside,
                "scientific_upside_rationale": score.scientific_upside_rationale,
                "evolution_potential": score.evolution_potential,
                "recommended_crossover_role": score.recommended_crossover_role,
                "wildcard_recommended": score.wildcard_recommended,
                "wildcard_rationale": score.wildcard_rationale,
                "diagnostics": model_dump(score.diagnostics, mode="json"),
                "rationale_missing": list(score.rationale_missing),
                "diagnostic_warnings": list(score.diagnostic_warnings),
                "profile_fit": model_dump(score.profile_fit, mode="json"),
            }
            if score_is_display_safe and score is not None
            else {"status": "unscored"}
        ),
        "final_idea_card": model_dump(final_card, mode="json") if final_card is not None else None,
        "evidence_composition": dossier.evidence_composition,
        "artifact_paths": list(dossier.artifact_paths),
        # Empty strings intentionally make the missing LLM enrichment visible;
        # they are not template-prose substitutions for scientific judgement.
        "gate1_card": {
            "role_summary": "",
            "evidence_interpretation": "",
            "selection_advice": "",
            "risk_summary": str(dossier.genome.risks.value),
            "user_edit_hint": "",
        },
        "degraded": True,
        "projection_status": "degraded",
        "projection_diagnostics": [diagnostic],
        "warnings": [*dossier.warnings, diagnostic],
        "pass2_screening": {
            "visible_to_gate": True,
            "screening_recommendation": "revise_before_selection",
            "selection_warning": diagnostic,
        },
    }


def _projection_diagnostic(value: str) -> str:
    """Return a bounded operational diagnostic without exposing raw model text."""

    text = " ".join(str(value or "").split())
    return text[:500] or "该候选的 Gate1 展示补充尚未完成；已保存的候选字段仍可供人工检查。"


def _supporting_papers(dossier: CandidateDossier) -> list[dict[str, str]]:
    supporting: list[dict[str, str]] = []
    for gene in (dossier.genome.problem, dossier.genome.mechanism, dossier.genome.validation_logic):
        for ref in gene.provenance.source_refs:
            supporting.append(
                {
                    "title": ref.paper_id or ref.source_path,
                    "ref": ref.citation_key,
                    "source_file": ref.source_path,
                    "claim_used": ref.note,
                    "evidence_level": ",".join(level.value for level in gene.provenance.reading_levels),
                }
            )
    return supporting


def _candidate_contributions(dossier: CandidateDossier) -> list[dict[str, str]]:
    return [
        {
            "id": f"{dossier.candidate_id}-C{index}",
            "source_contribution_id": item.contribution_id,
            "statement": item.statement,
            "type": str(item.contribution_type),
            "what_changes_if_true": item.what_changes_if_true,
        }
        for index, item in enumerate(dossier.contributions, start=1)
    ]


def _candidate_hypotheses(dossier: CandidateDossier) -> list[dict[str, str]]:
    return [
        {
            "id": f"{dossier.candidate_id}-H{index}",
            "source_hypothesis_id": item.hypothesis_id,
            "statement": item.statement,
            "mechanism": item.mechanism,
            "observable_prediction": item.observable_prediction,
            "discriminating_test": item.discriminating_test,
            "evidence_status": item.evidence_status,
        }
        for index, item in enumerate(dossier.hypotheses[:4], start=1)
    ]


def _score_has_compatibility_view(score: ScoreReport | None) -> bool:
    # The old compatibility grid is no longer a score contract. A typed
    # three-dimensional ScoreReport remains display-safe even when the legacy
    # fields are empty; renderers can show their absence as metadata.
    return score is not None


def _candidate_artifact_index(
    dossier: CandidateDossier,
    *,
    population: PopulationSnapshot,
    scored: bool,
) -> dict[str, str]:
    score_population = "P0" if population.generation == 0 else f"U{population.generation}"
    index = {
        "candidate": f"ideation/candidates/{dossier.candidate_id}.v{dossier.version}.json",
        "population": f"ideation/populations/{population.population_id}.json",
        "round": f"ideation/evolution/round_{population.generation}.json" if population.generation else "",
        "score": dossier.score_report_path or (f"ideation/scoring/{score_population}.json" if scored else ""),
        "lineage": f"ideation/candidates/{dossier.candidate_id}.v{dossier.version}.json",
    }
    return {key: value for key, value in index.items() if value}


def _seed_gate_view(dossier: CandidateDossier, supporting: list[dict[str, str]]) -> dict[str, Any]:
    """Expose a Seed structurally without inventing a missing scientific card.

    All research-bearing strings are direct Candidate fields. The fixed text
    only labels maturity and evidence limits for the researcher; it never
    turns a conjecture into an established result or makes a novelty claim.
    """

    first_hypothesis = dossier.hypotheses[0]
    contribution = dossier.contributions[0] if dossier.contributions else None
    thesis = str(dossier.genome.core_thesis.value)
    risk = str(dossier.genome.risks.value)
    validation = first_hypothesis.discriminating_test
    basis_sources = [
        {
            "ref": str(item.get("ref") or item.get("title") or item.get("source_file") or ""),
            "claim": str(item.get("claim_used") or ""),
            "implication": "",
        }
        for item in supporting
        if str(item.get("ref") or item.get("title") or item.get("source_file") or "").strip()
    ]
    creative = dossier.creative_context
    contribution_type = str(contribution.contribution_type) if contribution is not None else ""
    return {
        "title": thesis,
        "display_title": thesis,
        "idea_origin": dossier.genome.route,
        "constraint_status": "not_supported_by_current_evidence" if not basis_sources else "supplement",
        "counterfactual": validation,
        "practical_implication": contribution.what_changes_if_true if contribution is not None else thesis,
        "basis_summary": str(dossier.genome.opportunity.value),
        "basis_sources": basis_sources,
        "mechanism_family": f"seed:{dossier.genome.route}",
        "cross_domain_sources": [],
        "cross_domain_relation": "",
        "innovation": {
            "summary": contribution.statement if contribution is not None else thesis,
            # ``seed`` is lifecycle state, never an innovation type. These
            # strings all originate in the Generator's typed dossier.
            "type": contribution_type or "未由模型归类",
            "novelty_delta": creative.conceptual_leap or creative.research_program_potential,
            "non_incremental_reason": creative.surprising_prediction or creative.research_program_potential,
        },
        "minimum_validation": {
            "expected_signal": first_hypothesis.observable_prediction,
            "evidence_status": "proposed_not_verified",
            "source_refs": [],
        },
        "gate1_card": {
            "role_summary": creative.research_program_potential or creative.conceptual_leap or thesis,
            "evidence_interpretation": "; ".join(creative.reading_or_validation_upgrades) or creative.surprising_prediction or thesis,
            "selection_advice": creative.conceptual_leap or creative.research_program_potential or thesis,
            "risk_summary": risk,
            "user_edit_hint": creative.surprising_prediction or validation,
        },
    }


def _project_cross_domain_relation(value: object) -> str:
    """Map a readable LLM bridge explanation onto the retained relation taxonomy.

    CandidatePresentation keeps the original LLM wording. The historical Gate1
    schema instead expects a categorical relation. This adapter never infers a
    new scientific claim: it only assigns the broadest relation type supported
    by the wording so the structured compatibility view remains valid.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    if text in _CROSS_DOMAIN_RELATIONS:
        return text
    folded = text.casefold()
    if any(token in folded for token in ("mechanism", "机制", "causal pathway")):
        return "mechanism_bridge"
    if any(token in folded for token in ("evaluation", "metric", "评估", "指标", "测量")):
        return "evaluation_or_metric_bridge"
    if any(token in folded for token in ("baseline", "dataset", "benchmark", "基线", "数据集", "基准")):
        return "baseline_or_dataset_relevance"
    if any(token in folded for token in ("method", "methodology", "algorithm", "model", "技术", "方法", "算法", "模型", "融合", "transfer")):
        return "method_transfer"
    return "adjacent_application"


def _meaningful_explanation(value: object) -> bool:
    text = " ".join(str(value or "").split())
    return bool(text) and not bool(_EXPLANATION_PLACEHOLDER_RE.fullmatch(text))


def _pass2_review(candidate: dict[str, Any]) -> dict[str, Any]:
    is_seed = bool(candidate.get("degraded"))
    unscored = str(candidate.get("scoring_status") or "") == "unscored"
    unsupported = str(candidate.get("constraint_status") or "").strip().lower() == "not_supported_by_current_evidence"
    needs_enrichment = is_seed or unscored or unsupported
    return {
        "idea_id": candidate["id"],
        "visible_to_gate": True,
        # A faithfully projected Candidate can still be useful at Gate1 when
        # its presentation is incomplete.  It cannot, however, be labelled
        # ``proceed`` when its own constraint status says there is no current
        # evidence support.  That mismatch used to make the strict legacy
        # validator pause the whole T4 run instead of surfacing an honest
        # enrichment decision to the researcher.
        "screening_recommendation": "revise_before_selection" if needs_enrichment else "proceed",
        "counterfactual_check": "insufficient_evidence" if is_seed or unsupported else "independent",
        "counterfactual_note": candidate["counterfactual"],
        "nearest_prior_work": {"work": "not_computed", "distance": "not_computed"},
        "novelty_signal": "not_computed",
        "novelty_check": {"prior_art": "uncertain", "closest_baselines": [], "novelty_risk": "requires_t45_audit"},
        "feasibility_check": {"feasible_under_budget": not is_seed, "blocking_risks": ["Idea Seed 需要富化后再进入最终选择。"] if is_seed else []},
        "contribution_check": {
            "contribution_type": candidate["cdr_tuple"]["contribution_type"],
            "routine_risk": False,
            "reframe_needed": is_seed,
            "why": candidate["innovation"]["non_incremental_reason"],
        },
        "grounding_notes": [candidate["basis_summary"]],
        "selection_warning": (
            "该 Idea Seed 保持可见；需要补充证据与验证说明后才能进入最终选择。"
            if is_seed
            else "该候选作为探索性方向保持可见；当前证据尚不足以支持将其选为最终主张。"
            if unsupported
            else "独立评分当前不可用；该候选保持可见，但不参与自动排序。"
            if unscored
            else ""
        ),
    }


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
    candidate_by_id = {
        str(candidate.get("id") or "").strip(): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and str(candidate.get("id") or "").strip()
    }
    portfolio_ids = _current_portfolio_candidate_ids(store)
    # Native T4 creates exactly one Final Card for every Portfolio Candidate.
    # If this is an old structural projection without a Portfolio artifact,
    # there is deliberately no guessed human deck to render.
    visible_ids = portfolio_ids if portfolio_ids is not None else []
    completed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    incomplete: list[str] = []
    for candidate_id in visible_ids:
        candidate = candidate_by_id.get(candidate_id)
        final_card = _validated_final_card_payload(
            candidate.get("final_idea_card") if candidate is not None else None
        )
        if candidate is None or final_card is None:
            incomplete.append(candidate_id)
            continue
        completed.append((candidate, final_card))

    # Do not show a partial Portfolio. This mirrors the terminal renderer:
    # missing LLM explanations go to bounded repair while the Population stays
    # persisted for the recovery workflow.
    if portfolio_ids is None or not portfolio_ids or incomplete:
        missing_label = "、".join(incomplete) if incomplete else "当前 Portfolio"
        lines.extend(
            [
                "当前没有可展示的完整 LLM Final Card。请在 T4 恢复决策中选择继续 LLM 卡片修复；",
                "此文件不会用旧 Candidate 字段、`gate1_card` 或固定模板补写科研解释。",
                f"待修复卡片：{missing_label}",
                "",
            ]
        )
        store.path("ideation/_gate1_candidate_cards.md").write_text("\n".join(lines), encoding="utf-8")
        return

    for candidate, final_card in completed:
        lines.extend(
            [
                f"## {candidate['id']} · {final_card.get('short_title', '')}",
                "",
                "### 一句话命题",
                str(final_card.get("plain_language_summary") or ""),
                "",
                "### 核心命题",
                str(final_card.get("core_thesis") or ""),
                "",
                "### 为什么值得研究",
                str(final_card.get("why_it_matters") or ""),
                "",
                "### 当前问题与科学核心",
                str(final_card.get("current_failure") or ""),
                "",
                str(final_card.get("scientific_technical_core") or ""),
                "",
                "### 代表性场景",
                str(final_card.get("representative_scenario") or ""),
                "",
                "### 现实意义",
                str(final_card.get("real_world_significance") or ""),
                "",
                "### 创新",
                f"- 创新性质：{final_card.get('innovation_type', '')}",
                f"- 相对变化：{final_card.get('innovation_delta', '')}",
                f"- 非惯例理由：{final_card.get('non_routine_explanation', '')}",
                "",
                "### 关系与建议",
                f"- 与 Portfolio 的关系：{final_card.get('relationship_to_portfolio', '')}",
                f"- 组合建议：{final_card.get('composition_guidance', '')}",
                f"- 选择建议：{final_card.get('recommendation', '')}",
                f"- 瓶颈解释：{final_card.get('bottleneck_explanation', '')}",
                "",
                "### Evidence Status",
                str(final_card.get("evidence_status_summary") or ""),
                "",
                "### 风险与边界",
                *[f"- {item}" for item in final_card.get("risks_and_boundaries", [])],
                "",
                "### 当前不能主张",
                *[f"- {item}" for item in final_card.get("claims_not_to_make", [])],
                "",
                "### Candidate hypotheses",
            ]
        )
        for hypothesis in candidate.get("candidate_hypotheses", []):
            if isinstance(hypothesis, dict):
                lines.append(f"- **{hypothesis.get('id', '')}:** {hypothesis.get('statement', '')}")
        evolution_score = candidate.get("evolution_score") if isinstance(candidate.get("evolution_score"), dict) else {}
        dimensions = evolution_score.get("dimensions") if isinstance(evolution_score.get("dimensions"), dict) else {}
        if dimensions:
            lines.extend(["", "### 独立科研评分"])
            for key, value in dimensions.items():
                rationale = str((evolution_score.get("rationales") or {}).get(key) or "").strip()
                lines.append(f"- **{key} ({value}/5):** {rationale}" if rationale else f"- **{key} ({value}/5)**")
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


def _load_final_cards(
    store: T4ArtifactStore,
) -> tuple[dict[str, FinalIdeaCardTranslation], dict[str, str]]:
    """Load optional portfolio cards while isolating a bad single translation.

    A final-card translation is presentation enrichment.  One malformed card
    must not make the whole active Population or the other Portfolio cards
    disappear from Gate1.
    """

    try:
        payload = json.loads(store.path("ideation/final_cards/portfolio_cards.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}, {}
    raw_cards = payload.get("cards") if isinstance(payload, dict) and isinstance(payload.get("cards"), list) else []
    cards: dict[str, FinalIdeaCardTranslation] = {}
    diagnostics: dict[str, str] = {}
    for item in raw_cards:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        try:
            card = FinalIdeaCardTranslation.model_validate(item)
        except (TypeError, ValueError) as exc:
            if candidate_id:
                diagnostics[candidate_id] = _projection_diagnostic(
                    f"Final Idea Card 的展示字段未通过局部校验：{exc}"
                )
            continue
        cards[card.candidate_id] = card
    return cards, diagnostics


def _write_gate_brief(store: T4ArtifactStore, candidates: list[dict[str, Any]], population: PopulationSnapshot) -> None:
    origin_counts: dict[str, int] = {}
    for candidate in candidates:
        origin = candidate["idea_origin"]
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    selection_ready = [
        candidate
        for candidate in candidates
        if validate_candidate_selection_ready(candidate)[0]
    ]
    lines = [
        "# T4 Gate1 Selection Brief",
        "",
        f"Generation: {population.generation}",
        f"Active candidates: {len(candidates)}",
        "",
        "Select one complete candidate to prepare a pre-novelty hypothesis brief. A selection preserves the other candidates and can be rolled back. Keep candidates parallel when their mechanisms differ; request a composition only when you intentionally want a new compatibility-checked candidate.",
        "",
        (
            f"Selection-ready Candidates: {len(selection_ready)}."
            if selection_ready
            else "Selection-ready Candidates: 0. The visible Population has a structural gap such as a missing Final Card, score, core thesis, or draft hypothesis. Repair that gap before selecting T4.5."
        ),
        "",
        "## Population concentration",
        "Candidates remain visible even when they belong to a similar Idea Family, so the comparison is not silently narrowed before your decision.",
        "",
        "## Origin distribution",
    ]
    for origin, count in sorted(origin_counts.items()):
        lines.append(f"- {origin}: {count}")
    lines.extend(["", "## Novelty-Utility layout", "Use the score rationale and each Candidate's stated risk to compare higher-upside alternatives with candidates that have a clearer validation path. These are decision aids, not novelty conclusions.", "", "## Candidates"])
    for candidate in candidates:
        final_card = _validated_final_card_payload(candidate.get("final_idea_card"))
        if not final_card:
            continue
        lines.append(
            f"- **{candidate['id']}**: {final_card.get('short_title', '')} - {final_card.get('recommendation', '')}"
        )
    lines.extend(
        [
            "",
            "## Available actions",
            "- Select a full Candidate only when it is marked selection-ready. An independently scored IdeaSeed with a complete Final Card, core thesis, and at least one draft hypothesis may enter T4.5 as a provisional direction; its evidence and maturity warnings travel with the audit. An unscored or structurally incomplete Candidate must be repaired first.",
            "- Continue one evolution round to retain this version and produce additional Mutation Child or compatibility-gated Crossover Child candidates.",
            "- Keep multiple candidates parallel when they answer distinct questions.",
            "- Combine D1+D2 only as an explicit request. ResearchOS will create a new Human-composed Candidate, run a Compatibility Check and independent scoring, then ask for confirmation; it never concatenates candidates directly.",
            "",
            "Detailed candidate material is in `ideation/_gate1_candidate_cards.md`; structured population data is in `ideation/_candidate_directions.json` and Pass2 warnings are in `ideation/_pass2_grounding_review.json`.",
        ]
    )
    store.path("ideation/_gate1_selection_brief.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_bridge_coverage_review(
    store: T4ArtifactStore,
    candidates: list[dict[str, Any]],
    route_results: list[object],
) -> dict[str, list[str]]:
    """Project Bridge review state without inventing missing LLM reasoning.

    A Cross-domain route is an invited creative perspective, not a required
    output.  When the provider returns Candidates but omits its optional Bridge
    review (or the whole route is unavailable), Gate1 must still show the
    usable Population.  The projection therefore records an explicit
    ``unreviewed`` operational state.  It deliberately does *not* manufacture
    an escape-hatch rationale, a kill criterion, or a scientific judgement on
    the model's behalf.
    """

    outcome = {"degradations": []}

    plan_path = store.path("literature/bridge_domain_plan.json")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return outcome
    if not isinstance(plan, dict) or str(plan.get("source") or "").strip().casefold() == "none":
        return outcome
    domains = plan.get("bridge_domains") if isinstance(plan.get("bridge_domains"), list) else []
    domains = [item for item in domains if isinstance(item, dict) and str(item.get("bridge_id") or "").strip()]
    if not domains:
        return outcome
    reviews_by_id: dict[str, BridgeCoverageEntry] = {}
    invalid_review_codes: dict[str, str] = {}
    for route_result in route_results:
        raw_reviews = getattr(route_result, "bridge_reviews", [])
        for raw in raw_reviews if isinstance(raw_reviews, list) else []:
            review = raw if isinstance(raw, BridgeCoverageEntry) else BridgeCoverageEntry.model_validate(raw)
            if review.bridge_id in reviews_by_id:
                # A repeated optional review is not an evidence-integrity
                # failure. Choosing either version would make the controller
                # a scientific arbiter, so discard both review narratives and
                # surface an explicit enrichment diagnostic instead.
                reviews_by_id.pop(review.bridge_id, None)
                invalid_review_codes[review.bridge_id] = "llm_bridge_review_duplicate"
                continue
            if review.bridge_id in invalid_review_codes:
                continue
            reviews_by_id[review.bridge_id] = review
    active_ids = {item["id"] for item in candidates}
    candidate_bridge_ids = {
        bridge_id: [
            candidate["id"]
            for candidate in candidates
            if bridge_id in candidate.get("cross_domain_sources", [])
        ]
        for bridge_id in (str(item.get("bridge_id") or "").strip() for item in domains)
    }
    projected: list[dict[str, Any]] = []
    warnings: list[str] = []
    for domain in domains:
        bridge_id = str(domain.get("bridge_id") or "").strip()
        review = reviews_by_id.get(bridge_id)
        candidate_ids = list(candidate_bridge_ids.get(bridge_id, []))
        if review is not None:
            candidate_ids = [candidate_id for candidate_id in review.candidate_ids if candidate_id in active_ids]
            candidate_ids = list(dict.fromkeys([*candidate_ids, *candidate_bridge_ids.get(bridge_id, [])]))
            if not candidate_ids and (review.visible_to_gate or review.escape_status != "no_candidate_available"):
                # The review is tied to an old or inconsistent Population. It
                # must not hide, delete, or reinterpret the current active
                # Population, and the controller cannot rewrite its
                # scientific rationale. Treat it as an unavailable optional
                # enrichment and leave a durable diagnostic for an LLM retry.
                invalid_review_codes[bridge_id] = "llm_bridge_review_population_mismatch"
                review = None
        if review is None:
            # Do not fill in LLM-authored fields deterministically.  This is a
            # durable absence receipt: a reviewer can see that the bridge was
            # neither declared successful nor silently discarded.
            diagnostic_code = invalid_review_codes.get(bridge_id, "llm_bridge_review_missing")
            diagnostic_message = {
                "llm_bridge_review_duplicate": (
                    "The Cross-domain Route returned multiple LLM Bridge reviews for this bridge. "
                    "ResearchOS retained no arbitrary review narrative; a single review may be requested later."
                ),
                "llm_bridge_review_population_mismatch": (
                    "The LLM Bridge review did not match the current active Population. "
                    "No bridge-specific decision or evidence judgement was inferred."
                ),
                "llm_bridge_review_missing": (
                    "The Cross-domain Route completed without a separate LLM Bridge review. "
                    "No bridge-specific decision or evidence judgement was inferred."
                ),
            }[diagnostic_code]
            warnings.append(
                f"{bridge_id}: Cross-domain Bridge review is unavailable ({diagnostic_code}); "
                "candidate linkage, if any, remains visible and needs review."
            )
            degradation = f"bridge_review_unavailable:{bridge_id}"
            if diagnostic_code != "llm_bridge_review_missing":
                degradation = f"{degradation}:{diagnostic_code}"
            outcome["degradations"].append(degradation)
            projected.append(
                {
                    "bridge_id": bridge_id,
                    "bridge_name": str(domain.get("name") or "").strip(),
                    "bridge_rationale": str(domain.get("why") or "").strip(),
                    "planned_queries": [str(query).strip() for query in domain.get("queries", []) if str(query).strip()]
                    if isinstance(domain.get("queries"), list)
                    else [],
                    "priority": str(domain.get("priority") or "should_explore"),
                    "candidate_ids": candidate_ids,
                    "visible_to_gate": bool(candidate_ids),
                    "forced_surfaced": bool(domain.get("priority") == "must_explore" and candidate_ids),
                    "selected_into_hypotheses": False,
                    "review_status": "unreviewed",
                    "review_diagnostic": {
                        "code": diagnostic_code,
                        "message": diagnostic_message,
                    },
                }
            )
            continue
        if candidate_ids and not review.visible_to_gate:
            # Gate visibility is a deterministic governance consequence of the
            # active Population. Preserve the LLM-authored deferred/rejection
            # rationale and kill criteria, but never let an inconsistent
            # boolean hide a Candidate that the current Population retains.
            warnings.append(
                f"{bridge_id}: active Candidate(s) were surfaced at Gate1 despite the route review's hidden flag; "
                "the LLM-authored escape rationale remains attached."
            )
        if not candidate_ids:
            warnings.append(f"{bridge_id}: no active bridge candidate; escape hatch is shown at Gate1.")
        projected.append(
            {
                "bridge_id": bridge_id,
                "bridge_name": str(domain.get("name") or "").strip(),
                "bridge_rationale": str(domain.get("why") or "").strip(),
                "planned_queries": [str(query).strip() for query in domain.get("queries", []) if str(query).strip()]
                if isinstance(domain.get("queries"), list)
                else [],
                "priority": str(domain.get("priority") or "should_explore"),
                "candidate_ids": candidate_ids,
                "visible_to_gate": bool(candidate_ids),
                "forced_surfaced": bool(domain.get("priority") == "must_explore" and candidate_ids),
                "selected_into_hypotheses": False,
                "review_status": "llm_reviewed",
                "decision_summary": review.decision_summary,
                "escape_hatch": {
                    "status": review.escape_status,
                    "reason": review.escape_reason,
                    "falsification_or_kill_criteria": review.falsification_or_kill_criteria,
                    "can_revisit_if": review.can_revisit_if,
                },
            }
        )
    store.write_json(
        "ideation/bridge_coverage_review.json",
        {
            "version": "1.0.0",
            "semantics": "bridge_candidate_visibility_and_escape_hatch_review",
            "source_bridge_plan": "literature/bridge_domain_plan.json",
            "bridge_reviews": projected,
            "warnings": warnings,
        },
    )
    return outcome
